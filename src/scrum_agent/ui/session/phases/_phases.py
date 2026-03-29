"""Pipeline and chat phase orchestration for the TUI session.

# See README: "Architecture" — phase functions drive the interactive flow.
# Contains the pipeline stage loop (Phase D) and post-pipeline chat (Phase E),
# plus helper functions for story selection and pipeline choice screens.
#
# Intake phases are in _phases_intake.py, review phases in _phases_review.py.
"""

from __future__ import annotations

import logging
import threading
import time

from langchain_core.messages import AIMessage, HumanMessage
from rich.console import Console
from rich.live import Live

from scrum_agent.agent.state import ReviewDecision
from scrum_agent.repl._io import _export_checkpoint
from scrum_agent.repl._review import (
    _clear_downstream_artifacts,
    _serialize_artifacts_for_review,
)
from scrum_agent.repl._ui import _PIPELINE_STEPS, _SPINNER_MESSAGES, _predict_next_node
from scrum_agent.ui.session._renderers import _render_pipeline_artifacts
from scrum_agent.ui.session._utils import _invoke_graph_thread, _invoke_with_animation

# Re-export intake and review phase functions for backward compatibility.
# The __init__.py and other callers import these from _phases.
from scrum_agent.ui.session.phases._phases_intake import (  # noqa: F401
    _phase_description_input,
    _phase_intake_questions,
    _question_input_loop,
)
from scrum_agent.ui.session.phases._phases_review import (  # noqa: F401
    _edit_accordion_browse,
    _get_edit_input,
    _phase_intake_review,
)
from scrum_agent.ui.session.screens._screens_pipeline import _build_chat_screen, _build_pipeline_screen
from scrum_agent.ui.shared._animations import FRAME_TIME_30FPS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Story-level auto-scroll helper
# ---------------------------------------------------------------------------


def _find_story_panel_ranges(content_lines: list[str]) -> list[tuple[int, int]]:
    """Find (start_line, end_line) ranges for each story panel in content_lines.

    Scans for Rich Panel border characters (╭ = top, ╰ = bottom) to identify
    panel boundaries. Returns a list of (start, end) pairs — one per story panel.
    """
    import re

    # Find panel top-border lines (╭) and bottom-border lines (╰)
    panel_starts: list[int] = []
    panel_ends: list[int] = []
    for i, line in enumerate(content_lines):
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if "\u256d" in plain:
            panel_starts.append(i)
        elif "\u2570" in plain:
            panel_ends.append(i)

    # Pair starts with ends
    ranges: list[tuple[int, int]] = []
    for j, start in enumerate(panel_starts):
        end = panel_ends[j] if j < len(panel_ends) else len(content_lines) - 1
        ranges.append((start, end))
    return ranges


def _story_index_at_scroll(
    panel_ranges: list[tuple[int, int]],
    scroll_offset: int,
    viewport_h: int,
) -> int:
    """Determine which story is most visible in the viewport.

    Returns the index of the story panel that overlaps most with the
    viewport center. Falls back to 0 if no panels exist.
    """
    if not panel_ranges:
        return 0

    viewport_center = scroll_offset + viewport_h // 2
    best_idx = 0
    best_dist = float("inf")

    for idx, (start, end) in enumerate(panel_ranges):
        panel_center = (start + end) // 2
        dist = abs(panel_center - viewport_center)
        if dist < best_dist:
            best_dist = dist
            best_idx = idx

    return best_idx


# ---------------------------------------------------------------------------
# Pipeline choice screen (sprint selector, capacity warning)
# ---------------------------------------------------------------------------


