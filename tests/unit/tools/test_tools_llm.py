"""Tests for LLM-powered tools (estimate_complexity, generate_acceptance_criteria).

All LLM calls are mocked — no real API calls are made. Tests verify:
- Correct prompt construction (description, tech_stack, context injected)
- Return value is the LLM response content
- Error handling when LLM raises an exception
- Tool registration in get_tools()
"""

from unittest.mock import MagicMock

from scrum_agent.tools.llm_tools import (
    _FIBONACCI_POINTS,
    estimate_complexity,
    generate_acceptance_criteria,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm(response_text: str) -> MagicMock:
    """Return a mock get_llm() factory whose .invoke() returns response_text."""
    mock_response = MagicMock()
    mock_response.content = response_text
    mock_instance = MagicMock()
    mock_instance.invoke.return_value = mock_response
    return mock_instance


# ---------------------------------------------------------------------------
# estimate_complexity
# ---------------------------------------------------------------------------


class TestEstimateComplexity:
    def test_returns_llm_response(self, monkeypatch):
        """Should return the LLM response content as a string."""
        mock_llm = _mock_llm("Story Points: 3\n\nRationale:\n- Small scope\n- Clear requirements")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        result = estimate_complexity.invoke({"description": "Add a logout button"})

        assert "Story Points: 3" in result
        assert "Rationale" in result

    def test_description_injected_into_prompt(self, monkeypatch):
        """The story description should appear in the LLM prompt."""
        mock_llm = _mock_llm("Story Points: 2\n\nRationale:\n- Trivial")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        estimate_complexity.invoke({"description": "Reset password via email link"})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "Reset password via email link" in prompt_text

    def test_tech_stack_injected_when_provided(self, monkeypatch):
        """Tech stack should appear in the prompt when provided."""
        mock_llm = _mock_llm("Story Points: 5\n\nRationale:\n- Complex")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        estimate_complexity.invoke({"description": "Add auth", "tech_stack": "React, FastAPI"})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "React, FastAPI" in prompt_text

    def test_tech_stack_omitted_when_empty(self, monkeypatch):
        """Tech stack line should be absent when tech_stack is empty."""
        mock_llm = _mock_llm("Story Points: 1\n\nRationale:\n- Trivial")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        estimate_complexity.invoke({"description": "Fix typo in label"})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "Tech stack:" not in prompt_text

    def test_fibonacci_values_in_prompt(self, monkeypatch):
        """All Fibonacci point values should appear in the prompt."""
        mock_llm = _mock_llm("Story Points: 3\n\nRationale:\n- Medium")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        estimate_complexity.invoke({"description": "some story"})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        for val in _FIBONACCI_POINTS:
            assert str(val) in prompt_text

    def test_error_returns_error_string(self, monkeypatch):
        """LLM errors should return an 'Error:' prefixed string, not raise."""
        mock_instance = MagicMock()
        mock_instance.invoke.side_effect = RuntimeError("API unavailable")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_instance)

        result = estimate_complexity.invoke({"description": "some story"})

        assert result.startswith("Error")
        assert "API unavailable" in result

    def test_uses_low_temperature(self, monkeypatch):
        """estimate_complexity should call get_llm with temperature <= 0.5."""
        captured = {}

        def mock_get_llm(**kwargs):
            captured.update(kwargs)
            return _mock_llm("Story Points: 2\n\nRationale:\n- Simple")

        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", mock_get_llm)
        estimate_complexity.invoke({"description": "small fix"})

        assert "temperature" in captured
        assert captured["temperature"] <= 0.5


# ---------------------------------------------------------------------------
# generate_acceptance_criteria
# ---------------------------------------------------------------------------


