"""Secondary screen builders for mode selection: intake, offline, export, import.

# See README: "Architecture" — this module contains rendering functions
# for the intake mode selection, offline sub-menu, export success,
# import file path input, and project export success screens.
# These are pure functions that return Rich Panel renderables — no I/O or state.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.mode_select.screens._screens import _INTAKE_CARDS, _OFFLINE_CARDS, _build_mode_row
from scrum_agent.ui.shared._components import PAD, planning_title

_PAD = PAD  # alias for backward compatibility within this module


def _build_intake_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible_items: int = -1,
) -> Panel:
    """Build the intake mode selection screen with Planning title pinned at top.

    Shown after the user selects '+ New Project' on the project list.
    Uses the same ASCII art + shimmer + typewriter pattern as the top-level mode screen.
    visible_items: how many intake options to show (-1 = all). For staggered fade-in.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Select intake mode", style="dim", justify="left")

    # Intake option rows — same rendering as mode rows
    show_n = len(_INTAKE_CARDS) if visible_items < 0 else min(visible_items, len(_INTAKE_CARDS))
    body: list = []
    body_h = 0

    for i in range(show_n):
        card = _INTAKE_CARDS[i]
        is_sel = i == selected
        items = _build_mode_row(
            card,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show_n - 1:
            body.append(Text(""))
            body_h += 1

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_offline_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible_items: int = -1,
) -> Panel:
    """Build the offline sub-menu screen with Planning title pinned at top.

    Shown after the user selects 'Offline' on the intake screen.
    Uses the same ASCII art + shimmer + typewriter pattern as the intake mode screen.
    visible_items: how many offline options to show (-1 = all). For staggered reveal.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Offline questionnaire", style="dim", justify="left")

    # Offline option rows — same rendering as mode rows
    show_n = len(_OFFLINE_CARDS) if visible_items < 0 else min(visible_items, len(_OFFLINE_CARDS))
    body: list = []
    body_h = 0

    for i in range(show_n):
        card = _OFFLINE_CARDS[i]
        is_sel = i == selected
        items = _build_mode_row(
            card,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show_n - 1:
            body.append(Text(""))
            body_h += 1

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_export_success_screen(
    file_path: str,
    *,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the export success screen with Planning title pinned at top.

    Shown after a blank questionnaire template is exported.
    Displays confirmation, file path, and a hint to re-run the agent.
    """
    # Planning title pinned at top
    title = planning_title()

    # Success message body
    body: list = []
    body.append(Text(_PAD + "Questionnaire exported", style="bold bright_green", justify="left"))
    body.append(Text(""))
    body.append(Text(_PAD + f"Saved to: {file_path}", style="white", justify="left"))
    body.append(Text(""))
    body.append(
        Text(
            _PAD + "Fill it in at your own pace, then re-run the agent and select Import.",
            style="dim",
            justify="left",
        )
    )
    body.append(Text(""))
    body.append(Text(_PAD + "Press any key to exit.", style="dim", justify="left"))
    body_h = 7

    # Layout: blank + title(2) + blank + [body]
    inner_h = height - 4
    header_h = 4  # blank + title(2) + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_import_screen(
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    placeholder: str = "scrum-questionnaire.md",
) -> Panel:
    """Build the import file path input screen with Planning title pinned at top.

    Shown when the user selects 'Import' from the offline sub-menu.
    Same text input pattern as provider_select.py API key input.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Import questionnaire", style="dim", justify="left")

    # Input box
    box_w = min(70, width - 16)
    box_inner_w = box_w - 2 - 4  # panel border(2) + padding(4)

    if input_value:
        display = input_value + "\u2588"
        text_style = "bold white"
    else:
        display = placeholder + "\u2588"
        text_style = "rgb(80,80,80)"

    avail = box_inner_w - 4
    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(display) <= avail:
        input_content.append("  " + display, style=text_style)
    else:
        visible = display[-(avail - 1) :]
        input_content.append(" \u25c2", style="dim")
        input_content.append(visible, style=text_style)

    if error:
        border_color = "bright_red"
    else:
        border_color = "white"

    input_box = Panel(
        input_content,
        title=" File path ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )

    # Error text
    error_text = Text(_PAD + error, style="bright_red", justify="left") if error else Text("")

    # Hint
    hint = Text(
        _PAD + "Enter path to a filled .md questionnaire file. Press Enter to confirm.",
        style="dim",
        justify="left",
    )

    body: list = [
        Padding(input_box, (0, 0, 0, len(_PAD))),
        error_text,
        Text(""),
        hint,
    ]
    body_h = 8  # input_box(5) + error(1) + blank + hint(1)

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_project_export_success_screen(
    file_path: str,
    *,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the project export success screen.

    Shown after exporting a project's plan as Markdown and HTML.
    Displays confirmation, file paths, and a hint to continue.
    """
    title = planning_title()

    body: list = [
        Text(_PAD + "Plan exported", style="bold bright_green", justify="left"),
        Text(""),
    ]
    for line in file_path.splitlines():
        body.append(Text(_PAD + f"  {line}", style="white", justify="left"))
    body.extend(
        [
            Text(""),
            Text(_PAD + "Press any key to continue.", style="dim", justify="left"),
        ]
    )
    body_h = 3 + len(file_path.splitlines()) + 2

    inner_h = height - 4
    header_h = 4
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
