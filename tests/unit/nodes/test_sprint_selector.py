"""Tests for the resolve_sprint_selection helper.

The sprint_selector node was removed in Phase 13A — sprint selection is now
handled during intake (Q27). These tests cover the resolve_sprint_selection
helper which is still used by the intake node and REPL.
"""

from scrum_agent.agent.nodes import resolve_sprint_selection


class TestResolveSprintSelection:
    """Tests for the resolve_sprint_selection helper."""

    def test_option_1_returns_next_sprint(self):
        assert resolve_sprint_selection("1", 104) == 105

    def test_option_2_returns_plus_two(self):
        assert resolve_sprint_selection("2", 104) == 106

    def test_option_3_returns_plus_three(self):
        assert resolve_sprint_selection("3", 104) == 107

    def test_direct_number_input(self):
        assert resolve_sprint_selection("110", 104) == 110

    def test_direct_number_with_whitespace(self):
        assert resolve_sprint_selection("  110  ", 104) == 110

    def test_negative_number_returns_none(self):
        assert resolve_sprint_selection("-3", 104) is None

    def test_zero_returns_none(self):
        assert resolve_sprint_selection("0", 104) is None

    def test_non_numeric_returns_none(self):
        assert resolve_sprint_selection("abc", 104) is None

    def test_empty_string_returns_none(self):
        assert resolve_sprint_selection("", 104) is None

    def test_large_sprint_number(self):
        assert resolve_sprint_selection("999", 50) == 999