def _pipeline_choice_screen(
    live: Live,
    console: Console,
    _key,
    *,
    title: str,
    subtitle: str,
    options: list[str],
    step: int,
    total: int,
    stage_label: str,
    progress: str,
) -> int | None:
    """Show a simple choice picker during the pipeline flow.

    # See README: "Architecture" — intermediate pipeline questions.
    # Used for sprint selector and capacity warning intercepts.

    Returns the 0-based index of the selected option, or None on Esc.
    """
    from scrum_agent.ui.shared._components import PAD

    selected = 0
    num_opts = len(options)

    # ANSI escape helpers for styled content lines.
    # _build_pipeline_screen renders content_lines via Text.from_ansi(),
    # so we embed ANSI codes directly for colour/weight.
    _bold = "\033[1m"
    _dim = "\033[2m"
    _rst = "\033[0m"
    _amber = "\033[38;2;200;160;60m"
    _white = "\033[97m"
    _accent = "\033[38;2;70;100;180m"

    def _render_choices():
        w, _ = console.size
        wrap_w = max(40, w - 16)
        lines: list[str] = []
        lines.append("")
        # Title — bold white with warning icon
        lines.append(f"{PAD}{_bold}{_white}\u26a0  {title}{_rst}")
        lines.append("")
        # Subtitle (warning text) — amber, word-wrapped
        if subtitle:
            import textwrap

            for paragraph in subtitle.split("\n"):
                stripped = paragraph.strip()
                if stripped:
                    for wrapped in textwrap.wrap(stripped, wrap_w):
                        lines.append(f"{PAD}{_amber}{wrapped}{_rst}")
            lines.append("")
        # Divider
        lines.append(f"{PAD}{_dim}{'─' * min(50, wrap_w)}{_rst}")
        lines.append("")
        # Options — selected gets bold accent + arrow, unselected gets dim
        for i, opt in enumerate(options):
            if i == selected:
                lines.append(f"{PAD}  {_bold}{_accent}\u203a {opt}{_rst}")
            else:
                lines.append(f"{PAD}    {_dim}{opt}{_rst}")
        lines.append("")
        w, h = console.size
        return _build_pipeline_screen(
            stage_label,
            progress,
            lines,
            0,
            selected,
            status="complete",
            width=w,
            height=h,
            step=step,
            total=total,
            actions=["Select"],
        )

    live.update(_render_choices())

    while True:
        key = _key()
        if key == "esc":
            return None
        elif key in ("up", "scroll_up"):
            selected = (selected - 1) % num_opts
        elif key in ("down", "scroll_down"):
            selected = (selected + 1) % num_opts
        elif key == "enter":
            return selected
        elif key == "":
            pass
        else:
            continue
        live.update(_render_choices())


# ---------------------------------------------------------------------------
# Tracker detection helper
# ---------------------------------------------------------------------------


def _get_active_trackers() -> list[str]:
    """Detect which issue trackers are configured.

    Returns a list of configured trackers, e.g. ["jira"], ["azdevops"], ["jira", "azdevops"], or [].
    """
    from scrum_agent.jira_sync import is_jira_configured

    trackers: list[str] = []
    if is_jira_configured():
        trackers.append("jira")

    from scrum_agent.azdevops_sync import is_azdevops_board_configured

    if is_azdevops_board_configured():
        trackers.append("azdevops")

    return trackers


# ---------------------------------------------------------------------------
# Tracker sync helper (dispatches to Jira or Azure DevOps)
# ---------------------------------------------------------------------------


