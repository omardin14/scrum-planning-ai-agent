"""Project history persistence — save/load project metadata to ~/.scrum-agent/projects.json.

# See README: "Memory & State" — this is the first step toward session persistence.
# Each project gets a UUID and a snapshot of pipeline progress, artifact counts,
# and Jira sync status. The file is a simple JSON array — no database needed yet.

Public API:
    save_project_snapshot(project_id, graph_state) — upsert a project entry
    load_projects() — read all projects, sorted by updated_at desc
    create_project_id() — generate a new UUID string
    migrate_history_file() — rename ~/.scrum-agent/history → repl-history
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scrum_agent.ui.mode_select import ProjectSummary

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path.home() / ".scrum-agent"
_PROJECTS_FILE = _CONFIG_DIR / "projects.json"
_STATES_DIR = _CONFIG_DIR / "states"
_LOGS_DIR = _CONFIG_DIR / "logs"
_SCHEMA_VERSION = 1

# Pipeline stages in order — used to compute progress booleans.
# These match the node names in the LangGraph agent graph.
_PIPELINE_STAGES = (
    "description_input",
    "intake_complete",
    "project_analyzer",
    "epic_generator",
    "story_writer",
    "task_decomposer",
    "sprint_planner",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_project_id() -> str:
    """Generate a new UUID4 string for a project."""
    return str(uuid.uuid4())


def save_project_snapshot(project_id: str, graph_state: dict[str, Any]) -> None:
    """Upsert a project entry by ID into ~/.scrum-agent/projects.json.

    Inspects graph_state to compute project name, description, pipeline progress,
    artifact counts, and Jira sync status. If the project already exists (same ID),
    its entry is updated in place; otherwise a new entry is appended.
    """
    logger.debug("Saving project snapshot for project_id=%s", project_id)
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = _load_raw()
    projects = data.get("projects", [])

    now = datetime.now(UTC).isoformat()
    pipeline = _extract_pipeline_progress(graph_state)
    artifacts = _extract_artifact_counts(graph_state)
    jira_sync = _extract_jira_sync(graph_state)

    # Extract project name from analysis or questionnaire answers
    name = _extract_project_name(graph_state)
    description = _extract_project_description(graph_state)

    entry = {
        "id": project_id,
        "name": name,
        "description": description,
        "updated_at": now,
        "pipeline_progress": pipeline,
        "artifact_counts": artifacts,
        "jira_sync": jira_sync,
    }

    # Upsert — find existing entry by ID
    found = False
    for i, proj in enumerate(projects):
        if proj.get("id") == project_id:
            # Preserve created_at from original entry
            entry["created_at"] = proj.get("created_at", now)
            projects[i] = entry
            found = True
            break

    if not found:
        entry["created_at"] = now
        projects.append(entry)

    data["version"] = _SCHEMA_VERSION
    data["projects"] = projects

    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _PROJECTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    logger.info("Project snapshot saved: project_id=%s, name=%s", project_id, name)

    # Also persist the full graph state so the session can be resumed.
    save_graph_state(project_id, graph_state)


def save_graph_state(project_id: str, graph_state: dict[str, Any]) -> None:
    """Persist the full graph state to disk so the session can be resumed later.

    # See README: "Memory & State" — full graph state serialization.
    # Uses LangChain's message serialization for BaseMessage objects and
    # dataclasses.asdict() for frozen dataclass artifacts.

    Saved to ~/.scrum-agent/states/{project_id}.json alongside the lightweight
    project metadata in projects.json.
    """
    from dataclasses import asdict, fields

    from langchain_core.messages import messages_to_dict

    from scrum_agent.agent.state import QuestionnaireState

    _STATES_DIR.mkdir(parents=True, exist_ok=True)

    serialized: dict[str, Any] = {}

    # Serialize messages using LangChain's built-in serializer.
    # Only use messages_to_dict when the list contains actual BaseMessage objects;
    # plain strings (e.g. from tests) are stored as-is.
    messages = graph_state.get("messages", [])
    from langchain_core.messages import BaseMessage

    if messages and isinstance(messages[0], BaseMessage):
        serialized["messages"] = messages_to_dict(messages)
    else:
        serialized["messages"] = list(messages)

    # Serialize questionnaire (mutable dataclass)
    qs = graph_state.get("questionnaire")
    if isinstance(qs, QuestionnaireState):
        qs_dict = {}
        for f in fields(qs):
            val = getattr(qs, f.name)
            if isinstance(val, set):
                qs_dict[f.name] = list(val)
            elif isinstance(val, dict):
                # Convert int keys to strings for JSON, and tuple values to lists
                qs_dict[f.name] = {str(k): (list(v) if isinstance(v, tuple) else v) for k, v in val.items()}
            else:
                qs_dict[f.name] = val
        serialized["questionnaire"] = qs_dict

    # Serialize frozen dataclass artifacts
    for key in ("project_analysis", "epics", "stories", "tasks", "sprints"):
        val = graph_state.get(key)
        if val is not None:
            if isinstance(val, list):
                serialized[key] = [asdict(item) for item in val]
            else:
                serialized[key] = asdict(val)

    # Serialize simple scalar fields
    for key in (
        "project_name",
        "project_description",
        "_intake_mode",
        "team_size",
        "sprint_length_weeks",
        "velocity_per_sprint",
        "target_sprints",
        "net_velocity_per_sprint",
        "velocity_source",
        "sprint_start_date",
        "starting_sprint_number",
        "capacity_bank_holiday_days",
        "capacity_planned_leave_days",
        "capacity_unplanned_leave_pct",
        "capacity_onboarding_engineer_sprints",
        "capacity_ktlo_engineers",
        "capacity_discovery_pct",
        "sprint_capacities",
        "planned_leave_entries",
        "repo_context",
        "confluence_context",
        "pending_review",
        "last_review_decision",
        "last_review_feedback",
    ):
        if key in graph_state:
            val = graph_state[key]
            # ReviewDecision is an enum — store its value
            if hasattr(val, "value"):
                serialized[key] = val.value
            else:
                serialized[key] = val

    state_file = _STATES_DIR / f"{project_id}.json"
    state_file.write_text(json.dumps(serialized, indent=2, default=str), encoding="utf-8")
    logger.debug("Graph state saved to %s (%d keys)", state_file, len(serialized))


def load_graph_state(project_id: str) -> dict[str, Any] | None:
    """Load a previously saved graph state from disk.

    Returns the deserialized graph state dict ready to pass to run_session(),
    or None if no saved state exists for this project ID.
    """
    from dataclasses import fields

    from langchain_core.messages import messages_from_dict

    from scrum_agent.agent.state import (
        AcceptanceCriterion,
        Discipline,
        Epic,
        Priority,
        ProjectAnalysis,
        QuestionnaireState,
        ReviewDecision,
        Sprint,
        StoryPointValue,
        Task,
        UserStory,
    )

    state_file = _STATES_DIR / f"{project_id}.json"
    if not state_file.exists():
        logger.debug("No saved graph state for project_id=%s", project_id)
        return None

    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.error("Failed to load graph state from %s", state_file)
        return None

    graph_state: dict[str, Any] = {}

    # Deserialize messages — use LangChain's deserializer when messages are
    # stored as dicts (the normal case), fall back to raw list otherwise.
    if "messages" in raw:
        msgs = raw["messages"]
        if msgs and isinstance(msgs[0], dict) and "type" in msgs[0]:
            graph_state["messages"] = messages_from_dict(msgs)
        else:
            graph_state["messages"] = msgs

    # Deserialize questionnaire
    if "questionnaire" in raw:
        qs_raw = raw["questionnaire"]
        # Convert string keys back to ints, and lists back to sets/tuples
        qs_kwargs: dict[str, Any] = {}
        qs_fields = {f.name: f for f in fields(QuestionnaireState)}
        for key, val in qs_raw.items():
            if key not in qs_fields:
                continue
            f = qs_fields[key]
            f_type = str(f.type) if not isinstance(f.type, str) else f.type
            if "set" in f_type and isinstance(val, list):
                qs_kwargs[key] = set(val)
            elif "dict" in f_type and isinstance(val, dict):
                # Restore int keys where needed
                restored: dict = {}
                for k, v in val.items():
                    try:
                        int_key = int(k)
                    except (ValueError, TypeError):
                        int_key = k
                    if isinstance(v, list) and "tuple" in f_type:
                        restored[int_key] = tuple(v)
                    else:
                        restored[int_key] = v
                qs_kwargs[key] = restored
            else:
                qs_kwargs[key] = val
        graph_state["questionnaire"] = QuestionnaireState(**qs_kwargs)

    # Deserialize frozen dataclass artifacts
    _artifact_classes = {
        "project_analysis": ProjectAnalysis,
        "epics": Epic,
        "stories": UserStory,
        "tasks": Task,
        "sprints": Sprint,
    }

    def _restore_enums(cls, item_dict: dict) -> dict:
        """Restore enum and tuple fields from their JSON representations."""
        if cls in (Epic, UserStory):
            if "priority" in item_dict and isinstance(item_dict["priority"], str):
                item_dict["priority"] = Priority(item_dict["priority"])
        if cls is UserStory:
            if "story_points" in item_dict and isinstance(item_dict["story_points"], int):
                item_dict["story_points"] = StoryPointValue(item_dict["story_points"])
            if "discipline" in item_dict and isinstance(item_dict["discipline"], str):
                item_dict["discipline"] = Discipline(item_dict["discipline"])
            if "acceptance_criteria" in item_dict:
                item_dict["acceptance_criteria"] = tuple(
                    AcceptanceCriterion(**ac) if isinstance(ac, dict) else ac for ac in item_dict["acceptance_criteria"]
                )
            if "dod_applicable" in item_dict and isinstance(item_dict["dod_applicable"], list):
                item_dict["dod_applicable"] = tuple(item_dict["dod_applicable"])
        return item_dict

    for key, cls in _artifact_classes.items():
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(val, list):
            items = []
            for item_dict in val:
                _restore_enums(cls, item_dict)
                items.append(cls(**item_dict))
            graph_state[key] = items
        elif isinstance(val, dict):
            _restore_enums(cls, val)
            graph_state[key] = cls(**val)

    # Deserialize scalar fields
    for key in (
        "project_name",
        "project_description",
        "_intake_mode",
        "team_size",
        "sprint_length_weeks",
        "velocity_per_sprint",
        "target_sprints",
        "net_velocity_per_sprint",
        "velocity_source",
        "sprint_start_date",
        "starting_sprint_number",
        "capacity_bank_holiday_days",
        "capacity_planned_leave_days",
        "capacity_unplanned_leave_pct",
        "capacity_onboarding_engineer_sprints",
        "capacity_ktlo_engineers",
        "capacity_discovery_pct",
        "sprint_capacities",
        "planned_leave_entries",
        "repo_context",
        "confluence_context",
        "pending_review",
        "last_review_feedback",
    ):
        if key in raw:
            graph_state[key] = raw[key]

    # Restore ReviewDecision enum
    if "last_review_decision" in raw:
        try:
            graph_state["last_review_decision"] = ReviewDecision(raw["last_review_decision"])
        except (ValueError, KeyError):
            pass

    logger.debug("Graph state loaded for project_id=%s (%d keys)", project_id, len(graph_state))
    return graph_state


def load_projects() -> list[ProjectSummary]:
    """Read projects.json and return ProjectSummary list sorted by updated_at desc."""
    data = _load_raw()
    projects = data.get("projects", [])

    # Sort by updated_at descending (most recent first)
    projects.sort(key=lambda p: p.get("updated_at", ""), reverse=True)

    summaries: list[ProjectSummary] = []
    for proj in projects:
        pipeline = proj.get("pipeline_progress", {})
        artifacts = proj.get("artifact_counts", {})
        jira_sync = proj.get("jira_sync", {})

        summaries.append(
            ProjectSummary(
                name=proj.get("name", "Untitled Project"),
                id=proj.get("id", ""),
                created=_relative_time(proj.get("updated_at", "")),
                status=_compute_status(pipeline),
                epic_count=artifacts.get("epics", 0),
                story_count=artifacts.get("stories", 0),
                task_count=artifacts.get("tasks", 0),
                sprint_count=artifacts.get("sprints", 0),
                jira_summary=_compute_jira_summary(jira_sync),
                progress=_compute_progress(pipeline),
            )
        )

    logger.debug("Loaded %d project(s) from %s", len(summaries), _PROJECTS_FILE)
    return summaries


def delete_project(project_id: str) -> bool:
    """Remove a project and its associated files from ~/.scrum-agent/.

    Cleans up:
      - The project entry in projects.json
      - The state snapshot in states/{project_id}.json

    Returns True if the project was found and deleted, False otherwise.
    """
    data = _load_raw()
    projects = data.get("projects", [])
    original_len = len(projects)
    projects = [p for p in projects if p.get("id") != project_id]

    if len(projects) == original_len:
        logger.debug("Project not found for deletion: %s", project_id)
        return False  # not found

    data["projects"] = projects
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _PROJECTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Clean up the state snapshot file
    state_file = _STATES_DIR / f"{project_id}.json"
    if state_file.exists():
        state_file.unlink()
        logger.debug("Deleted state file %s", state_file)

    # Clean up the per-session log file
    log_file = _LOGS_DIR / f"{project_id}.log"
    if log_file.exists():
        log_file.unlink()
        logger.debug("Deleted session log %s", log_file)

    logger.info("Deleted project %s", project_id)
    return True


# ---------------------------------------------------------------------------
# Per-session logging
# ---------------------------------------------------------------------------
# Each session gets its own log file at ~/.scrum-agent/logs/{project_id}.log.
# The handler is attached when a session starts and removed when it ends.
# This keeps session logs isolated for easy debugging and clean deletion.

_session_handler: logging.Handler | None = None


def attach_session_logger(project_id: str) -> None:
    """Attach a per-session file handler to the scrum_agent logger.

    Creates ~/.scrum-agent/logs/{project_id}.log. Safe to call multiple times
    — subsequent calls replace the previous session handler.
    """
    global _session_handler  # noqa: PLW0603

    from scrum_agent.config import get_log_level

    # Remove any existing session handler first
    remove_session_logger()

    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOGS_DIR / f"{project_id}.log"
    log_level = getattr(logging, get_log_level(), logging.WARNING)

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"),
    )
    handler.setLevel(log_level)
    logging.getLogger("scrum_agent").addHandler(handler)
    _session_handler = handler
    logger.debug("Session logger attached: %s (level=%s)", log_path, get_log_level())


def remove_session_logger() -> None:
    """Remove the per-session file handler, flushing and closing the file."""
    global _session_handler  # noqa: PLW0603
    if _session_handler is not None:
        _session_handler.flush()
        _session_handler.close()
        logging.getLogger("scrum_agent").removeHandler(_session_handler)
        _session_handler = None


def export_project_json(project_id: str, output_dir: Path | None = None) -> Path | None:
    """Export a project's raw metadata as a JSON file.

    Writes to {sanitized-name}-export.json in output_dir (defaults to cwd).
    Returns the output Path or None if the project ID was not found.
    """
    data = _load_raw()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            name = proj.get("name", "project")
            # Sanitize filename: lowercase, replace spaces/special chars with hyphens
            safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower()).strip("-")
            safe_name = safe_name or "project"
            out_dir = output_dir or Path.cwd()
            out_path = out_dir / f"{safe_name}-export.json"
            out_path.write_text(json.dumps(proj, indent=2), encoding="utf-8")
            return out_path
    return None


def _safe_project_name(project_id: str) -> tuple[str, dict | None]:
    """Derive a filesystem-safe project name and its graph state.

    Returns (safe_name, graph_state) where graph_state may be None if no
    saved state exists for this project ID.
    """
    graph_state = load_graph_state(project_id)

    data = _load_raw()
    name = "project"
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            name = proj.get("name", "project")
            break

    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower()).strip("-") or "project"
    return safe_name, graph_state


def export_project_html(project_id: str, output_dir: Path | None = None) -> Path | None:
    """Export a project's full plan as an HTML file.

    Loads the saved graph state and produces {safe-name}-plan.html with all
    available artifacts (analysis, epics, stories, tasks, sprints).

    Returns the output Path, or None if the project has no saved state.
    """
    from scrum_agent.html_exporter import export_plan_html

    safe_name, graph_state = _safe_project_name(project_id)
    if graph_state is None:
        return None

    out_dir = output_dir or Path.cwd()
    html_path = out_dir / f"{safe_name}-plan.html"
    return export_plan_html(graph_state, path=html_path)


def export_project_md(project_id: str, output_dir: Path | None = None) -> Path | None:
    """Export a project's full plan as a Markdown file.

    Loads the saved graph state and produces {safe-name}-plan.md with all
    available artifacts (analysis, epics, stories, tasks, sprints).

    Returns the output Path, or None if the project has no saved state.
    """
    from scrum_agent.repl._io import _export_plan_markdown

    safe_name, graph_state = _safe_project_name(project_id)
    if graph_state is None:
        return None

    out_dir = output_dir or Path.cwd()
    md_path = out_dir / f"{safe_name}-plan.md"
    return _export_plan_markdown(graph_state, path=md_path)


def export_project_plan(project_id: str, output_dir: Path | None = None) -> list[Path]:
    """Export a project's full plan as both Markdown and HTML files.

    Convenience wrapper that calls export_project_html() and export_project_md().
    Used by the --export-only CLI flag in run_session.

    Returns a list of exported file paths, or an empty list if the project has
    no saved state.
    """
    paths: list[Path] = []
    html_path = export_project_html(project_id, output_dir)
    if html_path:
        paths.append(html_path)
    md_path = export_project_md(project_id, output_dir)
    if md_path:
        paths.append(md_path)
    return paths


def migrate_history_file() -> None:
    """Rename ~/.scrum-agent/history to repl-history if the old file exists.

    Called at startup so the REPL history file has a clearer name.
    No-op if the old file doesn't exist or the new file already exists.
    """
    old = _CONFIG_DIR / "history"
    new = _CONFIG_DIR / "repl-history"
    if old.exists() and not new.exists():
        old.rename(new)
        logger.info("Migrated history file: %s -> %s", old, new)


# ---------------------------------------------------------------------------
# Private helpers — data extraction from graph_state
# ---------------------------------------------------------------------------


def _extract_project_name(graph_state: dict[str, Any]) -> str:
    """Extract project name from analysis or questionnaire."""
    analysis = graph_state.get("project_analysis")
    if analysis is not None and hasattr(analysis, "project_name") and analysis.project_name:
        return analysis.project_name

    qs = graph_state.get("questionnaire")
    if qs is not None and hasattr(qs, "answers"):
        # Q1 is "Project name"
        name = qs.answers.get(1, "")
        if name:
            return name

    return "Untitled Project"


def _extract_project_description(graph_state: dict[str, Any]) -> str:
    """Extract project description from analysis or questionnaire."""
    analysis = graph_state.get("project_analysis")
    if analysis is not None and hasattr(analysis, "project_description") and analysis.project_description:
        return analysis.project_description[:200]

    qs = graph_state.get("questionnaire")
    if qs is not None and hasattr(qs, "answers"):
        # Q3 is "Project goals" — a reasonable fallback
        desc = qs.answers.get(3, "")
        if desc:
            return desc[:200]

    return ""


def _extract_pipeline_progress(graph_state: dict[str, Any]) -> dict[str, bool]:
    """Compute boolean map of which pipeline stages have completed."""
    qs = graph_state.get("questionnaire")
    has_messages = bool(graph_state.get("messages"))

    return {
        "description_input": has_messages,
        "intake_complete": qs is not None and hasattr(qs, "completed") and qs.completed,
        "project_analyzer": graph_state.get("project_analysis") is not None,
        "epic_generator": bool(graph_state.get("epics")),
        "story_writer": bool(graph_state.get("stories")),
        "task_decomposer": bool(graph_state.get("tasks")),
        "sprint_planner": bool(graph_state.get("sprints")) and not graph_state.get("pending_review"),
    }


def _extract_artifact_counts(graph_state: dict[str, Any]) -> dict[str, int]:
    """Count each type of artifact in the graph state."""
    return {
        "epics": len(graph_state.get("epics", [])),
        "stories": len(graph_state.get("stories", [])),
        "tasks": len(graph_state.get("tasks", [])),
        "sprints": len(graph_state.get("sprints", [])),
    }


def _extract_jira_sync(graph_state: dict[str, Any]) -> dict[str, int]:
    """Count Jira-synced vs total artifacts."""
    epics_total = len(graph_state.get("epics", []))
    stories_total = len(graph_state.get("stories", []))
    epics_synced = len(graph_state.get("jira_epic_keys", {}))
    stories_synced = len(graph_state.get("jira_story_keys", {}))

    return {
        "epics_synced": epics_synced,
        "epics_total": epics_total,
        "stories_synced": stories_synced,
        "stories_total": stories_total,
    }


# ---------------------------------------------------------------------------
# Private helpers — display formatting
# ---------------------------------------------------------------------------


def _relative_time(iso_str: str) -> str:
    """Convert an ISO timestamp to a human-readable relative time string.

    Returns "just now", "5 minutes ago", "2 days ago", etc.
    Returns "" if the timestamp is empty or unparseable.
    """
    if not iso_str:
        return ""

    try:
        then = datetime.fromisoformat(iso_str)
        # Ensure timezone-aware
        if then.tzinfo is None:
            then = then.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - then
    except (ValueError, TypeError):
        return ""

    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def _compute_progress(pipeline_progress: dict[str, bool]) -> str:
    """Return '3/7 stages complete' style string from pipeline booleans."""
    done = sum(1 for stage in _PIPELINE_STAGES if pipeline_progress.get(stage))
    total = len(_PIPELINE_STAGES)
    if done == 0:
        return ""
    if done == total:
        return "All stages complete"
    return f"{done}/{total} stages complete"


def _compute_status(pipeline_progress: dict[str, bool]) -> str:
    """Derive a human-readable status from pipeline progress."""
    if pipeline_progress.get("sprint_planner"):
        return "Complete"
    if any(pipeline_progress.get(stage) for stage in _PIPELINE_STAGES):
        return "In Progress"
    return "New"


def _compute_jira_summary(jira_sync: dict[str, int]) -> str:
    """Build a Jira sync summary string like '3/4 epics, 12/15 stories synced'."""
    parts: list[str] = []
    epics_synced = jira_sync.get("epics_synced", 0)
    epics_total = jira_sync.get("epics_total", 0)
    stories_synced = jira_sync.get("stories_synced", 0)
    stories_total = jira_sync.get("stories_total", 0)

    if epics_synced > 0 or stories_synced > 0:
        if epics_total > 0:
            parts.append(f"{epics_synced}/{epics_total} epics")
        if stories_total > 0:
            parts.append(f"{stories_synced}/{stories_total} stories")
        return ", ".join(parts) + " synced"

    return ""


# ---------------------------------------------------------------------------
# Private helpers — file I/O
# ---------------------------------------------------------------------------


def _load_raw() -> dict[str, Any]:
    """Read and parse projects.json, returning an empty structure if missing/corrupt."""
    if not _PROJECTS_FILE.exists():
        return {"version": _SCHEMA_VERSION, "projects": []}

    try:
        text = _PROJECTS_FILE.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return {"version": _SCHEMA_VERSION, "projects": []}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": _SCHEMA_VERSION, "projects": []}
