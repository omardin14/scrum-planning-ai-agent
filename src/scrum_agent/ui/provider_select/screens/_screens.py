"""Screen builder functions for the provider selection wizard.

# See README: "Architecture" — UI rendering layer for the setup wizard.
# Each function builds a Rich renderable for a specific wizard screen.
# These are pure rendering functions with no side effects.
"""

from __future__ import annotations

from typing import Any

from rich.align import Align
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.provider_select._constants import _PROVIDER_CARDS
from scrum_agent.ui.provider_select._verification import _validate_key
from scrum_agent.ui.shared._animations import shimmer_style
from scrum_agent.ui.shared._ascii_font import render_ascii_text

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

_STEPS = ["LLM Provider", "Version Control", "Atlassian"]


def _build_progress(current_step: int) -> Text:
    """Build a progress bar of space-separated filled/empty parallelogram blocks."""
    active_bg = "rgb(60,60,80)"
    done_bg = "rgb(30,80,50)"
    bar = Text(justify="center")
    for i, label in enumerate(_STEPS):
        if i < current_step:
            bar.append("\u259f", style=f"{done_bg} on default")
            bar.append(f" {label} ", style=f"bold white on {done_bg}")
            bar.append("\u259b", style=f"{done_bg} on default")
        elif i == current_step:
            bar.append("\u259f", style=f"{active_bg} on default")
            bar.append(f" {label} ", style=f"bold white on {active_bg}")
            bar.append("\u259b", style=f"{active_bg} on default")
        else:
            dim_bg = "rgb(35,35,45)"
            bar.append("\u259f", style=f"{dim_bg} on default")
            bar.append(f" {label} ", style=f"dim on {dim_bg}")
            bar.append("\u259b", style=f"{dim_bg} on default")
        if i < len(_STEPS) - 1:
            bar.append("  ")
    return bar


def _build_provider_row(
    provider: dict[str, Any], *, selected: bool, override_style: str = "", shimmer_tick: float = 0.0
) -> Text:
    """Render a provider name as two-line ASCII art text."""
    lines = render_ascii_text(provider["name"])
    rendered = Text(justify="center")

    if override_style:
        rendered.append(lines[0] + "\n", style=override_style)
        rendered.append(lines[1], style=override_style)
    elif selected:
        # Per-character shimmer effect on selected item
        total = max(len(lines[0]), len(lines[1]))
        for i, ch in enumerate(lines[0]):
            rendered.append(ch, style=shimmer_style(provider["color"], i, total, shimmer_tick))
        rendered.append("\n")
        for i, ch in enumerate(lines[1]):
            rendered.append(ch, style=shimmer_style(provider["color"], i, total, shimmer_tick))
    else:
        rendered.append(lines[0] + "\n", style="dim")
        rendered.append(lines[1], style="dim")

    return rendered


