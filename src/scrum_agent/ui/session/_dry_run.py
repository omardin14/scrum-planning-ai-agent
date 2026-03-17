"""Dry-run support — load pre-saved state and fake pipeline invocations.

Used by --dry-run to iterate on the TUI without LLM calls.
Loads the most complete saved project state and progressively reveals
artifacts stage-by-stage with fake delays.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATES_DIR = Path.home() / ".scrum-agent" / "states"
_PROJECTS_FILE = Path.home() / ".scrum-agent" / "projects.json"

# Pipeline stages in order — artifacts added at each stage.
_STAGE_ARTIFACTS: dict[str, list[str]] = {
    "project_analyzer": ["project_analysis"],
    "epic_generator": ["epics"],
    "story_writer": ["stories"],
    "task_decomposer": ["tasks"],
    "sprint_planner": ["sprints"],
}


def load_dry_run_state() -> dict[str, Any] | None:
    """Load the most complete saved state for dry-run playback.

    Picks the largest state file (most artifacts) as the source.
    Returns the deserialized state dict with LangChain messages reconstructed.
    """
    logger.debug("load_dry_run_state: looking for saved projects")
    from scrum_agent.persistence import load_graph_state

    if not _PROJECTS_FILE.exists():
        logger.warning("load_dry_run_state: projects file not found")
        return None

    try:
        data = json.loads(_PROJECTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    projects = data.get("projects", [])
    if not projects:
        return None

    # Find the most complete project (all pipeline stages done, most artifacts)
    best_id = None
    best_score = -1
    for proj in projects:
        pipeline = proj.get("pipeline_progress", {})
        artifacts = proj.get("artifact_counts", {})
        score = sum(1 for v in pipeline.values() if v) * 1000
        score += sum(artifacts.values())
        if score > best_score:
            best_score = score
            best_id = proj.get("id")

    if best_id is None:
        logger.warning("load_dry_run_state: no suitable project found")
        return None

    logger.info("load_dry_run_state: loaded project %s (score=%d)", best_id, best_score)
    return load_graph_state(best_id)


def build_stage_snapshot(full_state: dict[str, Any], up_to_stage: str) -> dict[str, Any]:
    """Return a copy of full_state with only artifacts up to and including the given stage.

    Used by the dry-run pipeline to progressively reveal artifacts:
    after "project_analyzer" → only project_analysis,
    after "epic_generator"   → project_analysis + epics, etc.
    """
    logger.debug("build_stage_snapshot: stage=%s", up_to_stage)
    snapshot = deepcopy(full_state)

    # Determine which artifact keys to keep
    keep: set[str] = set()
    for stage, keys in _STAGE_ARTIFACTS.items():
        keep.update(keys)
        if stage == up_to_stage:
            break

    # Remove artifacts beyond the current stage
    all_artifact_keys = {k for keys in _STAGE_ARTIFACTS.values() for k in keys}
    for key in all_artifact_keys - keep:
        snapshot.pop(key, None)

    # Set pending_review so the review UI shows after each stage
    snapshot["pending_review"] = up_to_stage

    return snapshot