def _handle_tracker_sync(
    live: Live,
    console: Console,
    _key,
    graph_state: dict,
    stage: str,
    stage_label: str,
    progress: str,
    step: int,
    total: int,
    tracker: str = "jira",
) -> dict | None:
    """Run tracker sync for the current pipeline stage with progress animation.

    Shows a confirmation summary, then a progress screen during sync,
    then updates graph_state with the results.
    Returns updated graph_state on success, or None on cancel/failure.

    tracker: "jira" or "azdevops" — determines which sync module to use.
    # See README: "Tools" — tool types, write tools, human-in-the-loop pattern
    """
    tracker_label = "Jira" if tracker == "jira" else "Azure DevOps"

    if tracker == "jira":
        from scrum_agent.jira_sync import (
            sync_sprints_to_jira,
            sync_stories_to_jira,
            sync_tasks_to_jira,
        )

        story_key_field = "jira_story_keys"
        task_key_field = "jira_task_keys"
        sprint_key_field = "jira_sprint_keys"
        epic_key_field = "jira_epic_key"
    else:
        from scrum_agent.azdevops_sync import (
            sync_iterations_to_azdevops,
            sync_stories_to_azdevops,
            sync_tasks_to_azdevops,
        )

        story_key_field = "azdevops_story_keys"
        task_key_field = "azdevops_task_keys"
        sprint_key_field = "azdevops_iteration_keys"
        epic_key_field = "azdevops_epic_id"

    # Pick the right sync function based on stage and tracker
    if stage == "story_writer":
        sync_fn = sync_stories_to_jira if tracker == "jira" else sync_stories_to_azdevops
    elif stage == "task_decomposer":
        sync_fn = sync_tasks_to_jira if tracker == "jira" else sync_tasks_to_azdevops
    elif stage == "sprint_planner":
        sync_fn = sync_sprints_to_jira if tracker == "jira" else sync_iterations_to_azdevops
    else:
        return None

    # Compute what will be created vs skipped for confirmation
    stories = graph_state.get("stories", [])
    tasks = graph_state.get("tasks", [])
    sprints = graph_state.get("sprints", [])
    existing_stories = len(graph_state.get(story_key_field, {}))
    existing_tasks = len(graph_state.get(task_key_field, {}))
    existing_sprints = len(graph_state.get(sprint_key_field, {}))
    has_epic = bool(graph_state.get(epic_key_field))

    # Build confirmation description
    parts: list[str] = []
    if stage == "story_writer":
        new_stories = len(stories) - existing_stories
        if not has_epic:
            parts.append("1 Epic")
        if new_stories > 0:
            parts.append(f"{new_stories} Stories")
        if existing_stories > 0:
            parts.append(f"({existing_stories} already exist)")
    elif stage == "task_decomposer":
        new_tasks = len(tasks) - existing_tasks
        if not existing_stories and stories:
            parts.append(f"{len(stories)} Stories (cascade)")
        if new_tasks > 0:
            task_label = "Sub-tasks" if tracker == "jira" else "Tasks"
            parts.append(f"{new_tasks} {task_label}")
        if existing_tasks > 0:
            parts.append(f"({existing_tasks} already exist)")
    elif stage == "sprint_planner":
        new_sprints = len(sprints) - existing_sprints
        sprint_label = "Sprints" if tracker == "jira" else "Iterations"
        if new_sprints > 0:
            parts.append(f"{new_sprints} {sprint_label}")
        if existing_sprints > 0:
            parts.append(f"({existing_sprints} already exist)")

    if not parts:
        return None  # Nothing to create

    # Show confirmation via choice screen
    desc = f"Create in {tracker_label}: " + ", ".join(parts)
    choice = _pipeline_choice_screen(
        live,
        console,
        _key,
        title=f"Create in {tracker_label}",
        subtitle=desc,
        options=["Confirm", "Cancel"],
        step=step,
        total=total,
        stage_label=stage_label,
        progress=progress,
    )
    if choice != 0:
        return None  # User cancelled

    # Run sync in a background thread with progress updates
    _sync_log: list[str] = []
    _sync_current: list[str] = ["Starting..."]
    _sync_counter: list[int] = [0, 0]  # [current, total]
    result_box: list = [None]
    state_box: list[dict | None] = [None]
    error_box: list[Exception | None] = [None]
    done = threading.Event()

    def _on_progress(current: int, total_items: int, desc: str) -> None:
        _sync_counter[0] = current
        _sync_counter[1] = total_items
        # Move previous current item to log
        if _sync_current[0] and _sync_current[0] != "Starting...":
            _sync_log.append(f"  \033[32m✓\033[0m {_sync_current[0]}")
        _sync_current[0] = desc

    def _run_sync() -> None:
        try:
            sync_result, new_state = sync_fn(graph_state, on_progress=_on_progress)
            result_box[0] = sync_result
            state_box[0] = new_state
        except Exception as e:
            error_box[0] = e
        finally:
            done.set()

    thread = threading.Thread(target=_run_sync, daemon=True)
    thread.start()

    # Animate progress while sync runs — show scrolling log of completed items
    start = time.monotonic()
    while not done.is_set():
        tick = time.monotonic() - start
        w, h = console.size
        viewport_h = max(3, h - 16)
        visible_log = _sync_log[-viewport_h:] if _sync_log else []
        cur = _sync_counter[0]
        tot = _sync_counter[1]
        counter = f"[{cur}/{tot}]" if tot else ""
        active_line = f"  \033[33m▸\033[0m {counter} {_sync_current[0]}"
        status_lines = [*visible_log, active_line]
        live.update(
            _build_pipeline_screen(
                stage_label,
                progress,
                status_lines,
                0,
                0,
                status="processing",
                width=w,
                height=h,
                tick=tick,
                step=step,
                total=total,
            )
        )
        time.sleep(FRAME_TIME_30FPS)

    thread.join()

    if error_box[0] is not None:
        logger.error("%s sync failed: %s", tracker_label, error_box[0])
        return None

    if state_box[0] is not None:
        sync_result = result_box[0]
        if sync_result and sync_result.errors:
            logger.warning("%s sync completed with %d errors", tracker_label, len(sync_result.errors))
        return state_box[0]

    return None


# ---------------------------------------------------------------------------
# Phase D: Pipeline Stages
# ---------------------------------------------------------------------------


