"""Screen builder functions for the mode selection flow.

# See README: "Architecture" — this module contains the rendering functions
# for the mode selection, intake, offline, export, import, and delete screens.
# These are pure functions that return Rich Panel renderables — no I/O or state.
"""

from __future__ import annotations

from typing import Any

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.shared._animations import COLOR_RGB, shimmer_style
from scrum_agent.ui.shared._ascii_font import render_ascii_text
from scrum_agent.ui.shared._components import PAD

# ---------------------------------------------------------------------------
# Mode definitions
# ---------------------------------------------------------------------------

_MODE_CARDS: list[dict[str, Any]] = [
    {
        "key": "project-planning",
        "title": "Planning",
        "description": "Decompose your project into epics, user stories, tasks, and a sprint plan.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
    {
        "key": "team-analysis",
        "title": "Analysis",
        "description": "Analyse your team's board to learn velocity, estimation patterns, and delivery signals.",
        "available": True,
        "color": "rgb(100,180,100)",
    },
]

# ---------------------------------------------------------------------------
# Intake mode definitions — shown when the user selects "+ New Project"
# ---------------------------------------------------------------------------

_INTAKE_CARDS: list[dict[str, Any]] = [
    {
        "key": "smart",
        "title": "Smart",
        "description": "Recommended — I'll extract answers from your description and only ask 2-4 follow-ups.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
    {
        "key": "offline",
        "title": "Offline",
        "description": "Export a blank template to fill in at your own pace, or import a completed one.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
]

# ---------------------------------------------------------------------------
# Offline sub-menu definitions — shown when user selects "Offline" intake
# ---------------------------------------------------------------------------

_OFFLINE_CARDS: list[dict[str, Any]] = [
    {
        "key": "export",
        "title": "Export",
        "description": "Save a blank template to scrum-questionnaire.md — fill it in at your own pace.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
    {
        "key": "import",
        "title": "Import",
        "description": "Load a completed questionnaire and jump straight to review.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
]

_PAD = PAD  # alias for backward compatibility within this module

# ---------------------------------------------------------------------------
# Rendering helpers — mode selection
# ---------------------------------------------------------------------------


def _build_mode_row(
    mode: dict[str, Any],
    *,
    selected: bool,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    override_style: str = "",
) -> list:
    """Render a mode as ASCII art title + optional description underneath.

    Returns a list of Rich renderables (1–3 items depending on state).
    desc_reveal: float — the fractional part fades in the next character for
        a smoother typewriter effect (e.g. 5.4 = 5 solid chars + 1 at 40% opacity).
    """
    available = mode["available"]
    color = mode["color"]
    lines = render_ascii_text(mode["title"])

    rendered = Text(justify="left")

    if override_style:
        rendered.append(_PAD + lines[0] + "\n", style=override_style)
        rendered.append(_PAD + lines[1], style=override_style)
    elif selected and available:
        total = max(len(lines[0]), len(lines[1]))
        rendered.append(_PAD)
        for i, ch in enumerate(lines[0]):
            rendered.append(ch, style=shimmer_style(color, i, total, shimmer_tick))
        rendered.append("\n" + _PAD)
        for i, ch in enumerate(lines[1]):
            rendered.append(ch, style=shimmer_style(color, i, total, shimmer_tick))
    elif selected and not available:
        rendered.append(_PAD + lines[0] + "\n", style="rgb(90,90,100)")
        rendered.append(_PAD + lines[1], style="rgb(90,90,100)")
    else:
        # Unselected: use a muted version of the mode's accent color
        r, g, b = COLOR_RGB.get(color, (100, 100, 120))
        _dim_r, _dim_g, _dim_b = r // 3, g // 3, b // 3
        _unsel_style = f"rgb({_dim_r},{_dim_g},{_dim_b})"
        rendered.append(_PAD + lines[0] + "\n", style=_unsel_style)
        rendered.append(_PAD + lines[1], style=_unsel_style)

    items: list = [rendered]

    # Always reserve space for description on the selected item to prevent
    # layout jumps when switching selection.
    if selected:
        desc_text = Text(justify="left")
        if desc_reveal > 0:
            desc_full = mode["description"]
            solid_count = int(desc_reveal)
            frac = desc_reveal - solid_count  # 0.0–1.0 fade for next char

            # Fully revealed characters
            solid = desc_full[:solid_count]

            if available:
                desc_text.append(_PAD + solid, style="white")
                # Sub-character fade: partially reveal the next character
                if frac > 0 and solid_count < len(desc_full):
                    gray = int(255 * frac)
                    desc_text.append(desc_full[solid_count], style=f"rgb({gray},{gray},{gray})")
            else:
                desc_text.append(_PAD + solid, style="rgb(70,70,80)")

            if not available and solid_count >= len(desc_full):
                desc_text.append("  (coming soon)", style="rgb(60,60,70)")

        items.append(Text(""))
        items.append(desc_text)

    return items


def _build_mode_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible: list[int] | None = None,
    fade_style: str = "",
    fade_indices: list[int] | None = None,
    selected_style: str = "",
) -> Panel:
    """Build the full-screen mode selection layout."""
    show = visible if visible is not None else list(range(len(_MODE_CARDS)))
    fading = fade_indices or []

    # Mode rows
    body: list = []
    body_h = 0
    for i, mode in enumerate(_MODE_CARDS):
        if i not in show:
            continue
        is_sel = i == selected

        if i in fading and fade_style:
            override = fade_style
        elif i == selected and selected_style:
            override = selected_style
        else:
            override = ""

        items = _build_mode_row(
            mode,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
            override_style=override,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show[-1]:
            body.append(Text(""))
            body_h += 1

    inner_h = height - 4
    mid_top = max(0, (inner_h - body_h) // 2)
    mid_bot = max(0, inner_h - body_h - mid_top)

    content = Group(
        *[Text("") for _ in range(mid_top)],
        *body,
        *[Text("") for _ in range(mid_bot)],
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
# Rendering helpers — slide transition
# ---------------------------------------------------------------------------


def _build_slide_frame(
    mode: dict[str, Any],
    *,
    top_offset: int,
    width: int = 80,
    height: int = 24,
    style: str = "",
) -> Panel:
    """Render a mode title at a given vertical offset inside the frame.

    Used to animate the Planning title sliding from center to top.
    The description is intentionally not shown — it disappears on selection.
    top_offset: number of blank lines above the title (0 = pinned at top).
    """
    lines = render_ascii_text(mode["title"])
    title_style = style or "bold white"

    rendered = Text(justify="left")
    rendered.append(_PAD + lines[0] + "\n", style=title_style)
    rendered.append(_PAD + lines[1], style=title_style)

    inner_h = height - 4
    block_h = 2  # title(2) only — description is not shown during slide
    below = max(0, inner_h - top_offset - block_h)

    content = Group(
        *[Text("") for _ in range(top_offset)],
        rendered,
        *[Text("") for _ in range(below)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
