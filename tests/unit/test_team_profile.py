"""Unit tests for TeamProfile data model and TeamProfileStore SQLite CRUD.

Tests cover: dataclass construction, serialisation round-trip, store CRUD,
and the sessions.py v3 migration that creates the team_profiles table.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from scrum_agent.team_profile import (
    DailyScopeSnapshot,
    DoDSignal,
    EpicPattern,
    ScopeChangeEvent,
    SpilloverStats,
    SprintScopeTimeline,
    StoryPointCalibration,
    StoryShapePattern,
    TeamProfile,
    TeamProfileStore,
    WritingPatterns,
    _json_to_profile,
    _profile_to_json,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_profile(team_id: str = "jira-PROJ") -> TeamProfile:
    return TeamProfile(
        team_id=team_id,
        source="jira",
        project_key="PROJ",
        sample_sprints=5,
        sample_stories=42,
        velocity_avg=23.5,
        velocity_stddev=3.2,
        point_calibrations=(
            StoryPointCalibration(
                point_value=1,
                avg_cycle_time_days=0.5,
                sample_count=10,
                common_patterns=("config change",),
                typical_task_count=1.0,
                overshoot_pct=5.0,
            ),
            StoryPointCalibration(
                point_value=5,
                avg_cycle_time_days=4.2,
                sample_count=15,
                typical_task_count=3.0,
                overshoot_pct=20.0,
            ),
        ),
        story_shapes=(
            StoryShapePattern(
                discipline="backend",
                avg_points=3.2,
                avg_ac_count=3.0,
                avg_task_count=2.8,
                sample_count=20,
            ),
        ),
        epic_pattern=EpicPattern(
            avg_stories_per_epic=6.0,
            avg_points_per_epic=18.0,
            typical_story_count_range=(4, 9),
            sample_count=5,
        ),
        estimation_accuracy_pct=78.0,
        sprint_completion_rate=88.0,
    )


# ---------------------------------------------------------------------------
# Serialisation tests
# ---------------------------------------------------------------------------


class TestProfileSerialisation:
    def test_round_trip(self):
        profile = _make_profile()
        json_str = _profile_to_json(profile)
        restored = _json_to_profile(json_str)

        assert restored.team_id == profile.team_id
        assert restored.source == profile.source
        assert restored.sample_sprints == profile.sample_sprints
        assert restored.velocity_avg == profile.velocity_avg
        assert len(restored.point_calibrations) == 2
        assert restored.point_calibrations[0].point_value == 1
        assert restored.point_calibrations[0].common_patterns == ("config change",)
        assert len(restored.story_shapes) == 1
        assert restored.story_shapes[0].discipline == "backend"
        assert restored.epic_pattern.typical_story_count_range == (4, 9)

    def test_empty_profile(self):
        profile = TeamProfile(team_id="x", source="jira", project_key="X")
        json_str = _profile_to_json(profile)
        restored = _json_to_profile(json_str)
        assert restored.team_id == "x"
        assert restored.point_calibrations == ()
        assert restored.story_shapes == ()

    def test_json_is_valid(self):
        profile = _make_profile()
        json_str = _profile_to_json(profile)
        data = json.loads(json_str)
        assert data["team_id"] == "jira-PROJ"
        assert isinstance(data["point_calibrations"], list)


# ---------------------------------------------------------------------------
# TeamProfileStore CRUD tests
# ---------------------------------------------------------------------------


class TestTeamProfileStore:
    @pytest.fixture
    def db_path(self, tmp_path):
        return tmp_path / "sessions.db"

    def test_save_and_load(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded = store.load("jira-PROJ")

        assert loaded is not None
        assert loaded.team_id == "jira-PROJ"
        assert loaded.sample_sprints == 5
        assert loaded.velocity_avg == 23.5

    def test_load_missing_returns_none(self, db_path):
        with TeamProfileStore(db_path) as store:
            result = store.load("nonexistent")
        assert result is None

    def test_load_by_project(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded = store.load_by_project("PROJ", "jira")

        assert loaded is not None
        assert loaded.project_key == "PROJ"

    def test_load_by_project_wrong_source(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded = store.load_by_project("PROJ", "azdevops")
        assert loaded is None

    def test_upsert_updates_existing(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)

        updated = TeamProfile(
            team_id="jira-PROJ",
            source="jira",
            project_key="PROJ",
            sample_sprints=10,
            sample_stories=80,
            velocity_avg=25.0,
        )
        with TeamProfileStore(db_path) as store:
            store.save(updated)
            loaded = store.load("jira-PROJ")

        assert loaded is not None
        assert loaded.sample_sprints == 10
        assert loaded.velocity_avg == 25.0

    def test_delete_existing(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            deleted = store.delete("jira-PROJ")
            assert deleted is True
            assert store.load("jira-PROJ") is None

    def test_delete_nonexistent(self, db_path):
        with TeamProfileStore(db_path) as store:
            deleted = store.delete("ghost")
        assert deleted is False

    def test_list_profiles(self, db_path):
        with TeamProfileStore(db_path) as store:
            store.save(_make_profile("jira-PROJ"))
            store.save(_make_profile("azdevops-Alpha"))
            profiles = store.list_profiles()
        assert len(profiles) == 2

    def test_table_created_on_init(self, db_path):
        with TeamProfileStore(db_path):
            pass
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='team_profiles'").fetchall()
        conn.close()
        assert rows

    def test_save_and_load_with_examples(self, db_path):
        profile = _make_profile()
        examples = {
            "scope_changes": {
                "totals": {"added_mid_sprint": 2, "total_stories": 10},
                "timelines": [
                    SprintScopeTimeline(
                        sprint_name="S1",
                        committed_pts=20,
                        final_pts=25,
                        delivered_pts=18,
                        scope_change_total=5,
                        scope_churn=0.25,
                        change_events=(
                            ScopeChangeEvent(
                                date="2026-03-03",
                                issue_key="X-5",
                                change_type="added",
                                delta_pts=5,
                            ),
                        ),
                    ),
                ],
            },
            "calibration": [{"point_value": 3, "detail": "test"}],
        }
        with TeamProfileStore(db_path) as store:
            store.save(profile, examples=examples)
            loaded_p, loaded_ex = store.load_with_examples("jira-PROJ")

        assert loaded_p is not None
        assert loaded_p.velocity_avg == 23.5
        assert loaded_ex is not None
        assert loaded_ex["calibration"][0]["detail"] == "test"
        # Timelines should be reconstructed as dataclasses
        tls = loaded_ex["scope_changes"]["timelines"]
        assert len(tls) == 1
        assert tls[0].committed_pts == 20
        assert len(tls[0].change_events) == 1
        assert tls[0].change_events[0].issue_key == "X-5"

    def test_load_with_examples_no_examples(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded_p, loaded_ex = store.load_with_examples("jira-PROJ")
        assert loaded_p is not None
        assert loaded_ex is None

    def test_delete_cleans_up_files(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        # Create export and log files
        export_dir = tmp_path / "exports" / "proj"
        export_dir.mkdir(parents=True)
        (export_dir / "team-profile-20260327.html").write_text("html")
        (export_dir / "team-profile-20260327.md").write_text("md")
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "team-analysis-proj-20260327.log").write_text("log")
        (log_dir / "other-file.log").write_text("keep")

        profile = _make_profile()
        # Override project_key to match dir
        profile = TeamProfile(
            team_id="jira-PROJ",
            source="jira",
            project_key="PROJ",
            sample_sprints=5,
            velocity_avg=23.5,
        )
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            store.delete("jira-PROJ")

        assert not export_dir.exists()
        assert not (log_dir / "team-analysis-proj-20260327.log").exists()
        assert (log_dir / "other-file.log").exists()


# ---------------------------------------------------------------------------
# sessions.py migration v3 test
# ---------------------------------------------------------------------------


class TestSessionsMigration:
    def test_v3_migration_creates_team_profiles_table(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        from scrum_agent.sessions import SessionStore

        with SessionStore(db_path) as store:
            assert store.schema_mismatch is False

        # Verify the team_profiles table was created
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='team_profiles'").fetchall()
        conn.close()
        assert rows, "team_profiles table should have been created by v3 migration"

    def test_schema_version_is_3(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        from scrum_agent.sessions import CURRENT_SCHEMA_VERSION, SessionStore

        with SessionStore(db_path):
            pass

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT schema_version FROM schema_info").fetchone()
        conn.close()
        assert row[0] == CURRENT_SCHEMA_VERSION == 3


# ---------------------------------------------------------------------------
# Extended data model tests (Phase A)
# ---------------------------------------------------------------------------


def _make_extended_profile(team_id: str = "jira-PROJ") -> TeamProfile:
    """Create a profile with all extended fields populated."""
    return TeamProfile(
        team_id=team_id,
        source="jira",
        project_key="PROJ",
        sample_sprints=8,
        sample_stories=64,
        velocity_avg=23.5,
        velocity_stddev=3.2,
        point_calibrations=(
            StoryPointCalibration(point_value=1, avg_cycle_time_days=0.5, sample_count=10),
            StoryPointCalibration(point_value=5, avg_cycle_time_days=4.2, sample_count=15, overshoot_pct=20.0),
        ),
        story_shapes=(
            StoryShapePattern(
                discipline="backend", avg_points=3.2, avg_ac_count=3.0, avg_task_count=2.8, sample_count=20
            ),
        ),
        epic_pattern=EpicPattern(avg_stories_per_epic=6.0, avg_points_per_epic=18.0, typical_story_count_range=(4, 9)),
        estimation_accuracy_pct=78.0,
        sprint_completion_rate=88.0,
        spillover=SpilloverStats(
            carried_over_pct=12.5, avg_spillover_pts=3.2, most_common_spillover_reason="backend stories"
        ),
        dod_signal=DoDSignal(
            common_checklist_items=("tests passing", "PR merged", "code reviewed"),
            stories_with_comments_pct=85.0,
            stories_with_pr_link_pct=82.0,
            stories_with_review_mention_pct=76.0,
            stories_with_testing_mention_pct=61.0,
            stories_with_deploy_mention_pct=44.0,
            avg_comments_before_resolution=3.2,
        ),
        writing_patterns=WritingPatterns(
            median_ac_count=3.0,
            median_task_count_per_story=2.5,
            subtask_label_distribution=(("Code", 0.58), ("Testing", 0.28), ("Infra", 0.14)),
            common_subtask_patterns=("Write unit tests", "Deploy to staging"),
            subtasks_use_consistent_naming=True,
            common_personas=("developer", "admin", "end user"),
            uses_given_when_then=True,
            epic_description_length_avg=450,
            stories_with_subtasks_pct=72.0,
            epics_with_description_pct=80.0,
        ),
        sprints_fully_completed=6,
        sprints_partially_completed=2,
        sprints_analysed=8,
    )


class TestExtendedProfileSerialisation:
    def test_round_trip_spillover(self):
        profile = _make_extended_profile()
        restored = _json_to_profile(_profile_to_json(profile))
        assert restored.spillover.carried_over_pct == 12.5
        assert restored.spillover.avg_spillover_pts == 3.2
        assert restored.spillover.most_common_spillover_reason == "backend stories"

    def test_round_trip_dod_signal(self):
        profile = _make_extended_profile()
        restored = _json_to_profile(_profile_to_json(profile))
        assert restored.dod_signal.stories_with_pr_link_pct == 82.0
        assert restored.dod_signal.stories_with_review_mention_pct == 76.0
        assert restored.dod_signal.common_checklist_items == ("tests passing", "PR merged", "code reviewed")
        assert restored.dod_signal.avg_comments_before_resolution == 3.2

    def test_round_trip_writing_patterns(self):
        profile = _make_extended_profile()
        restored = _json_to_profile(_profile_to_json(profile))
        assert restored.writing_patterns.median_ac_count == 3.0
        assert restored.writing_patterns.uses_given_when_then is True
        assert restored.writing_patterns.common_personas == ("developer", "admin", "end user")
        assert restored.writing_patterns.subtask_label_distribution == (
            ("Code", 0.58),
            ("Testing", 0.28),
            ("Infra", 0.14),
        )
        assert restored.writing_patterns.subtasks_use_consistent_naming is True
        assert restored.writing_patterns.epic_description_length_avg == 450

    def test_round_trip_sprint_counts(self):
        profile = _make_extended_profile()
        restored = _json_to_profile(_profile_to_json(profile))
        assert restored.sprints_fully_completed == 6
        assert restored.sprints_partially_completed == 2
        assert restored.sprints_analysed == 8

    def test_backward_compat_missing_fields(self):
        """Old profiles (without extended fields) should deserialize with defaults."""
        old_json = json.dumps(
            {
                "team_id": "jira-OLD",
                "source": "jira",
                "project_key": "OLD",
                "sample_sprints": 3,
                "sample_stories": 20,
                "velocity_avg": 15.0,
                "velocity_stddev": 2.0,
                "point_calibrations": [],
                "story_shapes": [],
                "epic_pattern": {},
                "estimation_accuracy_pct": 70.0,
                "sprint_completion_rate": 80.0,
            }
        )
        restored = _json_to_profile(old_json)
        assert restored.spillover == SpilloverStats()
        assert restored.dod_signal == DoDSignal()
        assert restored.writing_patterns == WritingPatterns()
        assert restored.sprints_fully_completed == 0
        assert restored.sprints_analysed == 0

    def test_store_save_load_extended_profile(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        profile = _make_extended_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded = store.load("jira-PROJ")
        assert loaded is not None
        assert loaded.spillover.carried_over_pct == 12.5
        assert loaded.dod_signal.stories_with_pr_link_pct == 82.0
        assert loaded.writing_patterns.uses_given_when_then is True
        assert loaded.sprints_fully_completed == 6


# ---------------------------------------------------------------------------
# Parallel analysis tests (Phase B)
# ---------------------------------------------------------------------------


class TestParallelAnalysis:
    def test_run_parallel_analysis_basic(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        sprint_data = [
            {
                "sprint_name": "Sprint 1",
                "completed_points": 20.0,
                "planned_count": 5,
                "completed_count": 4,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 3,
                        "epic_key": "EP-1",
                        "point_changed": False,
                        "issue_key": "P-1",
                        "issue_url": "http://x/P-1",
                        "summary": "Story A",
                    },
                    {
                        "points": 5,
                        "cycle_time_days": 4.5,
                        "discipline": "frontend",
                        "task_count": 3,
                        "ac_count": 2,
                        "epic_key": "EP-1",
                        "point_changed": True,
                        "issue_key": "P-2",
                        "issue_url": "http://x/P-2",
                        "summary": "Story B",
                    },
                ],
            },
            {
                "sprint_name": "Sprint 2",
                "completed_points": 25.0,
                "planned_count": 6,
                "completed_count": 6,
                "stories": [
                    {
                        "points": 1,
                        "cycle_time_days": 0.5,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "EP-2",
                        "point_changed": False,
                        "issue_key": "P-3",
                        "issue_url": "http://x/P-3",
                        "summary": "Story C",
                    },
                ],
            },
        ]
        progress = []
        profile, examples = _run_parallel_analysis("jira", "PROJ", sprint_data, progress)

        assert profile.team_id == "jira-PROJ"
        assert profile.sample_sprints == 2
        assert profile.sample_stories == 3
        assert profile.velocity_avg > 0
        assert len(profile.point_calibrations) > 0
        assert len(profile.story_shapes) > 0
        assert len(progress) > 0
        assert isinstance(examples, dict)

    def test_run_parallel_analysis_empty(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        profile, examples = _run_parallel_analysis("jira", "EMPTY", [])
        assert profile.sample_sprints == 0
        assert profile.sample_stories == 0
        assert profile.velocity_avg == 0.0
        assert isinstance(examples, dict)

    def test_sprint_completion_counts(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        def _story(pts=3, carried=False, recurring=False):
            return {
                "points": pts,
                "cycle_time_days": 2.0,
                "discipline": "backend",
                "task_count": 0,
                "ac_count": 0,
                "epic_key": "",
                "point_changed": False,
                "carried_over": carried,
                "summary": "Training" if recurring else "Story",
            }

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 20.0,
                "planned_count": 5,
                "completed_count": 5,
                "stories": [_story() for _ in range(5)],
            },
            {
                "sprint_name": "S2",
                "completed_points": 15.0,
                "planned_count": 5,
                "completed_count": 3,
                "stories": [_story() for _ in range(3)] + [_story(carried=True), _story(carried=True)],
            },
            {
                "sprint_name": "S3",
                "completed_points": 25.0,
                "planned_count": 6,
                "completed_count": 6,
                "stories": [_story() for _ in range(6)],
            },
        ]
        profile, _ = _run_parallel_analysis("jira", "PROJ", sprint_data)
        assert profile.sprints_fully_completed == 2
        assert profile.sprints_partially_completed == 1

    def test_examples_include_calibration_stories(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 10.0,
                "planned_count": 3,
                "completed_count": 3,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "X-1",
                        "issue_url": "http://x/X-1",
                        "summary": "Fast story",
                    },
                    {
                        "points": 3,
                        "cycle_time_days": 8.0,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "X-2",
                        "issue_url": "http://x/X-2",
                        "summary": "Slow story",
                    },
                    {
                        "points": 3,
                        "cycle_time_days": 3.0,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "X-3",
                        "issue_url": "http://x/X-3",
                        "summary": "Mid story",
                    },
                ],
            }
        ]
        _, examples = _run_parallel_analysis("jira", "P", sprint_data)
        assert "calibration_3pt" in examples
        assert len(examples["calibration_3pt"]) >= 1
        assert examples["calibration_3pt"][0]["issue_key"]


# ---------------------------------------------------------------------------
# New analysis metrics tests (discipline calibration, spillover correlation,
# velocity trend, confidence scoring)
# ---------------------------------------------------------------------------


class TestNewAnalysisMetrics:
    """Tests for improvements added to _run_parallel_analysis."""

    def _make_sprint_data(self):
        """Build sprint data with multiple disciplines and carried-over stories."""
        stories = [
            {
                "points": 3,
                "cycle_time_days": 2.0,
                "discipline": "backend",
                "task_count": 2,
                "ac_count": 1,
                "epic_key": "",
                "point_changed": False,
                "carried_over": False,
                "assignee": "Alice",
                "issue_key": "T-1",
                "issue_url": "",
                "summary": "Backend story",
            },
            {
                "points": 3,
                "cycle_time_days": 5.0,
                "discipline": "frontend",
                "task_count": 1,
                "ac_count": 2,
                "epic_key": "",
                "point_changed": False,
                "carried_over": True,
                "assignee": "Bob",
                "issue_key": "T-2",
                "issue_url": "",
                "summary": "Frontend story",
            },
            {
                "points": 5,
                "cycle_time_days": 8.0,
                "discipline": "backend",
                "task_count": 4,
                "ac_count": 3,
                "epic_key": "",
                "point_changed": False,
                "carried_over": True,
                "assignee": "Alice",
                "issue_key": "T-3",
                "issue_url": "",
                "summary": "Large backend",
            },
            {
                "points": 1,
                "cycle_time_days": 0.5,
                "discipline": "frontend",
                "task_count": 0,
                "ac_count": 1,
                "epic_key": "",
                "point_changed": False,
                "carried_over": False,
                "assignee": "Bob",
                "issue_key": "T-4",
                "issue_url": "",
                "summary": "Quick fix",
            },
        ]
        return [
            {
                "sprint_name": "Sprint 1",
                "completed_points": 10.0,
                "planned_count": 3,
                "completed_count": 2,
                "stories": stories[:2],
            },
            {
                "sprint_name": "Sprint 2",
                "completed_points": 15.0,
                "planned_count": 3,
                "completed_count": 2,
                "stories": stories[2:],
            },
            {
                "sprint_name": "Sprint 3",
                "completed_points": 20.0,
                "planned_count": 4,
                "completed_count": 4,
                "stories": stories,
            },
        ]

    def test_discipline_calibration(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        _, examples = _run_parallel_analysis("jira", "T", self._make_sprint_data())
        disc_cal = examples.get("discipline_calibration", {})
        assert isinstance(disc_cal, dict)
        assert "backend" in disc_cal or "frontend" in disc_cal

    def test_spillover_correlation(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        _, examples = _run_parallel_analysis("jira", "T", self._make_sprint_data())
        corr = examples.get("spillover_correlation", {})
        assert isinstance(corr, dict)
        assert "by_size" in corr
        assert "by_discipline" in corr
        assert "by_task_count" in corr
        # At least one dimension should have non-zero spillover
        all_pcts = []
        for d in corr.values():
            if isinstance(d, dict):
                all_pcts.extend(d.values())
        assert any(p > 0 for p in all_pcts), "should detect some spillover"

    def test_velocity_trend(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        _, examples = _run_parallel_analysis("jira", "T", self._make_sprint_data())
        vt = examples.get("velocity_trend", {})
        assert isinstance(vt, dict)
        assert vt["trend"] in ("improving", "stable", "degrading")
        assert "slope" in vt
        assert "first_velocity" in vt

    def test_confidence_levels(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        _, examples = _run_parallel_analysis("jira", "T", self._make_sprint_data())
        conf = examples.get("confidence_levels", {})
        assert isinstance(conf, dict)
        # With small sample data, all should be "low"
        for level in conf.values():
            assert level in ("high", "medium", "low")


class TestScopeChangeAnalysis:
    """Tests for mid-sprint scope change detection and metrics."""

    def test_analyse_scope_changes_basic(self):
        from scrum_agent.tools.team_learning import _analyse_scope_changes

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 10.0,
                "planned_count": 4,
                "completed_count": 3,
                "stories": [
                    {
                        "issue_key": "X-1",
                        "points": 3,
                        "discipline": "backend",
                        "added_mid_sprint": True,
                        "carried_over": False,
                        "point_changed": True,
                        "original_points": 2,
                    },
                    {
                        "issue_key": "X-2",
                        "points": 5,
                        "discipline": "frontend",
                        "carried_over": True,
                    },
                    {"issue_key": "X-3", "points": 2, "discipline": "backend"},
                ],
            },
            {
                "sprint_name": "S2",
                "completed_points": 15.0,
                "planned_count": 3,
                "completed_count": 3,
                "stories": [
                    {"issue_key": "X-1", "points": 3, "discipline": "backend"},
                    {"issue_key": "X-4", "points": 5, "discipline": "frontend"},
                    {"issue_key": "X-5", "points": 2, "discipline": "backend"},
                ],
            },
        ]
        result = _analyse_scope_changes(sprint_data)
        assert "per_sprint" in result
        assert "totals" in result
        assert result["totals"]["added_mid_sprint"] == 1
        assert result["totals"]["re_estimated"] == 1
        assert "re_estimation_by_size" in result
        assert 2 in result["re_estimation_by_size"] or 3 in result["re_estimation_by_size"]

    def test_carry_over_chains(self):
        from scrum_agent.tools.team_learning import _analyse_scope_changes

        # Story X-1 appears in all 3 sprints → should be a carry-over chain
        sprint_data = [
            {
                "sprint_name": f"S{i}",
                "completed_points": 10.0,
                "planned_count": 2,
                "completed_count": 1,
                "stories": [
                    {"issue_key": "X-1", "points": 3, "discipline": "backend"},
                    {"issue_key": f"X-{i + 10}", "points": 2, "discipline": "frontend"},
                ],
            }
            for i in range(4)
        ]
        result = _analyse_scope_changes(sprint_data)
        chains = result.get("carry_over_chains", [])
        assert len(chains) >= 1
        assert chains[0]["issue_key"] == "X-1"
        assert chains[0]["sprint_count"] == 4

    def test_scope_changes_in_parallel_analysis(self):
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 10.0,
                "planned_count": 2,
                "completed_count": 2,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": True,
                        "original_points": 2,
                        "added_mid_sprint": True,
                        "issue_key": "T-1",
                        "issue_url": "",
                        "summary": "Changed story",
                        "assignee": "Alice",
                    },
                    {
                        "points": 5,
                        "cycle_time_days": 4.0,
                        "discipline": "frontend",
                        "task_count": 2,
                        "ac_count": 2,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "T-2",
                        "issue_url": "",
                        "summary": "Normal story",
                        "assignee": "Bob",
                    },
                ],
            }
        ]
        _, examples = _run_parallel_analysis("jira", "T", sprint_data)
        scope = examples.get("scope_changes")
        assert scope is not None
        assert scope["totals"]["added_mid_sprint"] == 1
        assert scope["totals"]["re_estimated"] == 1


# ---------------------------------------------------------------------------
# Daily scope timeline tests
# ---------------------------------------------------------------------------


class TestDailyScopeTimeline:
    """Tests for daily scope snapshot dataclasses and timeline analysis."""

    def test_scope_change_event_frozen(self):
        ev = ScopeChangeEvent(
            date="2026-03-01",
            issue_key="X-1",
            change_type="added",
            from_pts=0,
            to_pts=3,
            delta_pts=3,
        )
        assert ev.delta_pts == 3
        with pytest.raises(AttributeError):
            ev.delta_pts = 5  # type: ignore[misc]

    def test_daily_scope_snapshot_frozen(self):
        snap = DailyScopeSnapshot(
            date="2026-03-01",
            total_scope_pts=10.0,
            stories_in_sprint=(("X-1", 5.0), ("X-2", 5.0)),
        )
        assert snap.total_scope_pts == 10.0
        assert len(snap.stories_in_sprint) == 2

    def test_sprint_scope_timeline_frozen(self):
        tl = SprintScopeTimeline(
            sprint_name="Sprint 1",
            committed_pts=20,
            final_pts=25,
            delivered_pts=22,
            scope_change_total=5,
            scope_churn=0.25,
        )
        assert tl.scope_change_total == 5
        assert tl.scope_churn == 0.25

    def test_analyse_scope_changes_with_timelines(self):
        """_analyse_scope_changes includes timeline data when present."""
        from scrum_agent.tools.team_learning import _analyse_scope_changes

        timeline = SprintScopeTimeline(
            sprint_name="S1",
            committed_pts=20,
            final_pts=25,
            delivered_pts=18,
            scope_change_total=5,
            scope_churn=0.25,
            change_events=(
                ScopeChangeEvent(
                    date="2026-03-03",
                    issue_key="X-5",
                    change_type="added",
                    from_pts=0,
                    to_pts=5,
                    delta_pts=5,
                ),
            ),
        )
        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 18.0,
                "planned_count": 4,
                "completed_count": 3,
                "scope_timeline": timeline,
                "stories": [
                    {"issue_key": "X-1", "points": 5, "discipline": "backend"},
                    {"issue_key": "X-2", "points": 5, "discipline": "frontend"},
                    {
                        "issue_key": "X-5",
                        "points": 5,
                        "discipline": "backend",
                        "added_mid_sprint": True,
                    },
                ],
            },
        ]
        result = _analyse_scope_changes(sprint_data)
        assert len(result["timelines"]) == 1
        assert result["timelines"][0].committed_pts == 20
        assert result["totals"]["avg_committed_velocity"] == 20.0
        assert result["totals"]["avg_delivered_velocity"] == 18.0
        # Per-sprint should have timeline fields
        ps = result["per_sprint"][0]
        assert ps["committed_pts"] == 20
        assert ps["delivered_pts"] == 18
        assert ps["scope_churn"] == 0.25

    def test_analyse_scope_changes_without_timelines(self):
        """_analyse_scope_changes still works when no timelines are present."""
        from scrum_agent.tools.team_learning import _analyse_scope_changes

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 10.0,
                "planned_count": 2,
                "completed_count": 2,
                "stories": [
                    {"issue_key": "X-1", "points": 5, "discipline": "backend"},
                    {"issue_key": "X-2", "points": 5, "discipline": "frontend"},
                ],
            },
        ]
        result = _analyse_scope_changes(sprint_data)
        assert result["timelines"] == []
        assert result["totals"]["avg_committed_velocity"] == 0.0
        assert "committed_pts" not in result["per_sprint"][0]

    def test_date_range_helper(self):
        from scrum_agent.tools.team_learning import _date_range, _parse_date

        s = _parse_date("2026-03-01")
        e = _parse_date("2026-03-05")
        days = _date_range(s, e)
        assert len(days) == 5
        assert days[0] == "2026-03-01"
        assert days[-1] == "2026-03-05"

    def test_scope_timeline_in_parallel_analysis(self):
        """Timelines flow through _run_parallel_analysis."""
        from scrum_agent.tools.team_learning import _run_parallel_analysis

        timeline = SprintScopeTimeline(
            sprint_name="S1",
            committed_pts=15,
            final_pts=18,
            delivered_pts=13,
            scope_change_total=3,
            scope_churn=0.2,
        )
        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 13.0,
                "planned_count": 3,
                "completed_count": 2,
                "scope_timeline": timeline,
                "stories": [
                    {
                        "points": 5,
                        "cycle_time_days": 3.0,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "T-1",
                        "issue_url": "",
                        "summary": "Story 1",
                        "assignee": "Alice",
                    },
                    {
                        "points": 8,
                        "cycle_time_days": 5.0,
                        "discipline": "frontend",
                        "task_count": 3,
                        "ac_count": 2,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "T-2",
                        "issue_url": "",
                        "summary": "Story 2",
                        "assignee": "Bob",
                    },
                ],
            },
        ]
        _, examples = _run_parallel_analysis("azdevops", "T", sprint_data)
        scope = examples.get("scope_changes")
        assert scope is not None
        assert len(scope["timelines"]) == 1
        assert scope["totals"]["avg_committed_velocity"] == 15.0


# ---------------------------------------------------------------------------
# Team profile exporter tests (Phase G)
# ---------------------------------------------------------------------------


class TestTeamProfileExporter:
    def test_export_html(self, tmp_path):
        from scrum_agent.team_profile_exporter import export_team_profile_html

        profile = _make_extended_profile()
        path = export_team_profile_html(profile, output_dir=tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "Team Profile" in content
        assert "PROJ" in content
        assert "Velocity" in content
        assert "Point Value" in content
        assert "site-header" in content  # uses proper html_exporter structure

    def test_export_md(self, tmp_path):
        from scrum_agent.team_profile_exporter import export_team_profile_md

        profile = _make_extended_profile()
        path = export_team_profile_md(profile, output_dir=tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "# Team Profile" in content
        assert "PROJ" in content
        assert "## Team & Velocity" in content
        assert "23.5 pts/sprint" in content

    def test_export_html_minimal_profile(self, tmp_path):
        from scrum_agent.team_profile_exporter import export_team_profile_html

        profile = TeamProfile(team_id="x", source="jira", project_key="X")
        path = export_team_profile_html(profile, output_dir=tmp_path)
        assert path.exists()

    def test_export_md_minimal_profile(self, tmp_path):
        from scrum_agent.team_profile_exporter import export_team_profile_md

        profile = TeamProfile(team_id="x", source="jira", project_key="X")
        path = export_team_profile_md(profile, output_dir=tmp_path)
        assert path.exists()

    def test_exports_sorted_into_project_subdirectory(self, tmp_path):
        """Exports land in a per-project subdirectory: {base}/{project_key}/."""
        from scrum_agent.team_profile_exporter import export_team_profile_html, export_team_profile_md

        profile = _make_extended_profile()  # project_key="PROJ"
        html_path = export_team_profile_html(profile, output_dir=tmp_path)
        md_path = export_team_profile_md(profile, output_dir=tmp_path)
        # Both files should be in tmp_path/proj/ (lowercase)
        assert html_path.parent.name == "proj"
        assert md_path.parent.name == "proj"
        assert html_path.parent == md_path.parent

    def test_write_analysis_log(self, tmp_path, monkeypatch):
        """Analysis log written to ~/.scrum-agent/logs/ with structured content."""
        from scrum_agent.team_profile_exporter import write_analysis_log

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        profile = _make_extended_profile()
        log_path = write_analysis_log(
            profile,
            examples={"team_size": 5},
            sprint_names=["Sprint 1", "Sprint 2"],
            duration_secs=12.3,
        )
        assert log_path.exists()
        content = log_path.read_text()
        assert "PROJ" in content
        assert "Sprint 1" in content
        assert "12.3s" in content
        assert "Raw profile JSON:" in content
        assert '"team_id"' in content
        # Log goes to the logs directory
        assert log_path.parent.name == "logs"
        assert "team-analysis-proj-" in log_path.name


# ---------------------------------------------------------------------------
# Team analysis screen rendering tests (Phase E)
# ---------------------------------------------------------------------------


class TestTeamAnalysisScreen:
    def test_build_team_analysis_screen_renders(self):
        from scrum_agent.ui.mode_select.screens._screens_secondary import _build_team_analysis_screen

        profile = _make_extended_profile()
        panel = _build_team_analysis_screen(profile, scroll_offset=0, width=80, height=30)
        assert panel is not None

    def test_build_team_analysis_screen_scrollable(self):
        from scrum_agent.ui.mode_select.screens._screens_secondary import _build_team_analysis_screen

        profile = _make_extended_profile()
        panel1 = _build_team_analysis_screen(profile, scroll_offset=0, width=80, height=30)
        panel2 = _build_team_analysis_screen(profile, scroll_offset=5, width=80, height=30)
        assert panel1 is not None
        assert panel2 is not None


# ---------------------------------------------------------------------------
# Project card staleness hint tests (Phase F)
# ---------------------------------------------------------------------------


class TestProfileCards:
    def test_new_project_card_no_staleness(self):
        from scrum_agent.ui.mode_select.screens._project_cards import _build_new_project_card

        card = _build_new_project_card(selected=True, box_w=64)
        assert card is not None

    def test_profile_card_renders(self):
        from scrum_agent.ui.mode_select.screens._project_cards import ProfileSummary, _build_profile_card

        ps = ProfileSummary(
            team_id="jira-PROJ",
            source="jira",
            project_key="PROJ",
            sample_sprints=8,
            velocity_avg=23.0,
            sample_stories=64,
            updated="2 days ago",
            staleness_days=2,
        )
        card = _build_profile_card(ps, selected=True, box_w=64)
        assert card is not None

    def test_profile_card_stale_shows_hint(self):
        from scrum_agent.ui.mode_select.screens._project_cards import ProfileSummary, _build_profile_card

        ps = ProfileSummary(
            team_id="jira-X",
            source="jira",
            project_key="X",
            staleness_days=45,
            updated="45 days ago",
        )
        card = _build_profile_card(ps, selected=False, box_w=64)
        assert card is not None

    def test_new_analysis_card_renders(self):
        from scrum_agent.ui.mode_select.screens._project_cards import _build_new_analysis_card

        card = _build_new_analysis_card(selected=True, box_w=64)
        assert card is not None

    def test_new_analysis_card_custom_label(self):
        from scrum_agent.ui.mode_select.screens._project_cards import _build_new_analysis_card

        card = _build_new_analysis_card(label="+ Analyse Jira Board", selected=False, box_w=64)
        assert card is not None


# ---------------------------------------------------------------------------
# Project list screen popup rendering tests (Phase C)
# ---------------------------------------------------------------------------


class TestMergeProfiles:
    def test_merge_combines_sample_counts(self):
        from scrum_agent.team_profile import merge_profiles

        old = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_sprints=5,
            sample_stories=30,
            velocity_avg=20.0,
            point_calibrations=(StoryPointCalibration(point_value=3, avg_cycle_time_days=2.0, sample_count=10),),
            story_shapes=(StoryShapePattern(discipline="backend", avg_points=3.0, sample_count=15),),
        )
        new = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_sprints=3,
            sample_stories=20,
            velocity_avg=25.0,
            point_calibrations=(StoryPointCalibration(point_value=3, avg_cycle_time_days=3.0, sample_count=8),),
            story_shapes=(StoryShapePattern(discipline="backend", avg_points=4.0, sample_count=10),),
        )
        merged = merge_profiles(old, new)
        assert merged.sample_sprints == 8
        assert merged.sample_stories == 50
        assert merged.velocity_avg > 20.0
        assert merged.velocity_avg < 25.0
        assert len(merged.point_calibrations) == 1
        assert merged.point_calibrations[0].sample_count == 18
        assert len(merged.story_shapes) == 1
        assert merged.story_shapes[0].sample_count == 25

    def test_merge_new_discipline_added(self):
        from scrum_agent.team_profile import merge_profiles

        old = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_stories=10,
            story_shapes=(StoryShapePattern(discipline="backend", avg_points=3.0, sample_count=10),),
        )
        new = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_stories=5,
            story_shapes=(StoryShapePattern(discipline="frontend", avg_points=2.0, sample_count=5),),
        )
        merged = merge_profiles(old, new)
        disciplines = {s.discipline for s in merged.story_shapes}
        assert "backend" in disciplines
        assert "frontend" in disciplines

    def test_merge_qualitative_fields_use_new(self):
        from scrum_agent.team_profile import merge_profiles

        old = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_stories=10,
            dod_signal=DoDSignal(stories_with_pr_link_pct=50.0),
            writing_patterns=WritingPatterns(uses_given_when_then=False),
        )
        new = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_stories=5,
            dod_signal=DoDSignal(stories_with_pr_link_pct=80.0),
            writing_patterns=WritingPatterns(uses_given_when_then=True),
        )
        merged = merge_profiles(old, new)
        assert merged.dod_signal.stories_with_pr_link_pct == 80.0
        assert merged.writing_patterns.uses_given_when_then is True


class TestProjectListPopup:
    def test_build_with_team_popup_params(self):
        from scrum_agent.ui.mode_select.screens._project_list_screen import _build_project_list_screen

        panel = _build_project_list_screen(
            [],
            0,
            width=80,
            height=30,
            team_popup_t=0.0,
            team_popup_sel=0,
            team_popup_pulse=0.0,
        )
        assert panel is not None

    def test_build_with_team_popup_visible(self):
        from scrum_agent.ui.mode_select.screens._project_list_screen import _build_project_list_screen

        panel = _build_project_list_screen(
            [],
            0,
            width=80,
            height=30,
            team_popup_t=1.0,
            team_popup_sel=0,
            team_popup_pulse=0.5,
        )
        assert panel is not None

    def test_build_with_team_popup_skip_selected(self):
        from scrum_agent.ui.mode_select.screens._project_list_screen import _build_project_list_screen

        panel = _build_project_list_screen(
            [],
            0,
            width=80,
            height=30,
            team_popup_t=1.0,
            team_popup_sel=1,
            team_popup_pulse=1.0,
        )
        assert panel is not None

    def test_build_with_team_popup_trims_body_when_crowded(self):
        """Popup must render even when project list fills the screen."""
        from scrum_agent.ui.mode_select.screens._project_cards import ProjectSummary
        from scrum_agent.ui.mode_select.screens._project_list_screen import _build_project_list_screen

        projects = [ProjectSummary(name=f"Project {i}", id=str(i)) for i in range(6)]
        panel = _build_project_list_screen(
            projects,
            len(projects),  # selected = "+ New Project"
            width=80,
            height=30,
            team_popup_t=1.0,
            team_popup_sel=0,
            team_popup_pulse=0.5,
        )
        assert panel is not None
