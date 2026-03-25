"""Project list screen and project row composition.

# See README: "Architecture" — this module composes project cards with
# action buttons into rows, and builds the full project list screen
# with viewport scrolling, delete popup overlay, and "+ New Project" button.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from scrum_agent.ui.mode_select.screens._project_cards import (
    _BTN_W,
    _CARD_H,
    _CARD_SPACING,
    _EXPORT_SUB_BTN_W,
    _PEEK_H,
    _build_action_button,
    _build_empty_state_card,
    _build_new_project_card,
    _build_peek_above,
    _build_peek_below,
    _build_project_card,
    _compute_viewport,
)
from scrum_agent.ui.shared._animations import BLACK_RGB, lerp_color
from scrum_agent.ui.shared._components import PAD, planning_title

_PAD = PAD  # alias for backward compatibility within this module


def _build_project_row(
    project,
    *,
    selected: bool,
    focus: int = 0,
    box_w: int = 48,
    opacity: float = 1.0,
    del_fade: float = 0.0,
    exp_fade: float = 0.0,
    card_fade: float = 0.0,
    pulse: float = 0.0,
    action_btns_visible: float = 0.0,
    show_export_submenu: bool = False,
    submenu_sel: int = 0,
    submenu_html_fade: float = 0.0,
    submenu_md_fade: float = 0.0,
    submenu_jira_fade: float = 0.0,
    submenu_azdevops_fade: float = 0.0,
    submenu_visible: float = 0.0,
    jira_enabled: bool = True,
    azdevops_enabled: bool = False,
) -> Table:
    """Build a project card with optional Delete/Export buttons to its right.

    # See README: "Architecture" — each project row is a horizontal grid:
    # [project card] [Delete btn] [Export btn], all the same height.
    # Buttons only appear on the selected row and stagger in one by one.
    # When Export is activated, three sub-buttons [HTML] [Markdown] [Jira]
    # fade in to the right.

    action_btns_visible: 0.0-2.0 stagger-reveal for Delete (0->1) and Export (1->2).
    focus: 0 = card focused, 1 = Delete focused, 2 = Export focused.
    show_export_submenu: when True, HTML, Markdown, and Jira buttons appear.
    submenu_sel: 0 = HTML, 1 = Markdown, 2 = Jira (which button is selected).
    """
    card = _build_project_card(
        project,
        selected=selected,
        box_w=box_w,
        opacity=opacity,
        card_fade=card_fade,
        pulse=pulse,
    )

    row = Table.grid(padding=(0, 1, 0, 0), pad_edge=False)
    row.add_column(width=box_w)

    # Staggered reveal of action buttons — Delete appears first, then Export.
    # action_btns_visible (0->2) controls staggered opacity per button.
    # Non-selected rows get action_btns_visible=0 so no buttons render.
    del_opacity = min(1.0, max(0.0, action_btns_visible))
    exp_opacity = min(1.0, max(0.0, action_btns_visible - 1.0))

    action_btns: list = []
    if del_opacity > 0:
        action_btns.append(
            _build_action_button(
                "Delete",
                focused=(selected and focus == 1),
                card_selected=selected,
                color=(220, 60, 60),
                opacity=opacity * del_opacity,
                fade_t=del_fade,
            )
        )
        row.add_column(width=_BTN_W)
    if exp_opacity > 0:
        action_btns.append(
            _build_action_button(
                "Export",
                focused=(selected and focus == 2 and not show_export_submenu),
                card_selected=selected,
                color=(70, 100, 180),
                opacity=opacity * exp_opacity,
                fade_t=exp_fade,
            )
        )
        row.add_column(width=_BTN_W)

    if show_export_submenu and submenu_visible > 0 and exp_opacity > 0:
        # Staggered reveal: each button fades in as submenu_visible passes
        # its index (0=HTML, 1=Markdown, 2+=tracker buttons). On close, reverse order.
        html_opacity = min(1.0, max(0.0, submenu_visible))
        md_opacity = min(1.0, max(0.0, submenu_visible - 1.0))
        jira_opacity = min(1.0, max(0.0, submenu_visible - 2.0))
        azdevops_opacity = min(1.0, max(0.0, submenu_visible - 3.0))

        # Build dynamic submenu: HTML + Markdown always, then configured trackers
        _sub_items: list[tuple[str, int, float, float, bool]] = [
            ("HTML", 0, submenu_html_fade, html_opacity, True),
            ("Markdown", 1, submenu_md_fade, md_opacity, True),
        ]
        _next_idx = 2
        if jira_enabled:
            _sub_items.append(("Jira", _next_idx, submenu_jira_fade, jira_opacity, True))
            _next_idx += 1
        if azdevops_enabled:
            _sub_items.append(("Azure DevOps", _next_idx, submenu_azdevops_fade, azdevops_opacity, True))
            _next_idx += 1

        sub_btns: list = []
        for btn_label, btn_idx, btn_fade, btn_opacity, _enabled in _sub_items:
            if btn_opacity > 0:
                sub_btns.append(
                    _build_action_button(
                        btn_label,
                        focused=(selected and submenu_sel == btn_idx),
                        card_selected=selected,
                        color=(255, 255, 255),
                        opacity=opacity * btn_opacity,
                        fade_t=btn_fade,
                        btn_w=_EXPORT_SUB_BTN_W,
                    )
                )
                row.add_column(width=_EXPORT_SUB_BTN_W)
        row.add_row(card, *action_btns, *sub_btns)
    else:
        row.add_row(card, *action_btns)
    return row


def _build_project_list_screen(
    projects,
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    card_opacity: float = 1.0,
    cards_visible: float = 999.0,
    show_subtitle: bool = True,
    focus: int = 0,
    del_fade: float = 0.0,
    exp_fade: float = 0.0,
    card_fade: float = 0.0,
    pulse: float = 0.0,
    action_btns_visible: float = 0.0,
    show_export_submenu: bool = False,
    submenu_sel: int = 0,
    submenu_html_fade: float = 0.0,
    submenu_md_fade: float = 0.0,
    submenu_jira_fade: float = 0.0,
    submenu_azdevops_fade: float = 0.0,
    submenu_visible: float = 0.0,
    delete_popup_name: str = "",
    delete_popup_t: float = 0.0,
    delete_popup_pulse: float = 0.0,
    delete_popup_flash: float = 0.0,
    jira_enabled: bool = True,
    azdevops_enabled: bool = False,
) -> Panel:
    """Build the project list screen with Planning title pinned at top.

    Items list:
    - If no projects: [empty_state_card, new_project_card]
    - If projects:    [*project_cards, new_project_card]
    The selected index covers all items.
    card_opacity: 0.0-1.0 controls fade-in of all cards from near-black.
    show_subtitle: whether to show "Your projects" subtitle.
    focus: 0 = card, 1 = Delete button, 2 = Export button (only for project rows).
    del_fade / exp_fade: 0.0-1.0 button colour animation progress.
    card_fade: 0.0-1.0 selected card border animation progress (dim -> blue).
    pulse: 0.0-1.0 one-shot white flash on selected card border (Enter).
    show_export_submenu: when True, the selected row shows the split HTML/Markdown panel.
    submenu_*: parameters forwarded to the export submenu rendering.
    """
    # ASCII "Planning" title pinned at top — uses shared planning_title()
    title = planning_title()

    sub_color = lerp_color(card_opacity, BLACK_RGB, (100, 100, 100))
    sub = Text(_PAD + "Your projects", style=sub_color, justify="left") if show_subtitle else Text("")

    # Card width leaves room for two action buttons + gaps to the right.
    # Total row: box_w + 1 (gap) + btn_w + 1 (gap) + btn_w
    # The export submenu extends beyond when visible (temporary, ok to overflow).
    box_w = min(56, width - 12 - 2 * _BTN_W)
    box_w = max(30, box_w)  # floor so it never collapses
    body: list = []
    body_h = 0

    # Left pad matches _PAD (4 chars) so cards align with the ASCII title
    _card_pad = (0, 0, 0, len(_PAD))

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank

    if not projects:
        # No scrolling needed for empty state (only 2 items)
        body.append(
            Padding(
                _build_empty_state_card(selected=(selected == 0), box_w=box_w, opacity=card_opacity),
                _card_pad,
            )
        )
        body_h += 6  # empty state card: border(2) + padding(2) + content(2)
        body.append(Text(""))
        body_h += 1
        body.append(
            Padding(
                _build_new_project_card(selected=(selected == 1), box_w=box_w, opacity=card_opacity),
                _card_pad,
            )
        )
        body_h += 3
    else:
        # Viewport scrolling — show only cards that fit on screen with peek
        # stubs at the edges hinting at off-screen cards.
        # See README: "Architecture" — viewport keeps selected card visible.
        n_items = len(projects) + 1  # project cards + "+ New Project"
        available_h = inner_h - header_h
        start, end, show_above, show_below = _compute_viewport(n_items, selected, available_h)

        # Helper to get the display title for any item index
        def _item_title(idx: int) -> str:
            return projects[idx].name if idx < len(projects) else "+ New Project"

        # Peek above: border + title for the card just above the viewport
        if show_above:
            body.append(
                Padding(
                    _build_peek_above(box_w=box_w, opacity=card_opacity, title=_item_title(start - 1)),
                    _card_pad,
                )
            )
            body_h += _PEEK_H

        # Full cards in viewport — staggered reveal via cards_visible.
        # Each card at viewport position vi gets opacity from cards_visible.
        for vi, i in enumerate(range(start, end)):
            # Per-card stagger: card appears once cards_visible passes its index.
            # No gradual fade — cards pop in at full opacity for a snappy feel.
            if vi >= cards_visible:
                break  # remaining cards aren't visible yet
            item_opacity = card_opacity

            if i < len(projects):
                # Project row: card + Delete + Export buttons to the right
                is_sel = i == selected
                row = _build_project_row(
                    projects[i],
                    selected=is_sel,
                    focus=focus if is_sel else 0,
                    box_w=box_w,
                    opacity=item_opacity,
                    del_fade=del_fade if is_sel else 0.0,
                    exp_fade=exp_fade if is_sel else 0.0,
                    card_fade=card_fade if is_sel else 0.0,
                    pulse=pulse if is_sel else 0.0,
                    action_btns_visible=action_btns_visible if is_sel else 0.0,
                    show_export_submenu=show_export_submenu if is_sel else False,
                    submenu_sel=submenu_sel if is_sel else 0,
                    submenu_html_fade=submenu_html_fade if is_sel else 0.0,
                    submenu_md_fade=submenu_md_fade if is_sel else 0.0,
                    submenu_jira_fade=submenu_jira_fade if is_sel else 0.0,
                    submenu_azdevops_fade=submenu_azdevops_fade if is_sel else 0.0,
                    submenu_visible=submenu_visible if is_sel else 0.0,
                    jira_enabled=jira_enabled,
                    azdevops_enabled=azdevops_enabled,
                )
                body.append(Padding(row, _card_pad))
            else:
                card = _build_new_project_card(
                    selected=(i == selected),
                    box_w=box_w,
                    opacity=item_opacity,
                )
                body.append(Padding(card, _card_pad))
            body_h += _CARD_H
            if i < end - 1:
                body.append(Text(""))
                body_h += _CARD_SPACING

        # Peek below: border + title for the card just below the viewport
        if show_below:
            body.append(
                Padding(
                    _build_peek_below(box_w=box_w, opacity=card_opacity, title=_item_title(end)),
                    _card_pad,
                )
            )
            body_h += _PEEK_H

    remaining = max(0, inner_h - header_h - body_h)

    # Delete popup — a full rectangle that renders "on top" of the content,
    # overlaying the bottom of the screen.  The popup is always positioned so
    # its bottom border is fully visible.  When the remaining blank space is
    # too small for the popup, trailing body items (spacers, peek stubs) are
    # trimmed so the popup fits without clipping.
    popup_before: list = []
    popup_mid: list = []
    popup_after: list = []
    if delete_popup_name and delete_popup_t > 0:
        popup_msg = f'Delete "{delete_popup_name}"?  Enter to confirm'
        panel_inner_w = width - 6  # panel border(2) + panel padding(4)
        popup_w = min(panel_inner_w, max(40, len(popup_msg) + 8))

        # Build the popup as raw Text lines instead of a nested Panel so each
        # line is exactly 1 visual row — Rich Panels inside Panels can expand
        # beyond their stated height and get clipped by the outer fixed-height panel.
        #
        # Border colour animation — same pattern as loading_border_color()
        # in shared/_animations.py (sine-wave oscillation between two colours).
        #
        # - Slide-up phase: border fades from pale pink to dark red.
        # - Settled phase: border oscillates between dark red and bright red
        #   (like a loader pulses between grey and white).
        # - Confirm flash: border goes white, then decays back.
        import math as _math

        dark_red = (140, 30, 30)
        bright_red = (255, 90, 90)

        # Oscillation: sine wave between dark_red and bright_red,
        # using delete_popup_pulse as the running clock tick.
        t = (_math.cos(delete_popup_pulse * 3) + 1) / 2  # 0->1 oscillation
        br = int(dark_red[0] + (bright_red[0] - dark_red[0]) * t)
        bg = int(dark_red[1] + (bright_red[1] - dark_red[1]) * t)
        bb = int(dark_red[2] + (bright_red[2] - dark_red[2]) * t)

        # White flash overlay on confirm (lerp toward white)
        if delete_popup_flash > 0:
            br = int(br + (255 - br) * delete_popup_flash)
            bg = int(bg + (255 - bg) * delete_popup_flash)
            bb = int(bb + (255 - bb) * delete_popup_flash)

        border_style = f"rgb({br},{bg},{bb})"

        inner_w = popup_w - 2  # content width inside the left/right borders

        # Center the message within the inner width
        msg_pad_l = max(0, (inner_w - len(popup_msg)) // 2)
        msg_pad_r = max(0, inner_w - len(popup_msg) - msg_pad_l)
        centered_msg = " " * msg_pad_l + popup_msg + " " * msg_pad_r

        # Horizontally center the popup box within the screen
        h_pad = " " * max(0, (panel_inner_w - popup_w) // 2)

        # 5 lines: top border, blank, message, blank, bottom border
        line_top = Text(h_pad, justify="left")
        line_top.append("\u256d" + "\u2500" * inner_w + "\u256e", style=border_style)

        line_blank1 = Text(h_pad, justify="left")
        line_blank1.append("\u2502" + " " * inner_w + "\u2502", style=border_style)

        line_msg = Text(h_pad, justify="left")
        line_msg.append("\u2502", style=border_style)
        line_msg.append(centered_msg, style="bold white")
        line_msg.append("\u2502", style=border_style)

        line_blank2 = Text(h_pad, justify="left")
        line_blank2.append("\u2502" + " " * inner_w + "\u2502", style=border_style)

        line_bot = Text(h_pad, justify="left")
        line_bot.append("\u256e" + "\u2500" * inner_w + "\u256f", style=border_style)

        popup_lines = [line_top, line_blank1, line_msg, line_blank2, line_bot]
        popup_h = len(popup_lines)

        # If the popup doesn't fit in the remaining blank space, trim
        # trailing body items to make room.  The popup renders "on top"
        # of whatever was at the bottom — spacers, peek stubs, and even
        # lower cards get covered, giving a floating overlay effect.
        overflow = popup_h - remaining
        while overflow > 0 and body and body_h > 0:
            last = body[-1]
            # Determine the height of the last body item
            if isinstance(last, Text) and not last.plain.strip():
                item_h = _CARD_SPACING  # blank spacer
            elif isinstance(last, Padding) and isinstance(last.renderable, Group):
                item_h = _PEEK_H  # peek stub
            else:
                item_h = _CARD_H  # project row or new-project card
            body.pop()
            body_h -= item_h
            overflow -= item_h
        remaining = max(0, inner_h - header_h - body_h)

        # Resting position: popup at the very bottom of available space.
        resting_above = max(0, remaining - popup_h)
        # At t=0 the popup is fully below (above = remaining, i.e. off-screen).
        start_above = remaining
        current_above = int(start_above + (resting_above - start_above) * delete_popup_t)
        current_below = max(0, remaining - current_above - popup_h)

        popup_before = [Text("") for _ in range(current_above)]
        popup_mid = popup_lines
        popup_after = [Text("") for _ in range(current_below)]
    else:
        popup_before = [Text("") for _ in range(remaining)]

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *popup_before,
        *popup_mid,
        *popup_after,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
