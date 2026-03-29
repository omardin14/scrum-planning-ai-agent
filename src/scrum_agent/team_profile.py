"""Team profile — data model and SQLite store for team learning calibration.

# See README: "Scrum Standards" — team learning, self-calibrating estimates
#
# Stores historical analysis of a team's actual sprint data (from Jira or AzDO)
# so that future plans can be calibrated to how THIS team actually works.
# The profile captures: what each story point value means in practice,
# story shape patterns by discipline, epic sizing norms, and estimation accuracy.
#
# Frozen dataclasses follow the same immutability pattern as Feature, UserStory, etc.
# SQLite storage uses the existing ~/.scrum-agent/sessions.db with a new table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model — frozen dataclasses for team calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoryPointCalibration:
    """Calibration data for a single story point value (1/2/3/5/8).

    Captures what this point value actually means for the team based on
    historical sprint data: average cycle time, common patterns, and
    how often stories at this size overshoot their estimate.

    # See README: "Scrum Standards" — story points, team learning
    """

    point_value: int  # 1, 2, 3, 5, or 8
    avg_cycle_time_days: float = 0.0  # Average days from started to done
    sample_count: int = 0  # Number of stories at this point value
    common_patterns: tuple[str, ...] = ()  # e.g. ("single API endpoint", "config change")
    typical_task_count: float = 0.0  # Average sub-tasks per story at this size
    overshoot_pct: float = 0.0  # % of stories that took longer than expected


@dataclass(frozen=True)
class StoryShapePattern:
    """Average story shape for a given discipline (frontend, backend, etc.).

    # See README: "Scrum Standards" — discipline tagging, team learning
    """

    discipline: str  # "frontend", "backend", "fullstack", etc.
    avg_points: float = 0.0
    avg_ac_count: float = 0.0
    avg_task_count: float = 0.0
    sample_count: int = 0


@dataclass(frozen=True)
class EpicPattern:
    """Typical epic sizing based on historical data.

    # See README: "Scrum Standards" — team learning
    """

    avg_stories_per_epic: float = 0.0
    avg_points_per_epic: float = 0.0
    typical_story_count_range: tuple[int, int] = (0, 0)  # (min, max) stories per epic
    sample_count: int = 0


@dataclass(frozen=True)
class SpilloverStats:
    """Sprint spillover metrics — stories that slipped to the next sprint."""

    carried_over_pct: float = 0.0  # % stories that slipped to next sprint
    avg_spillover_pts: float = 0.0  # avg story points that slip per sprint
    most_common_spillover_reason: str = ""  # e.g. "backend stories > 5 pts"


@dataclass(frozen=True)
class DoDSignal:
    """Behavioural Definition of Done — inferred from ticket comments/descriptions.

    Not a formal checklist field (teams rarely fill those in) but a behavioural
    fingerprint: what actions consistently appear in tickets before they're closed.
    """

    common_checklist_items: tuple[str, ...] = ()
    stories_with_comments_pct: float = 0.0
    stories_with_pr_link_pct: float = 0.0
    stories_with_review_mention_pct: float = 0.0
    stories_with_testing_mention_pct: float = 0.0
    stories_with_deploy_mention_pct: float = 0.0
    avg_comments_before_resolution: float = 0.0


@dataclass(frozen=True)
class WritingPatterns:
    """How the team writes stories, tasks, and epics — inferred from text analysis."""

    median_ac_count: float = 0.0
    median_task_count_per_story: float = 0.0
    subtask_label_distribution: tuple[tuple[str, float], ...] = ()
    common_subtask_patterns: tuple[str, ...] = ()
    subtasks_use_consistent_naming: bool = False
    common_personas: tuple[str, ...] = ()
    uses_given_when_then: bool = False
    epic_description_length_avg: int = 0
    stories_with_subtasks_pct: float = 0.0
    epics_with_description_pct: float = 0.0


@dataclass(frozen=True)
class ScopeChangeEvent:
    """A single scope change within a sprint (story added, removed, or re-estimated).

    # See README: "Scrum Standards" — scope tracking, velocity accuracy
    """

    date: str = ""  # ISO date of the change
    issue_key: str = ""
    issue_url: str = ""
    summary: str = ""
    change_type: str = ""  # "added", "removed", "re_estimated_up", "re_estimated_down", "pointed"
    from_pts: float = 0.0
    to_pts: float = 0.0
    delta_pts: float = 0.0  # positive = scope increase, negative = decrease


@dataclass(frozen=True)
class DailyScopeSnapshot:
    """Sprint scope on a single day — which stories were in scope and total points.

    # See README: "Scrum Standards" — daily scope tracking
    """

    date: str = ""  # ISO date
    total_scope_pts: float = 0.0
    stories_in_sprint: tuple[tuple[str, float], ...] = ()  # ((issue_key, points), ...)


@dataclass(frozen=True)
class SprintScopeTimeline:
    """Day-by-day scope reconstruction for a single sprint.

    Tracks committed scope (day 1), final scope, delivered points,
    and every scope change event in between.

    # See README: "Scrum Standards" — scope tracking, velocity accuracy
    """

    sprint_name: str = ""
    committed_pts: float = 0.0  # scope on day 1
    final_pts: float = 0.0  # scope on last day
    delivered_pts: float = 0.0  # completed points
    scope_change_total: float = 0.0  # net change (final - committed)
    scope_churn: float = 0.0  # sum of absolute daily deltas / committed
    daily_snapshots: tuple[DailyScopeSnapshot, ...] = ()
    change_events: tuple[ScopeChangeEvent, ...] = ()


@dataclass(frozen=True)
class TeamProfile:
    """Top-level container for a team's calibration data.

    Built from historical sprint analysis (Jira or AzDO) and persisted
    to SQLite so future planning sessions can use team-specific calibration
    instead of generic Fibonacci rules.

    # See README: "Scrum Standards" — team learning, self-calibrating estimates
    """

    team_id: str  # Unique identifier (e.g. "jira-PROJ" or "azdevops-MyProject")
    source: str  # "jira" or "azdevops"
    project_key: str  # Jira project key or AzDO project name
    sample_sprints: int = 0  # Number of sprints analysed
    sample_stories: int = 0  # Total stories analysed
    velocity_avg: float = 0.0  # Average velocity (story points per sprint)
    velocity_stddev: float = 0.0  # Velocity standard deviation
    point_calibrations: tuple[StoryPointCalibration, ...] = ()
    story_shapes: tuple[StoryShapePattern, ...] = ()
    epic_pattern: EpicPattern = field(default_factory=EpicPattern)
    estimation_accuracy_pct: float = 0.0  # % of stories completed at original estimate
    sprint_completion_rate: float = 0.0  # % of planned stories completed per sprint
    spillover: SpilloverStats = field(default_factory=SpilloverStats)
    dod_signal: DoDSignal = field(default_factory=DoDSignal)
    writing_patterns: WritingPatterns = field(default_factory=WritingPatterns)
    sprints_fully_completed: int = 0
    sprints_partially_completed: int = 0
    sprints_analysed: int = 0
    created_at: str = ""  # ISO timestamp
    updated_at: str = ""  # ISO timestamp


# ---------------------------------------------------------------------------
# Incremental merge — weighted combination of old and new profiles
# ---------------------------------------------------------------------------


def _wavg(old: float, new: float, old_w: int, new_w: int) -> float:
    """Weighted average favouring the newer profile."""
    total = old_w + new_w
    if total == 0:
        return new
    return round((old * old_w + new * new_w) / total, 1)


def merge_profiles(old: TeamProfile, new: TeamProfile) -> TeamProfile:
    """Merge a new analysis into an existing profile using weighted averaging.

    The new profile's data is combined with the old using sample-weighted
    averages. This gives better statistical significance while still letting
    recent data have proportional influence. Qualitative fields (DoD, writing
    patterns) are replaced outright since they reflect current team behaviour.
    """
    ow = old.sample_stories
    nw = new.sample_stories

    # Merge point calibrations
    old_cals = {c.point_value: c for c in old.point_calibrations}
    new_cals = {c.point_value: c for c in new.point_calibrations}
    merged_cals = []
    for pts in (1, 2, 3, 5, 8):
        oc = old_cals.get(pts)
        nc = new_cals.get(pts)
        if nc and oc and oc.sample_count > 0 and nc.sample_count > 0:
            os, ns = oc.sample_count, nc.sample_count
            merged_cals.append(
                StoryPointCalibration(
                    point_value=pts,
                    avg_cycle_time_days=_wavg(oc.avg_cycle_time_days, nc.avg_cycle_time_days, os, ns),
                    sample_count=os + ns,
                    common_patterns=nc.common_patterns or oc.common_patterns,
                    typical_task_count=_wavg(oc.typical_task_count, nc.typical_task_count, os, ns),
                    overshoot_pct=_wavg(oc.overshoot_pct, nc.overshoot_pct, os, ns),
                )
            )
        elif nc and nc.sample_count > 0:
            merged_cals.append(nc)
        elif oc and oc.sample_count > 0:
            merged_cals.append(oc)

    # Merge story shapes
    old_shapes = {s.discipline: s for s in old.story_shapes}
    new_shapes = {s.discipline: s for s in new.story_shapes}
    merged_shapes = []
    for disc in sorted(set(old_shapes) | set(new_shapes)):
        os = old_shapes.get(disc)
        ns = new_shapes.get(disc)
        if ns and os and os.sample_count > 0 and ns.sample_count > 0:
            oc, nc = os.sample_count, ns.sample_count
            merged_shapes.append(
                StoryShapePattern(
                    discipline=disc,
                    avg_points=_wavg(os.avg_points, ns.avg_points, oc, nc),
                    avg_ac_count=_wavg(os.avg_ac_count, ns.avg_ac_count, oc, nc),
                    avg_task_count=_wavg(os.avg_task_count, ns.avg_task_count, oc, nc),
                    sample_count=oc + nc,
                )
            )
        elif ns:
            merged_shapes.append(ns)
        elif os:
            merged_shapes.append(os)

    return TeamProfile(
        team_id=new.team_id,
        source=new.source,
        project_key=new.project_key,
        sample_sprints=old.sample_sprints + new.sample_sprints,
        sample_stories=ow + nw,
        velocity_avg=_wavg(old.velocity_avg, new.velocity_avg, ow, nw),
        velocity_stddev=_wavg(old.velocity_stddev, new.velocity_stddev, ow, nw),
        point_calibrations=tuple(merged_cals),
        story_shapes=tuple(merged_shapes),
        epic_pattern=new.epic_pattern if new.epic_pattern.sample_count > 0 else old.epic_pattern,
        estimation_accuracy_pct=_wavg(old.estimation_accuracy_pct, new.estimation_accuracy_pct, ow, nw),
        sprint_completion_rate=_wavg(old.sprint_completion_rate, new.sprint_completion_rate, ow, nw),
        spillover=new.spillover,
        dod_signal=new.dod_signal,
        writing_patterns=new.writing_patterns,
        sprints_fully_completed=old.sprints_fully_completed + new.sprints_fully_completed,
        sprints_partially_completed=old.sprints_partially_completed + new.sprints_partially_completed,
        sprints_analysed=old.sprints_analysed + new.sprints_analysed,
        created_at=old.created_at,
        updated_at=new.updated_at,
    )


# ---------------------------------------------------------------------------
# SQLite table schema
# ---------------------------------------------------------------------------

_TEAM_PROFILES_SCHEMA = """\
CREATE TABLE IF NOT EXISTS team_profiles (
    team_id        TEXT PRIMARY KEY,
    project_key    TEXT NOT NULL,
    source         TEXT NOT NULL,
    profile_json   TEXT NOT NULL,
    examples_json  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);"""

# Migration: add examples_json column to existing tables
_ADD_EXAMPLES_COL = """\
ALTER TABLE team_profiles ADD COLUMN examples_json TEXT;
"""


# ---------------------------------------------------------------------------
# Serialisation helpers — same pattern as sessions.py
# ---------------------------------------------------------------------------


def _profile_to_json(profile: TeamProfile) -> str:
    """Serialize a TeamProfile to JSON string."""
    d = asdict(profile)
    return json.dumps(d, ensure_ascii=False)


def _examples_to_json(examples: dict) -> str:
    """Serialize the examples dict to JSON, handling dataclass objects."""

    def _default(obj: object) -> object:
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)  # type: ignore[arg-type]
        return str(obj)

    return json.dumps(examples, ensure_ascii=False, default=_default)


def _json_to_examples(json_str: str) -> dict:
    """Deserialize examples JSON, reconstructing scope timeline dataclasses."""
    d = json.loads(json_str)
    # Reconstruct SprintScopeTimeline objects in scope_changes.timelines
    scope = d.get("scope_changes")
    if isinstance(scope, dict):
        raw_timelines = scope.get("timelines", [])
        rebuilt: list[SprintScopeTimeline] = []
        for tl in raw_timelines:
            if isinstance(tl, dict):
                rebuilt.append(
                    SprintScopeTimeline(
                        sprint_name=tl.get("sprint_name", ""),
                        committed_pts=tl.get("committed_pts", 0.0),
                        final_pts=tl.get("final_pts", 0.0),
                        delivered_pts=tl.get("delivered_pts", 0.0),
                        scope_change_total=tl.get("scope_change_total", 0.0),
                        scope_churn=tl.get("scope_churn", 0.0),
                        daily_snapshots=tuple(
                            DailyScopeSnapshot(
                                date=s.get("date", ""),
                                total_scope_pts=s.get("total_scope_pts", 0.0),
                                stories_in_sprint=tuple(tuple(p) for p in s.get("stories_in_sprint", ())),
                            )
                            for s in tl.get("daily_snapshots", ())
                        ),
                        change_events=tuple(
                            ScopeChangeEvent(
                                **{
                                    k: ev.get(k, df)
                                    for k, df in [
                                        ("date", ""),
                                        ("issue_key", ""),
                                        ("issue_url", ""),
                                        ("summary", ""),
                                        ("change_type", ""),
                                        ("from_pts", 0.0),
                                        ("to_pts", 0.0),
                                        ("delta_pts", 0.0),
                                    ]
                                }
                            )
                            for ev in tl.get("change_events", ())
                        ),
                    )
                )
        scope["timelines"] = rebuilt
    return d


def _dict_to_point_calibration(d: dict) -> StoryPointCalibration:
    """Reconstruct a StoryPointCalibration from a JSON-parsed dict."""
    return StoryPointCalibration(
        point_value=d["point_value"],
        avg_cycle_time_days=d.get("avg_cycle_time_days", 0.0),
        sample_count=d.get("sample_count", 0),
        common_patterns=tuple(d.get("common_patterns", ())),
        typical_task_count=d.get("typical_task_count", 0.0),
        overshoot_pct=d.get("overshoot_pct", 0.0),
    )


def _dict_to_story_shape(d: dict) -> StoryShapePattern:
    """Reconstruct a StoryShapePattern from a JSON-parsed dict."""
    return StoryShapePattern(
        discipline=d["discipline"],
        avg_points=d.get("avg_points", 0.0),
        avg_ac_count=d.get("avg_ac_count", 0.0),
        avg_task_count=d.get("avg_task_count", 0.0),
        sample_count=d.get("sample_count", 0),
    )


def _dict_to_epic_pattern(d: dict) -> EpicPattern:
    """Reconstruct an EpicPattern from a JSON-parsed dict."""
    range_raw = d.get("typical_story_count_range", [0, 0])
    return EpicPattern(
        avg_stories_per_epic=d.get("avg_stories_per_epic", 0.0),
        avg_points_per_epic=d.get("avg_points_per_epic", 0.0),
        typical_story_count_range=tuple(range_raw) if len(range_raw) == 2 else (0, 0),
        sample_count=d.get("sample_count", 0),
    )


def _dict_to_spillover_stats(d: dict) -> SpilloverStats:
    """Reconstruct a SpilloverStats from a JSON-parsed dict."""
    return SpilloverStats(
        carried_over_pct=d.get("carried_over_pct", 0.0),
        avg_spillover_pts=d.get("avg_spillover_pts", 0.0),
        most_common_spillover_reason=d.get("most_common_spillover_reason", ""),
    )


def _dict_to_dod_signal(d: dict) -> DoDSignal:
    """Reconstruct a DoDSignal from a JSON-parsed dict."""
    return DoDSignal(
        common_checklist_items=tuple(d.get("common_checklist_items", ())),
        stories_with_comments_pct=d.get("stories_with_comments_pct", 0.0),
        stories_with_pr_link_pct=d.get("stories_with_pr_link_pct", 0.0),
        stories_with_review_mention_pct=d.get("stories_with_review_mention_pct", 0.0),
        stories_with_testing_mention_pct=d.get("stories_with_testing_mention_pct", 0.0),
        stories_with_deploy_mention_pct=d.get("stories_with_deploy_mention_pct", 0.0),
        avg_comments_before_resolution=d.get("avg_comments_before_resolution", 0.0),
    )


def _dict_to_writing_patterns(d: dict) -> WritingPatterns:
    """Reconstruct a WritingPatterns from a JSON-parsed dict."""
    raw_dist = d.get("subtask_label_distribution", ())
    dist = tuple(tuple(pair) for pair in raw_dist) if raw_dist else ()
    return WritingPatterns(
        median_ac_count=d.get("median_ac_count", 0.0),
        median_task_count_per_story=d.get("median_task_count_per_story", 0.0),
        subtask_label_distribution=dist,
        common_subtask_patterns=tuple(d.get("common_subtask_patterns", ())),
        subtasks_use_consistent_naming=d.get("subtasks_use_consistent_naming", False),
        common_personas=tuple(d.get("common_personas", ())),
        uses_given_when_then=d.get("uses_given_when_then", False),
        epic_description_length_avg=d.get("epic_description_length_avg", 0),
        stories_with_subtasks_pct=d.get("stories_with_subtasks_pct", 0.0),
        epics_with_description_pct=d.get("epics_with_description_pct", 0.0),
    )


def _json_to_profile(json_str: str) -> TeamProfile:
    """Reconstruct a TeamProfile from a JSON string."""
    d = json.loads(json_str)
    return TeamProfile(
        team_id=d["team_id"],
        source=d["source"],
        project_key=d["project_key"],
        sample_sprints=d.get("sample_sprints", 0),
        sample_stories=d.get("sample_stories", 0),
        velocity_avg=d.get("velocity_avg", 0.0),
        velocity_stddev=d.get("velocity_stddev", 0.0),
        point_calibrations=tuple(_dict_to_point_calibration(pc) for pc in d.get("point_calibrations", ())),
        story_shapes=tuple(_dict_to_story_shape(ss) for ss in d.get("story_shapes", ())),
        epic_pattern=_dict_to_epic_pattern(d.get("epic_pattern", {})),
        estimation_accuracy_pct=d.get("estimation_accuracy_pct", 0.0),
        sprint_completion_rate=d.get("sprint_completion_rate", 0.0),
        spillover=_dict_to_spillover_stats(d.get("spillover", {})),
        dod_signal=_dict_to_dod_signal(d.get("dod_signal", {})),
        writing_patterns=_dict_to_writing_patterns(d.get("writing_patterns", {})),
        sprints_fully_completed=d.get("sprints_fully_completed", 0),
        sprints_partially_completed=d.get("sprints_partially_completed", 0),
        sprints_analysed=d.get("sprints_analysed", 0),
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
    )


# ---------------------------------------------------------------------------
# TeamProfileStore — SQLite CRUD
# ---------------------------------------------------------------------------


class TeamProfileStore:
    """SQLite-backed store for team calibration profiles.

    Uses the same database as SessionStore (sessions.db) with a separate
    ``team_profiles`` table. Follows the same patterns: autocommit mode,
    context manager support, explicit close.

    # See README: "Memory & State" — session persistence
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None
        self._conn.execute(_TEAM_PROFILES_SCHEMA)
        # Migrate: add examples_json column if missing
        try:
            self._conn.execute(_ADD_EXAMPLES_COL)
        except sqlite3.OperationalError:
            pass  # column already exists

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> TeamProfileStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    # ── Write operations ──────────────────────────────────────────────────

    def save(self, profile: TeamProfile, *, examples: dict | None = None) -> None:
        """Insert or update a team profile, optionally with examples data."""
        now = datetime.now(UTC).isoformat()
        json_str = _profile_to_json(profile)
        ex_str = _examples_to_json(examples) if examples else None
        self._conn.execute(
            """INSERT INTO team_profiles
                   (team_id, project_key, source, profile_json, examples_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(team_id) DO UPDATE SET
                   project_key = excluded.project_key,
                   source = excluded.source,
                   profile_json = excluded.profile_json,
                   examples_json = excluded.examples_json,
                   updated_at = excluded.updated_at""",
            (profile.team_id, profile.project_key, profile.source, json_str, ex_str, now, now),
        )
        logger.info("Saved team profile: %s", profile.team_id)

    def delete(self, team_id: str) -> bool:
        """Delete a team profile by ID and clean up associated exports/logs.

        Removes the SQLite row, export files (HTML/MD), and analysis logs
        for the project_key associated with this profile.
        """
        # Look up project_key before deleting the row
        row = self._conn.execute(
            "SELECT project_key FROM team_profiles WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        project_key = row[0] if row else None

        cursor = self._conn.execute("DELETE FROM team_profiles WHERE team_id = ?", (team_id,))
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Deleted team profile: %s", team_id)
            if project_key:
                self._cleanup_files(project_key)
        return deleted

    def _cleanup_files(self, project_key: str) -> None:
        """Remove exports and logs associated with a project_key."""
        import shutil

        base = self._db_path.parent  # ~/.scrum-agent/
        pk_lower = project_key.lower()

        # Remove export directory: ~/.scrum-agent/exports/{project_key}/
        export_dir = base / "exports" / pk_lower
        if export_dir.is_dir():
            shutil.rmtree(export_dir, ignore_errors=True)
            logger.info("Removed exports: %s", export_dir)

        # Remove analysis logs: ~/.scrum-agent/logs/team-analysis-{project_key}-*.log
        log_dir = base / "logs"
        if log_dir.is_dir():
            prefix = f"team-analysis-{pk_lower}-"
            for f in log_dir.iterdir():
                if f.name.startswith(prefix) and f.is_file():
                    f.unlink(missing_ok=True)
                    logger.info("Removed log: %s", f.name)

    # ── Read operations ───────────────────────────────────────────────────

    def load(self, team_id: str) -> TeamProfile | None:
        """Load a team profile by ID, or None if not found."""
        row = self._conn.execute(
            "SELECT profile_json FROM team_profiles WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return _json_to_profile(row[0])
        except Exception:
            logger.error("Failed to deserialize team profile: %s", team_id)
            return None

    def load_with_examples(self, team_id: str) -> tuple[TeamProfile | None, dict | None]:
        """Load a team profile and its examples dict by ID."""
        row = self._conn.execute(
            "SELECT profile_json, examples_json FROM team_profiles WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        if row is None:
            return None, None
        try:
            profile = _json_to_profile(row[0])
        except Exception:
            logger.error("Failed to deserialize team profile: %s", team_id)
            return None, None
        examples = _json_to_examples(row[1]) if row[1] else None
        return profile, examples

    def load_by_project(self, project_key: str, source: str) -> TeamProfile | None:
        """Load a team profile by project key and source (jira/azdevops)."""
        row = self._conn.execute(
            "SELECT profile_json FROM team_profiles WHERE project_key = ? AND source = ?",
            (project_key, source),
        ).fetchone()
        if row is None:
            return None
        try:
            return _json_to_profile(row[0])
        except Exception:
            logger.error("Failed to deserialize team profile for %s/%s", source, project_key)
            return None

    def list_profiles(self) -> list[TeamProfile]:
        """Return all stored team profiles.

        Uses the DB's updated_at column (set on save) rather than the JSON field,
        since the profile object may have been created with updated_at=''.
        """
        rows = self._conn.execute(
            "SELECT profile_json, updated_at FROM team_profiles ORDER BY updated_at DESC"
        ).fetchall()
        profiles = []
        for json_str, db_updated_at in rows:
            try:
                p = _json_to_profile(json_str)
                # Override with the DB timestamp if the JSON field is empty
                if db_updated_at and not p.updated_at:
                    # Frozen dataclass — reconstruct with updated_at
                    p = TeamProfile(
                        **{
                            **{f.name: getattr(p, f.name) for f in p.__dataclass_fields__.values()},
                            "updated_at": db_updated_at,
                        }
                    )
                profiles.append(p)
            except Exception:
                logger.error("Skipping corrupt team profile row")
        return profiles
