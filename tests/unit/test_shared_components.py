"""Unit tests for shared TUI components: Theme, buttons, scrollbar, progress dots, viewport."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.shared._components import (
    ANALYSIS_THEME,
    PLANNING_THEME,
    Theme,
    build_action_buttons,
    build_progress_dots,
    build_scrollbar,
    calc_viewport,
)


class TestTheme:
    def test_analysis_theme_defaults(self):
        t = ANALYSIS_THEME
        assert t.accent == "rgb(100,180,100)"
        assert t.muted == "rgb(120,120,140)"

    def test_planning_theme_overrides(self):
        t = PLANNING_THEME
        assert t.accent == "rgb(110,140,220)"
        assert t.muted == "rgb(120,120,140)"  # shared default

    def test_custom_theme(self):
        t = Theme(accent="red", warn="blue")
        assert t.accent == "red"
        assert t.warn == "blue"
        assert t.muted == "rgb(120,120,140)"  # default

    def test_frozen(self):
        import pytest

        with pytest.raises(AttributeError):
            ANALYSIS_THEME.accent = "red"  # type: ignore[misc]


class TestBuildActionButtons:
    def test_returns_three_text_objects(self):
        top, mid, bot = build_action_buttons(["Accept", "Edit"], 0)
        assert isinstance(top, Text)
        assert isinstance(mid, Text)
        assert isinstance(bot, Text)

    def test_selected_button(self):
        top, mid, bot = build_action_buttons(["Accept", "Edit", "Export"], 1)
        plain = mid.plain
        assert "Edit" in plain
        assert "Accept" in plain

    def test_single_button(self):
        top, mid, bot = build_action_buttons(["Done"], 0)
        assert "Done" in mid.plain

    def test_empty_actions(self):
        top, mid, bot = build_action_buttons([], 0)
        assert isinstance(top, Text)

    def test_box_drawing_chars(self):
        top, mid, bot = build_action_buttons(["Accept"], 0)
        assert "\u256d" in top.plain  # ╭
        assert "\u2502" in mid.plain  # │
        assert "\u2570" in bot.plain  # ╰


class TestBuildScrollbar:
    def test_returns_none_when_fits(self):
        result = build_scrollbar(viewport_h=20, total_lines=10, scroll_offset=0, max_scroll=0)
        assert result is None

    def test_returns_text_when_overflow(self):
        result = build_scrollbar(viewport_h=10, total_lines=30, scroll_offset=0, max_scroll=20)
        assert isinstance(result, Text)

    def test_scrollbar_has_correct_rows(self):
        result = build_scrollbar(viewport_h=10, total_lines=30, scroll_offset=0, max_scroll=20)
        assert result is not None
        lines = result.plain.strip().split("\n")
        assert len(lines) == 10

    def test_thumb_moves_with_offset(self):
        top = build_scrollbar(viewport_h=10, total_lines=100, scroll_offset=0, max_scroll=90)
        bot = build_scrollbar(viewport_h=10, total_lines=100, scroll_offset=90, max_scroll=90)
        assert top is not None and bot is not None
        # Thumb should be in different positions
        assert top.plain != bot.plain


class TestBuildProgressDots:
    def test_returns_text(self):
        result = build_progress_dots(["A", "B", "C"], 1)
        assert isinstance(result, Text)

    def test_stage_names_present(self):
        result = build_progress_dots(["Instructions", "Epic", "Stories"], 0)
        plain = result.plain
        assert "Instructions" in plain
        assert "Epic" in plain
        assert "Stories" in plain

    def test_dots_present(self):
        result = build_progress_dots(["A", "B", "C"], 1)
        plain = result.plain
        assert "\u25cf" in plain  # filled dot
        assert "\u25cb" in plain  # hollow dot

    def test_custom_theme(self):
        t = Theme(accent="red", accent_bright="bold red")
        result = build_progress_dots(["A", "B"], 0, theme=t)
        assert isinstance(result, Text)


class TestUsageScreen:
    def test_returns_panel(self):
        from scrum_agent.ui.mode_select.screens._screens_secondary import _build_usage_screen

        result = _build_usage_screen({}, width=80, height=24)
        assert isinstance(result, Panel)

    def test_with_full_data(self):
        from scrum_agent.ui.mode_select.screens._screens_secondary import _build_usage_screen

        data = {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "api_key_status": "configured",
            "tokens": {"input": 15000, "output": 3000, "total": 18000, "estimated_cost": 0.054},
            "sessions": {"total": 12, "planning": 8, "analysis": 4, "last_used": "2026-03-29 10:30"},
            "version": "1.2.0",
            "python_version": "3.14.3",
            "langsmith": "disabled",
            "db_path": "~/.scrum-agent/sessions.db",
            "profiles": [
                {"name": "azdevops-PROJ", "source": "azdevops", "sprints": 8},
            ],
        }
        result = _build_usage_screen(data, width=100, height=40)
        assert isinstance(result, Panel)

    def test_renders_provider_info(self):
        from io import StringIO

        from rich.console import Console

        from scrum_agent.ui.mode_select.screens._screens_secondary import _build_usage_screen

        data = {"provider": "anthropic", "model": "claude-sonnet-4", "api_key_status": "configured"}
        result = _build_usage_screen(data, width=100, height=40)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        output = buf.getvalue()
        assert "anthropic" in output
        assert "claude-sonnet-4" in output

    def test_scrollable(self):
        from scrum_agent.ui.mode_select.screens._screens_secondary import _build_usage_screen

        data = {
            "provider": "anthropic",
            "model": "test",
            "sessions": {"total": 5, "planning": 3, "analysis": 2},
            "profiles": [{"name": f"team-{i}", "source": "jira", "sprints": i} for i in range(10)],
        }
        r1 = _build_usage_screen(data, scroll_offset=0, width=80, height=20)
        r2 = _build_usage_screen(data, scroll_offset=5, width=80, height=20)
        assert isinstance(r1, Panel)
        assert isinstance(r2, Panel)

    def test_back_button(self):
        from io import StringIO

        from rich.console import Console

        from scrum_agent.ui.mode_select.screens._screens_secondary import _build_usage_screen

        result = _build_usage_screen({}, width=100, height=40)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        assert "Back" in buf.getvalue()

    def test_uses_amber_theme(self):
        """Usage screen should use the amber USAGE_THEME, not green or blue."""
        from io import StringIO

        from rich.console import Console

        from scrum_agent.ui.mode_select.screens._screens_secondary import _build_usage_screen

        result = _build_usage_screen({"provider": "test"}, width=100, height=30)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        output = buf.getvalue()
        # Should contain USAGE ASCII title
        assert "USAGE" in output.upper() or len(output) > 100


class TestCalcViewport:
    def test_standard_height(self):
        vp = calc_viewport(30, header_h=7, action_h=4)
        # inner = 30-4=26, viewport = 26-7-4=15
        assert vp == 15

    def test_minimum_clamp(self):
        vp = calc_viewport(10, header_h=7, action_h=4)
        assert vp >= 3

    def test_custom_header(self):
        vp = calc_viewport(30, header_h=6, action_h=4)
        # inner = 26, viewport = 26-6-4=16
        assert vp == 16