def _phase_pipeline(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    export_only: bool,
    bell: bool,
    project_id: str = "",
    dry_run: bool = False,
) -> dict | None:
    """Run 5 pipeline stages: analyzer -> features -> stories -> tasks -> sprints.

    Each stage: invoke graph -> show result -> Accept/Edit/Export review.
    Returns updated graph_state after all stages, or None on cancel.

    When dry_run=True, skips graph invocations and progressively loads
    pre-saved artifacts with fake animation delays (1.5-3s per stage).
    """
    logger.info("_phase_pipeline started: export_only=%s dry_run=%s", export_only, dry_run)
    from scrum_agent.persistence import save_project_snapshot

    # Pre-load the complete state for dry-run playback.
    dry_run_full_state = None
    if dry_run:
        from scrum_agent.ui.session._dry_run import build_stage_snapshot, load_dry_run_state

        dry_run_full_state = load_dry_run_state()
        if dry_run_full_state is None:
            return graph_state

    while True:
        # If a previous session left pending_review set (user exited mid-review),
        # skip the LLM call and jump straight to the review screen.
        pending = graph_state.get("pending_review")
        if pending and pending in _PIPELINE_STEPS:
            next_node = pending
        elif dry_run:
            # In dry-run, walk through pipeline stages sequentially.
            # Determine next stage based on which artifacts are missing.
            if "project_analysis" not in graph_state:
                next_node = "project_analyzer"
            elif "features" not in graph_state:
                next_node = "feature_generator"
            elif "stories" not in graph_state:
                next_node = "story_writer"
            elif "tasks" not in graph_state:
                next_node = "task_decomposer"
            elif "sprints" not in graph_state:
                next_node = "sprint_planner"
            else:
                return graph_state  # all stages done
        else:
            next_node = _predict_next_node(graph_state)

        if next_node == "agent":
            # Pipeline complete
            return graph_state
        if next_node == "project_intake":
            # Still in intake — shouldn't happen but handle gracefully
            return graph_state

        logger.info("Pipeline stage entry: %s", next_node)
        # feature_skip occupies the same pipeline slot as feature_generator (step 2/5).
        step_node = "feature_generator" if next_node == "feature_skip" else next_node
        step = _PIPELINE_STEPS.index(step_node) + 1 if step_node in _PIPELINE_STEPS else 0
        total = len(_PIPELINE_STEPS)
        progress = f"[{step}/{total}]"
        stage_label = _SPINNER_MESSAGES.get(next_node, "Working")

        # Skip the LLM call if we already have artifacts awaiting review
        # (resumed session or re-entering the loop after an edit request).
        if not pending:
            if dry_run:
                # Fake animation delay (1.5-3s) with the pipeline processing screen
                import random

                delay = random.uniform(1.5, 3.0)
                anim_start = time.monotonic()
                while time.monotonic() - anim_start < delay:
                    tick = time.monotonic() - anim_start
                    w, h = console.size
                    live.update(
                        _build_pipeline_screen(
                            stage_label,
                            progress,
                            [],
                            0,
                            0,
                            status="processing",
                            width=w,
                            height=h,
                            tick=tick,
                            step=step,
                            total=total,
                        )
                    )
                    time.sleep(FRAME_TIME_30FPS)
                # Load the artifacts for this stage from the pre-saved state
                graph_state = build_stage_snapshot(dry_run_full_state, next_node)
            else:
                # Invoke the pipeline node
                user_msg = HumanMessage(content="continue")
                invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}

                result = _invoke_with_animation(
                    live, console, graph, invoke_state, stage_label, progress, step=step, total=total
                )
                if result is None:
                    return None

                graph_state = result

            if bell:
                console.bell()

            # Save immediately after LLM call so work isn't lost if user exits mid-review.
            if project_id:
                save_project_snapshot(project_id, graph_state)

        # ── Capacity warning intercept ────────────────────────────────
        # When total story points exceed sprint capacity, sprint_planner
        # returns a warning with capacity_override_target < -1.
        # Show a choice popup BEFORE generating sprints so the user can
        # decide whether to extend or keep the original target.
        # See README: "Guardrails" — human-in-the-loop pattern
        _cap_sel = graph_state.get("capacity_override_target", 0)
        if _cap_sel < -1 and not dry_run:
            recommended = abs(_cap_sel)
            original_target = graph_state.get("_original_target_sprints", recommended)
            recommended_team = graph_state.get("_recommended_team_size", 0)
            current_team = graph_state.get("team_size", 1)
            # Extract warning text from the last AI message
            ai_msgs = graph_state.get("messages", [])
            cap_warning_text = ""
            if ai_msgs and isinstance(ai_msgs[-1], AIMessage):
                cap_warning_text = ai_msgs[-1].content.replace("**", "")
            # Build 3 options: extend sprints, increase team, keep as-is (overload).
            # Options 1 and 2 are both viable — only option 3 is flagged as not recommended.
            # When team is already at the Jira org cap, the "increase team" option is
            # replaced with a disabled note explaining why it's unavailable.
            # See README: "Guardrails" — human-in-the-loop pattern
            options = [
                f"Extend to {recommended} sprints",
            ]
            team_can_grow = recommended_team > current_team
            if team_can_grow:
                options.append(f"Keep {original_target} sprints — increase team to {recommended_team} engineers")
            elif recommended_team > 0:
                # Jira cap reached — show disabled note in subtitle
                cap_warning_text += (
                    f"\n\nIncrease team is unavailable — your Jira board has "
                    f"{current_team} team member(s), which is already the maximum."
                )
            options.append(f"Keep {original_target} sprints, {current_team} engineer(s) — overload (not recommended)")
            # Show interactive choice popup before re-invoking sprint_planner
            choice = _pipeline_choice_screen(
                live,
                console,
                _key,
                title="Capacity Overflow",
                subtitle=cap_warning_text,
                options=options,
                step=step,
                total=total,
                stage_label=stage_label,
                progress=progress,
            )
            overload_index = len(options) - 1
            team_index = 1 if team_can_grow else -1  # -1 = no team option
            if choice == team_index:
                # Increase team size — sprint_planner will recalculate velocity
                graph_state["capacity_override_target"] = -1
                graph_state["_capacity_team_override"] = recommended_team
            elif choice == overload_index:
                # Keep as-is with overloaded sprints — sprint_planner will use enforce_target
                graph_state["capacity_override_target"] = -1
            else:
                # Extend to recommended (default if Esc or option 0)
                graph_state["capacity_override_target"] = recommended
            # Save warning text as a banner for the sprint review screen
            graph_state["_capacity_warning"] = {"text": cap_warning_text, "recommended": recommended}
            continue  # Re-invoke graph — sprint_planner generates sprints

        # Check for pending_review
        pending = graph_state.get("pending_review")
        if not pending:
            continue

        # Story highlighting: for the story_writer stage, normal line-by-line
        # scrolling is preserved but the story closest to the viewport center
        # is highlighted with a white border. The selected_story index is
        # recomputed from scroll_offset on every frame.
        # See README: "Architecture" — story highlighting in pipeline review
        is_story_stage = pending == "story_writer"
        story_count = len(graph_state.get("stories", [])) if is_story_stage else 0
        selected_story = 0 if is_story_stage and story_count > 0 else None

        # Render artifacts for review (with story selection highlight if applicable)
        content_lines, sticky_headers = _render_pipeline_artifacts(
            console, graph_state, selected_story=selected_story if is_story_stage else None
        )
        # Pre-compute panel ranges for story highlighting
        story_panel_ranges = _find_story_panel_ranges(content_lines) if is_story_stage else []

        if export_only:
            # Auto-accept
            graph_state.pop("pending_review", None)
            graph_state.pop("last_review_decision", None)
            graph_state.pop("last_review_feedback", None)
            continue

        # Review loop
        scroll_offset = 0
        menu_selected = 0
        status_msg = ""

        # Action buttons differ by stage:
        # story_writer/task_decomposer/sprint_planner: Accept | Edit | Regenerate | Export [| Jira]
        # others: Accept | Edit | Export
        is_analysis_stage = pending == "project_analyzer"
        is_feature_stage = pending == "feature_generator"
        is_task_stage = pending == "task_decomposer"
        is_sprint_stage = pending == "sprint_planner"
        if is_story_stage or is_feature_stage or is_task_stage or is_sprint_stage:
            actions = ["Accept", "Edit", "Regenerate", "Export"]
        else:
            actions = ["Accept", "Edit", "Export"]

        # Add tracker sync buttons to stages that produce syncable artifacts.
        # When the user chose a preferred tracker at intake (both were configured),
        # only show that tracker's button. Otherwise show all configured trackers.
        # Feature stage does NOT get sync buttons — features map to labels/tags, not issues.
        # See README: "Tools" — tool types, write tools, human-in-the-loop pattern
        _active_trackers = _get_active_trackers()
        _qs = graph_state.get("questionnaire")
        _pref = getattr(_qs, "_preferred_tracker", "") if _qs else ""
        if _pref:
            # User chose a preferred tracker — only show that one
            _active_trackers = [_pref] if _pref in _active_trackers else _active_trackers
        if _active_trackers and (is_story_stage or is_task_stage or is_sprint_stage):
            for _trk in _active_trackers:
                actions.append("Jira" if _trk == "jira" else "Azure DevOps")
        num_actions = len(actions)

        # Capacity warning state — shown as a banner on the sprint review screen.
        # The user already made their choice before sprint generation, so this is
        # display-only (no popup on Accept).
        cap_warning = graph_state.get("_capacity_warning")
        cap_warning_text = cap_warning["text"] if cap_warning else ""

        # Button fade animation state — same pattern as intake review
        btn_fades = [1.0] + [0.0] * (num_actions - 1)
        btn_targets = list(btn_fades)

        w, h = console.size
        live.update(
            _build_pipeline_screen(
                stage_label,
                progress,
                content_lines,
                scroll_offset,
                menu_selected,
                status="complete",
                width=w,
                height=h,
                btn_fades=btn_fades,
                step=step,
                total=total,
                sticky_headers=sticky_headers,
                actions=actions,
                warning_text=cap_warning_text if is_sprint_stage else "",
            )
        )

        while True:
            key = _key()

            if key == "esc":
                return None
            elif key in ("up", "scroll_up"):
                scroll_offset = max(0, scroll_offset - 1)
                # Track which story is closest to viewport center (for Edit action)
                if is_story_stage and story_panel_ranges:
                    _, viewport_h = console.size
                    vp_h = max(3, viewport_h - 14)
                    new_sel = _story_index_at_scroll(story_panel_ranges, scroll_offset, vp_h)
                    if new_sel != selected_story:
                        selected_story = new_sel
            elif key in ("down", "scroll_down"):
                scroll_offset += 1
                if is_story_stage and story_panel_ranges:
                    _, viewport_h = console.size
                    vp_h = max(3, viewport_h - 14)
                    new_sel = _story_index_at_scroll(story_panel_ranges, scroll_offset, vp_h)
                    if new_sel != selected_story:
                        selected_story = new_sel
            elif key == "left":
                menu_selected = (menu_selected - 1) % num_actions
                btn_targets = [1.0 if i == menu_selected else 0.0 for i in range(num_actions)]
            elif key == "right":
                menu_selected = (menu_selected + 1) % num_actions
                btn_targets = [1.0 if i == menu_selected else 0.0 for i in range(num_actions)]
            elif key == "enter":
                action = actions[menu_selected]
                if action == "Accept":
                    logger.info("Review decision: Accept for %s", pending)
                    graph_state.pop("pending_review", None)
                    graph_state.pop("last_review_decision", None)
                    graph_state.pop("last_review_feedback", None)
                    # Save Point C — persist after each pipeline stage acceptance
                    if project_id:
                        save_project_snapshot(project_id, graph_state)
                    break
                elif action == "Edit":
                    logger.info("Review decision: Edit for %s", pending)
                    if is_story_stage and selected_story is not None:
                        # Direct text editor for the selected story
                        from scrum_agent.ui.session.editor._editor import edit_story

                        stories = graph_state.get("stories", [])
                        if 0 <= selected_story < len(stories):
                            edited = edit_story(live, console, stories[selected_story], _key, width=w, height=h)
                            if edited is not None:
                                # Replace the story in the list (stories is a plain list at this point)
                                stories[selected_story] = edited
                                graph_state["stories"] = stories
                                # Re-render with the updated story
                                content_lines, sticky_headers = _render_pipeline_artifacts(
                                    console, graph_state, selected_story=selected_story
                                )
                        # Stay in review loop — no LLM call
                    elif is_feature_stage:
                        # Direct text editor for all epics
                        from scrum_agent.ui.session.editor._editor_artifacts import edit_feature

                        epic_list = graph_state.get("features", [])
                        if epic_list:
                            edited_epics = edit_feature(
                                live,
                                console,
                                epic_list,
                                _key,
                                width=w,
                                height=h,
                            )
                            if edited_epics is not None:
                                graph_state["features"] = edited_epics
                                content_lines, sticky_headers = _render_pipeline_artifacts(console, graph_state)
                        # Stay in review loop — no LLM call
                    elif is_task_stage:
                        # Direct text editor for tasks — find the story group
                        # closest to viewport center using panel ranges.
                        from scrum_agent.ui.session.editor._editor_artifacts import edit_task

                        all_tasks = graph_state.get("tasks", [])
                        stories = graph_state.get("stories", [])
                        # Build story groups in the same order as rendering
                        tasks_by_story: dict[str, list] = {}
                        for t in all_tasks:
                            tasks_by_story.setdefault(t.story_id, []).append(t)
                        story_group_ids = [s.id for s in stories if s.id in tasks_by_story]
                        # Determine selected story group from scroll position
                        task_panel_ranges = _find_story_panel_ranges(content_lines)
                        _, vp_h = console.size
                        vp_h_eff = max(3, vp_h - 14)
                        sel_group_idx = _story_index_at_scroll(task_panel_ranges, scroll_offset, vp_h_eff)
                        if 0 <= sel_group_idx < len(story_group_ids):
                            sid = story_group_ids[sel_group_idx]
                            group_tasks = tasks_by_story[sid]
                            edited_tasks = edit_task(
                                live,
                                console,
                                group_tasks,
                                _key,
                                width=w,
                                height=h,
                                story_id=sid,
                            )
                            if edited_tasks is not None:
                                # Replace the edited tasks in the full list
                                task_ids = {t.id for t in group_tasks}
                                new_all = [t for t in all_tasks if t.id not in task_ids]
                                new_all.extend(edited_tasks)
                                # Re-sort to preserve original order
                                orig_order = {t.id: i for i, t in enumerate(all_tasks)}
                                new_all.sort(key=lambda t: orig_order.get(t.id, 999))
                                graph_state["tasks"] = new_all
                                content_lines, sticky_headers = _render_pipeline_artifacts(console, graph_state)
                        # Stay in review loop — no LLM call
                    elif is_sprint_stage:
                        # Direct text editor for sprints — find the sprint
                        # closest to viewport center using panel ranges.
                        from scrum_agent.ui.session.editor._editor_artifacts import edit_sprint

                        sprint_list = graph_state.get("sprints", [])
                        sprint_panel_ranges = _find_story_panel_ranges(content_lines)
                        _, vp_h = console.size
                        vp_h_eff = max(3, vp_h - 14)
                        sel_sprint_idx = _story_index_at_scroll(sprint_panel_ranges, scroll_offset, vp_h_eff)
                        if 0 <= sel_sprint_idx < len(sprint_list):
                            edited_sprint = edit_sprint(
                                live,
                                console,
                                sprint_list[sel_sprint_idx],
                                _key,
                                width=w,
                                height=h,
                            )
                            if edited_sprint is not None:
                                sprint_list[sel_sprint_idx] = edited_sprint
                                graph_state["sprints"] = sprint_list
                                content_lines, sticky_headers = _render_pipeline_artifacts(console, graph_state)
                        # Stay in review loop — no LLM call
                    elif is_analysis_stage:
                        # Go back to the accordion to edit questionnaire answers,
                        # then regenerate the analysis from the updated answers.
                        edit_result = _edit_accordion_browse(
                            live,
                            console,
                            graph,
                            graph_state,
                            _key,
                            export_only,
                        )
                        if edit_result is None:
                            return None
                        graph_state = edit_result
                        # Clear analysis so it gets regenerated from updated answers
                        graph_state.pop("project_analysis", None)
                        graph_state.pop("pending_review", None)
                        break  # Re-invoke graph to regenerate analysis
                    else:
                        # Non-story stages: prompt for feedback → LLM regeneration
                        feedback = _get_edit_input(
                            live,
                            console,
                            _key,
                            "Describe what you'd like to change:",
                        )
                        if feedback:
                            pending_node = graph_state["pending_review"]
                            serialized = _serialize_artifacts_for_review(graph_state, pending_node)
                            _clear_downstream_artifacts(graph_state, pending_node)
                            graph_state["last_review_decision"] = ReviewDecision.EDIT
                            if serialized:
                                graph_state["last_review_feedback"] = (
                                    f"{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}"
                                )
                            else:
                                graph_state["last_review_feedback"] = feedback
                            graph_state.pop("pending_review", None)
                            break
                        # Cancel edit — stay on review
                elif action == "Regenerate":
                    logger.info("Review decision: Regenerate for %s", pending)
                    # LLM-assisted regeneration (story stage only)
                    feedback = _get_edit_input(
                        live,
                        console,
                        _key,
                        "Describe what you'd like the AI to change:",
                    )
                    if feedback:
                        pending_node = graph_state["pending_review"]
                        serialized = _serialize_artifacts_for_review(graph_state, pending_node)
                        _clear_downstream_artifacts(graph_state, pending_node)
                        graph_state["last_review_decision"] = ReviewDecision.EDIT
                        if serialized:
                            graph_state["last_review_feedback"] = f"{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}"
                        else:
                            graph_state["last_review_feedback"] = feedback
                        graph_state.pop("pending_review", None)
                        break
                    # Cancel — stay on review
                elif action == "Export":
                    logger.info("Review decision: Export for %s", pending)
                    from scrum_agent.html_exporter import export_plan_html
                    from scrum_agent.repl._io import _export_plan_markdown

                    html_path = export_plan_html(graph_state, stage=pending)
                    md_path = _export_plan_markdown(graph_state)
                    logger.info("Exported: HTML=%s, MD=%s", html_path, md_path)

                    # Show export success screen using shared component
                    from scrum_agent.ui.mode_select.screens._screens_secondary import (
                        _build_project_export_success_screen,
                    )

                    paths = f"HTML  {html_path}\nMD    {md_path}"
                    w, h = console.size
                    live.update(
                        _build_project_export_success_screen(
                            paths,
                            width=w,
                            height=h,
                            subtitle="Exported (HTML + MD)",
                            mode="planning",
                        )
                    )
                    import time as _export_time

                    _t0 = _export_time.monotonic()
                    while True:
                        try:
                            _ek = _key(timeout=0.05)
                        except TypeError:
                            _ek = _key()
                        if _export_time.monotonic() - _t0 > 1.0 and _ek:
                            break
                    status_msg = ""
                    # Force immediate redraw
                    w, h = console.size
                    live.update(
                        _build_pipeline_screen(
                            stage_label,
                            progress,
                            content_lines,
                            scroll_offset,
                            menu_selected,
                            status="complete",
                            width=w,
                            height=h,
                            status_msg=status_msg,
                            btn_fades=btn_fades,
                            step=step,
                            total=total,
                            sticky_headers=sticky_headers,
                            actions=actions,
                            warning_text=cap_warning_text if is_sprint_stage else "",
                        )
                    )
                elif action in ("Jira", "Azure DevOps"):
                    _btn_tracker = "jira" if action == "Jira" else "azdevops"
                    logger.info("%s sync requested for %s", action, pending)
                    tracker_result = _handle_tracker_sync(
                        live,
                        console,
                        _key,
                        graph_state,
                        pending,
                        stage_label,
                        progress,
                        step,
                        total,
                        tracker=_btn_tracker,
                    )
                    if tracker_result is not None:
                        graph_state = tracker_result
                        # Save after tracker sync
                        if project_id:
                            save_project_snapshot(project_id, graph_state)
                        # Re-render artifacts (content_lines unchanged, but status_msg updates)
                        _sk = "jira_story_keys" if _btn_tracker == "jira" else "azdevops_story_keys"
                        created = len(graph_state.get(_sk, {}))
                        status_msg = f"Synced to {action} ({created} stories)"

            # Animate button fades toward targets
            _fade_step = 0.15
            for i in range(num_actions):
                if btn_fades[i] < btn_targets[i]:
                    btn_fades[i] = min(btn_fades[i] + _fade_step, btn_targets[i])
                elif btn_fades[i] > btn_targets[i]:
                    btn_fades[i] = max(btn_fades[i] - _fade_step, btn_targets[i])

            w, h = console.size
            live.update(
                _build_pipeline_screen(
                    stage_label,
                    progress,
                    content_lines,
                    scroll_offset,
                    menu_selected,
                    status="complete",
                    width=w,
                    height=h,
                    status_msg=status_msg,
                    btn_fades=btn_fades,
                    step=step,
                    total=total,
                    sticky_headers=sticky_headers,
                    actions=actions,
                    warning_text=cap_warning_text if is_sprint_stage else "",
                )
            )


