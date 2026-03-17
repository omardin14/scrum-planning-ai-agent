"""Issue tracking (Atlassian) phase of the provider selection wizard.

# See README: "Architecture" — this module handles the Jira/Confluence
# multi-field form input with verification and animated feedback.
"""

from __future__ import annotations

import math
import sys
import termios
import time
import tty
from typing import Any

from rich.console import Console
from rich.live import Live

from scrum_agent.ui.provider_select._config import _save_progress
from scrum_agent.ui.provider_select._constants import _ISSUE_TRACKING_FIELDS
from scrum_agent.ui.provider_select._verification import _verify_jira
from scrum_agent.ui.provider_select.screens._screens_vc import _build_issue_tracking_screen
from scrum_agent.ui.shared._animations import FRAME_TIME_30FPS


def _run_issue_tracking(
    console: Console,
    read_key,
    existing_config: dict[str, str] | None,
    provider: dict[str, Any],
    api_key: str,
    vc: dict[str, Any],
    vc_token: str,
    *,
    live: Live | None = None,
) -> dict[str, str] | None:
    """Run the issue tracking (Atlassian) phase.

    If live is None, creates its own Live context (for debug skip).
    Otherwise uses the existing Live display.
    """
    import threading

    it_selected = 0
    it_n = len(_ISSUE_TRACKING_FIELDS)
    _cfg = existing_config or {}
    it_values: dict[int, str] = {}
    for i, field in enumerate(_ISSUE_TRACKING_FIELDS):
        it_values[i] = _cfg.get(field["env_var"], "")
    it_errors: dict[int, str] = {}
    it_verified: dict[int, bool] = {}

    def _run_loop(_live: Live) -> dict[str, str] | None:
        nonlocal it_selected, it_values, it_errors, it_verified

        # Drain any leftover data in stdin before entering the input loop
        import select as _sel

        _drain_fd = sys.stdin.fileno()
        _drain_old = termios.tcgetattr(_drain_fd)
        try:
            tty.setcbreak(_drain_fd)
            while _sel.select([_drain_fd], [], [], 0.05)[0]:
                sys.stdin.read(1)
        finally:
            termios.tcsetattr(_drain_fd, termios.TCSADRAIN, _drain_old)

        w, h = console.size
        _live.update(_build_issue_tracking_screen(it_selected, it_values, width=w, height=h))

        while True:
            key = read_key()

            if key in ("up", "scroll_up"):
                it_selected = (it_selected - 1) % it_n
            elif key in ("down", "scroll_down"):
                it_selected = (it_selected + 1) % it_n
            elif key == "enter":
                missing = False
                for i, field in enumerate(_ISSUE_TRACKING_FIELDS):
                    if field["required"] and not it_values.get(i, "").strip():
                        it_errors[i] = f"{field['label']} is required"
                        if not missing:
                            it_selected = i
                        missing = True

                if missing:
                    w, h = console.size
                    _live.update(
                        _build_issue_tracking_screen(
                            it_selected,
                            it_values,
                            width=w,
                            height=h,
                            errors=it_errors,
                        )
                    )
                    continue

                jira_url = it_values[0].strip()
                jira_email = it_values[1].strip()
                jira_token_val = it_values[2].strip()

                verify_result: list[tuple[bool, str]] = []

                def _do_jira_verify():
                    verify_result.append(_verify_jira(jira_url, jira_email, jira_token_val))

                thread = threading.Thread(target=_do_jira_verify, daemon=True)
                thread.start()

                pulse_start = time.monotonic()
                while thread.is_alive():
                    elapsed = time.monotonic() - pulse_start
                    intensity = (math.sin(elapsed * 6) + 1) / 2
                    v = int(60 + 140 * intensity)
                    bo = {i: f"rgb({v},{v},{v})" for i in range(it_n)}
                    w, h = console.size
                    _live.update(
                        _build_issue_tracking_screen(
                            it_selected,
                            it_values,
                            width=w,
                            height=h,
                            border_overrides=bo,
                        )
                    )
                    time.sleep(FRAME_TIME_30FPS)

                thread.join()
                ok, msg = verify_result[0]

                if ok:
                    green_r, green_g, green_b = 80, 220, 120
                    for frame in range(10):
                        t = frame / 9
                        intensity = math.sin(t * math.pi)
                        r = int(green_r + (255 - green_r) * intensity)
                        g = int(green_g + (255 - green_g) * intensity)
                        b = int(green_b + (255 - green_b) * intensity)
                        bo = {i: f"rgb({r},{g},{b})" for i in range(it_n)}
                        w, h = console.size
                        _live.update(
                            _build_issue_tracking_screen(
                                it_selected,
                                it_values,
                                width=w,
                                height=h,
                                verified={i: True for i in range(it_n)},
                                border_overrides=bo,
                            )
                        )
                        time.sleep(FRAME_TIME_30FPS)

                    w, h = console.size
                    _live.update(
                        _build_issue_tracking_screen(
                            it_selected,
                            it_values,
                            width=w,
                            height=h,
                            verified={i: True for i in range(it_n)},
                        )
                    )
                    time.sleep(0.6)

                    issue_data = {}
                    for i, field in enumerate(_ISSUE_TRACKING_FIELDS):
                        val = it_values.get(i, "").strip()
                        if val:
                            issue_data[field["env_var"]] = val

                    _save_progress(issue_data)
                    return {
                        "name": provider["full_name"],
                        "env_var": provider["env_var"],
                        "provider_val": provider["provider_val"],
                        "prefix": provider["prefix"],
                        "instructions": provider["instructions"],
                        "api_key": api_key,
                        "vc_env_var": vc["env_var"],
                        "vc_token": vc_token,
                        "issue_tracking": issue_data,
                    }
                else:
                    it_errors[2] = msg
                    it_selected = 2
                    w, h = console.size
                    _live.update(
                        _build_issue_tracking_screen(
                            it_selected,
                            it_values,
                            width=w,
                            height=h,
                            errors=it_errors,
                        )
                    )
                    continue

            elif key == "esc":
                return None
            elif key == "clear":
                it_values[it_selected] = ""
                it_errors.pop(it_selected, None)
                it_verified.pop(it_selected, None)
            elif key == "backspace":
                it_values[it_selected] = it_values[it_selected][:-1]
                it_errors.pop(it_selected, None)
                it_verified.pop(it_selected, None)
            elif key == "tab":
                it_selected = (it_selected + 1) % it_n
            elif key.startswith("paste:"):
                it_values[it_selected] = it_values.get(it_selected, "") + key[6:]
                it_errors.pop(it_selected, None)
                it_verified.pop(it_selected, None)
            elif len(key) == 1 and key.isprintable():
                it_values[it_selected] = it_values.get(it_selected, "") + key
                it_errors.pop(it_selected, None)
                it_verified.pop(it_selected, None)

            w, h = console.size
            _live.update(
                _build_issue_tracking_screen(
                    it_selected,
                    it_values,
                    width=w,
                    height=h,
                    errors=it_errors,
                    verified=it_verified,
                )
            )

    if live is not None:
        return _run_loop(live)
    else:
        w, h = console.size
        with Live(
            _build_issue_tracking_screen(0, it_values, width=w, height=h),
            console=console,
            refresh_per_second=30,
            screen=True,
        ) as new_live:
            return _run_loop(new_live)
