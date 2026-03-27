"""Shared UI constants and reusable components for the TUI screens.

# See README: "Architecture" — shared UI component layer.
# Consolidates duplicated constants (_PAD, center_label, planning_title)
# that were previously defined independently in multiple screen modules.
# Also provides the build_popup() helper for confirmation dialogs.
"""

from __future__ import annotations

import rich.box
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.shared._animations import COLOR_RGB
from scrum_agent.ui.shared._ascii_font import render_ascii_text

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Left indent for visual balance inside full-screen panels.
# Previously duplicated in _project_cards.py, _screens.py,
# session/_screens.py, and session/__init__.py.
PAD = "    "

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
    base_r, base_g, base_b = COLOR_RGB.get("rgb(70,100,180)", (70, 100, 180))
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
