"""Full-screen provider selection screen using Rich Live + raw terminal input.

# See README: "Architecture" — this is a UI component in the CLI layer.
# It uses Rich's Live display to redraw the full screen on each keypress,
# and reads raw keypresses via sys.stdin in cbreak/raw mode.

The screen shows three provider names as ASCII art text, stacked vertically.
Arrow keys navigate, Enter selects, q/Esc cancels.
After selection, unselected providers animate away and an API key input fades in.
Transitions between states use a common fade animation pattern.
"""

from __future__ import annotations

import math
import time

from rich.console import Console
from rich.live import Live

from scrum_agent.ui.provider_select._config import _save_progress  # noqa: F401
from scrum_agent.ui.provider_select._constants import _PROVIDER_CARDS, _VC_OPTIONS
from scrum_agent.ui.provider_select._phase_issue_tracking import _run_issue_tracking  # noqa: F401
from scrum_agent.ui.provider_select._transitions import _transition_to_input  # noqa: F401
from scrum_agent.ui.provider_select._verification import _verify_api_key, _verify_vc_token
from scrum_agent.ui.provider_select.screens._screens import _build_input_screen, _build_select_screen
from scrum_agent.ui.provider_select.screens._screens_vc import (
    _build_vc_input_screen,
    _build_vc_select_screen,
)
from scrum_agent.ui.shared._animations import COLOR_RGB, FADE_IN_LEVELS, FADE_OUT_LEVELS, FRAME_TIME_30FPS
from scrum_agent.ui.shared._input import disable_bracketed_paste, enable_bracketed_paste
from scrum_agent.ui.shared._input import read_key as _read_key  # noqa: F401 — re-export for compat


