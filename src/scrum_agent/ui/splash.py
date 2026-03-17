"""Startup splash animation — "SCRUM AGENT" fades in then out.

# See README: "Architecture" — the splash is a CLI-layer component shown
# before the setup wizard or mode selection screen. It replaces the static
# welcome panel as the first branded intro users see.

Animation sequence (~2s total):
  Phase 1 — Fade in:  text appears from nothing → brand blue (~0.8s).
  Phase 2 — Hold:     full brightness (~0.4s).
  Phase 3 — Fade out: brand blue → nothing (~0.8s).
"""

from __future__ import annotations

import time

import rich.box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.shared._ascii_font import render_ascii_text

# ---------------------------------------------------------------------------
# Animation constants
# ---------------------------------------------------------------------------

_FRAME_TIME = 1.0 / 60  # ~60fps target

# Brand blue — same as mode_select._COLOR_RGB
_BRAND_RGB = (70, 100, 180)


# ---------------------------------------------------------------------------
# Easing
# ---------------------------------------------------------------------------


def _ease_out_cubic(t: float) -> float:
    """Cubic ease-out: fast start, smooth deceleration."""
    return 1 - (1 - t) ** 3


# ---------------------------------------------------------------------------
# Frame builder
# ---------------------------------------------------------------------------


def _build_splash_frame(
    text_lines: list[str],
    *,
    width: int,
    height: int,
    opacity: float = 1.0,
) -> Panel:
    """Build a single splash animation frame.

    text_lines: two-line ASCII art (from render_ascii_text).
    opacity: 0.0–1.0 controls visibility. At 0 the text is invisible
        (spaces only) so it blends with any terminal background. At 1 the
        text is full brand-blue.
    """
    # At very low opacity, replace characters with spaces so nothing is
    # visible — avoids a near-black colour standing out against the
    # terminal background regardless of its colour scheme.
    if opacity < 0.01:
        rendered = Text(justify="center")
        for line_idx, line in enumerate(text_lines):
            rendered.append(" " * len(line))
            if line_idx < len(text_lines) - 1:
                rendered.append("\n")
    else:
        r = int(_BRAND_RGB[0] * opacity)
        g = int(_BRAND_RGB[1] * opacity)
        b = int(_BRAND_RGB[2] * opacity)
        style = f"bold rgb({r},{g},{b})"

        rendered = Text(justify="center")
        for line_idx, line in enumerate(text_lines):
            rendered.append(line, style=style)
            if line_idx < len(text_lines) - 1:
                rendered.append("\n")

    # Centre vertically inside the panel
    inner_h = height - 4  # panel border + padding
    block_h = len(text_lines)
    top_pad = max(0, (inner_h - block_h) // 2)
    bot_pad = max(0, inner_h - block_h - top_pad)

    content = Group(
        *[Text("") for _ in range(top_pad)],
        rendered,
        *[Text("") for _ in range(bot_pad)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def show_splash(console: Console) -> None:
    """Show the startup splash animation (~2s). Non-interactive, timed.

    # See README: "Architecture" — this replaces _build_welcome_panel() as
    # the first thing users see. "SCRUM AGENT" fades in from nothing, holds
    # briefly, then fades back out to nothing.

    Alt-screen management: we enter the alternate screen buffer manually
    before starting the animation and intentionally leave it active when
    the splash ends. The next fullscreen UI (setup wizard or mode-select)
    uses Live(screen=True) which re-enters alt-screen seamlessly. This
    avoids the visible flicker that would occur if the splash exited
    alt-screen and the next UI immediately re-entered it.
    """
    text_lines = render_ascii_text("SCRUM AGENT")
    w, h = console.size

    # Phase durations (in frames at ~60fps)
    fade_in_frames = 48  # ~0.8s
    hold_frames = 24  # ~0.4s
    fade_out_frames = 48  # ~0.8s

    # Enter alt-screen once — stays active through to the next fullscreen UI.
    # Live is created without screen=True so it doesn't toggle alt-screen
    # on enter/exit, eliminating the flicker between screens.
    console.set_alt_screen(True)
    console.clear()

    with Live(
        _build_splash_frame(text_lines, width=w, height=h, opacity=0.0),
        console=console,
        refresh_per_second=60,
        screen=False,
    ) as live:
        # Phase 1 — Fade in: nothing → brand blue
        for frame in range(fade_in_frames):
            t = _ease_out_cubic(frame / max(fade_in_frames - 1, 1))
            w, h = console.size
            live.update(_build_splash_frame(text_lines, width=w, height=h, opacity=t))
            time.sleep(_FRAME_TIME)

        # Phase 2 — Hold at full brightness
        w, h = console.size
        live.update(_build_splash_frame(text_lines, width=w, height=h, opacity=1.0))
        time.sleep(_FRAME_TIME * hold_frames)

        # Phase 3 — Fade out: brand blue → nothing
        for frame in range(fade_out_frames):
            t = 1.0 - _ease_out_cubic(frame / max(fade_out_frames - 1, 1))
            w, h = console.size
            live.update(_build_splash_frame(text_lines, width=w, height=h, opacity=t))
            time.sleep(_FRAME_TIME)

    # Alt-screen is intentionally left active — the next Live(screen=True)
    # in wizard or mode-select will take over without a visible gap.