def _build_screen_frame(
    *,
    subtitle: str,
    step: int,
    body_items: list,
    body_height: int,
    width: int = 80,
    height: int = 24,
    title_text: str = "",
) -> Panel:
    """Shared screen frame: ASCII title at top, subtitle + progress at bottom.

    body_items: list of Rich renderables to vertically centre in the middle.
    body_height: estimated line count of body_items (for centering math).
    title_text: text to render as ASCII art title. Defaults to current step name.
    """
    import rich.box
    from rich.console import Group

    display_title = title_text or "Setup Wizard"
    ascii_lines = render_ascii_text(display_title)
    title = Text(justify="center")
    title.append(ascii_lines[0] + "\n", style="bold white")
    title.append(ascii_lines[1], style="bold white")

    sub = Text(subtitle, style="dim", justify="center")
    progress = _build_progress(current_step=step)

    inner_h = height - 4  # panel border + padding
    header_h = 5  # 2-line ASCII title + blank + subtitle + blank
    footer_h = 2  # blank + progress
    middle_h = max(0, inner_h - header_h - footer_h)
    mid_top = max(0, (middle_h - body_height) // 2)
    mid_bot = max(0, middle_h - body_height - mid_top)

    content = Group(
        Align.center(title),
        Text(""),
        Align.center(sub),
        Text(""),
        *[Text("") for _ in range(mid_top)],
        *body_items,
        *[Text("") for _ in range(mid_bot)],
        Text(""),
        Align.center(progress),
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_select_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    visible: list[int] | None = None,
    step: int = 0,
    fade_style: str = "",
    fade_indices: list[int] | None = None,
    shimmer_tick: float = 0.0,
    selected_style: str = "",
) -> Panel:
    """Build the provider selection screen."""
    show = visible if visible is not None else list(range(len(_PROVIDER_CARDS)))
    fading = fade_indices or []

    rows: list[Text] = []
    for i, p in enumerate(_PROVIDER_CARDS):
        if i in show:
            if i == selected and selected_style:
                override = selected_style
            elif i in fading and fade_style:
                override = fade_style
            else:
                override = ""
            rows.append(
                _build_provider_row(
                    p,
                    selected=(i == selected),
                    override_style=override,
                    shimmer_tick=shimmer_tick,
                )
            )

    body = [item for row in rows for item in (Align.center(row), Text(""))]
    if body:
        body = body[:-1]  # remove trailing blank
    body_h = len(rows) * 3 - 1 if rows else 0

    return _build_screen_frame(
        subtitle="Select your LLM provider",
        step=step,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
    )


def _build_input_screen(
    provider: dict[str, Any],
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    masked: bool = True,
    verified: bool | None = None,
    verifying: bool = False,
    input_fade: str = "",
    border_override: str = "",
) -> Panel:
    """Build the API key input screen.

    verified: None=not checked, True=verified OK, False=verification failed.
    verifying: True while the verification API call is in progress.
    input_fade: override style for fade-in animation on the input elements.
    """
    import rich.box

    # Selected provider in ASCII art
    style = provider["color"]
    lines = render_ascii_text(provider["name"])
    provider_text = Text(justify="center")
    provider_text.append(lines[0] + "\n", style=style)
    provider_text.append(lines[1], style=style)

    # Instructions
    instr_style = input_fade if input_fade else "dim"
    instructions = Text(provider["instructions"], style=instr_style, justify="center")

    # Realtime format validation
    status, validation_hint = _validate_key(provider, input_value)

    # Input box content — env var label goes in the panel border title.
    # Scroll: only show the rightmost chars that fit in one line.
    box_inner_w = min(70, width - 10) - 2 - 4  # panel border(2) + padding(4)
    display_val = "\u2022" * len(input_value) if masked else input_value
    cursor = "\u2588" if not verifying else ""
    full_text = display_val + cursor
    avail = box_inner_w - 4  # reserve space for overflow indicators + padding
    text_style = input_fade if input_fade else "bold white"
    dim_style = input_fade if input_fade else "dim"

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(full_text) <= avail:
        input_content.append("  " + full_text, style=text_style)
    else:
        visible = full_text[-(avail - 1) :]
        input_content.append(" \u25c2", style=dim_style)
        input_content.append(visible, style=text_style)

    # Border colour logic
    if border_override:
        border_color = border_override
    elif input_fade:
        border_color = input_fade
    elif verified is True:
        border_color = "bright_green"
    elif verified is False or error:
        border_color = "bright_red"
    else:
        border_color = "white"

    input_box = Panel(
        input_content,
        title=f" {provider['env_var']} ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=min(70, width - 10),
    )

    # Status line below input
    if verifying:
        status_text = Text("")
    elif verified is True:
        status_text = Text("")
    elif verified is False:
        status_text = Text(f"\u2717 {error}", style="bright_red", justify="center")
    elif validation_hint and input_value and status in ("bad_prefix", "too_short"):
        hint_style = "bright_red" if status == "bad_prefix" else "yellow"
        status_text = Text(validation_hint, style=input_fade or hint_style, justify="center")
    else:
        status_text = Text("")

    # Error (only for non-validation errors like empty submit)
    if error and verified is None:
        error_text = Text(error, style="bright_red", justify="center")
    else:
        error_text = Text("")

    body = [
        Align.center(provider_text),
        Text(""),
        Align.center(instructions),
        Text(""),
        Align.center(input_box),
        Align.center(status_text),
        Align.center(error_text),
    ]
    body_h = 10  # provider(2) + blank + instructions(1) + blank + input_box(5) + status + error

    return _build_screen_frame(
        subtitle="Enter your API key",
        step=0,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
    )