# ---------------------------------------------------------------------------
# Phase E: Completion + Chat
# ---------------------------------------------------------------------------


def _phase_chat(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    export_only: bool,
    project_id: str = "",
) -> None:
    """Post-pipeline chat — the user can ask questions or export.

    For export_only mode, just exports and returns.
    """
    logger.info("_phase_chat started")
    from scrum_agent.persistence import save_project_snapshot
    from scrum_agent.repl._io import _export_plan_markdown

    if export_only:
        _export_plan_markdown(graph_state)
        return

    messages: list[tuple[str, str]] = []
    input_value = ""
    scroll_offset = 0

    w, h = console.size
    live.update(_build_chat_screen(messages, input_value, scroll_offset, width=w, height=h))

    while True:
        key = _key()

        if key == "esc":
            return
        elif key == "enter":
            if not input_value.strip():
                continue

            text = input_value.strip()

            # Handle export command
            if text.lower() == "export":
                _export_checkpoint(console, graph_state, stage="complete")
                messages.append(("ai", "Plan exported successfully."))
                input_value = ""
                scroll_offset = max(0, len(messages) * 2 - 5)
                w, h = console.size
                live.update(_build_chat_screen(messages, input_value, scroll_offset, width=w, height=h))
                continue

            if text.lower() in {"exit", "quit"}:
                return

            messages.append(("user", text))
            input_value = ""
            logger.debug("Chat message sent: len=%d", len(text))

            # Invoke graph in background
            user_msg = HumanMessage(content=text)
            invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}

            # Show processing state
            result_box: list = [None, None]
            thread = threading.Thread(
                target=_invoke_graph_thread,
                args=(graph, invoke_state, result_box),
                daemon=True,
            )
            thread.start()

            start = time.monotonic()
            while thread.is_alive():
                tick = time.monotonic() - start
                w, h = console.size
                live.update(
                    _build_chat_screen(
                        messages,
                        "",
                        scroll_offset,
                        width=w,
                        height=h,
                        processing=True,
                        tick=tick,
                    )
                )
                time.sleep(FRAME_TIME_30FPS)
            thread.join()

            if result_box[0] is not None:
                graph_state = result_box[0]
                ai_msgs = graph_state.get("messages", [])
                if ai_msgs and isinstance(ai_msgs[-1], AIMessage):
                    messages.append(("ai", ai_msgs[-1].content))
                # Save Point D — persist after chat messages
                if project_id:
                    save_project_snapshot(project_id, graph_state)
            elif result_box[1] is not None:
                logger.error("Chat graph invoke failed: %s", result_box[1])
                messages.append(("ai", f"Error: {result_box[1]}"))

            scroll_offset = max(0, len(messages) * 2 - 5)

        elif key == "backspace":
            input_value = input_value[:-1]
        elif key == "clear":
            input_value = ""
        elif isinstance(key, str) and key.startswith("paste:"):
            input_value += key[6:]
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            input_value += key
        elif key == "":
            pass
        else:
            continue

        w, h = console.size
        live.update(_build_chat_screen(messages, input_value, scroll_offset, width=w, height=h))
