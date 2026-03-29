"""Full-screen mode selection screen using Rich Live + raw terminal input.

# See README: "Architecture" — this is a UI component in the CLI layer.
# Shown after the setup wizard completes (or on subsequent launches).
# The user picks which agent mode to run: Project Planning, Code Review, etc.
# After selecting Planning, the title slides up and the project list fades in.

Mode names are rendered as two-line ASCII art, stacked vertically.
When a mode is selected, its description typewriter-scrolls in underneath.
Arrow keys navigate, Enter selects. "Coming soon" modes are visible but
not selectable.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path

from rich.console import Console
from rich.live import Live

from scrum_agent.ui.mode_select.screens._project_cards import (  # noqa: F401
    ProfileSummary,
    ProjectSummary,
    _build_action_button,
    _build_empty_state_card,
    _build_new_analysis_card,
    _build_new_project_card,
    _build_peek_above,
    _build_peek_below,
    _build_profile_card,
    _build_project_card,
    _compute_viewport,
)
from scrum_agent.ui.mode_select.screens._project_list_screen import (  # noqa: F401
    _build_project_list_screen,
    _build_project_row,
)

# Re-exports for backwards compatibility and test imports.
from scrum_agent.ui.mode_select.screens._screens import (  # noqa: F401
    _INTAKE_CARDS,
    _MODE_CARDS,
    _OFFLINE_CARDS,
    _build_mode_screen,
    _build_slide_frame,
)
from scrum_agent.ui.mode_select.screens._screens_secondary import (  # noqa: F401
    _build_export_success_screen,
    _build_import_screen,
    _build_intake_screen,
    _build_offline_screen,
    _build_project_export_success_screen,
    _build_team_analysis_screen,
)
from scrum_agent.ui.shared._animations import (
    COLOR_RGB,
    FADE_IN_LEVELS,
    FADE_OUT_LEVELS,
    FRAME_TIME_60FPS,
    ease_out_cubic,
)
from scrum_agent.ui.shared._input import read_key as _read_key

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants used only by the orchestrator
# ---------------------------------------------------------------------------

_DESC_SCROLL_SPEED = 200  # characters per second for typewriter reveal
_FRAME_TIME = FRAME_TIME_60FPS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_ana_sid = ""  # module-level analysis session ID
_ana_dbp = Path.home() / ".scrum-agent" / "sessions.db"  # module-level DB path


def _load_ana_session(project_key: str) -> dict | None:
    """Load the most recent analysis session for a project, or None."""
    try:
        from scrum_agent.sessions import SessionStore

        with SessionStore(_ana_dbp) as store:
            sessions = store.list_analysis_sessions()
            for sess in sessions:
                if project_key in sess.get("project_name", ""):
                    state = store.load_state(sess["session_id"])
                    if state and state.get("last_page"):
                        global _ana_sid  # noqa: PLW0603
                        _ana_sid = sess["session_id"]
                        logger.info(
                            "Resuming analysis session %s at page '%s'",
                            sess["session_id"],
                            state["last_page"],
                        )
                        return state
        logger.debug("No resumable analysis session for %s", project_key)
    except Exception:
        logger.debug("Analysis session load failed", exc_info=True)
    return None


def _save_ana(state: dict, node: str) -> None:
    """Save analysis session state (extracted to reduce nesting depth)."""
    if not _ana_sid:
        return
    try:
        from scrum_agent.sessions import SessionStore

        with SessionStore(_ana_dbp) as store:
            store.save_state(_ana_sid, state)
            store.update_last_node(_ana_sid, node)
        logger.info("Analysis session saved: page='%s', session=%s", node, _ana_sid)
    except Exception:
        logger.debug("Analysis session save failed", exc_info=True)


def _run_preview_flow(
    live,
    console,
    read_key,
    frame_time,
    supports_timeout,
    instr_text,
    ta_profile,
    ta_examples,
    *,
    resume_state: dict | None = None,
):
    """Run the analysis preview flow (Instructions → Epic → Stories → Tasks → Sprint).

    If resume_state is provided, jumps to the appropriate page.
    """
    from scrum_agent.tools.team_learning import (
        generate_sample_epic,
        generate_sample_stories,
        generate_sample_tasks,
    )
    from scrum_agent.ui.mode_select.screens._screens_secondary import (
        _build_analysis_progress_screen,
        _build_instructions_review_screen,
        _build_sample_epic_screen,
        _build_sample_stories_screen,
        _build_sample_tasks_screen,
    )

    _rk = lambda: read_key(timeout=frame_time) if supports_timeout else read_key()  # noqa: E731

    # ── Inline editor helpers for dict-based artifacts ────────────
    def _dict_editable_start(line: str) -> int | None:
        """Return column where editable value starts, or None if non-editable."""
        stripped = line.strip()
        if not stripped:
            return None
        if stripped.startswith("\u2500\u2500") and stripped.endswith("\u2500\u2500"):
            return None
        import re as _re

        m = _re.match(r"^[A-Za-z][A-Za-z /]+:\s*", line)
        if m:
            return m.end()
        return None  # non-label lines are not editable

    def _edit_dict_artifact(artifact: dict, fields: list[str], label: str) -> dict | None:
        """Open inline editor for a dict artifact. Returns edited dict or None on cancel."""
        from scrum_agent.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel
        from scrum_agent.ui.shared._components import analysis_title as _a_title

        # Serialize to text
        w = max(len(f) for f in fields) + 2
        buf_lines: list[str] = []
        for f in fields:
            display_label = f.replace("_", " ").title()
            val = artifact.get(f, "")
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            buf_lines.append(f"{display_label + ':':<{w}}{val}")
            buf_lines.append("")

        buffer = buf_lines
        cr, cc = 0, len(buffer[0].split(":")[0]) + 2 if buffer else 0
        # Find first editable position
        for i, ln in enumerate(buffer):
            col = _dict_editable_start(ln)
            if col is not None:
                cr, cc = i, col
                break
        _atitle = _a_title()

        def _render(buf, _cr, _cc, so, rw, rh):
            return render_editor_panel(
                buf,
                _cr,
                _cc,
                so,
                width=rw,
                height=rh,
                editor_label=label,
                title_override=_atitle,
            )

        result = edit_buffer_loop(
            live,
            console,
            buffer,
            cr,
            cc,
            _rk,
            editable_start_fn=_dict_editable_start,
            render_fn=_render,
        )
        if result is None:
            return None

        # Parse back: extract "Label: value" pairs
        import re as _re

        edited = dict(artifact)  # shallow copy
        for line in result:
            m = _re.match(r"^([A-Za-z][A-Za-z /]+):\s*(.*)", line)
            if m:
                key_display = m.group(1).strip()
                value = m.group(2).strip()
                # Map display label back to dict key
                key = key_display.lower().replace(" ", "_")
                if key in artifact:
                    orig = artifact[key]
                    if isinstance(orig, int):
                        try:
                            value = int(value)
                        except ValueError:
                            continue
                    elif isinstance(orig, list):
                        value = [v.strip() for v in value.split(",") if v.strip()]
                    edited[key] = value
        return edited

    def _edit_story_dict(story: dict) -> dict | None:
        """Edit a single story dict using the planning mode story editor."""
        from scrum_agent.agent.state import (
            AcceptanceCriterion,
            Discipline,
            Priority,
            StoryPointValue,
            UserStory,
        )
        from scrum_agent.ui.session.editor._editor import edit_story

        # Convert dict → UserStory
        acs = tuple(
            AcceptanceCriterion(given=ac.get("given", ""), when=ac.get("when", ""), then=ac.get("then", ""))
            for ac in story.get("acceptance_criteria", [])
            if isinstance(ac, dict)
        )
        pts_raw = story.get("story_points", 3)
        pts_val = pts_raw if pts_raw in (1, 2, 3, 5, 8) else 3
        pri_str = story.get("priority", "medium").lower()
        pri = Priority(pri_str) if pri_str in ("critical", "high", "medium", "low") else Priority.MEDIUM
        disc_str = story.get("discipline", "fullstack").lower()
        try:
            disc = Discipline(disc_str)
        except ValueError:
            disc = Discipline.FULLSTACK

        user_story = UserStory(
            id=story.get("id", "S1"),
            feature_id="F1",
            persona=story.get("persona", "user"),
            goal=story.get("goal", ""),
            benefit=story.get("benefit", ""),
            acceptance_criteria=acs,
            story_points=StoryPointValue(pts_val),
            priority=pri,
            title=story.get("title", ""),
            discipline=disc,
            points_rationale=story.get("rationale", ""),
        )

        w, h = console.size
        edited = edit_story(live, console, user_story, _rk, width=w, height=h)
        if edited is None:
            return None

        # Convert UserStory → dict (preserving extra keys from original)
        result = dict(story)
        result["title"] = edited.title
        result["persona"] = edited.persona
        result["goal"] = edited.goal
        result["benefit"] = edited.benefit
        result["story_points"] = int(edited.story_points)
        result["priority"] = edited.priority.value
        result["discipline"] = edited.discipline.value
        result["acceptance_criteria"] = [
            {"given": ac.given, "when": ac.when, "then": ac.then} for ac in edited.acceptance_criteria
        ]
        result["rationale"] = edited.points_rationale
        return result

    def _edit_task_dict(tasks_for_story: list[dict], story_id: str) -> list[dict] | None:
        """Edit tasks for a story using the planning mode task editor (with ANALYSIS title)."""
        from scrum_agent.agent.state import Task
        from scrum_agent.ui.session.editor._editor_artifacts import _find_first_editable, _task_editable_start
        from scrum_agent.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel
        from scrum_agent.ui.shared._components import analysis_title as _a_title

        task_objs = [
            Task(
                id=t.get("id", f"T-{story_id}-{i:02d}"),
                story_id=t.get("story_id", story_id),
                title=t.get("title", ""),
                description=t.get("description", ""),
                label=t.get("label", "Code"),
                test_plan=t.get("test_plan", ""),
            )
            for i, t in enumerate(tasks_for_story, 1)
        ]
        from scrum_agent.ui.session.editor._editor_artifacts import _parse_edited_tasks, _tasks_to_text

        text = _tasks_to_text(task_objs)
        buffer = text.split("\n")
        cr, cc = _find_first_editable(buffer, _task_editable_start)
        _atitle = _a_title()

        def _render(buf, _cr, _cc, so, rw, rh):
            return render_editor_panel(
                buf,
                _cr,
                _cc,
                so,
                width=rw,
                height=rh,
                editor_label=f"tasks for {story_id}",
                title_override=_atitle,
            )

        result = edit_buffer_loop(
            live,
            console,
            buffer,
            cr,
            cc,
            _rk,
            editable_start_fn=_task_editable_start,
            render_fn=_render,
        )
        if result is None:
            return None
        edited_objs = _parse_edited_tasks("\n".join(result), task_objs)
        return [
            {**orig, "title": et.title, "description": et.description} for orig, et in zip(tasks_for_story, edited_objs)
        ]

    def _edit_epic_dict(epic: dict) -> dict | None:
        """Edit an epic dict using the planning mode feature editor (with ANALYSIS title)."""
        from scrum_agent.agent.state import Feature, Priority
        from scrum_agent.ui.session.editor._editor_artifacts import (
            _feature_editable_start,
            _features_to_text,
            _find_first_editable,
            _parse_edited_features,
        )
        from scrum_agent.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel
        from scrum_agent.ui.shared._components import analysis_title as _a_title

        pri_str = epic.get("priority", "high").lower()
        pri = Priority(pri_str) if pri_str in ("critical", "high", "medium", "low") else Priority.HIGH
        feature = Feature(
            id="F1",
            title=epic.get("title", ""),
            description=epic.get("description", ""),
            priority=pri,
        )
        text = _features_to_text([feature])
        buffer = text.split("\n")
        cr, cc = _find_first_editable(buffer, _feature_editable_start)
        _atitle = _a_title()

        def _render(buf, _cr, _cc, so, rw, rh):
            return render_editor_panel(
                buf,
                _cr,
                _cc,
                so,
                width=rw,
                height=rh,
                editor_label="epic",
                title_override=_atitle,
            )

        result = edit_buffer_loop(
            live,
            console,
            buffer,
            cr,
            cc,
            _rk,
            editable_start_fn=_feature_editable_start,
            render_fn=_render,
        )
        if result is None:
            return None
        edited_list = _parse_edited_features("\n".join(result), [feature])
        edited = edited_list[0]
        result_dict = dict(epic)
        result_dict["title"] = edited.title
        result_dict["description"] = edited.description
        result_dict["priority"] = edited.priority.value
        return result_dict

    def _regenerate(fn, label: str):
        """Run an LLM generation function in a background thread with animation."""
        import threading

        logger.info("Regenerating %s via LLM", label)

        result_box: list = [None, None]

        def _worker():
            try:
                result_box[0] = fn()
            except Exception as exc:
                result_box[1] = exc

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        start = time.monotonic()
        while thread.is_alive():
            elapsed = time.monotonic() - start
            w, h = console.size
            live.update(
                _build_analysis_progress_screen(
                    [f"Regenerating {label}\u2026"],
                    width=w,
                    height=h,
                    elapsed=elapsed,
                    anim_tick=elapsed,
                    source="",
                    mode="analysis",
                )
            )
            time.sleep(1 / 30)
        thread.join()
        if result_box[1] is not None:
            logger.warning("Regeneration failed: %s", result_box[1])
            return None
        logger.info("Regeneration complete: %s", label)
        return result_box[0]

    _flow_start = time.monotonic()
    last_page = (resume_state or {}).get("last_page", "")
    logger.info(
        "Preview flow started: resume=%s, last_page='%s'",
        resume_state is not None,
        last_page,
    )

    def _do_export():
        """Cumulative export — includes analysis profile + all accepted samples."""
        logger.info("Preview: exporting analysis (HTML + MD)")
        from scrum_agent.team_profile_exporter import (
            export_team_profile_html,
            export_team_profile_md,
        )

        html_path = export_team_profile_html(ta_profile, examples=ta_examples)
        md_path = export_team_profile_md(ta_profile, examples=ta_examples)
        w, h = console.size
        from scrum_agent.ui.mode_select.screens._screens_secondary import (
            _build_project_export_success_screen,
        )

        paths = f"HTML  {html_path}\nMD    {md_path}"
        live.update(
            _build_project_export_success_screen(
                paths,
                width=w,
                height=h,
                subtitle="Exported (HTML + MD)",
                mode="analysis",
            )
        )
        import time as _t

        _t0 = _t.monotonic()
        while True:
            _ek = _rk()
            if _t.monotonic() - _t0 > 1.5 and _ek:
                break

    # Determine starting point and load saved artifacts
    last_page = (resume_state or {}).get("last_page", "")
    _instr = (resume_state or {}).get("instructions", "") or instr_text
    _epic = (resume_state or {}).get("sample_epic")
    _stories = (resume_state or {}).get("sample_stories")
    _tasks = (resume_state or {}).get("sample_tasks")

    # ── Page 1: Instructions ──────────────────────────────────────
    logger.info("Preview: entering Instructions page")
    if last_page not in ("epic", "stories", "tasks", "sprint"):
        scroll, sel = 0, 0
        while True:
            k = _rk()
            if k in ("up", "scroll_up"):
                scroll = max(0, scroll - 1)
            elif k in ("down", "scroll_down"):
                scroll += 1
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(2, sel + 1)
            elif k in ("enter", " "):
                if sel == 0:
                    _save_ana({"instructions": _instr, "last_page": "instructions"}, "instructions")
                    break  # → epic
                elif sel == 1:
                    # Edit — inline buffer editor (matches planning mode)
                    logger.info("Preview: user editing instructions")
                    from scrum_agent.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel
                    from scrum_agent.ui.shared._components import analysis_title as _a_title

                    _buf = _instr.split("\n")
                    _cr, _cc = 0, 0
                    _atitle = _a_title()

                    def _instr_render(buf, cr, cc, so, rw, rh):
                        return render_editor_panel(
                            buf,
                            cr,
                            cc,
                            so,
                            width=rw,
                            height=rh,
                            editor_label="instructions",
                            title_override=_atitle,
                        )

                    _edited = edit_buffer_loop(
                        live,
                        console,
                        _buf,
                        _cr,
                        _cc,
                        _rk,
                        editable_start_fn=lambda line: 0,
                        render_fn=_instr_render,
                    )
                    if _edited is not None:
                        _instr = "\n".join(_edited)
                elif sel == 2:
                    _do_export()
            elif k in ("esc", "q"):
                _save_ana({"instructions": _instr, "last_page": "instructions"}, "instructions")
                return
            w, h = console.size
            live.update(
                _build_instructions_review_screen(
                    _instr,
                    scroll_offset=scroll,
                    width=w,
                    height=h,
                    action_sel=sel,
                )
            )

    # ── Page 2: Epic ──────────────────────────────────────────────
    logger.info("Preview: entering Epic page")
    if not _epic:
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                ["Generating sample epic\u2026"],
                width=w,
                height=h,
                elapsed=0,
                anim_tick=0,
                source="",
                mode="analysis",
            )
        )
        logger.info("Preview: generating sample epic via LLM")
        result = _regenerate(lambda: generate_sample_epic(_instr, ta_examples), "epic")
        if result is not None:
            _epic = result
        logger.info("Preview: sample epic generated: %s", _epic.get("title", "?"))

    if last_page not in ("stories", "tasks", "sprint"):
        scroll, sel = 0, 0
        while True:
            k = _rk()
            if k in ("up", "scroll_up"):
                scroll = max(0, scroll - 1)
            elif k in ("down", "scroll_down"):
                scroll += 1
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(3, sel + 1)
            elif k in ("enter", " "):
                if sel == 0:
                    _save_ana({"instructions": _instr, "sample_epic": _epic, "last_page": "epic"}, "epic")
                    break  # → stories
                elif sel == 1:
                    logger.info("Preview: user editing epic")
                    edited = _edit_epic_dict(_epic)
                    if edited is not None:
                        _epic = edited
                elif sel == 2:
                    result = _regenerate(lambda: generate_sample_epic(_instr, ta_examples), "epic")
                    if result is not None:
                        _epic = result
                elif sel == 3:
                    _do_export()
            elif k in ("esc", "q"):
                _save_ana({"instructions": _instr, "sample_epic": _epic, "last_page": "epic"}, "epic")
                return
            w, h = console.size
            live.update(
                _build_sample_epic_screen(
                    _epic,
                    scroll_offset=scroll,
                    width=w,
                    height=h,
                    action_sel=sel,
                    examples=ta_examples,
                )
            )

    # ── Page 3: Stories ───────────────────────────────────────────
    logger.info("Preview: entering Stories page")
    if not _stories:
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                ["Generating sample stories\u2026"],
                width=w,
                height=h,
                elapsed=0,
                anim_tick=0,
                source="",
                mode="analysis",
            )
        )
        logger.info("Preview: generating sample stories via LLM")
        result = _regenerate(lambda: generate_sample_stories(_instr, _epic, ta_examples), "stories")
        if result is not None:
            _stories = result
        logger.info("Preview: %d sample stories generated", len(_stories))

    if last_page not in ("tasks", "sprint"):
        scroll, sel = 0, 0
        while True:
            k = _rk()
            if k in ("up", "scroll_up"):
                scroll = max(0, scroll - 1)
            elif k in ("down", "scroll_down"):
                scroll += 1
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(3, sel + 1)
            elif k in ("enter", " "):
                if sel == 0:
                    _st = {
                        "instructions": _instr,
                        "sample_epic": _epic,
                        "sample_stories": _stories,
                        "last_page": "stories",
                    }  # noqa: E501
                    _save_ana(_st, "stories")
                    break  # → tasks
                elif sel == 1:
                    logger.info("Preview: user editing stories")
                    for si, _s in enumerate(_stories):
                        edited = _edit_story_dict(_s)
                        if edited is not None:
                            _stories[si] = edited
                        else:
                            break  # Esc cancels remaining edits
                elif sel == 2:
                    result = _regenerate(lambda: generate_sample_stories(_instr, _epic, ta_examples), "stories")
                    if result is not None:
                        _stories = result
                elif sel == 3:
                    _do_export()
            elif k in ("esc", "q"):
                _save_ana(
                    {"instructions": _instr, "sample_epic": _epic, "sample_stories": _stories, "last_page": "stories"},
                    "stories",
                )
                return
            w, h = console.size
            live.update(
                _build_sample_stories_screen(
                    _stories,
                    scroll_offset=scroll,
                    width=w,
                    height=h,
                    action_sel=sel,
                    epic_title=_epic.get("title", ""),
                    examples=ta_examples,
                )
            )

    # ── Page 4: Tasks ─────────────────────────────────────────────
    logger.info("Preview: entering Tasks page")
    if not _tasks:
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                ["Generating sample tasks\u2026"],
                width=w,
                height=h,
                elapsed=0,
                anim_tick=0,
                source="",
                mode="analysis",
            )
        )
        logger.info("Preview: generating sample tasks via LLM")
        result = _regenerate(lambda: generate_sample_tasks(_instr, _stories, ta_examples), "tasks")
        if result is not None:
            _tasks = result
        logger.info("Preview: %d sample tasks generated", len(_tasks))

    if last_page != "sprint":
        scroll, sel = 0, 0
        while True:
            k = _rk()
            if k in ("up", "scroll_up"):
                scroll = max(0, scroll - 1)
            elif k in ("down", "scroll_down"):
                scroll += 1
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(3, sel + 1)
            elif k in ("enter", " "):
                if sel == 0:
                    _st = {
                        "instructions": _instr,
                        "sample_epic": _epic,
                        "sample_stories": _stories,
                        "sample_tasks": _tasks,
                        "last_page": "tasks",
                    }  # noqa: E501
                    _save_ana(_st, "tasks")
                    break  # → sprint
                elif sel == 1:
                    logger.info("Preview: user editing tasks")
                    # Group tasks by story and edit each group
                    _by_story: dict[str, list[tuple[int, dict]]] = {}
                    for ti, _t in enumerate(_tasks):
                        sid = _t.get("story_id", "?")
                        _by_story.setdefault(sid, []).append((ti, _t))
                    _cancelled = False
                    for sid, group in _by_story.items():
                        group_tasks = [t for _, t in group]
                        edited_group = _edit_task_dict(group_tasks, sid)
                        if edited_group is None:
                            _cancelled = True
                            break
                        for (ti, _), et in zip(group, edited_group):
                            _tasks[ti] = et
                elif sel == 2:
                    result = _regenerate(lambda: generate_sample_tasks(_instr, _stories, ta_examples), "tasks")
                    if result is not None:
                        _tasks = result
                elif sel == 3:
                    _do_export()
            elif k in ("esc", "q"):
                _save_ana(
                    {
                        "instructions": _instr,
                        "sample_epic": _epic,
                        "sample_stories": _stories,
                        "sample_tasks": _tasks,
                        "last_page": "tasks",
                    },
                    "tasks",
                )
                return
            w, h = console.size
            live.update(
                _build_sample_tasks_screen(
                    _tasks,
                    scroll_offset=scroll,
                    width=w,
                    height=h,
                    action_sel=sel,
                    stories=_stories,
                )
            )

    # ── Page 5: Sprint ────────────────────────────────────────────
    logger.info(
        "Preview: entering Sprint page (%.1fs elapsed)",
        time.monotonic() - _flow_start,
    )
    _run_sprint_review(
        live,
        console,
        read_key,
        frame_time,
        supports_timeout,
        _instr,
        _stories,
        _tasks,
        ta_examples,
    )
    # Clear session so next run starts fresh (not resuming from sprint)
    _save_ana({"last_page": ""}, "done")
    logger.info(
        "Preview flow completed in %.1fs",
        time.monotonic() - _flow_start,
    )


def _run_sprint_review(
    live,
    console,
    read_key,
    frame_time,
    supports_timeout,
    instr_text,
    sample_stories,
    sample_tasks,
    ta_examples,
):
    """Run the sample sprint review loop (extracted to reduce nesting depth)."""
    logger.info("Sprint review: generating sample sprint via LLM")
    import threading as _threading

    from scrum_agent.tools.team_learning import generate_sample_sprint
    from scrum_agent.ui.mode_select.screens._screens_secondary import (
        _build_analysis_progress_screen,
        _build_sample_sprint_screen,
    )

    def _regen_sprint():
        result_box: list = [None, None]

        def _worker():
            try:
                result_box[0] = generate_sample_sprint(instr_text, sample_stories, sample_tasks, ta_examples)
            except Exception as exc:
                result_box[1] = exc

        thread = _threading.Thread(target=_worker, daemon=True)
        thread.start()
        start = time.monotonic()
        while thread.is_alive():
            elapsed = time.monotonic() - start
            w, h = console.size
            live.update(
                _build_analysis_progress_screen(
                    ["Regenerating sprint\u2026"],
                    width=w,
                    height=h,
                    elapsed=elapsed,
                    anim_tick=elapsed,
                    source="",
                    mode="analysis",
                )
            )
            time.sleep(1 / 30)
        thread.join()
        if result_box[1] is not None:
            logger.warning("Sprint regeneration failed: %s", result_box[1])
            return None
        return result_box[0]

    sprint = _regen_sprint() or {
        "sprint_name": "Sprint 1",
        "velocity_target": 20,
        "stories_included": [s.get("id", "") for s in sample_stories],
        "total_points": sum(s.get("story_points", 0) for s in sample_stories),
        "capacity_notes": "Fallback — generation failed.",
        "risks": [],
        "rationale": "Fallback sprint plan.",
    }
    scroll = 0
    sel = 0
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        if k in ("up", "scroll_up"):
            scroll = max(0, scroll - 1)
        elif k in ("down", "scroll_down"):
            scroll += 1
        elif k == "left":
            sel = max(0, sel - 1)
        elif k == "right":
            sel = min(2, sel + 1)
        elif k in ("enter", " "):
            if sel == 0:
                break  # Done
            elif sel == 1:
                result = _regen_sprint()
                if result is not None:
                    sprint = result
            elif sel == 2:
                pass  # Export (handled at report level)
        elif k in ("esc", "q"):
            break
        w, h = console.size
        live.update(
            _build_sample_sprint_screen(
                sprint,
                sample_stories,
                scroll_offset=scroll,
                width=w,
                height=h,
                action_sel=sel,
            )
        )


def _collect_usage_data() -> dict:
    """Gather usage statistics for the Usage dashboard page."""
    import os
    import sys

    data: dict = {}

    # Provider info
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    model = os.environ.get("LLM_MODEL", "")
    if not model:
        _defaults = {
            "anthropic": "claude-sonnet-4-20250514",
            "openai": "gpt-4o",
            "google": "gemini-2.0-flash",
            "bedrock": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        }
        model = _defaults.get(provider, "unknown")
    data["provider"] = provider
    data["model"] = model

    # API key status
    _key_vars = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "bedrock": "AWS_REGION",
    }
    key_var = _key_vars.get(provider, "ANTHROPIC_API_KEY")
    data["api_key_status"] = "configured" if os.environ.get(key_var) else "not configured"

    # Session history
    try:
        from scrum_agent.sessions import SessionStore

        db_path = _ana_dbp
        with SessionStore(db_path) as store:
            all_sessions = store.list_sessions()
            analysis_sessions = store.list_analysis_sessions()
            planning_count = len(all_sessions) - len(analysis_sessions)
            last_used = all_sessions[0].get("last_modified", "") if all_sessions else ""
            data["sessions"] = {
                "total": len(all_sessions),
                "planning": planning_count,
                "analysis": len(analysis_sessions),
                "last_used": last_used[:19].replace("T", " ") if last_used else "",
            }
    except Exception:
        data["sessions"] = {"total": 0, "planning": 0, "analysis": 0}

    # Environment
    from scrum_agent import __version__

    data["version"] = __version__
    data["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    from scrum_agent.config import is_langsmith_enabled

    data["langsmith"] = "enabled" if is_langsmith_enabled() else "disabled"
    data["db_path"] = str(_ana_dbp)

    # Team profiles
    try:
        from scrum_agent.team_profile import TeamProfileStore

        with TeamProfileStore(_ana_dbp) as ps:
            profiles = ps.list_profiles()
            data["profiles"] = [
                {
                    "name": getattr(p, "team_id", "?"),
                    "source": getattr(p, "source", "?"),
                    "sprints": getattr(p, "sample_sprints", 0),
                }
                for p in profiles
            ]
    except Exception:
        data["profiles"] = []

    # Token usage — session (in-memory) + lifetime (from DB)
    def _calc_cost(inp: int, out: int) -> float:
        # Claude Sonnet 4: $3/MTok input, $15/MTok output
        return round((inp * 3.0 + out * 15.0) / 1_000_000, 4)

    try:
        from scrum_agent.agent.llm import get_usage_stats

        stats = get_usage_stats()
        logger.info("Usage stats: %s", stats)
        if stats.get("call_count", 0) > 0:
            inp = stats.get("input_tokens", 0)
            out = stats.get("output_tokens", 0)
            data["tokens"] = {
                "input": inp,
                "output": out,
                "total": inp + out,
                "calls": stats.get("call_count", 0),
                "estimated_cost": _calc_cost(inp, out),
            }
        else:
            data["tokens"] = {}
    except Exception:
        data["tokens"] = {}

    # Lifetime usage from DB (persisted across all sessions)
    try:
        from scrum_agent.sessions import SessionStore

        with SessionStore(_ana_dbp) as store:
            lifetime = store.get_lifetime_usage()
            if lifetime.get("call_count", 0) > 0:
                lt_inp = lifetime["input_tokens"]
                lt_out = lifetime["output_tokens"]
                data["lifetime_tokens"] = {
                    "input": lt_inp,
                    "output": lt_out,
                    "total": lt_inp + lt_out,
                    "calls": lifetime["call_count"],
                    "estimated_cost": _calc_cost(lt_inp, lt_out),
                }
            else:
                data["lifetime_tokens"] = {}
    except Exception:
        data["lifetime_tokens"] = {}

    return data


def select_mode(
    console: Console | None = None, *, dry_run: bool = False, _read_key_fn=None
) -> tuple[str, str | None, str | None] | None:
    """Show full-screen mode selection, then project list → intake mode for Planning.

    Returns (mode_key, intake_mode, questionnaire_path) tuple or None if cancelled.
    - Smart:  ("project-planning", "smart", None)
    - Full:   ("project-planning", "standard", None)
    - Import: ("project-planning", None, "/path/to/questionnaire.md")
    - Export/Cancel: None
    Only available modes can be selected.
    """
    console = console or Console()
    read_key = _read_key_fn or _read_key
    selected = 0
    n = len(_MODE_CARDS)

    w, h = console.size
    start_time = time.monotonic()
    select_time = start_time

    import inspect

    _supports_timeout = "timeout" in inspect.signature(read_key).parameters

    all_mode_indices = list(range(n))

    # If alt-screen is already active (from splash), use screen=False so
    # Live doesn't toggle it (which causes a visible flicker).  If not
    # active, let Live manage it normally with screen=True.
    _screen_managed_by_live = not console.is_alt_screen

    with Live(
        _build_mode_screen(
            selected,
            width=w,
            height=h,
            shimmer_tick=0.0,
            desc_reveal=0,
            fade_style=FADE_IN_LEVELS[0],
            fade_indices=all_mode_indices,
        ),
        console=console,
        refresh_per_second=60,
        screen=_screen_managed_by_live,
    ) as live:
        # Outer loop: returns here when user presses Esc from project list
        # to go back to mode selection (instead of recursive select_mode call).
        _restart_mode_select = True
        _skip_fade_in = False
        while _restart_mode_select:
            _restart_mode_select = False

            if _skip_fade_in:
                # Esc transition already rendered all items — no fade needed.
                # Description typewriter starts fresh from now.
                _skip_fade_in = False
            else:
                # Fade in all three mode items from near-black to full colour
                for grey in FADE_IN_LEVELS:
                    w, h = console.size
                    live.update(
                        _build_mode_screen(
                            selected,
                            width=w,
                            height=h,
                            shimmer_tick=0.0,
                            desc_reveal=0,
                            fade_style=grey,
                            fade_indices=all_mode_indices,
                        )
                    )
                    time.sleep(_FRAME_TIME)
                # Final frame with normal styling (no fade override)
                w, h = console.size
                live.update(_build_mode_screen(selected, width=w, height=h, shimmer_tick=0.0, desc_reveal=0))
            select_time = time.monotonic()

            # ── Phase 1: Mode selection ───────────────────────────────────────
            while True:
                key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                if key in ("up", "left", "scroll_up"):
                    selected = (selected - 1) % n
                    select_time = time.monotonic()
                elif key in ("down", "right", "scroll_down"):
                    selected = (selected + 1) % n
                    select_time = time.monotonic()
                elif key == "enter":
                    mode = _MODE_CARDS[selected]
                    if mode["available"]:
                        break
                    continue
                elif key in ("q", "esc"):
                    return None

                elapsed = time.monotonic() - select_time
                reveal = elapsed * _DESC_SCROLL_SPEED  # float for sub-char fade

                w, h = console.size
                tick = time.monotonic() - start_time
                live.update(
                    _build_mode_screen(
                        selected,
                        width=w,
                        height=h,
                        shimmer_tick=tick,
                        desc_reveal=reveal,
                    )
                )

            # ── Phase 2: Transition ───────────────────────────────────────────
            chosen = _MODE_CARDS[selected]
            all_indices = list(range(n))
            others = [i for i in all_indices if i != selected]
            base_r, base_g, base_b = COLOR_RGB.get(chosen["color"], (180, 180, 180))
            base_style = f"bold rgb({base_r},{base_g},{base_b})"

            # 2a: Pulse the selected mode
            for frame in range(12):
                t = frame / 11
                intensity = math.sin(t * math.pi)
                r = int(base_r + (255 - base_r) * intensity)
                g = int(base_g + (255 - base_g) * intensity)
                b = int(base_b + (255 - base_b) * intensity)
                pulse_style = f"bold rgb({r},{g},{b})"
                w, h = console.size
                live.update(
                    _build_mode_screen(
                        selected,
                        width=w,
                        height=h,
                        visible=all_indices,
                        fade_style=pulse_style,
                        fade_indices=[selected],
                    )
                )
                time.sleep(_FRAME_TIME)

            # 2b: Fade out unselected modes
            for grey in FADE_OUT_LEVELS:
                w, h = console.size
                live.update(
                    _build_mode_screen(
                        selected,
                        width=w,
                        height=h,
                        visible=all_indices,
                        fade_style=grey,
                        fade_indices=others,
                        selected_style=base_style,
                    )
                )
                time.sleep(_FRAME_TIME)

            # 2c: Slide Planning title + description from center to top.
            # Description fades out as the title slides up.
            w, h = console.size
            inner_h = h - 4
            block_h = 2  # title(2) only — description disappears on selection
            start_offset = max(0, (inner_h - block_h) // 2)
            end_offset = 1  # one blank line above title to match project list layout

            slide_frames = 15
            for frame in range(slide_frames + 1):
                t = frame / slide_frames
                eased = ease_out_cubic(t)
                current_offset = int(start_offset + (end_offset - start_offset) * eased)
                w, h = console.size
                live.update(
                    _build_slide_frame(
                        chosen,
                        top_offset=current_offset,
                        width=w,
                        height=h,
                        style=base_style,
                    )
                )
                time.sleep(_FRAME_TIME)

            # ── Route: Team Analysis mode → dedicated analysis flow ──────
            if chosen["key"] == "team-analysis":
                logger.info("Analysis mode selected")
                from scrum_agent.azdevops_sync import is_azdevops_board_configured as _azdevops_check
                from scrum_agent.jira_sync import is_jira_configured as _jira_check

                _jira_ok = _jira_check()
                _azdevops_ok = _azdevops_check()
                _board_configured = _jira_ok or _azdevops_ok

                if not _board_configured:
                    # No board configured — show message and return to mode select
                    w, h = console.size
                    live.update(
                        _build_project_export_success_screen(
                            "No board configured.\n\n"
                            "Set JIRA_BASE_URL + JIRA_API_TOKEN\n"
                            "or AZURE_DEVOPS_ORG_URL + AZURE_DEVOPS_TOKEN\n"
                            "in your .env file.",
                            width=w,
                            height=h,
                            subtitle="Board required",
                            hint="Press any key to go back.",
                        )
                    )
                    while True:
                        k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                        if k:
                            break
                    _restart_mode_select = True
                    _skip_fade_in = True
                    continue

                # Load existing team profiles
                _profiles_for_analysis: list = []
                try:
                    from datetime import UTC, datetime

                    from scrum_agent.team_profile import TeamProfileStore

                    _tp_db = Path.home() / ".scrum-agent" / "sessions.db"
                    if _tp_db.exists():
                        with TeamProfileStore(_tp_db) as _tp_store:
                            _raw_profiles = _tp_store.list_profiles()
                        for _rp in _raw_profiles:
                            days = 0
                            if _rp.updated_at:
                                try:
                                    _up = datetime.fromisoformat(_rp.updated_at)
                                    days = (datetime.now(UTC) - _up).days
                                except Exception:
                                    pass
                            _profiles_for_analysis.append(
                                ProfileSummary(
                                    team_id=_rp.team_id,
                                    source=_rp.source,
                                    project_key=_rp.project_key,
                                    sample_sprints=_rp.sample_sprints,
                                    velocity_avg=_rp.velocity_avg,
                                    sample_stories=_rp.sample_stories,
                                    updated="today" if days == 0 else (f"{days} day{'s' if days != 1 else ''} ago"),
                                    staleness_days=days,
                                )
                            )
                except Exception:
                    pass

                # Load resumable analysis sessions
                _ana_sessions: list[dict] = []
                try:
                    from scrum_agent.sessions import SessionStore as _SessStore

                    _sess_db = Path.home() / ".scrum-agent" / "sessions.db"
                    if _sess_db.exists():
                        with _SessStore(_sess_db) as _ss:
                            _ana_sessions = _ss.list_analysis_sessions()
                except Exception:
                    pass

                logger.info(
                    "Analysis mode: %d profiles, %d sessions, jira=%s, azdevops=%s",
                    len(_profiles_for_analysis),
                    len(_ana_sessions),
                    _jira_ok,
                    _azdevops_ok,
                )

                # Always one button; board picker popup shown if both configured
                _ana_labels = ["+ New Analysis"]

                # Show profile list or go straight to analysis
                _ana_items = _profiles_for_analysis + _ana_labels  # type: ignore[operator]
                _ana_selected = 0
                _ana_n = len(_profiles_for_analysis) + len(_ana_labels)

                # Stagger reveal
                _reveal_target = float(_ana_n)
                _cards_visible = 0.0
                _reveal_speed = 15.0
                _reveal_start = time.monotonic()
                while _cards_visible < _reveal_target:
                    dt_r = time.monotonic() - _reveal_start
                    _cards_visible = min(_reveal_target, dt_r * _reveal_speed)
                    w, h = console.size
                    live.update(
                        _build_project_list_screen(
                            [],
                            _ana_selected,
                            width=w,
                            height=h,
                            cards_visible=_cards_visible,
                            card_fade=1.0,
                            jira_enabled=_jira_ok,
                            azdevops_enabled=_azdevops_ok,
                            profiles=_profiles_for_analysis,
                            new_analysis_labels=_ana_labels,
                            mode="analysis",
                        )
                    )
                    time.sleep(_FRAME_TIME)

                # Analysis mode interaction loop
                _team_popup_result = ""
                _ana_focus = 0
                _ana_card_fade = 1.0
                _ana_restart = True
                while _ana_restart:
                    _ana_restart = False
                    _ana_focus = 0
                    _ana_action_btns = 0.0
                    _has_prof = _profiles_for_analysis and _ana_selected < len(_profiles_for_analysis)
                    _ana_action_btns_target = 2.0 if _has_prof else 0.0
                    _ana_del_fade = 0.0
                    _ana_exp_fade = 0.0
                    _ana_export_submenu = False
                    _ana_sub_sel = 0
                    _ana_sub_html_fade = 0.0
                    _ana_sub_md_fade = 0.0
                    _ana_sub_visible = 0.0
                    _ana_sub_visible_target = 0.0
                    _ana_del_popup_open = False
                    _ana_del_popup_t = 0.0
                    _ana_del_popup_target = 0.0
                    _ana_del_popup_name = ""
                    _ana_del_popup_pulse = 0.0
                    _ana_del_popup_flash = 0.0
                    _ana_del_pending = False
                    _ana_prev = time.monotonic()

                    while True:
                        key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                        _is_profile = _ana_selected < len(_profiles_for_analysis)
                        _is_analysis_btn = _ana_selected >= len(_profiles_for_analysis)

                        # ── Export submenu mode ───────────────────────────
                        if _ana_export_submenu and key:
                            if key == "left":
                                _ana_sub_sel = max(0, _ana_sub_sel - 1)
                                _ana_sub_html_fade = 1.0 if _ana_sub_sel == 0 else 0.0
                                _ana_sub_md_fade = 1.0 if _ana_sub_sel == 1 else 0.0
                            elif key == "right":
                                _ana_sub_sel = min(1, _ana_sub_sel + 1)
                                _ana_sub_html_fade = 1.0 if _ana_sub_sel == 0 else 0.0
                                _ana_sub_md_fade = 1.0 if _ana_sub_sel == 1 else 0.0
                            elif key == "enter":
                                _sel_p = _profiles_for_analysis[_ana_selected]
                                _tp_db = Path.home() / ".scrum-agent" / "sessions.db"
                                _full_p = None
                                _st_ex: dict | None = None
                                if _tp_db.exists():
                                    from scrum_agent.team_profile import TeamProfileStore

                                    with TeamProfileStore(_tp_db) as _s:
                                        _full_p, _st_ex = _s.load_with_examples(_sel_p.team_id)
                                if _full_p:
                                    if _ana_sub_sel == 0:
                                        from scrum_agent.team_profile_exporter import export_team_profile_html

                                        _ep = export_team_profile_html(_full_p, examples=_st_ex)
                                    else:
                                        from scrum_agent.team_profile_exporter import export_team_profile_md

                                        _ep = export_team_profile_md(_full_p, examples=_st_ex)
                                    w, h = console.size
                                    live.update(
                                        _build_project_export_success_screen(
                                            str(_ep),
                                            width=w,
                                            height=h,
                                            subtitle="Team profile exported",
                                        )
                                    )
                                    _et = time.monotonic()
                                    while True:
                                        ek = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                        if time.monotonic() - _et > 1.5 and ek:
                                            break
                                _ana_export_submenu = False
                                _ana_sub_visible_target = 0.0
                                _ana_sub_html_fade = 0.0
                                _ana_sub_md_fade = 0.0
                                _ana_exp_fade = 1.0
                            elif key in ("esc", "q"):
                                _ana_export_submenu = False
                                _ana_sub_visible_target = 0.0
                                _ana_sub_html_fade = 0.0
                                _ana_sub_md_fade = 0.0
                                _ana_exp_fade = 1.0
                            continue

                        # ── Delete confirmation popup ─────────────────
                        if _ana_del_popup_open and key:
                            if key == "enter":
                                _ana_del_popup_flash = 1.0
                                _ana_del_pending = True
                            elif key in ("esc", "q"):
                                _ana_del_popup_target = 0.0
                            continue

                        # Perform delete after popup slides out
                        if _ana_del_popup_open and _ana_del_popup_target == 0.0 and _ana_del_popup_t <= 0:
                            if _ana_del_pending:
                                try:
                                    from scrum_agent.team_profile import TeamProfileStore

                                    _tp_db = Path.home() / ".scrum-agent" / "sessions.db"
                                    if _tp_db.exists():
                                        _del_p = _profiles_for_analysis[_ana_selected]
                                        with TeamProfileStore(_tp_db) as _s:
                                            _s.delete(_del_p.team_id)
                                    _profiles_for_analysis.pop(_ana_selected)
                                    _ana_n = len(_profiles_for_analysis) + len(_ana_labels)
                                    _ana_selected = min(_ana_selected, _ana_n - 1)
                                    _ana_focus = 0
                                    _ana_action_btns = 0.0
                                    _ana_del_fade = 0.0
                                    _ana_exp_fade = 0.0
                                    _has_prof = _profiles_for_analysis and _ana_selected < len(_profiles_for_analysis)
                                    _ana_action_btns_target = 2.0 if _has_prof else 0.0
                                except Exception:
                                    pass
                            _ana_del_popup_open = False
                            _ana_del_popup_name = ""
                            _ana_del_pending = False

                        if key in ("up", "scroll_up"):
                            _ana_selected = (_ana_selected - 1) % _ana_n
                            _ana_focus = 0
                            _ana_action_btns = 0.0
                            _is_profile = _ana_selected < len(_profiles_for_analysis)
                            _ana_action_btns_target = 2.0 if _is_profile else 0.0
                            _ana_del_fade = 0.0
                            _ana_exp_fade = 0.0
                            _ana_export_submenu = False
                            _ana_sub_visible_target = 0.0
                        elif key in ("down", "scroll_down"):
                            _ana_selected = (_ana_selected + 1) % _ana_n
                            _ana_focus = 0
                            _ana_action_btns = 0.0
                            _is_profile = _ana_selected < len(_profiles_for_analysis)
                            _ana_action_btns_target = 2.0 if _is_profile else 0.0
                            _ana_del_fade = 0.0
                            _ana_exp_fade = 0.0
                            _ana_export_submenu = False
                            _ana_sub_visible_target = 0.0
                        elif key == "left":
                            if _ana_focus > 0:
                                _ana_focus -= 1
                            _ana_del_fade = 0.0 if _ana_focus != 1 else 1.0
                            _ana_exp_fade = 0.0 if _ana_focus != 2 else 1.0
                        elif key == "right":
                            if _is_profile and _ana_focus < 2:
                                _ana_focus += 1
                            _ana_del_fade = 0.0 if _ana_focus != 1 else 1.0
                            _ana_exp_fade = 0.0 if _ana_focus != 2 else 1.0
                        elif key == "enter":
                            if _is_profile and _ana_focus == 0:
                                # View profile results
                                _sel_p = _profiles_for_analysis[_ana_selected]
                                from scrum_agent.team_profile import TeamProfileStore

                                _tp_db = Path.home() / ".scrum-agent" / "sessions.db"
                                _full = None
                                _stored_ex: dict | None = None
                                if _tp_db.exists():
                                    with TeamProfileStore(_tp_db) as _s:
                                        _full, _stored_ex = _s.load_with_examples(
                                            _sel_p.team_id,
                                        )
                                if _full:
                                    from scrum_agent.ui.mode_select.screens._screens_secondary import (
                                        _build_team_analysis_screen,
                                    )

                                    _scr = 0
                                    _esel = 2
                                    while True:
                                        kk = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                        if kk in ("up", "scroll_up"):
                                            _scr = max(0, _scr - 1)
                                        elif kk in ("down", "scroll_down"):
                                            _scr += 1
                                        elif kk == "left":
                                            _esel = max(0, _esel - 1)
                                        elif kk == "right":
                                            _esel = min(1, _esel + 1)
                                        elif kk == "enter" or kk == " ":
                                            if _esel == 1:
                                                # Continue → run preview flow
                                                from scrum_agent.agent.nodes import _format_team_calibration

                                                _si_text = _format_team_calibration(
                                                    _full,
                                                    examples=_stored_ex,
                                                )
                                                if _si_text.strip():
                                                    _run_preview_flow(
                                                        live,
                                                        console,
                                                        read_key,
                                                        _FRAME_TIME,
                                                        _supports_timeout,
                                                        _si_text,
                                                        _full,
                                                        _stored_ex,
                                                    )
                                                break
                                            if _esel == 0:
                                                # Export both HTML and MD
                                                from scrum_agent.team_profile_exporter import (
                                                    export_team_profile_html,
                                                    export_team_profile_md,
                                                )

                                                export_team_profile_html(_full, examples=_stored_ex)
                                                _ep = export_team_profile_md(_full, examples=_stored_ex)
                                            w, h = console.size
                                            live.update(
                                                _build_project_export_success_screen(
                                                    str(_ep),
                                                    width=w,
                                                    height=h,
                                                    subtitle="Team profile exported",
                                                )
                                            )
                                            _et = time.monotonic()
                                            while True:
                                                ek = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                                if time.monotonic() - _et > 1.5 and ek:
                                                    break
                                        elif kk in ("esc", "q"):
                                            break
                                        w, h = console.size
                                        live.update(
                                            _build_team_analysis_screen(
                                                _full,
                                                scroll_offset=_scr,
                                                width=w,
                                                height=h,
                                                export_sel=_esel,
                                                examples=_stored_ex,
                                            )
                                        )
                                continue
                            elif _is_profile and _ana_focus == 1:
                                # Delete profile — open confirmation popup
                                _sel_p = _profiles_for_analysis[_ana_selected]
                                _ana_del_popup_open = True
                                _ana_del_popup_target = 1.0
                                _ana_del_popup_name = f"{_sel_p.source}/{_sel_p.project_key}"
                                _ana_del_popup_pulse = 0.0
                                _ana_del_popup_flash = 0.0
                                _ana_del_pending = False
                                continue
                            elif _is_profile and _ana_focus == 2:
                                # Export → open submenu
                                _ana_export_submenu = True
                                _ana_sub_sel = 0
                                _ana_sub_visible_target = 2.0
                                _ana_sub_html_fade = 1.0
                                _ana_sub_md_fade = 0.0
                                _ana_exp_fade = 0.0
                                continue
                            elif _is_analysis_btn:
                                # New analysis — if both boards, show picker popup
                                if _jira_ok and _azdevops_ok:
                                    from rich.console import Group
                                    from rich.text import Text

                                    _ana_popup_sel = 0  # 0=Jira, 1=AzDO
                                    _ana_popup_open = True
                                    _ana_popup_tick = 0.0
                                    while _ana_popup_open:
                                        _ana_popup_tick += _FRAME_TIME
                                        w, h = console.size
                                        import rich.box as _rbox
                                        from rich.padding import Padding  # noqa: F811
                                        from rich.panel import Panel as _PickPanel

                                        from scrum_agent.ui.shared._components import analysis_title as _at

                                        _ana_title = _at()

                                        # Styled board picker with green accent
                                        _accent = "#22c55e"
                                        _pick_inner_w = min(w - 10, 50)
                                        _pick_msg = "Which board to analyse?"
                                        _pick_pad = max(0, (_pick_inner_w - len(_pick_msg)) // 2)

                                        _pick_body: list = [Text("")]
                                        _pick_body.append(
                                            Text(
                                                " " * _pick_pad + _pick_msg,
                                                style="bold white",
                                                justify="left",
                                            )
                                        )
                                        _pick_body.append(Text(""))

                                        # Buttons with green highlight
                                        _btn_line = Text(justify="center")
                                        for bi, bl in enumerate(["Jira", "Azure DevOps"]):
                                            if bi > 0:
                                                _btn_line.append("     ")
                                            if bi == _ana_popup_sel:
                                                _btn_line.append(
                                                    f" [ {bl} ] ",
                                                    style=f"bold {_accent}",
                                                )
                                            else:
                                                _btn_line.append(
                                                    f"   {bl}   ",
                                                    style="dim",
                                                )
                                        _pick_body.append(_btn_line)
                                        _pick_body.append(Text(""))

                                        _hint = Text(
                                            "← → select  ·  Enter confirm  ·  Esc cancel",
                                            style="rgb(60,60,80)",
                                            justify="center",
                                        )
                                        _pick_body.append(_hint)

                                        # Center the popup vertically
                                        _popup_h = 7
                                        _top_pad = max(0, (h - 8 - _popup_h) // 2)
                                        _bot_pad = max(0, h - 8 - _popup_h - _top_pad)

                                        live.update(
                                            _PickPanel(
                                                Group(
                                                    _ana_title,
                                                    *[Text("") for _ in range(_top_pad)],
                                                    Padding(
                                                        _PickPanel(
                                                            Group(*_pick_body),
                                                            border_style=_accent,
                                                            box=_rbox.ROUNDED,
                                                            width=_pick_inner_w + 4,
                                                            padding=(0, 2),
                                                        ),
                                                        (0, 0, 0, max(0, (w - _pick_inner_w - 8) // 2)),
                                                    ),
                                                    *[Text("") for _ in range(_bot_pad)],
                                                ),
                                                border_style="white",
                                                box=_rbox.ROUNDED,
                                                expand=True,
                                                height=h,
                                                padding=(1, 2),
                                            )
                                        )
                                        pk = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                        if pk == "left":
                                            _ana_popup_sel = 0
                                        elif pk == "right":
                                            _ana_popup_sel = 1
                                        elif pk == "enter":
                                            _team_popup_result = (
                                                "analyse_jira" if _ana_popup_sel == 0 else "analyse_azdevops"
                                            )
                                            _ana_popup_open = False
                                        elif pk in ("esc", "q"):
                                            _ana_popup_open = False
                                    if _team_popup_result.startswith("analyse"):
                                        break
                                    continue  # user pressed Esc on picker
                                elif _jira_ok:
                                    _team_popup_result = "analyse"
                                else:
                                    _team_popup_result = "analyse_azdevops"
                                break
                        elif key in ("esc", "q"):
                            _restart_mode_select = True
                            _skip_fade_in = True
                            break

                        # Animate
                        _now = time.monotonic()
                        _dt = _now - _ana_prev
                        _ana_prev = _now
                        _astep = _dt * 12.0
                        if _ana_action_btns < _ana_action_btns_target:
                            _ana_action_btns = min(_ana_action_btns + _astep, _ana_action_btns_target)
                        elif _ana_action_btns > _ana_action_btns_target:
                            _ana_action_btns = max(_ana_action_btns - _astep, _ana_action_btns_target)
                        if _ana_sub_visible < _ana_sub_visible_target:
                            _ana_sub_visible = min(_ana_sub_visible + _astep, _ana_sub_visible_target)
                        elif _ana_sub_visible > _ana_sub_visible_target:
                            _ana_sub_visible = max(_ana_sub_visible - _astep, _ana_sub_visible_target)
                        # Delete popup animation
                        if _ana_del_popup_t < _ana_del_popup_target:
                            _ana_del_popup_t = min(_ana_del_popup_t + _astep * 0.5, _ana_del_popup_target)
                        elif _ana_del_popup_t > _ana_del_popup_target:
                            _ana_del_popup_t = max(_ana_del_popup_t - _astep * 0.5, _ana_del_popup_target)
                        if _ana_del_popup_open:
                            _ana_del_popup_pulse += _dt * 4.0
                        if _ana_del_popup_flash > 0:
                            _ana_del_popup_flash = max(0.0, _ana_del_popup_flash - _dt * 3.0)
                            if _ana_del_popup_flash <= 0.1 and _ana_del_pending:
                                _ana_del_popup_target = 0.0

                        w, h = console.size
                        live.update(
                            _build_project_list_screen(
                                [],
                                _ana_selected,
                                width=w,
                                height=h,
                                jira_enabled=_jira_ok,
                                azdevops_enabled=_azdevops_ok,
                                profiles=_profiles_for_analysis,
                                new_analysis_labels=_ana_labels,
                                profile_focus=_ana_focus,
                                profile_del_fade=_ana_del_fade,
                                profile_card_fade=1.0,
                                profile_action_btns_visible=_ana_action_btns,
                                profile_exp_fade=_ana_exp_fade,
                                profile_export_submenu=_ana_export_submenu,
                                profile_submenu_sel=_ana_sub_sel,
                                profile_submenu_html_fade=_ana_sub_html_fade,
                                profile_submenu_md_fade=_ana_sub_md_fade,
                                profile_submenu_visible=_ana_sub_visible,
                                delete_popup_name=_ana_del_popup_name,
                                delete_popup_t=_ana_del_popup_t,
                                delete_popup_pulse=_ana_del_popup_pulse,
                                delete_popup_flash=_ana_del_popup_flash,
                                mode="analysis",
                            )
                        )

                    if _restart_mode_select:
                        break  # break out of _ana_restart loop → back to mode select

                    # Run team analysis (reuse Phase 3a logic)
                    if _team_popup_result.startswith("analyse"):
                        import threading

                        from scrum_agent.team_profile import TeamProfileStore
                        from scrum_agent.tools.team_learning import (
                            _fetch_azdevops_history,
                            _fetch_jira_history,
                            _run_parallel_analysis,
                        )

                        if _team_popup_result == "analyse_jira":
                            _ta_source = "jira"
                        elif _team_popup_result == "analyse_azdevops":
                            _ta_source = "azdevops"
                        else:
                            _ta_source = "jira" if _jira_ok else "azdevops"

                        _ta_project_key = ""
                        _ta_team_name = ""
                        try:
                            if _ta_source == "jira":
                                from scrum_agent.config import get_jira_project_key

                                _ta_project_key = get_jira_project_key() or ""
                            else:
                                from scrum_agent.config import (
                                    get_azure_devops_project,
                                    get_azure_devops_team,
                                )

                                _ta_project_key = get_azure_devops_project() or ""
                                _ta_team_name = get_azure_devops_team() or ""
                        except Exception:
                            pass

                        _ta_progress: list[str] = ["Fetching sprint history\u2026"]
                        _ta_profile_box: list = [None]
                        _ta_examples_box: list = [None]
                        _ta_sprint_names_box: list = [[]]
                        _ta_error_box: list[str] = [""]
                        _ta_done = threading.Event()

                        def _run_team_analysis_mode():
                            try:
                                if _ta_source == "jira":
                                    sprint_data = _fetch_jira_history(_ta_project_key, 8)
                                else:
                                    sprint_data = _fetch_azdevops_history(_ta_project_key, 8)
                                if not sprint_data:
                                    _ta_error_box[0] = "No closed sprints found."
                                else:
                                    _ta_sprint_names_box[0] = [sd.get("sprint_name", "") for sd in sprint_data]
                                    _result = _run_parallel_analysis(
                                        _ta_source,
                                        _ta_project_key or "unknown",
                                        sprint_data,
                                        _ta_progress,
                                    )
                                    _ta_profile_box[0] = _result[0]
                                    _ta_examples_box[0] = _result[1]
                            except Exception as exc:
                                _ta_error_box[0] = str(exc)
                            finally:
                                _ta_done.set()

                        _ta_thread_start = time.monotonic()
                        _ta_thread = threading.Thread(
                            target=_run_team_analysis_mode,
                            daemon=True,
                        )
                        logger.info(
                            "Analysis: starting %s analysis for %s",
                            _ta_source,
                            _ta_project_key,
                        )
                        _ta_thread.start()

                        from scrum_agent.ui.mode_select.screens._screens_secondary import (
                            _build_analysis_progress_screen,
                        )

                        _ta_anim_tick = 0.0
                        while not _ta_done.is_set():
                            _ta_anim_tick += _FRAME_TIME
                            w, h = console.size
                            live.update(
                                _build_analysis_progress_screen(
                                    _ta_progress,
                                    width=w,
                                    height=h,
                                    elapsed=time.monotonic() - _ta_thread_start,
                                    anim_tick=_ta_anim_tick,
                                    source=_ta_source,
                                    mode="analysis",
                                )
                            )
                            time.sleep(_FRAME_TIME)
                        _ta_thread.join()

                        _ta_profile = _ta_profile_box[0]
                        _ta_duration = time.monotonic() - _ta_thread_start
                        if _ta_profile:
                            logger.info(
                                "Analysis completed in %.1fs: %d sprints, %d stories, vel=%.1f",
                                _ta_duration,
                                _ta_profile.sample_sprints,
                                _ta_profile.sample_stories,
                                _ta_profile.velocity_avg,
                            )
                        elif _ta_error_box[0]:
                            logger.error("Analysis failed: %s", _ta_error_box[0])
                        if _ta_profile:
                            db_dir = Path.home() / ".scrum-agent"
                            db_dir.mkdir(parents=True, exist_ok=True)
                            with TeamProfileStore(db_dir / "sessions.db") as store:
                                store.save(_ta_profile, examples=_ta_examples_box[0])
                            try:
                                from scrum_agent.team_profile_exporter import write_analysis_log

                                write_analysis_log(
                                    _ta_profile,
                                    examples=_ta_examples_box[0],
                                    sprint_names=_ta_sprint_names_box[0],
                                    duration_secs=_ta_duration,
                                )
                            except Exception:
                                pass

                            # Show results
                            from scrum_agent.ui.mode_select.screens._screens_secondary import (
                                _build_team_analysis_screen,
                            )

                            _ta_scroll = 0
                            _ta_export_sel = 2
                            _ta_examples = _ta_examples_box[0] or {}
                            _ta_sprint_names = _ta_sprint_names_box[0]
                            while True:
                                kk = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                if kk in ("up", "scroll_up"):
                                    _ta_scroll = max(0, _ta_scroll - 1)
                                elif kk in ("down", "scroll_down"):
                                    _ta_scroll += 1
                                elif kk == "left":
                                    _ta_export_sel = max(0, _ta_export_sel - 1)
                                elif kk == "right":
                                    _ta_export_sel = min(1, _ta_export_sel + 1)
                                elif kk == "enter" or kk == " ":
                                    if _ta_export_sel == 1:
                                        # Continue → run preview flow
                                        global _ana_sid  # noqa: PLW0603

                                        from scrum_agent.agent.nodes import _format_team_calibration
                                        from scrum_agent.sessions import SessionStore as _AStore
                                        from scrum_agent.sessions import make_session_id

                                        _ana_sid = make_session_id()
                                        try:
                                            with _AStore(_ana_dbp) as _as:
                                                _as.create_session(
                                                    _ana_sid,
                                                    _ta_profile.project_key if _ta_profile else "",
                                                    mode="analysis",
                                                )
                                        except Exception:
                                            pass

                                        _instr_text = _format_team_calibration(
                                            _ta_profile,
                                            examples=_ta_examples,
                                        )
                                        if _instr_text.strip():
                                            _run_preview_flow(
                                                live,
                                                console,
                                                read_key,
                                                _FRAME_TIME,
                                                _supports_timeout,
                                                _instr_text,
                                                _ta_profile,
                                                _ta_examples,
                                            )
                                        break
                                    if _ta_export_sel == 0:
                                        # Export both HTML and MD
                                        from scrum_agent.team_profile_exporter import (
                                            export_team_profile_html,
                                            export_team_profile_md,
                                        )

                                        export_team_profile_html(
                                            _ta_profile,
                                            examples=_ta_examples,
                                            sprint_names=_ta_sprint_names,
                                        )
                                        _ep = export_team_profile_md(
                                            _ta_profile,
                                            examples=_ta_examples,
                                            sprint_names=_ta_sprint_names,
                                        )
                                    w, h = console.size
                                    live.update(
                                        _build_project_export_success_screen(
                                            str(_ep),
                                            width=w,
                                            height=h,
                                            subtitle="Team profile exported",
                                        )
                                    )
                                    _et = time.monotonic()
                                    while True:
                                        ek = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                        if time.monotonic() - _et > 1.5 and ek:
                                            break
                                elif kk in ("esc", "q"):
                                    break
                                w, h = console.size
                                live.update(
                                    _build_team_analysis_screen(
                                        _ta_profile,
                                        scroll_offset=_ta_scroll,
                                        width=w,
                                        height=h,
                                        export_sel=_ta_export_sel,
                                        examples=_ta_examples,
                                        sprint_names=_ta_sprint_names,
                                        team_name=_ta_team_name,
                                    )
                                )
                        elif _ta_error_box[0]:
                            w, h = console.size
                            live.update(
                                _build_project_export_success_screen(
                                    _ta_error_box[0],
                                    width=w,
                                    height=h,
                                    subtitle="Analysis failed",
                                    hint="Press any key to continue.",
                                )
                            )
                            while True:
                                k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                if k:
                                    break

                        # Reload profiles and restart analysis list
                        try:
                            from datetime import UTC, datetime

                            from scrum_agent.team_profile import TeamProfileStore

                            _tp_db = Path.home() / ".scrum-agent" / "sessions.db"
                            if _tp_db.exists():
                                with TeamProfileStore(_tp_db) as _tp_s:
                                    _raw2 = _tp_s.list_profiles()
                                _profiles_for_analysis = []
                                for _rp in _raw2:
                                    days = 0
                                    if _rp.updated_at:
                                        try:
                                            _up = datetime.fromisoformat(_rp.updated_at)
                                            days = (datetime.now(UTC) - _up).days
                                        except Exception:
                                            pass
                                    _profiles_for_analysis.append(
                                        ProfileSummary(
                                            team_id=_rp.team_id,
                                            source=_rp.source,
                                            project_key=_rp.project_key,
                                            sample_sprints=_rp.sample_sprints,
                                            velocity_avg=_rp.velocity_avg,
                                            sample_stories=_rp.sample_stories,
                                            updated="today"
                                            if days == 0
                                            else (f"{days} day{'s' if days != 1 else ''} ago"),
                                            staleness_days=days,
                                        )
                                    )
                        except Exception:
                            pass
                        _ana_n = len(_profiles_for_analysis) + len(_ana_labels)
                        _ana_selected = 0
                        _ana_restart = True
                        _team_popup_result = ""
                        continue

                    # Esc from analysis list → back to mode select
                    _restart_mode_select = True
                    _skip_fade_in = True

                # Always return to mode select after analysis mode exits
                continue

            # 2d: Smooth fade-in — all cards appear together, opacity 0→1
            # See README: "Memory & State" — load persisted project history
            from scrum_agent.persistence import load_projects as _load_projects

            projects = _load_projects()
            proj_selected = 0
            if projects:
                proj_n = len(projects) + 1
            else:
                proj_n = 2

            # Check which trackers are configured — used to show/dim submenu buttons.
            from scrum_agent.azdevops_sync import is_azdevops_board_configured as _azdevops_check
            from scrum_agent.jira_sync import is_jira_configured as _jira_check

            _jira_ok = _jira_check()
            _azdevops_ok = _azdevops_check()
            # Submenu has HTML(0), Markdown(1), then tracker buttons dynamically
            _submenu_max = 1 + (1 if _jira_ok else 0) + (1 if _azdevops_ok else 0)

            # Check team profile staleness for the popup on "+ New Project"
            _board_configured = _jira_ok or _azdevops_ok
            _staleness_days: int | None = None
            if _board_configured:
                try:
                    from scrum_agent.team_profile import TeamProfileStore

                    _tp_db = Path.home() / ".scrum-agent" / "sessions.db"
                    if _tp_db.exists():
                        with TeamProfileStore(_tp_db) as _tp_store:
                            _tp_profiles = _tp_store.list_profiles()
                        # Filter to profiles matching the configured board(s)
                        _matching_profiles = []
                        for _tpp in _tp_profiles:
                            if _jira_ok and _tpp.source == "jira":
                                _matching_profiles.append(_tpp)
                            elif _azdevops_ok and _tpp.source == "azdevops":
                                _matching_profiles.append(_tpp)
                        if _matching_profiles:
                            from datetime import UTC
                            from datetime import datetime as _dt

                            _latest = _matching_profiles[0]
                            if _latest.updated_at:
                                try:
                                    _up = _dt.fromisoformat(_latest.updated_at)
                                    _staleness_days = (_dt.now(UTC) - _up).days
                                except Exception:
                                    pass
                except Exception:
                    pass
            logger.info(
                "Board config: jira=%s, azdevops=%s, staleness_days=%s",
                _jira_ok,
                _azdevops_ok,
                _staleness_days,
            )

            # ── Route: Usage mode → single-page dashboard ────────────────
            if chosen["key"] == "usage":
                logger.info("Usage mode selected")
                from scrum_agent.ui.mode_select.screens._screens_secondary import _build_usage_screen

                _usage_data = _collect_usage_data()
                _u_scroll, _u_sel = 0, 0
                w, h = console.size
                live.update(
                    _build_usage_screen(
                        _usage_data,
                        scroll_offset=_u_scroll,
                        width=w,
                        height=h,
                        action_sel=_u_sel,
                    )
                )
                while True:
                    k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                    if k in ("up", "scroll_up"):
                        _u_scroll = max(0, _u_scroll - 1)
                    elif k in ("down", "scroll_down"):
                        _u_scroll += 1
                    elif k in ("enter", " ", "esc", "q"):
                        break
                    w, h = console.size
                    live.update(
                        _build_usage_screen(
                            _usage_data,
                            scroll_offset=_u_scroll,
                            width=w,
                            height=h,
                            action_sel=_u_sel,
                        )
                    )
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # Staggered vertical reveal — cards pop in one by one, fast.
            _reveal_target = float(proj_n)
            _cards_visible = 0.0
            _reveal_speed = 15.0  # cards per second (~1 card every 4 frames)
            _reveal_start = time.monotonic()
            while _cards_visible < _reveal_target:
                dt_r = time.monotonic() - _reveal_start
                _cards_visible = min(_reveal_target, dt_r * _reveal_speed)
                w, h = console.size
                live.update(
                    _build_project_list_screen(
                        projects,
                        proj_selected,
                        width=w,
                        height=h,
                        cards_visible=_cards_visible,
                        card_fade=1.0,
                        jira_enabled=_jira_ok,
                        azdevops_enabled=_azdevops_ok,
                    )
                )
                time.sleep(_FRAME_TIME)

            # ── Phase 3: Project list interaction ─────────────────────────────
            # focus: 0 = project card, 1 = Delete button, 2 = Export button.
            # Up/Down navigates between projects (resets focus to card).
            # Left/Right navigates between card ↔ Delete ↔ Export within a row.
            # Enter activates the focused element (open project, delete, export).
            #
            # When Export is activated, a split submenu [HTML | Markdown] slides
            # out from the Export button. Left/Right switches between the two
            # halves; Enter exports; Esc closes the submenu.
            #
            # Button colour animation: buttons start grey and smoothly fade
            # to their accent colour when focused, then fade back to grey
            # when focus leaves.  del_fade_target / exp_fade_target track the
            # desired end state; del_fade / exp_fade are the animated values.
            #
            # _restart_project_list: set to True when a session ends (Esc or
            # completed) so we loop back to this point from Phase 4.
            _restart_project_list = True
            while _restart_project_list:
                _restart_project_list = False
                focus = 0
                del_fade = 0.0  # current animated value 0.0 (grey) → 1.0 (colour)
                exp_fade = 0.0
                card_fade = 1.0  # start fully visible for initially selected card
                pulse = 0.0  # one-shot white flash on Enter (decays from 1.0 → 0.0)
                del_fade_target = 0.0
                exp_fade_target = 0.0
                card_fade_target = 1.0
                fade_speed = 6.0  # units per second — full transition ≈ 0.17s

                _is_project_row = lambda: projects and proj_selected < len(projects)  # noqa: E731

                # Action buttons (Delete/Export) stagger-reveal on the selected row
                action_btns_visible = 0.0
                action_btns_visible_target = 2.0 if _is_project_row() else 0.0

                # Export submenu state — the split [HTML | Markdown | Jira] panel
                export_submenu_open = False
                submenu_sel = 0  # 0 = HTML, 1 = Markdown, 2 = Jira
                submenu_html_fade = 0.0
                submenu_md_fade = 0.0
                submenu_jira_fade = 0.0
                submenu_azdevops_fade = 0.0
                submenu_html_fade_target = 0.0
                submenu_md_fade_target = 0.0
                submenu_jira_fade_target = 0.0
                submenu_azdevops_fade_target = 0.0
                submenu_visible = 0.0
                submenu_visible_target = 0.0

                # Delete popup state — non-blocking overlay instead of full-screen modal.
                # The popup slides up from the bottom of the project list screen.
                delete_popup_open = False
                delete_popup_t = 0.0  # animated 0→1 (slide-up progress)
                delete_popup_target = 0.0  # 0.0 = hidden, 1.0 = visible
                delete_popup_name = ""
                delete_popup_pulse = 0.0  # sine-wave phase for red pulsing
                delete_popup_flash = 0.0  # white flash on confirm (1→0 decay)
                _delete_pending = False  # True after Enter confirm, delete after slide-out

                # Team analysis popup state — staleness prompt when profile >30d old
                team_popup_open = False
                team_popup_t = 0.0
                team_popup_target = 0.0
                team_popup_sel = 0  # 0 = Yes Analyse, 1 = Skip
                team_popup_pulse = 0.0
                _team_popup_result = ""  # "analyse" or "skip"
                _team_popup_msg = ""  # dynamic staleness message

                prev_tick = time.monotonic()

                while True:
                    key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                    # ── Export submenu mode ────────────────────────────────────
                    # When the submenu is open, capture all keys here. Left/Right
                    # switches between HTML and Markdown; Enter exports; Esc closes.
                    # Build dynamic submenu index → action mapping
                    _submenu_actions = ["html", "markdown"]
                    if _jira_ok:
                        _submenu_actions.append("jira")
                    if _azdevops_ok:
                        _submenu_actions.append("azdevops")

                    def _update_submenu_fades():
                        nonlocal submenu_html_fade_target, submenu_md_fade_target
                        nonlocal submenu_jira_fade_target, submenu_azdevops_fade_target
                        submenu_html_fade_target = 1.0 if submenu_sel == 0 else 0.0
                        submenu_md_fade_target = 1.0 if submenu_sel == 1 else 0.0
                        _jira_idx = _submenu_actions.index("jira") if "jira" in _submenu_actions else -1
                        _azdo_idx = _submenu_actions.index("azdevops") if "azdevops" in _submenu_actions else -1
                        submenu_jira_fade_target = 1.0 if submenu_sel == _jira_idx else 0.0
                        submenu_azdevops_fade_target = 1.0 if submenu_sel == _azdo_idx else 0.0

                    if export_submenu_open:
                        if key == "left":
                            submenu_sel = max(0, submenu_sel - 1)
                            _update_submenu_fades()
                        elif key == "right":
                            submenu_sel = min(_submenu_max, submenu_sel + 1)
                            _update_submenu_fades()
                        elif key == "enter":
                            project = projects[proj_selected]
                            path = None
                            _action = _submenu_actions[submenu_sel] if submenu_sel < len(_submenu_actions) else ""
                            if _action == "html":
                                from scrum_agent.persistence import export_project_html

                                path = export_project_html(project.id)
                            elif _action == "markdown":
                                from scrum_agent.persistence import export_project_md

                                path = export_project_md(project.id)
                            elif _action in ("jira", "azdevops"):
                                # Tracker export — full sync: Epic + Stories + Tasks + Sprints
                                import threading

                                from scrum_agent.persistence import (
                                    load_graph_state,
                                    save_graph_state,
                                    save_project_snapshot,
                                )

                                _tracker_label = "Jira" if _action == "jira" else "Azure DevOps"
                                if _action == "jira":
                                    from scrum_agent.jira_sync import sync_all_to_jira as _sync_all_fn
                                else:
                                    from scrum_agent.azdevops_sync import sync_all_to_azdevops as _sync_all_fn

                                if True:
                                    gs = load_graph_state(project.id)
                                    if not gs:
                                        path = "No saved state for this project"
                                    else:
                                        # Run sync in background thread with live progress
                                        _sync_result_box: list = [None, None]  # [result, error]
                                        _sync_state_box: list = [None]
                                        _sync_done = threading.Event()
                                        # Shared progress state: log of completed items + current active item
                                        _sync_log: list[str] = []
                                        _sync_current: list[str] = ["Starting..."]
                                        _sync_counter: list[int] = [0, 0]  # [current, total]

                                        def _on_sync_progress(current, total, desc):
                                            _sync_counter[0] = current
                                            _sync_counter[1] = total
                                            if _sync_current[0] and _sync_current[0] != "Starting...":
                                                _sync_log.append(f"  ✓ {_sync_current[0]}")
                                            _sync_current[0] = desc

                                        def _run_jira_sync():
                                            try:
                                                r, s = _sync_all_fn(gs, on_progress=_on_sync_progress)
                                                _sync_result_box[0] = r
                                                _sync_state_box[0] = s
                                            except Exception as exc:
                                                _sync_result_box[1] = exc
                                            finally:
                                                _sync_done.set()

                                        _sync_thread = threading.Thread(target=_run_jira_sync, daemon=True)
                                        _sync_thread.start()

                                        # Show live scrolling log while the thread runs
                                        while not _sync_done.is_set():
                                            w, h = console.size
                                            viewport_h = max(3, h - 12)
                                            visible_log = _sync_log[-viewport_h:] if _sync_log else []
                                            cur = _sync_counter[0]
                                            tot = _sync_counter[1]
                                            counter = f"[{cur}/{tot}]" if tot else ""
                                            active = f"  ▸ {counter} {_sync_current[0]}"
                                            display_lines = "\n".join([*visible_log, active])
                                            live.update(
                                                _build_project_export_success_screen(
                                                    display_lines,
                                                    width=w,
                                                    height=h,
                                                    subtitle=f"{_tracker_label} sync",
                                                    hint="",
                                                )
                                            )
                                            time.sleep(_FRAME_TIME)
                                        _sync_thread.join()

                                        if _sync_result_box[1] is not None:
                                            path = f"{_tracker_label} sync failed: {_sync_result_box[1]}"
                                        elif _sync_result_box[0] is not None:
                                            sr = _sync_result_box[0]
                                            new_gs = _sync_state_box[0]
                                            if new_gs:
                                                save_graph_state(project.id, new_gs)
                                                save_project_snapshot(project.id, new_gs)
                                            _iters = getattr(sr, "sprints_created", None) or getattr(
                                                sr, "iterations_created", {}
                                            )
                                            created = len(sr.stories_created) + len(sr.tasks_created) + len(_iters)
                                            skipped = sr.skipped
                                            errors = len(sr.errors)
                                            parts = []
                                            if created:
                                                parts.append(f"{created} created")
                                            if skipped:
                                                parts.append(f"{skipped} skipped")
                                            if errors:
                                                parts.append(f"{errors} errors")
                                            epic = getattr(sr, "epic_key", None) or getattr(sr, "epic_id", None) or ""
                                            prefix = f"Epic: {epic} — " if epic else ""
                                            summary = ", ".join(parts) or "Nothing to sync"
                                            # Show first error for diagnosis
                                            if sr.errors:
                                                first_err = sr.errors[0][:80]
                                                summary += f"\n{first_err}"
                                                # Write all errors to log file for debugging
                                                _err_path = Path.home() / ".scrum-agent" / "jira-sync-errors.log"
                                                _err_path.write_text("\n".join(sr.errors), encoding="utf-8")
                                            path = prefix + summary

                            if path:
                                w, h = console.size
                                live.update(
                                    _build_project_export_success_screen(
                                        str(path),
                                        width=w,
                                        height=h,
                                    )
                                )
                                # Show for at least 1.5s, then wait for a real keypress
                                _export_t0 = time.monotonic()
                                while True:
                                    k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                    elapsed = time.monotonic() - _export_t0
                                    if elapsed < 1.5:
                                        continue  # enforce minimum display time
                                    if k and k not in ("scroll_up", "scroll_down", ""):
                                        break

                            # Close submenu after export
                            export_submenu_open = False
                            submenu_visible_target = 0.0
                            submenu_html_fade = 0.0
                            submenu_md_fade = 0.0
                            submenu_jira_fade = 0.0
                            submenu_azdevops_fade = 0.0
                            submenu_html_fade_target = 0.0
                            submenu_md_fade_target = 0.0
                            submenu_jira_fade_target = 0.0
                            submenu_azdevops_fade_target = 0.0
                            exp_fade_target = 1.0  # restore Export highlight
                        elif key in ("esc", "q"):
                            export_submenu_open = False
                            submenu_visible_target = 0.0
                            submenu_html_fade_target = 0.0
                            submenu_md_fade_target = 0.0
                            submenu_jira_fade_target = 0.0
                            submenu_azdevops_fade_target = 0.0
                            exp_fade_target = 1.0  # restore Export highlight

                    # ── Team analysis popup mode ──────────────────────────────
                    # Button selector: Left/Right navigates, Enter confirms.
                    # When both boards configured: [Jira] [AzDO] [Skip] (3 buttons)
                    # When one board configured:   [Yes, Analyse] [Skip] (2 buttons)
                    elif team_popup_open:
                        _both_boards = _jira_ok and _azdevops_ok
                        _popup_btn_count = 3 if _both_boards else 2
                        if key == "left":
                            team_popup_sel = max(0, team_popup_sel - 1)
                        elif key == "right":
                            team_popup_sel = min(_popup_btn_count - 1, team_popup_sel + 1)
                        elif key == "enter":
                            if _both_boards:
                                # 0=Jira, 1=AzDO, 2=Skip
                                if team_popup_sel == 0:
                                    _team_popup_result = "analyse_jira"
                                elif team_popup_sel == 1:
                                    _team_popup_result = "analyse_azdevops"
                                else:
                                    _team_popup_result = "skip"
                            else:
                                # 0=Yes, 1=Skip
                                _team_popup_result = "analyse" if team_popup_sel == 0 else "skip"
                            team_popup_target = 0.0  # slide out
                        elif key in ("esc", "q"):
                            _team_popup_result = "skip"
                            team_popup_target = 0.0

                    # ── Delete popup mode ─────────────────────────────────────
                    # When the popup is open, Enter confirms delete, Esc dismisses.
                    # All other keys are ignored so the user can't navigate away.
                    elif delete_popup_open:
                        if key == "enter":
                            # Confirm delete — white flash, THEN slide down.
                            # Setting flash to 1.0 triggers the flash phase.
                            # The slide-down only begins once the flash decays
                            # below a threshold (see animation section below).
                            delete_popup_flash = 1.0
                            _delete_pending = True
                        elif key in ("esc", "q"):
                            # Dismiss popup without deleting
                            delete_popup_target = 0.0

                    # ── Normal project list mode ───────────────────────────────
                    elif key in ("up", "scroll_up"):
                        proj_selected = (proj_selected - 1) % proj_n
                        focus = 0
                        del_fade_target = 0.0
                        exp_fade_target = 0.0
                        card_fade = 0.0
                        card_fade_target = 1.0
                        action_btns_visible = 0.0
                        action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                    elif key in ("down", "scroll_down"):
                        proj_selected = (proj_selected + 1) % proj_n
                        focus = 0
                        del_fade_target = 0.0
                        exp_fade_target = 0.0
                        card_fade = 0.0
                        card_fade_target = 1.0
                        action_btns_visible = 0.0
                        action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                    elif key == "left":
                        if focus > 0:
                            focus -= 1
                        else:
                            proj_selected = (proj_selected - 1) % proj_n
                            focus = 0
                            card_fade = 0.0
                            card_fade_target = 1.0
                            action_btns_visible = 0.0
                            action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                        del_fade_target = 1.0 if focus == 1 else 0.0
                        exp_fade_target = 1.0 if focus == 2 else 0.0
                    elif key == "right":
                        if _is_project_row() and focus < 2:
                            focus += 1
                        else:
                            proj_selected = (proj_selected + 1) % proj_n
                            focus = 0
                            card_fade = 0.0
                            card_fade_target = 1.0
                            action_btns_visible = 0.0
                            action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                        del_fade_target = 1.0 if focus == 1 else 0.0
                        exp_fade_target = 1.0 if focus == 2 else 0.0

                    elif key == "enter":
                        # ── Focus 1: Delete → open popup overlay ───────────
                        if focus == 1 and _is_project_row():
                            delete_popup_open = True
                            delete_popup_target = 1.0
                            delete_popup_name = projects[proj_selected].name

                        # ── Focus 2: Export → open submenu ────────────────
                        elif focus == 2 and _is_project_row():
                            export_submenu_open = True
                            submenu_sel = 0  # default to HTML
                            submenu_visible_target = float(_submenu_max + 1)  # stagger-reveal all buttons
                            submenu_html_fade_target = 1.0
                            submenu_md_fade_target = 0.0
                            exp_fade_target = 0.0  # grey out Export while submenu is active

                        # ── Focus 0: Card (empty state / new project) ────
                        elif not projects or proj_selected == len(projects):
                            # Check freshness — show popup only if stale (>30d) or missing
                            _profile_fresh = _staleness_days is not None and _staleness_days <= 30
                            if _board_configured and not team_popup_open and not _profile_fresh:
                                # Build dynamic staleness message
                                if _staleness_days is not None:
                                    _team_popup_msg = (
                                        f"Your team analysis is {_staleness_days} days old. Re-analyse before planning?"
                                    )
                                else:
                                    _team_popup_msg = "No team analysis found. Analyse your board before planning?"
                                team_popup_open = True
                                team_popup_target = 1.0
                                team_popup_sel = 0
                                team_popup_pulse = 0.0
                                _team_popup_result = ""
                            else:
                                pulse = 1.0
                                break  # → intake mode selection
                        else:
                            # White pulse flash on selected card before opening
                            pulse = 1.0
                            _pulse_frames = 8
                            for _pf in range(_pulse_frames):
                                pulse = max(0.0, 1.0 - (_pf + 1) / _pulse_frames)
                                w, h = console.size
                                live.update(
                                    _build_project_list_screen(
                                        projects,
                                        proj_selected,
                                        width=w,
                                        height=h,
                                        focus=focus,
                                        del_fade=del_fade,
                                        exp_fade=exp_fade,
                                        card_fade=card_fade,
                                        pulse=pulse,
                                        jira_enabled=_jira_ok,
                                        azdevops_enabled=_azdevops_ok,
                                    )
                                )
                                time.sleep(_FRAME_TIME)

                            # Resume an existing project — load its saved graph state
                            # so the session can skip already-completed phases.
                            # See README: "Memory & State" — session persistence.
                            from langchain_core.messages import HumanMessage

                            from scrum_agent.persistence import load_graph_state
                            from scrum_agent.ui.session import run_session

                            project = projects[proj_selected]
                            saved_state = load_graph_state(project.id)

                            # Fallback: if no state file exists (project created before
                            # state persistence was added), build a minimal graph state
                            # from project metadata so the session skips Phase A.
                            if saved_state is None:
                                saved_state = {
                                    "messages": [HumanMessage(content=project.name)],
                                }

                            run_session(
                                live,
                                console,
                                intake_mode=saved_state.get("_intake_mode", "smart"),
                                resume_project_id=project.id,
                                resume_graph_state=saved_state,
                                dry_run=dry_run,
                                _read_key_fn=_read_key_fn,
                            )
                            # Session ended (Esc or completed) — return to project list
                            projects = _load_projects()
                            proj_n = len(projects) + 1
                            proj_selected = min(proj_selected, proj_n - 1)
                            pulse = 0.0
                            continue

                    elif key == "esc":
                        # ── Reverse transition: fade out cards → slide title down ──
                        # 1) cards fade out, 2) Planning slides from top to its
                        # position in the 3-item layout, 3) other titles fade in
                        # as Planning reaches its resting position.

                        # Step 1: Reverse stagger — cards disappear bottom-to-top
                        _dismiss_target = 0.0
                        _dismiss_visible = float(proj_n)
                        _dismiss_speed = 15.0  # cards per second (matches reveal)
                        _dismiss_start = time.monotonic()
                        while _dismiss_visible > _dismiss_target:
                            dt_d = time.monotonic() - _dismiss_start
                            _dismiss_visible = max(_dismiss_target, float(proj_n) - dt_d * _dismiss_speed)
                            w, h = console.size
                            live.update(
                                _build_project_list_screen(
                                    projects,
                                    proj_selected,
                                    width=w,
                                    height=h,
                                    cards_visible=_dismiss_visible,
                                    jira_enabled=_jira_ok,
                                    azdevops_enabled=_azdevops_ok,
                                )
                            )
                            time.sleep(_FRAME_TIME)

                        # Step 2: Slide Planning title from top down to its 3-item
                        # layout position. In the last ~40% of the slide, fade in
                        # the other two mode titles so they appear as Planning lands.
                        chosen = _MODE_CARDS[selected]
                        base_r, base_g, base_b = COLOR_RGB.get(chosen["color"], (180, 180, 180))
                        base_style = f"bold rgb({base_r},{base_g},{base_b})"
                        others = [i for i in range(n) if i != selected]

                        w, h = console.size
                        inner_h = h - 4
                        # Target: where Planning sits in the full 3-item mode screen.
                        # body_h for 3 items with Planning selected (no desc during slide):
                        # Planning(2) + blank(1) + CodeReview(2) + blank(1) + Sprint(2) = 8
                        body_h_no_desc = 2 * n + (n - 1)
                        target_offset = max(0, (inner_h - body_h_no_desc) // 2)
                        start_offset = 1  # current position (top of project list)

                        slide_frames = 18
                        for frame in range(slide_frames + 1):
                            t = frame / slide_frames
                            eased = ease_out_cubic(t)
                            current_offset = int(start_offset + (target_offset - start_offset) * eased)

                            # Fade others in during the last 40% of the slide
                            fade_t = max(0.0, (t - 0.6) / 0.4)

                            w, h = console.size
                            if fade_t <= 0:
                                # Only Planning visible — use slide frame
                                live.update(
                                    _build_slide_frame(
                                        chosen,
                                        top_offset=current_offset,
                                        width=w,
                                        height=h,
                                        style=base_style,
                                    )
                                )
                            else:
                                # Cross-fade: show all items, fade others from dark
                                # to their resting dim colour (100,100,100).
                                from scrum_agent.ui.shared._animations import BLACK_RGB, lerp_color

                                dim_rgb = (100, 100, 100)
                                fade_rgb = lerp_color(fade_t, BLACK_RGB, dim_rgb)
                                live.update(
                                    _build_mode_screen(
                                        selected,
                                        width=w,
                                        height=h,
                                        shimmer_tick=0.0,
                                        desc_reveal=0,
                                        fade_style=fade_rgb,
                                        fade_indices=others,
                                    )
                                )
                            time.sleep(_FRAME_TIME)

                        # Step 3: Restart mode selection, skip the fade-in.
                        # Description typewriter starts fresh from select_time.
                        _restart_mode_select = True
                        _skip_fade_in = True
                        break  # break Phase 3 loop → restart Phase 1

                    # Animate button fade — smoothly move current values toward targets
                    now = time.monotonic()
                    dt = now - prev_tick
                    prev_tick = now
                    step = fade_speed * dt

                    if del_fade < del_fade_target:
                        del_fade = min(del_fade + step, del_fade_target)
                    elif del_fade > del_fade_target:
                        del_fade = max(del_fade - step, del_fade_target)
                    if exp_fade < exp_fade_target:
                        exp_fade = min(exp_fade + step, exp_fade_target)
                    elif exp_fade > exp_fade_target:
                        exp_fade = max(exp_fade - step, exp_fade_target)
                    if card_fade < card_fade_target:
                        card_fade = min(card_fade + step, card_fade_target)
                    elif card_fade > card_fade_target:
                        card_fade = max(card_fade - step, card_fade_target)
                    # Pulse decays toward 0
                    if pulse > 0:
                        pulse = max(0.0, pulse - step)

                    # Action buttons stagger animation (same speed as export submenu)
                    action_stagger_step = dt * 12.0
                    if action_btns_visible < action_btns_visible_target:
                        action_btns_visible = min(action_btns_visible + action_stagger_step, action_btns_visible_target)
                    elif action_btns_visible > action_btns_visible_target:
                        action_btns_visible = max(action_btns_visible - action_stagger_step, action_btns_visible_target)

                    # Export submenu stagger animation — faster rate so the
                    # three buttons pop in/out quickly one after another.
                    stagger_step = dt * 12.0  # ~0.25s to reveal all 3 buttons
                    if submenu_visible < submenu_visible_target:
                        submenu_visible = min(submenu_visible + stagger_step, submenu_visible_target)
                    elif submenu_visible > submenu_visible_target:
                        submenu_visible = max(submenu_visible - stagger_step, submenu_visible_target)
                    if submenu_html_fade < submenu_html_fade_target:
                        submenu_html_fade = min(submenu_html_fade + step, submenu_html_fade_target)
                    elif submenu_html_fade > submenu_html_fade_target:
                        submenu_html_fade = max(submenu_html_fade - step, submenu_html_fade_target)
                    if submenu_md_fade < submenu_md_fade_target:
                        submenu_md_fade = min(submenu_md_fade + step, submenu_md_fade_target)
                    elif submenu_md_fade > submenu_md_fade_target:
                        submenu_md_fade = max(submenu_md_fade - step, submenu_md_fade_target)
                    if submenu_jira_fade < submenu_jira_fade_target:
                        submenu_jira_fade = min(submenu_jira_fade + step, submenu_jira_fade_target)
                    elif submenu_jira_fade > submenu_jira_fade_target:
                        submenu_jira_fade = max(submenu_jira_fade - step, submenu_jira_fade_target)
                    if submenu_azdevops_fade < submenu_azdevops_fade_target:
                        submenu_azdevops_fade = min(submenu_azdevops_fade + step, submenu_azdevops_fade_target)
                    elif submenu_azdevops_fade > submenu_azdevops_fade_target:
                        submenu_azdevops_fade = max(submenu_azdevops_fade - step, submenu_azdevops_fade_target)

                    # Team analysis popup slide animation
                    if team_popup_t < team_popup_target:
                        team_popup_t = min(team_popup_t + step, team_popup_target)
                    elif team_popup_t > team_popup_target:
                        team_popup_t = max(team_popup_t - step, team_popup_target)

                    if team_popup_open and team_popup_t > 0:
                        team_popup_pulse += dt
                    elif team_popup_t <= 0:
                        team_popup_pulse = 0.0

                    # When team popup finishes sliding out, resolve the result.
                    if team_popup_open and team_popup_target == 0.0 and team_popup_t <= 0:
                        team_popup_open = False
                        if _team_popup_result.startswith("analyse"):
                            break
                        # "skip" falls through to normal intake
                        pulse = 1.0
                        break  # → intake mode selection

                    # Delete popup slide animation
                    if delete_popup_t < delete_popup_target:
                        delete_popup_t = min(delete_popup_t + step, delete_popup_target)
                    elif delete_popup_t > delete_popup_target:
                        delete_popup_t = max(delete_popup_t - step, delete_popup_target)

                    # Pulse clock: ticks whenever the popup is visible so the
                    # border oscillates between dark/bright red (like a loader).
                    if delete_popup_open and delete_popup_t > 0:
                        delete_popup_pulse += dt
                    elif delete_popup_t <= 0:
                        delete_popup_pulse = 0.0

                    # White flash decays toward 0 (slower rate so it's visible)
                    if delete_popup_flash > 0:
                        delete_popup_flash = max(0.0, delete_popup_flash - dt * 3.0)
                        # Once flash finishes, start the slide-down
                        if delete_popup_flash <= 0 and _delete_pending:
                            delete_popup_target = 0.0

                    # When popup finishes sliding out, clear the open state.
                    # If a delete was confirmed (_delete_pending), perform it now.
                    if delete_popup_open and delete_popup_target == 0.0 and delete_popup_t <= 0:
                        if _delete_pending:
                            from scrum_agent.persistence import delete_project

                            project = projects[proj_selected]
                            delete_project(project.id)
                            projects = _load_projects()
                            if projects:
                                proj_n = len(projects) + 1
                                proj_selected = min(proj_selected, proj_n - 1)
                            else:
                                proj_n = 2
                                proj_selected = 0
                            _delete_pending = False
                            focus = 0
                            # Reset button animations so focus returns to card
                            del_fade = 0.0
                            del_fade_target = 0.0
                            exp_fade = 0.0
                            exp_fade_target = 0.0
                            action_btns_visible = 0.0
                            action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                        else:
                            # Esc dismiss — keep Delete button focused
                            focus = 1
                            del_fade = 1.0
                            del_fade_target = 1.0
                            exp_fade = 0.0
                            exp_fade_target = 0.0
                        delete_popup_open = False
                        delete_popup_name = ""
                        delete_popup_flash = 0.0
                        card_fade = 1.0
                        card_fade_target = 1.0

                    w, h = console.size
                    live.update(
                        _build_project_list_screen(
                            projects,
                            proj_selected,
                            width=w,
                            height=h,
                            focus=focus,
                            del_fade=del_fade,
                            exp_fade=exp_fade,
                            card_fade=card_fade,
                            pulse=pulse,
                            action_btns_visible=action_btns_visible,
                            show_export_submenu=export_submenu_open or submenu_visible > 0,
                            submenu_sel=submenu_sel,
                            submenu_html_fade=submenu_html_fade,
                            submenu_md_fade=submenu_md_fade,
                            submenu_jira_fade=submenu_jira_fade,
                            submenu_azdevops_fade=submenu_azdevops_fade,
                            submenu_visible=submenu_visible,
                            delete_popup_name=delete_popup_name,
                            delete_popup_t=delete_popup_t,
                            delete_popup_pulse=delete_popup_pulse,
                            delete_popup_flash=delete_popup_flash,
                            team_popup_t=team_popup_t,
                            team_popup_sel=team_popup_sel,
                            team_popup_pulse=team_popup_pulse,
                            team_popup_message=_team_popup_msg,
                            jira_enabled=_jira_ok,
                            azdevops_enabled=_azdevops_ok,
                        )
                    )

                # Guard: Esc from project list sets _restart_mode_select → skip to outer loop
                if _restart_mode_select:
                    break

                # ── Phase 3a: Team analysis (if user selected "Analyse") ──────────
                if _team_popup_result.startswith("analyse"):
                    import threading

                    from scrum_agent.team_profile import TeamProfileStore
                    from scrum_agent.tools.team_learning import (
                        _fetch_azdevops_history,
                        _fetch_jira_history,
                        _run_parallel_analysis,
                    )

                    # Determine source from popup result
                    if _team_popup_result == "analyse_jira":
                        _ta_source = "jira"
                    elif _team_popup_result == "analyse_azdevops":
                        _ta_source = "azdevops"
                    else:
                        _ta_source = "jira" if _jira_ok else "azdevops"
                    _ta_project_key = ""
                    _ta_team_name = ""
                    try:
                        if _ta_source == "jira":
                            from scrum_agent.config import get_jira_project_key

                            _ta_project_key = get_jira_project_key() or ""
                        else:
                            from scrum_agent.config import (
                                get_azure_devops_project,
                                get_azure_devops_team,
                            )

                            _ta_project_key = get_azure_devops_project() or ""
                            _ta_team_name = get_azure_devops_team() or ""
                    except Exception:
                        pass

                    _ta_progress: list[str] = ["Fetching sprint history\u2026"]
                    _ta_profile_box: list = [None]
                    _ta_examples_box: list = [None]
                    _ta_sprint_names_box: list = [[]]
                    _ta_error_box: list[str] = [""]
                    _ta_done = threading.Event()

                    def _run_team_analysis():
                        try:
                            if _ta_source == "jira":
                                sprint_data = _fetch_jira_history(_ta_project_key, 8)
                            else:
                                sprint_data = _fetch_azdevops_history(_ta_project_key, 8)
                            if not sprint_data:
                                _ta_error_box[0] = "No closed sprints found."
                            else:
                                _ta_sprint_names_box[0] = [sd.get("sprint_name", "") for sd in sprint_data]
                                _result = _run_parallel_analysis(
                                    _ta_source, _ta_project_key or "unknown", sprint_data, _ta_progress
                                )
                                _ta_profile_box[0] = _result[0]
                                _ta_examples_box[0] = _result[1]
                        except Exception as exc:
                            _ta_error_box[0] = str(exc)
                        finally:
                            _ta_done.set()

                    logger.info(
                        "Starting team analysis: source=%s, project=%s",
                        _ta_source,
                        _ta_project_key,
                    )
                    _ta_thread_start = time.monotonic()
                    _ta_thread = threading.Thread(target=_run_team_analysis, daemon=True)
                    _ta_thread.start()

                    # Processing animation while waiting
                    from scrum_agent.ui.mode_select.screens._screens_secondary import (
                        _build_analysis_progress_screen,
                    )

                    _ta_anim_tick = 0.0
                    while not _ta_done.is_set():
                        _ta_anim_tick += _FRAME_TIME
                        w, h = console.size
                        live.update(
                            _build_analysis_progress_screen(
                                _ta_progress,
                                width=w,
                                height=h,
                                elapsed=time.monotonic() - _ta_thread_start,
                                anim_tick=_ta_anim_tick,
                                source=_ta_source,
                                mode="analysis",
                            )
                        )
                        time.sleep(_FRAME_TIME)
                    _ta_thread.join()

                    _ta_profile = _ta_profile_box[0]
                    _ta_duration = time.monotonic() - _ta_thread_start
                    if _ta_profile:
                        logger.info(
                            "Analysis complete: %s — %d sprints, %d stories (%.1fs)",
                            _ta_profile.team_id,
                            _ta_profile.sample_sprints,
                            _ta_profile.sample_stories,
                            _ta_duration,
                        )

                        # Save the fresh profile
                        db_dir = Path.home() / ".scrum-agent"
                        db_dir.mkdir(parents=True, exist_ok=True)
                        with TeamProfileStore(db_dir / "sessions.db") as store:
                            store.save(_ta_profile, examples=_ta_examples_box[0])
                        logger.info("Profile saved to %s/sessions.db", db_dir)

                        # Write structured analysis log to ~/.scrum-agent/logs/
                        try:
                            from scrum_agent.team_profile_exporter import write_analysis_log

                            _log_path = write_analysis_log(
                                _ta_profile,
                                examples=_ta_examples_box[0],
                                sprint_names=_ta_sprint_names_box[0],
                                duration_secs=_ta_duration,
                            )
                            logger.info("Analysis log: %s", _log_path)
                        except Exception as _log_exc:
                            logger.warning("Failed to write analysis log: %s", _log_exc)

                        # Show results screen
                        from scrum_agent.ui.mode_select.screens._screens_secondary import (
                            _build_team_analysis_screen,
                        )

                        _ta_scroll = 0
                        _ta_export_sel = 2  # 0=HTML, 1=Markdown, 2=Continue (default Continue)
                        _ta_export_fade = 0.0
                        _ta_export_fade_target = 0.0

                        _ta_examples = _ta_examples_box[0] or {}
                        _ta_sprint_names = _ta_sprint_names_box[0]

                        # Initial render before waiting for input
                        w, h = console.size
                        live.update(
                            _build_team_analysis_screen(
                                _ta_profile,
                                scroll_offset=_ta_scroll,
                                width=w,
                                height=h,
                                export_sel=_ta_export_sel,
                                examples=_ta_examples,
                                sprint_names=_ta_sprint_names,
                                team_name=_ta_team_name,
                            )
                        )

                        while True:
                            key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                            if key in ("up", "scroll_up"):
                                _ta_scroll = max(0, _ta_scroll - 1)
                            elif key in ("down", "scroll_down"):
                                _ta_scroll += 1
                            elif key == "left":
                                _ta_export_sel = max(0, _ta_export_sel - 1)
                            elif key == "right":
                                _ta_export_sel = min(1, _ta_export_sel + 1)
                            elif key == "enter" or key == " ":
                                if _ta_export_sel == 0:
                                    # Export both HTML and MD
                                    from scrum_agent.team_profile_exporter import (
                                        export_team_profile_html,
                                        export_team_profile_md,
                                    )

                                    export_team_profile_html(
                                        _ta_profile,
                                        examples=_ta_examples,
                                        sprint_names=_ta_sprint_names,
                                    )
                                    _exp_path = export_team_profile_md(
                                        _ta_profile,
                                        examples=_ta_examples,
                                        sprint_names=_ta_sprint_names,
                                    )
                                    w, h = console.size
                                    live.update(
                                        _build_project_export_success_screen(
                                            str(_exp_path),
                                            width=w,
                                            height=h,
                                            subtitle="Team profile exported (HTML + MD)",
                                        )
                                    )
                                    _exp_t0 = time.monotonic()
                                    while True:
                                        k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                        if time.monotonic() - _exp_t0 > 1.5 and k:
                                            break
                                else:
                                    break  # Continue → intake
                            elif key in ("esc", "q"):
                                break

                            w, h = console.size
                            live.update(
                                _build_team_analysis_screen(
                                    _ta_profile,
                                    scroll_offset=_ta_scroll,
                                    width=w,
                                    height=h,
                                    export_sel=_ta_export_sel,
                                    examples=_ta_examples,
                                    sprint_names=_ta_sprint_names,
                                    team_name=_ta_team_name,
                                )
                            )
                    elif _ta_error_box[0]:
                        w, h = console.size
                        live.update(
                            _build_project_export_success_screen(
                                _ta_error_box[0],
                                width=w,
                                height=h,
                                subtitle="Analysis failed",
                                hint="Press any key to continue.",
                            )
                        )
                        while True:
                            k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                            if k:
                                break

                # ── Phase 3b: Transition to intake mode selection ─────────────────
                # Show title + new subtitle, then stagger-reveal intake options.
                intake_selected = 0
                intake_n = len(_INTAKE_CARDS)
                intake_start = time.monotonic()

                # Blank frame — title + subtitle, no intake items yet
                w, h = console.size
                live.update(
                    _build_intake_screen(
                        intake_selected,
                        width=w,
                        height=h,
                        visible_items=0,
                    )
                )
                time.sleep(_FRAME_TIME * 2)

                # Stagger-reveal intake options one at a time
                for item_i in range(1, intake_n + 1):
                    w, h = console.size
                    live.update(
                        _build_intake_screen(
                            intake_selected,
                            width=w,
                            height=h,
                            visible_items=item_i,
                        )
                    )
                    time.sleep(_FRAME_TIME * 2)

                # ── Phase 4: Intake mode selection ────────────────────────────────
                chosen_intake = None
                while True:
                    key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                    if key in ("up", "left", "scroll_up"):
                        intake_selected = (intake_selected - 1) % intake_n
                        intake_start = time.monotonic()
                    elif key in ("down", "right", "scroll_down"):
                        intake_selected = (intake_selected + 1) % intake_n
                        intake_start = time.monotonic()
                    elif key == "enter":
                        chosen_intake = _INTAKE_CARDS[intake_selected]["key"]
                        if chosen_intake != "offline":
                            # Launch the full TUI session inside this Live context
                            # so there's no screen-clearing gap between mode selection
                            # and the first intake question.
                            # See README: "Architecture" — session replaces run_repl()
                            from scrum_agent.ui.session import run_session

                            run_session(
                                live,
                                console,
                                intake_mode=chosen_intake,
                                dry_run=dry_run,
                                _read_key_fn=_read_key_fn,
                            )
                            # Session ended (Esc or completed) — return to project list
                            projects = _load_projects()
                            proj_n = len(projects) + 1
                            proj_selected = min(proj_selected, proj_n - 1)
                            _restart_project_list = True
                            break  # break Phase 4 loop → restart Phase 3
                        break  # → offline sub-menu (Phase 5)
                    elif key == "esc":
                        # Back to project list
                        _restart_project_list = True
                        break

                    elapsed = time.monotonic() - intake_start
                    reveal = elapsed * _DESC_SCROLL_SPEED  # float for sub-char fade

                    w, h = console.size
                    tick = time.monotonic() - start_time
                    live.update(
                        _build_intake_screen(
                            intake_selected,
                            width=w,
                            height=h,
                            shimmer_tick=tick,
                            desc_reveal=reveal,
                        )
                    )

                # Guard: Phase 4 Esc or session-end sets restart → skip Phase 5
                if _restart_project_list:
                    continue
                if _restart_mode_select:
                    break

                # ── Phase 5: Offline sub-menu (Export / Import) ───────────────
                offline_selected = 0
                offline_n = len(_OFFLINE_CARDS)
                offline_start = time.monotonic()

                # Blank frame — title + subtitle, no items yet
                w, h = console.size
                live.update(
                    _build_offline_screen(
                        offline_selected,
                        width=w,
                        height=h,
                        visible_items=0,
                    )
                )
                time.sleep(_FRAME_TIME * 2)

                # Stagger-reveal offline options one at a time
                for item_i in range(1, offline_n + 1):
                    w, h = console.size
                    live.update(
                        _build_offline_screen(
                            offline_selected,
                            width=w,
                            height=h,
                            visible_items=item_i,
                        )
                    )
                    time.sleep(_FRAME_TIME * 2)

                # Phase 5 interaction loop
                while True:
                    key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                    if key in ("up", "left", "scroll_up"):
                        offline_selected = (offline_selected - 1) % offline_n
                        offline_start = time.monotonic()
                    elif key in ("down", "right", "scroll_down"):
                        offline_selected = (offline_selected + 1) % offline_n
                        offline_start = time.monotonic()
                    elif key == "enter":
                        break  # → Phase 5b (export or import)
                    elif key == "esc":
                        # Go back to project list
                        _restart_project_list = True
                        break

                    elapsed = time.monotonic() - offline_start
                    reveal = elapsed * _DESC_SCROLL_SPEED  # float for sub-char fade

                    w, h = console.size
                    tick = time.monotonic() - start_time
                    live.update(
                        _build_offline_screen(
                            offline_selected,
                            width=w,
                            height=h,
                            shimmer_tick=tick,
                            desc_reveal=reveal,
                        )
                    )

                # Guard: if Phase 5 Esc or Import Esc set restart, skip 5b
                if _restart_project_list:
                    continue

                # ── Phase 5b: Export or Import ────────────────────────────────
                offline_choice = _OFFLINE_CARDS[offline_selected]["key"]

                if offline_choice == "export":
                    # Export a blank questionnaire template directly
                    from scrum_agent.questionnaire_io import export_questionnaire_md

                    out_path = export_questionnaire_md(None, Path("scrum-questionnaire.md"))
                    w, h = console.size
                    live.update(_build_export_success_screen(str(out_path), width=w, height=h))
                    # Wait for any keypress to exit
                    while True:
                        key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                        if key:
                            break
                    return None  # cli.py exits

                else:
                    # Import — show text input for file path
                    import_value = ""
                    import_error = ""
                    _default_path = "scrum-questionnaire.md"

                    w, h = console.size
                    live.update(_build_import_screen(import_value, width=w, height=h, placeholder=_default_path))

                    while True:
                        key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                        if key == "enter":
                            # Use default if empty
                            file_path = import_value.strip() if import_value.strip() else _default_path
                            p = Path(file_path)
                            if not p.exists():
                                import_error = f"File not found: {file_path}"
                            elif not p.suffix == ".md":
                                import_error = f"Expected a .md file, got: {p.suffix or 'no extension'}"
                            else:
                                return ("project-planning", None, str(p))

                            w, h = console.size
                            live.update(
                                _build_import_screen(
                                    import_value,
                                    width=w,
                                    height=h,
                                    error=import_error,
                                    placeholder=_default_path,
                                )
                            )
                            continue

                        elif key == "esc":
                            _restart_project_list = True
                            break
                        elif key == "backspace":
                            import_value = import_value[:-1]
                            import_error = ""
                        elif key == "clear":
                            import_value = ""
                            import_error = ""
                        elif key.startswith("paste:") if isinstance(key, str) else False:
                            import_value += key[6:]
                            import_error = ""
                        elif len(key) == 1 and key.isprintable():
                            import_value += key
                            import_error = ""
                        elif key == "":
                            pass  # timeout, no input
                        else:
                            continue

                        w, h = console.size
                        live.update(
                            _build_import_screen(
                                import_value,
                                width=w,
                                height=h,
                                error=import_error,
                                placeholder=_default_path,
                            )
                        )

    return None