def _detect_aws_region() -> str | None:
    """Auto-detect AWS region from environment, config, or instance metadata.

    Uses boto3's built-in resolution chain which covers:
    1. AWS_REGION / AWS_DEFAULT_REGION env vars
    2. ~/.aws/config (all profile formats including [profile name])
    3. EC2/Lightsail instance metadata (IMDSv1 + IMDSv2)

    Falls back to manual parsing if boto3 is not installed.
    """
    import os

    # 1. Env vars (fast path, no imports needed)
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if region:
        return region

    # 2. boto3 session — handles ~/.aws/config and IMDS natively
    try:
        import boto3

        session = boto3.session.Session()
        if session.region_name:
            return session.region_name
    except Exception:
        pass

    # 3. Manual fallback — parse ~/.aws/config directly
    try:
        from pathlib import Path

        config_path = Path.home() / ".aws" / "config"
        if config_path.exists():
            for line in config_path.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("region"):
                    _, _, value = stripped.partition("=")
                    if value.strip():
                        return value.strip()
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_provider(
    console: Console | None = None, *, _read_key_fn=None, existing_config: dict[str, str] | None = None
) -> dict[str, str] | None:
    """Show full-screen provider selection, then API key input with verification.

    Organized as a step-based loop so Esc navigates back between steps:
    Step 0: LLM Provider selection + API key verification
    Step 1: Issue Tracking (Jira / Azure DevOps Boards / Skip)
    Step 2: Version Control (GitHub PAT)

    Returns a dict compatible with setup_wizard._PROVIDERS values (with an
    added 'api_key' field), or None if the user cancelled.
    """
    console = console or Console()
    read_key = _read_key_fn or _read_key

    # Enable bracketed paste mode so the terminal wraps pasted text in
    # \x1b[200~ ... \x1b[201~ markers. _read_key detects these and returns
    # the full pasted content as a single "paste:..." string.
    enable_bracketed_paste()

    import inspect

    _supports_timeout = "timeout" in inspect.signature(read_key).parameters

    # State preserved across steps (so going back retains previous choices)
    provider = None
    api_key = ""
    vc = None
    vc_token = ""
    step = 0

    w, h = console.size
    with Live(
        _build_select_screen(0, width=w, height=h, shimmer_tick=0.0),
        console=console,
        refresh_per_second=30,
        screen=True,
    ) as live:
        while step < 3:
            # ══════════════════════════════════════════════════════════════
            # Step 0: LLM Provider selection + API key input
            # ══════════════════════════════════════════════════════════════
            if step == 0:
                selected = 0
                n = len(_PROVIDER_CARDS)
                start_time = time.monotonic()

                w, h = console.size
                live.update(_build_select_screen(selected, width=w, height=h, shimmer_tick=0.0))

                # Phase 1: Provider selection
                _step0_done = False
                while True:
                    key = read_key(timeout=FRAME_TIME_30FPS) if _supports_timeout else read_key()
                    if key in ("up", "scroll_up"):
                        selected = (selected - 1) % n
                    elif key in ("down", "scroll_down"):
                        selected = (selected + 1) % n
                    elif key == "enter":
                        break
                    elif key in ("q", "esc"):
                        disable_bracketed_paste()
                        return None
                    w, h = console.size
                    tick = time.monotonic() - start_time
                    live.update(_build_select_screen(selected, width=w, height=h, shimmer_tick=tick))

                # Transition animation
                provider = _PROVIDER_CARDS[selected]
                _transition_to_input(live, console, selected, provider)

                # Phase 2: API key input
                _cfg = existing_config or {}
                input_value = _cfg.get(provider["env_var"], "")
                if provider.get("is_region_input") and not input_value:
                    input_value = _detect_aws_region() or ""
                error = ""
                verified: bool | None = None

                w, h = console.size
                live.update(_build_input_screen(provider, input_value, width=w, height=h))

                while True:
                    key = read_key()
                    if key == "enter":
                        if not input_value.strip():
                            error = f"{provider['env_var']} is required."
                            w, h = console.size
                            live.update(_build_input_screen(provider, input_value, width=w, height=h, error=error))
                            continue

                        import threading

                        verify_result: list[tuple[bool, str]] = []

                        def _do_verify():
                            verify_result.append(_verify_api_key(provider, input_value.strip()))

                        thread = threading.Thread(target=_do_verify, daemon=True)
                        thread.start()

                        pulse_start = time.monotonic()
                        while thread.is_alive():
                            elapsed = time.monotonic() - pulse_start
                            intensity = (math.sin(elapsed * 6) + 1) / 2
                            v = int(60 + 140 * intensity)
                            w, h = console.size
                            live.update(
                                _build_input_screen(
                                    provider,
                                    input_value,
                                    width=w,
                                    height=h,
                                    verifying=True,
                                    border_override=f"rgb({v},{v},{v})",
                                )
                            )
                            time.sleep(FRAME_TIME_30FPS)

                        thread.join()
                        ok, msg = verify_result[0]
                        verified = ok

                        if ok:
                            green_r, green_g, green_b = 80, 220, 120
                            for frame in range(10):
                                t = frame / 9
                                intensity = math.sin(t * math.pi)
                                r = int(green_r + (255 - green_r) * intensity)
                                g = int(green_g + (255 - green_g) * intensity)
                                b = int(green_b + (255 - green_b) * intensity)
                                w, h = console.size
                                live.update(
                                    _build_input_screen(
                                        provider,
                                        input_value,
                                        width=w,
                                        height=h,
                                        verified=True,
                                        border_override=f"rgb({r},{g},{b})",
                                    )
                                )
                                time.sleep(FRAME_TIME_30FPS)

                            w, h = console.size
                            live.update(_build_input_screen(provider, input_value, width=w, height=h, verified=True))
                            time.sleep(0.6)
                            api_key = input_value.strip()
                            _save_progress({"LLM_PROVIDER": provider["provider_val"], provider["env_var"]: api_key})
                            _step0_done = True
                            break
                        else:
                            w, h = console.size
                            live.update(
                                _build_input_screen(
                                    provider,
                                    input_value,
                                    width=w,
                                    height=h,
                                    verified=False,
                                    error=msg,
                                )
                            )
                        continue

                    elif key == "esc":
                        # Go back to provider selection — restart step 0
                        break
                    elif key == "clear":
                        input_value = ""
                        error = ""
                        verified = None
                    elif key == "backspace":
                        input_value = input_value[:-1]
                        error = ""
                        verified = None
                    elif key.startswith("paste:"):
                        input_value += key[6:]
                        error = ""
                        verified = None
                    elif len(key) == 1 and key.isprintable():
                        input_value += key
                        error = ""
                        verified = None
                    w, h = console.size
                    live.update(
                        _build_input_screen(
                            provider,
                            input_value,
                            width=w,
                            height=h,
                            error=error,
                            verified=verified,
                        )
                    )

                if _step0_done:
                    step = 1
                # else: Esc pressed → loop restarts step 0
                continue

            # ══════════════════════════════════════════════════════════════
            # Step 1: Issue Tracking (Jira / Azure DevOps Boards / Skip)
            # ══════════════════════════════════════════════════════════════
            elif step == 1:
                # Fade out LLM input, fade in issue tracking
                for grey in FADE_OUT_LEVELS:
                    w, h = console.size
                    live.update(_build_input_screen(provider, api_key, width=w, height=h, input_fade=grey))
                    time.sleep(FRAME_TIME_30FPS)

                # vc/vc_token not known yet — pass placeholders
                _dummy_vc = {"env_var": "", "name": ""}
                result = _run_issue_tracking(
                    console,
                    read_key,
                    existing_config,
                    provider,
                    api_key,
                    _dummy_vc,
                    "",
                    live=live,
                )

                if result is not None:
                    _issue_tracking_result = result
                    step = 2
                else:
                    step = 0  # Esc → go back to LLM provider
                continue

            # ══════════════════════════════════════════════════════════════
            # Step 2: Version Control (GitHub PAT)
            # ══════════════════════════════════════════════════════════════
            elif step == 2:
                vc_selected = 0
                vc_n = len(_VC_OPTIONS)
                vc_start = time.monotonic()

                for grey in FADE_IN_LEVELS:
                    w, h = console.size
                    live.update(
                        _build_vc_select_screen(
                            vc_selected,
                            width=w,
                            height=h,
                            fade_style=grey,
                            fade_indices=list(range(vc_n)),
                        )
                    )
                    time.sleep(FRAME_TIME_30FPS)

                # VC selection loop
                _step2_selected = False
                while True:
                    key = read_key(timeout=FRAME_TIME_30FPS) if _supports_timeout else read_key()
                    if key in ("up", "scroll_up"):
                        vc_selected = (vc_selected - 1) % vc_n
                    elif key in ("down", "scroll_down"):
                        vc_selected = (vc_selected + 1) % vc_n
                    elif key == "enter":
                        _step2_selected = True
                        break
                    elif key in ("q", "esc"):
                        break  # go back
                    w, h = console.size
                    tick = time.monotonic() - vc_start
                    live.update(_build_vc_select_screen(vc_selected, width=w, height=h, shimmer_tick=tick))

                if not _step2_selected:
                    step = 1
                    continue

                vc = _VC_OPTIONS[vc_selected]

                # Skip selected — no PAT needed, finish wizard
                if not vc["env_var"]:
                    _issue_tracking_result["vc_env_var"] = ""
                    _issue_tracking_result["vc_token"] = ""
                    disable_bracketed_paste()
                    return _issue_tracking_result

                # Transition: pulse selected, fade others, crossfade to input
                all_vc = list(range(vc_n))
                others_vc = [i for i in all_vc if i != vc_selected]
                base_r, base_g, base_b = COLOR_RGB.get(vc["color"], (180, 180, 180))
                base_style = f"rgb({base_r},{base_g},{base_b})"

                for frame in range(12):
                    t = frame / 11
                    intensity = math.sin(t * math.pi)
                    r = int(base_r + (255 - base_r) * intensity)
                    g = int(base_g + (255 - base_g) * intensity)
                    b = int(base_b + (255 - base_b) * intensity)
                    pulse_style = f"rgb({r},{g},{b})"
                    w, h = console.size
                    live.update(
                        _build_vc_select_screen(
                            vc_selected,
                            width=w,
                            height=h,
                            visible=all_vc,
                            fade_style=pulse_style,
                            fade_indices=[vc_selected],
                        )
                    )
                    time.sleep(FRAME_TIME_30FPS)

                for grey in FADE_OUT_LEVELS:
                    w, h = console.size
                    live.update(
                        _build_vc_select_screen(
                            vc_selected,
                            width=w,
                            height=h,
                            visible=all_vc,
                            fade_style=grey,
                            fade_indices=others_vc,
                            selected_style=base_style,
                        )
                    )
                    time.sleep(FRAME_TIME_30FPS)

                for grey in FADE_IN_LEVELS:
                    w, h = console.size
                    live.update(_build_vc_input_screen(vc, "", width=w, height=h, input_fade=grey))
                    time.sleep(FRAME_TIME_30FPS)
                w, h = console.size
                live.update(_build_vc_input_screen(vc, "", width=w, height=h))

                # PAT token input
                _cfg = existing_config or {}
                vc_input = _cfg.get(vc["env_var"], "")
                vc_error = ""
                vc_verified: bool | None = None
                _step2_done = False

                while True:
                    key = read_key()
                    if key == "enter":
                        if not vc_input.strip():
                            vc_error = f"{vc['env_var']} is required."
                            w, h = console.size
                            live.update(_build_vc_input_screen(vc, vc_input, width=w, height=h, error=vc_error))
                            continue

                        import threading

                        verify_result: list[tuple[bool, str]] = []

                        def _do_vc_verify():
                            verify_result.append(_verify_vc_token(vc, vc_input.strip()))

                        thread = threading.Thread(target=_do_vc_verify, daemon=True)
                        thread.start()

                        pulse_start = time.monotonic()
                        while thread.is_alive():
                            elapsed = time.monotonic() - pulse_start
                            intensity = (math.sin(elapsed * 6) + 1) / 2
                            v = int(60 + 140 * intensity)
                            w, h = console.size
                            live.update(
                                _build_vc_input_screen(
                                    vc,
                                    vc_input,
                                    width=w,
                                    height=h,
                                    verifying=True,
                                    border_override=f"rgb({v},{v},{v})",
                                )
                            )
                            time.sleep(FRAME_TIME_30FPS)

                        thread.join()
                        ok, msg = verify_result[0]
                        vc_verified = ok

                        if ok:
                            green_r, green_g, green_b = 80, 220, 120
                            for frame in range(10):
                                t = frame / 9
                                intensity = math.sin(t * math.pi)
                                r = int(green_r + (255 - green_r) * intensity)
                                g = int(green_g + (255 - green_g) * intensity)
                                b = int(green_b + (255 - green_b) * intensity)
                                w, h = console.size
                                live.update(
                                    _build_vc_input_screen(
                                        vc,
                                        vc_input,
                                        width=w,
                                        height=h,
                                        verified=True,
                                        border_override=f"rgb({r},{g},{b})",
                                    )
                                )
                                time.sleep(FRAME_TIME_30FPS)

                            w, h = console.size
                            live.update(_build_vc_input_screen(vc, vc_input, width=w, height=h, verified=True))
                            time.sleep(0.6)
                            vc_token = vc_input.strip()
                            _save_progress({vc["env_var"]: vc_token})
                            _step2_done = True
                            break
                        else:
                            w, h = console.size
                            live.update(
                                _build_vc_input_screen(
                                    vc,
                                    vc_input,
                                    width=w,
                                    height=h,
                                    verified=False,
                                    error=msg,
                                )
                            )
                        continue

                    elif key == "esc":
                        break  # go back to step 1
                    elif key == "clear":
                        vc_input = ""
                        vc_error = ""
                        vc_verified = None
                    elif key == "backspace":
                        vc_input = vc_input[:-1]
                        vc_error = ""
                        vc_verified = None
                    elif key.startswith("paste:"):
                        vc_input += key[6:]
                        vc_error = ""
                        vc_verified = None
                    elif len(key) == 1 and key.isprintable():
                        vc_input += key
                        vc_error = ""
                        vc_verified = None
                    w, h = console.size
                    live.update(
                        _build_vc_input_screen(
                            vc,
                            vc_input,
                            width=w,
                            height=h,
                            error=vc_error,
                            verified=vc_verified,
                        )
                    )

                if _step2_done:
                    # Build final result — merge issue tracking data with VC
                    _issue_tracking_result["vc_env_var"] = vc["env_var"]
                    _issue_tracking_result["vc_token"] = vc_token
                    disable_bracketed_paste()
                    return _issue_tracking_result
                else:
                    step = 1  # Esc → go back to issue tracking
                    continue

    disable_bracketed_paste()
    return None
