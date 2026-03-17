"""Seed ~/.scrum-agent/ with mock project data for UI development.

Copies the real "Restaurant Reservation App" state and creates additional
projects at different pipeline stages so the mode selector has varied data
to display without needing live LLM calls.

Usage:
    uv run python scripts/seed_mock_data.py          # seed mock data
    uv run python scripts/seed_mock_data.py --clean   # remove mock data only
"""

from __future__ import annotations

import json
import sys
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

CONFIG_DIR = Path.home() / ".scrum-agent"
PROJECTS_FILE = CONFIG_DIR / "projects.json"
STATES_DIR = CONFIG_DIR / "states"

# Tag so we can identify and clean up mock entries later.
_MOCK_TAG = "__mock__"

# Source project — the fully-completed Restaurant Reservation App.
_SOURCE_STATE_ID = "711eea7f-f49f-4667-b4d5-dc35e49046e2"


def _now_iso(offset_days: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=abs(offset_days))).isoformat()


def _make_id() -> str:
    return str(uuid.uuid4())


def _load_projects() -> dict:
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "projects": []}


def _save_projects(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_source_state() -> dict | None:
    path = STATES_DIR / f"{_SOURCE_STATE_ID}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(project_id: str, state: dict) -> None:
    STATES_DIR.mkdir(parents=True, exist_ok=True)
    path = STATES_DIR / f"{project_id}.json"
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _strip_to_stage(state: dict, up_to: str) -> dict:
    """Return a copy of state with artifacts removed beyond a given stage."""
    trimmed = deepcopy(state)

    # Ordered artifact keys by pipeline stage
    stage_keys = {
        "intake_complete": [],
        "project_analyzer": ["project_analysis"],
        "epic_generator": ["project_analysis", "epics"],
        "story_writer": ["project_analysis", "epics", "stories"],
        "task_decomposer": ["project_analysis", "epics", "stories", "tasks"],
        "sprint_planner": ["project_analysis", "epics", "stories", "tasks", "sprints"],
    }

    keep = set(stage_keys.get(up_to, []))
    for key in ("project_analysis", "epics", "stories", "tasks", "sprints"):
        if key not in keep and key in trimmed:
            del trimmed[key]

    return trimmed


def _clean_mocks() -> int:
    """Remove all mock-tagged projects and their state files."""
    data = _load_projects()
    original = data.get("projects", [])
    remaining = []
    removed = 0

    for proj in original:
        if proj.get(_MOCK_TAG):
            pid = proj.get("id", "")
            state_path = STATES_DIR / f"{pid}.json"
            if state_path.exists():
                state_path.unlink()
            removed += 1
        else:
            remaining.append(proj)

    data["projects"] = remaining
    _save_projects(data)
    return removed


def seed() -> None:
    source_state = _load_source_state()
    if source_state is None:
        print(f"Source state not found: {STATES_DIR / f'{_SOURCE_STATE_ID}.json'}")
        print("Run a full planning session first to generate source data.")
        sys.exit(1)

    # Clean previous mocks first
    cleaned = _clean_mocks()
    if cleaned:
        print(f"Cleaned {cleaned} previous mock project(s)")

    data = _load_projects()
    projects = data.get("projects", [])

    mock_projects = [
        {
            "name": "E-Commerce Platform",
            "description": (
                "Multi-vendor marketplace with payment processing, inventory management, and seller dashboards."
            ),
            "stage": "sprint_planner",  # fully complete
            "days_ago": 2,
        },
        {
            "name": "Healthcare Patient Portal",
            "description": (
                "Secure patient portal for appointment scheduling, medical records, and telemedicine integration."
            ),
            "stage": "story_writer",  # mid-pipeline
            "days_ago": 5,
        },
        {
            "name": "Internal DevOps Dashboard",
            "description": "Real-time CI/CD pipeline monitoring with deployment tracking and incident alerting.",
            "stage": "epic_generator",  # early pipeline
            "days_ago": 1,
        },
        {
            "name": "AI Chatbot for Customer Support",
            "description": "NLP-powered chatbot handling tier-1 support tickets with human escalation workflow.",
            "stage": "intake_complete",  # just finished intake
            "days_ago": 0,
        },
    ]

    pipeline_booleans = {
        "intake_complete": {
            "description_input": True,
            "intake_complete": True,
            "project_analyzer": False,
            "epic_generator": False,
            "story_writer": False,
            "task_decomposer": False,
            "sprint_planner": False,
        },
        "epic_generator": {
            "description_input": True,
            "intake_complete": True,
            "project_analyzer": True,
            "epic_generator": True,
            "story_writer": False,
            "task_decomposer": False,
            "sprint_planner": False,
        },
        "story_writer": {
            "description_input": True,
            "intake_complete": True,
            "project_analyzer": True,
            "epic_generator": True,
            "story_writer": True,
            "task_decomposer": False,
            "sprint_planner": False,
        },
        "sprint_planner": {
            "description_input": True,
            "intake_complete": True,
            "project_analyzer": True,
            "epic_generator": True,
            "story_writer": True,
            "task_decomposer": True,
            "sprint_planner": True,
        },
    }

    artifact_counts_by_stage = {
        "intake_complete": {"epics": 0, "stories": 0, "tasks": 0, "sprints": 0},
        "epic_generator": {"epics": 5, "stories": 0, "tasks": 0, "sprints": 0},
        "story_writer": {"epics": 5, "stories": 18, "tasks": 0, "sprints": 0},
        "sprint_planner": {"epics": 6, "stories": 22, "tasks": 74, "sprints": 6},
    }

    for mock in mock_projects:
        pid = _make_id()
        stage = mock["stage"]
        now = _now_iso(mock["days_ago"])

        # Create project metadata entry
        entry = {
            "id": pid,
            "name": mock["name"],
            "description": mock["description"],
            "created_at": now,
            "updated_at": now,
            "pipeline_progress": pipeline_booleans[stage],
            "artifact_counts": artifact_counts_by_stage[stage],
            "jira_sync": {"epics_synced": 0, "epics_total": 0, "stories_synced": 0, "stories_total": 0},
            _MOCK_TAG: True,
        }
        projects.append(entry)

        # Create trimmed state file from the real source
        trimmed = _strip_to_stage(source_state, stage)
        # Patch the questionnaire project name to match mock
        if "questionnaire" in trimmed:
            qs = trimmed["questionnaire"]
            if isinstance(qs, dict) and "answers" in qs:
                qs["answers"]["1"] = mock["name"]
        _save_state(pid, trimmed)

        print(f"  + {mock['name']:<40} [{stage}]")

    data["projects"] = projects
    _save_projects(data)
    print(f"\nSeeded {len(mock_projects)} mock projects into {PROJECTS_FILE}")
    print(f"State files written to {STATES_DIR}/")
    print("\nRun with:  make run-mock")


if __name__ == "__main__":
    if "--clean" in sys.argv:
        removed = _clean_mocks()
        print(f"Removed {removed} mock project(s)")
    else:
        seed()
