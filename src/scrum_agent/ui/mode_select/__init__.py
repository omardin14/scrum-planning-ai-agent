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
                                            _esel = min(2, _esel + 1)
                                        elif kk == "enter" or kk == " ":
                                            if _esel == 2:
                                                break
                                            if _esel == 0:
                                                from scrum_agent.team_profile_exporter import export_team_profile_html

                                                _ep = export_team_profile_html(
                                                    _full,
                                                    examples=_stored_ex,
                                                )
                                            else:
                                                from scrum_agent.team_profile_exporter import export_team_profile_md

                                                _ep = export_team_profile_md(
                                                    _full,
                                                    examples=_stored_ex,
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
                                    while _ana_popup_open:
                                        w, h = console.size
                                        # Render a simple inline picker
                                        _pick_lines: list = [Text("")]
                                        _ph = Text("      Which board to analyse?", style="bold white", justify="left")
                                        _pick_lines.append(_ph)
                                        _pick_lines.append(Text(""))
                                        _pb = Text("      ", justify="left")
                                        for bi, bl in enumerate(["Jira", "Azure DevOps"]):
                                            if bi > 0:
                                                _pb.append("    ")
                                            sty = "bold white" if bi == _ana_popup_sel else "dim"
                                            _pb.append(f"[ {bl} ]", style=sty)
                                        _pick_lines.append(_pb)
                                        _pick_lines.append(Text(""))
                                        import rich.box as _rbox
                                        from rich.panel import Panel as _PickPanel

                                        from scrum_agent.ui.shared._components import analysis_title as _at

                                        _ana_title = _at()
                                        live.update(
                                            _PickPanel(
                                                Group(_ana_title, Text(""), *_pick_lines),
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
                        _ta_thread.start()

                        _ta_anim_tick = 0.0
                        while not _ta_done.is_set():
                            _ta_anim_tick += _FRAME_TIME
                            _ta_dots = int(_ta_anim_tick * 3) % 4
                            dots_str = "." * _ta_dots + " " * (3 - _ta_dots)
                            w, h = console.size
                            live.update(
                                _build_project_export_success_screen(
                                    "\n".join(_ta_progress),
                                    width=w,
                                    height=h,
                                    subtitle=f"Analysing team board{dots_str}",
                                    hint="",
                                )
                            )
                            time.sleep(_FRAME_TIME)
                        _ta_thread.join()

                        _ta_profile = _ta_profile_box[0]
                        _ta_duration = time.monotonic() - _ta_thread_start
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
                                    _ta_export_sel = min(2, _ta_export_sel + 1)
                                elif kk == "enter" or kk == " ":
                                    if _ta_export_sel == 2:
                                        break
                                    if _ta_export_sel == 0:
                                        from scrum_agent.team_profile_exporter import export_team_profile_html

                                        _ep = export_team_profile_html(
                                            _ta_profile,
                                            examples=_ta_examples,
                                            sprint_names=_ta_sprint_names,
                                        )
                                    else:
                                        from scrum_agent.team_profile_exporter import export_team_profile_md

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
                        if _tp_profiles:
                            from datetime import UTC
                            from datetime import datetime as _dt

                            _latest = _tp_profiles[0]
                            if _latest.updated_at:
                                try:
                                    _up = _dt.fromisoformat(_latest.updated_at)
                                    _staleness_days = (_dt.now(UTC) - _up).days
                                except Exception:
                                    pass
                except Exception:
                    pass
            logger.debug(
                "Board config: jira=%s, azdevops=%s, staleness_days=%s",
                _jira_ok,
                _azdevops_ok,
                _staleness_days,
            )

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
                    _ta_anim_tick = 0.0
                    _ta_dots = 0
                    while not _ta_done.is_set():
                        _ta_anim_tick += _FRAME_TIME
                        _ta_dots = int(_ta_anim_tick * 3) % 4
                        dots_str = "." * _ta_dots + " " * (3 - _ta_dots)
                        w, h = console.size
                        live.update(
                            _build_project_export_success_screen(
                                "\n".join(_ta_progress),
                                width=w,
                                height=h,
                                subtitle=f"Analysing team board{dots_str}",
                                hint="",
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
                                _ta_export_sel = min(2, _ta_export_sel + 1)
                            elif key == "enter" or key == " ":
                                if _ta_export_sel == 0:
                                    from scrum_agent.team_profile_exporter import export_team_profile_html

                                    _exp_path = export_team_profile_html(
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
                                            subtitle="Team profile exported",
                                        )
                                    )
                                    _exp_t0 = time.monotonic()
                                    while True:
                                        k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                        if time.monotonic() - _exp_t0 > 1.5 and k:
                                            break
                                elif _ta_export_sel == 1:
                                    from scrum_agent.team_profile_exporter import export_team_profile_md

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
                                            subtitle="Team profile exported",
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
