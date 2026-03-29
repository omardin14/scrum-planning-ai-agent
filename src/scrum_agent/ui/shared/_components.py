"""Shared UI constants and reusable components for the TUI screens.

# See README: "Architecture" — shared UI component layer.
# Provides Theme dataclass, action buttons, scrollbar, progress dots,
# viewport helpers, and popup builder — used across all TUI screens
# for visual consistency. Think of these as React-like primitives.
"""

from __future__ import annotations

from dataclasses import dataclass

import rich.box
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.shared._animations import COLOR_RGB
from scrum_agent.ui.shared._ascii_font import render_ascii_text

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

PAD = "    "


# ---------------------------------------------------------------------------
# Theme — centralised color palette for both modes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """Color palette for TUI screens. Use ANALYSIS_THEME or PLANNING_THEME."""

    accent: str = "rgb(100,180,100)"
    accent_bright: str = "rgb(80,220,120)"
    muted: str = "rgb(120,120,140)"
    value: str = "bold white"
    good: str = "rgb(80,220,120)"
    warn: str = "rgb(220,180,60)"
    bad: str = "rgb(220,80,80)"
    dim: str = "dim"
    sep: str = "rgb(50,60,80)"
    id: str = "cyan"
    desc: str = "rgb(160,160,160)"


ANALYSIS_THEME = Theme()
PLANNING_THEME = Theme(accent="rgb(110,140,220)", accent_bright="rgb(140,170,255)")

# Button color scheme: (accent_border, accent_label, grey_border, grey_label)
_BTN_COLORS: dict[str, tuple[str, str, str, str]] = {
    "Accept": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Done": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Continue": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Edit": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Regenerate": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Export": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Jira": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Azure DevOps": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
}
_BTN_DEFAULT = ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)")
_BTN_MIN_W = 12
_BTN_GAP = 2

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def center_label(label: str, width: int) -> str:
    """Center a label string within the given width, padding with spaces.

    Previously duplicated in _project_cards.py and _screens.py.
    """
    pad_l = (width - len(label)) // 2
    pad_r = width - len(label) - pad_l
    return " " * pad_l + label + " " * pad_r


def planning_title() -> Text:
    """Return the Planning ASCII title styled with the brand colour.

    # See README: "Architecture" — the "Planning" header is pinned at the
    # top of every screen in the planning flow. This was previously defined
    # inline in 4+ functions and as _planning_title() in session/_screens.py.
    """
    ascii_lines = render_ascii_text("Planning")
    base_r, base_g, base_b = COLOR_RGB.get("rgb(110,140,220)", (110, 140, 220))
    title_style = f"bold rgb({base_r},{base_g},{base_b})"
    title = Text(justify="left")
    title.append(PAD + ascii_lines[0] + "\n", style=title_style)
    title.append(PAD + ascii_lines[1], style=title_style)
    return title


def analysis_title() -> Text:
    """Return the Analysis ASCII title styled with the green accent colour."""
    ascii_lines = render_ascii_text("Analysis")
    base_r, base_g, base_b = COLOR_RGB.get("rgb(100,180,100)", (100, 180, 100))
    title_style = f"bold rgb({base_r},{base_g},{base_b})"
    title = Text(justify="left")
    title.append(PAD + ascii_lines[0] + "\n", style=title_style)
    title.append(PAD + ascii_lines[1], style=title_style)
    return title