class TestGenerateAcceptanceCriteria:
    _SAMPLE_ACS = (
        "Acceptance Criteria:\n\n"
        "1. Given I am logged in\n"
        "   When I click logout\n"
        "   Then I am redirected to the login page"
    )

    def test_returns_llm_response(self, monkeypatch):
        """Should return the LLM response content as a string."""
        mock_llm = _mock_llm(self._SAMPLE_ACS)
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        result = generate_acceptance_criteria.invoke(
            {"story": "As a user, I want to log out, so that my session is ended."}
        )

        assert "Acceptance Criteria" in result
        assert "Given" in result

    def test_story_injected_into_prompt(self, monkeypatch):
        """The story text should appear in the LLM prompt."""
        mock_llm = _mock_llm(self._SAMPLE_ACS)
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        story = "As a user, I want to reset my password via email."
        generate_acceptance_criteria.invoke({"story": story})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert story in prompt_text

    def test_context_injected_when_provided(self, monkeypatch):
        """Additional context should appear in the prompt when provided."""
        mock_llm = _mock_llm(self._SAMPLE_ACS)
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        generate_acceptance_criteria.invoke({"story": "As a user...", "context": "Uses SendGrid for email delivery"})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "SendGrid" in prompt_text

    def test_context_omitted_when_empty(self, monkeypatch):
        """Context section should be absent when context is empty."""
        mock_llm = _mock_llm(self._SAMPLE_ACS)
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        generate_acceptance_criteria.invoke({"story": "As a user..."})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "Additional context" not in prompt_text

    def test_given_when_then_format_in_prompt(self, monkeypatch):
        """The prompt should instruct Given/When/Then format."""
        mock_llm = _mock_llm(self._SAMPLE_ACS)
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        generate_acceptance_criteria.invoke({"story": "some story"})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "Given" in prompt_text
        assert "When" in prompt_text
        assert "Then" in prompt_text

    def test_error_returns_error_string(self, monkeypatch):
        """LLM errors should return an 'Error:' prefixed string, not raise."""
        mock_instance = MagicMock()
        mock_instance.invoke.side_effect = RuntimeError("timeout")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_instance)

        result = generate_acceptance_criteria.invoke({"story": "some story"})

        assert result.startswith("Error")
        assert "timeout" in result

    def test_uses_low_temperature(self, monkeypatch):
        """generate_acceptance_criteria should call get_llm with temperature <= 0.5."""
        captured = {}

        def mock_get_llm(**kwargs):
            captured.update(kwargs)
            return _mock_llm(self._SAMPLE_ACS)

        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", mock_get_llm)
        generate_acceptance_criteria.invoke({"story": "some story"})

        assert "temperature" in captured
        assert captured["temperature"] <= 0.5


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestLlmToolsRegistered:
    def test_both_tools_in_get_tools(self):
        """estimate_complexity and generate_acceptance_criteria should be in get_tools()."""
        from scrum_agent.tools import get_tools

        names = {t.name for t in get_tools()}
        assert "estimate_complexity" in names
        assert "generate_acceptance_criteria" in names

    def test_fibonacci_points_constant(self):
        assert _FIBONACCI_POINTS == (1, 2, 3, 5, 8)


# ---------------------------------------------------------------------------
# Input validation edge cases
# ---------------------------------------------------------------------------


class TestLlmToolsInputValidation:
    """Test tool input validation — empty/edge-case inputs."""

    def test_estimate_empty_description(self, monkeypatch):
        """Empty description should still invoke the LLM (no crash)."""
        mock_llm = _mock_llm("Story Points: 1\n\nRationale:\n- No info")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        result = estimate_complexity.invoke({"description": ""})

        assert "Story Points" in result
        mock_llm.invoke.assert_called_once()

    def test_estimate_very_long_description(self, monkeypatch):
        """Very long description should still work (no truncation in the tool)."""
        long_desc = "Build a feature that " + "does many things. " * 500
        mock_llm = _mock_llm("Story Points: 8\n\nRationale:\n- Massive scope")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        result = estimate_complexity.invoke({"description": long_desc})

        assert "Story Points: 8" in result
        # Verify the full description was passed to the LLM
        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "does many things" in prompt_text

    def test_generate_ac_empty_story(self, monkeypatch):
        """Empty story text should still invoke the LLM (no crash)."""
        mock_llm = _mock_llm("Acceptance Criteria:\n\n1. Given ...\n   When ...\n   Then ...")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        result = generate_acceptance_criteria.invoke({"story": ""})

        assert "Acceptance Criteria" in result

    def test_estimate_whitespace_only_tech_stack(self, monkeypatch):
        """Whitespace-only tech_stack should be treated as empty (no 'Tech stack:' line)."""
        mock_llm = _mock_llm("Story Points: 2\n\nRationale:\n- Simple")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        estimate_complexity.invoke({"description": "Add button", "tech_stack": "   "})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "Tech stack:" not in prompt_text

    def test_generate_ac_whitespace_only_context(self, monkeypatch):
        """Whitespace-only context should be treated as empty (no 'Additional context' section)."""
        mock_llm = _mock_llm("Acceptance Criteria:\n\n1. Given ...\n   When ...\n   Then ...")
        monkeypatch.setattr("scrum_agent.tools.llm_tools.get_llm", lambda **kw: mock_llm)

        generate_acceptance_criteria.invoke({"story": "As a user...", "context": "   "})

        call_args = mock_llm.invoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "Additional context" not in prompt_text
