"""Screen builders for version control and issue tracking wizard steps.

# See README: "Architecture" — UI rendering layer for the setup wizard.
# Each function builds a Rich renderable for a specific wizard screen.
# These are pure rendering functions with no side effects.
"""

from __future__ import annotations

from typing import Any

from rich.align import Align
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.provider_select._constants import _ISSUE_TRACKING_FIELDS, _VC_OPTIONS
from scrum_agent.ui.provider_select.screens._screens import _build_provider_row, _build_screen_frame
from scrum_agent.ui.shared._ascii_font import render_ascii_text


def _build_vc_select_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    visible: list[int] | None = None,
    fade_style: str = "",
    fade_indices: list[int] | None = None,
    shimmer_tick: float = 0.0,
    selected_style: str = "",
) -> Panel:
    """Build the version control provider selection screen."""
    show = visible if visible is not None else list(range(len(_VC_OPTIONS)))
    fading = fade_indices or []

    rows: list[Text] = []
    for i, vc in enumerate(_VC_OPTIONS):
        if i in show:
            if i == selected and selected_style:
                override = selected_style
            elif i in fading and fade_style:
                override = fade_style
            else:
                override = ""
            rows.append(
                _build_provider_row(
                    vc,
                    selected=(i == selected),
                    override_style=override,
                    shimmer_tick=shimmer_tick,
                )
            )

    body = [item for row in rows for item in (Align.center(row), Text(""))]
    if body:
        body = body[:-1]
    body_h = len(rows) * 3 - 1 if rows else 0

    return _build_screen_frame(
        subtitle="Version Control",
        step=1,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
    )


def _build_vc_input_screen(
    vc: dict[str, Any],
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    verified: bool | None = None,
    verifying: bool = False,
    input_fade: str = "",
    border_override: str = "",
) -> Panel:
    """Build the PAT token input screen for version control."""
    import rich.box

    style = vc["color"]
    lines = render_ascii_text(vc["name"])
    vc_text = Text(justify="center")
    vc_text.append(lines[0] + "\n", style=style)
    vc_text.append(lines[1], style=style)

    instr_style = input_fade if input_fade else "dim"
    instructions = Text(vc["instructions"], style=instr_style, justify="center")

    box_inner_w = min(70, width - 10) - 2 - 4
    display_val = "\u2022" * len(input_value)
    cursor = "\u2588" if not verifying else ""
    full_text = display_val + cursor
    avail = box_inner_w - 4
    text_style = input_fade if input_fade else "bold white"
    dim_style = input_fade if input_fade else "dim"

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(full_text) <= avail:
        input_content.append("  " + full_text, style=text_style)
    else:
        visible = full_text[-(avail - 1) :]
        input_content.append(" \u25c2", style=dim_style)
        input_content.append(visible, style=text_style)

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
        title=f" {vc['env_var']} ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=min(70, width - 10),
    )

    if verified is False:
        status_text = Text(f"\u2717 {error}", style="bright_red", justify="center")
    elif error:
        status_text = Text(error, style="bright_red", justify="center")
    else:
        status_text = Text("")

    body = [
        Align.center(vc_text),
        Text(""),
        Align.center(instructions),
        Text(""),
        Align.center(input_box),
        Align.center(status_text),
    ]
    body_h = 10

    return _build_screen_frame(
        subtitle="Enter your PAT token",
        step=1,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
    )


def _build_field_box(
    field: dict[str, Any],
    value: str,
    *,
    is_active: bool,
    box_w: int,
    error: str = "",
    verified_flag: bool | None = None,
    border_override: str = "",
    fade_style: str = "",
) -> Panel:
    """Build a single input field box."""
    import rich.box

    # Display text — active fields NEVER show placeholder, only value + cursor.
    # Inactive fields show value if present, otherwise placeholder in dark grey.
    if is_active:
        if field["masked"] and value:
            display = "\u2022" * len(value)
        else:
            display = value
        display += "\u2588"
        text_style = fade_style if fade_style else "bold white"
    elif value:
        display = "\u2022" * len(value) if field["masked"] else value
        text_style = fade_style if fade_style else "dim white"
    else:
        display = field.get("placeholder", "")
        text_style = fade_style if fade_style else "rgb(40,40,40)"

    inner_w = box_w - 2 - 4 - 4
    dim_style = fade_style if fade_style else "dim"

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(display) <= inner_w:
        input_content.append("  " + display, style=text_style)
    else:
        visible = display[-(inner_w - 1) :]
        input_content.append(" \u25c2", style=dim_style)
        input_content.append(visible, style=text_style)

    # Border colour
    if border_override:
        border_color = border_override
    elif fade_style:
        border_color = fade_style
    elif verified_flag is True:
        border_color = "bright_green"
    elif error:
        border_color = "bright_red"
    elif is_active:
        border_color = "white"
    else:
        border_color = "rgb(60,60,60)"

    label = f" {field['label']} "
    if not field["required"]:
        label = f" {field['label']} (optional) "

    return Panel(
        input_content,
        title=label,
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )


def _build_issue_tracking_screen(
    selected: int,
    values: dict[int, str],
    *,
    width: int = 80,
    height: int = 24,
    scroll_offset: int = 0,
    errors: dict[int, str] | None = None,
    verified: dict[int, bool] | None = None,
    border_overrides: dict[int, str] | None = None,
    fade_style: str = "",
    fields: list[dict[str, Any]] | None = None,
    subtitle: str = "Issue Tracking",
) -> Panel:
    """Build the issue tracking multi-field form screen with viewport scrolling.

    scroll_offset: index of the first visible field (0-based).
    Fields that don't fit in the available height are clipped; scroll indicators
    (^/v) show when there's content above or below.
    fields: optional field definitions to use instead of the default Jira fields.
    subtitle: label shown in the screen header (e.g. "Jira", "Azure DevOps Boards").
    """
    errors = errors or {}
    verified = verified or {}
    border_overrides = border_overrides or {}
    active_fields = fields if fields is not None else _ISSUE_TRACKING_FIELDS

    box_w = min(70, width - 10)
    field_h = 5  # each field box is 5 lines tall (padding 1 + content 1 + padding 1 + 2 border)

    # Calculate available height for fields:
    # inner_h = height - 4 (panel border+padding)
    # header = 3 (ASCII title + blank), footer = 4 (subtitle + blank + progress + blank)
    # scroll indicators = 2 (top + bottom, reserved always for consistency)
    inner_h = height - 4
    chrome_h = 3 + 4 + 2  # header + footer + scroll indicator lines
    fields_available_h = max(field_h, inner_h - chrome_h)
    max_visible = fields_available_h // field_h

    n = len(active_fields)

    # Clamp scroll_offset so selected field is always visible
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + max_visible:
        scroll_offset = selected - max_visible + 1
    scroll_offset = max(0, min(scroll_offset, n - max_visible))

    visible_end = min(scroll_offset + max_visible, n)

    body: list = []
    body_h = 0

    # Visible fields
    for vi, i in enumerate(range(scroll_offset, visible_end)):
        field = active_fields[i]
        is_active = i == selected
        val = values.get(i, "")
        err = errors.get(i, "")

        input_box = _build_field_box(
            field,
            val,
            is_active=is_active,
            box_w=box_w,
            error=err,
            verified_flag=verified.get(i),
            border_override=border_overrides.get(i, ""),
            fade_style=fade_style,
        )

        body.append(Align.center(input_box))
        body_h += field_h

        # Error text
        if err and is_active:
            body.append(Text(f"  {err}", style="bright_red", justify="center"))
            body_h += 1

    return _build_screen_frame(
        subtitle=subtitle,
        step=2,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
    )