def build_popup(
    message: str,
    *,
    width: int = 50,
    border_style: str = "rgb(220,60,60)",
) -> Panel:
    """Build a popup rectangle for confirmation dialogs.

    Returns a rounded Panel that slides up from the bottom of the screen,
    matching the slide animation pattern used by _build_slide_frame.
    The popup is 5 rows tall (border + padding + message + padding + border)
    for a balanced visual appearance.

    Args:
        message: The text to display inside the popup.
        width: Total width of the popup panel.
        border_style: Rich style string for the panel border.
    """
    content = Text(message, style="bold white", justify="center")
    return Panel(
        content,
        border_style=border_style,
        box=rich.box.ROUNDED,
        width=width,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Reusable UI primitives
# ---------------------------------------------------------------------------


def build_action_buttons(
    actions: list[str],
    selected: int,
    *,
    pad: str = PAD,
) -> tuple[Text, Text, Text]:
    """Build the 3 Text lines (top/mid/bot) for a row of action buttons.

    Each button is a rounded box-drawing rectangle. The *selected* button
    gets its accent color; others are greyed out.

    Returns (btn_top, btn_mid, btn_bot) — three Text objects to append to a Group.
    """
    btn_top = Text(pad, justify="left")
    btn_mid = Text(pad, justify="left")
    btn_bot = Text(pad, justify="left")

    for i, label in enumerate(actions):
        if i > 0:
            btn_top.append(" " * _BTN_GAP)
            btn_mid.append(" " * _BTN_GAP)
            btn_bot.append(" " * _BTN_GAP)

        inner_w = max(_BTN_MIN_W - 2, len(label) + 2)
        pad_l = (inner_w - len(label)) // 2
        pad_r = inner_w - len(label) - pad_l
        centered = " " * pad_l + label + " " * pad_r

        accent_b, accent_l, grey_b, grey_l = _BTN_COLORS.get(label, _BTN_DEFAULT)
        if i == selected:
            b_style, l_style = accent_b, f"bold {accent_l}"
        else:
            b_style, l_style = grey_b, grey_l

        btn_top.append("\u256d" + "\u2500" * inner_w + "\u256e", style=b_style)
        btn_mid.append("\u2502" + centered + "\u2502", style=l_style)
        btn_bot.append("\u2570" + "\u2500" * inner_w + "\u256f", style=b_style)

    return btn_top, btn_mid, btn_bot


def build_scrollbar(viewport_h: int, total_lines: int, scroll_offset: int, max_scroll: int) -> Text | None:
    """Build a scrollbar Text column, or None if content fits.

    Returns a Text object with viewport_h rows of thin/thick vertical bars,
    or None if total_lines <= viewport_h (no scrollbar needed).
    """
    if total_lines <= viewport_h:
        return None

    thumb_size = max(1, round(viewport_h * viewport_h / max(total_lines, 1)))
    thumb_pos = round(scroll_offset / max(max_scroll, 1) * (viewport_h - thumb_size)) if max_scroll > 0 else 0

    sb = Text(justify="left")
    for i in range(viewport_h):
        is_thumb = thumb_pos <= i < thumb_pos + thumb_size
        if is_thumb:
            sb.append("\u2503\n", style="rgb(100,100,120)")
        else:
            sb.append("\u2502\n", style="rgb(40,40,50)")
    return sb


def build_progress_dots(
    stages: list[str],
    current: int,
    *,
    pad: str = PAD,
    theme: Theme | None = None,
) -> Text:
    """Build a progress indicator: ● Instructions  ● Epic  ○ Stories ...

    Filled dots for completed stages, bright dot for current, hollow for future.
    """
    _theme = theme or ANALYSIS_THEME
    progress = Text(pad, justify="left")
    for i, stage_name in enumerate(stages):
        if i > 0:
            progress.append("  ", style="dim")
        if i < current:
            progress.append("\u25cf", style=_theme.accent)
        elif i == current:
            progress.append("\u25cf", style=_theme.accent_bright)
        else:
            progress.append("\u25cb", style="rgb(60,60,70)")
        progress.append(f" {stage_name}", style="dim" if i != current else "bold white")
    return progress


def calc_viewport(height: int, *, header_h: int = 7, action_h: int = 4) -> int:
    """Calculate viewport height from terminal height.

    Accounts for panel border (2) + padding (2) = 4 rows overhead,
    then subtracts header and action areas.
    """
    inner_h = height - 4
    return max(3, inner_h - header_h - action_h)
