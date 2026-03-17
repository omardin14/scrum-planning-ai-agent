"""Tests for review intent parsing and generation node review state handling."""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from scrum_agent.agent.nodes import (
    _parse_review_intent,
    epic_generator,
    sprint_planner,
    story_writer,
    task_decomposer,
)
from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Epic,
    Priority,
    QuestionnaireState,
    ReviewDecision,
    StoryPointValue,
    UserStory,
)
from tests._node_helpers import (
    VALID_EPICS_JSON,
    VALID_SPRINTS_JSON,
    VALID_STORIES_JSON,
    VALID_TASKS_JSON,
    make_dummy_analysis,
)


class TestParseReviewIntent:
    """Tests for _parse_review_intent()."""

    # Accept keywords
    @pytest.mark.parametrize("text", ["accept", "ACCEPT", "approve", "ok", "yes", "y", "looks good", "lgtm", "proceed"])
    def test_accept_keywords(self, text):
        decision, feedback = _parse_review_intent(text)
        assert decision == ReviewDecision.ACCEPT
        assert feedback == ""

    # Reject keywords (bare)
    @pytest.mark.parametrize("text", ["reject", "redo", "regenerate", "again", "no", "n"])
    def test_reject_keywords_bare(self, text):
        decision, feedback = _parse_review_intent(text)
        assert decision == ReviewDecision.REJECT
        assert feedback == ""

    # Reject with inline feedback
    def test_reject_with_inline_feedback(self):
        decision, feedback = _parse_review_intent("reject: need more detail")
        assert decision == ReviewDecision.REJECT
        assert feedback == "need more detail"

    # Edit keywords (bare) — handles "2" → "edit" from numbered menu
    @pytest.mark.parametrize("text", ["edit", "change", "modify", "update", "adjust", "tweak", "revise"])
    def test_edit_keywords_bare(self, text):
        decision, feedback = _parse_review_intent(text)
        assert decision == ReviewDecision.EDIT
        assert feedback == ""

    # Edit prefixes
    @pytest.mark.parametrize("prefix", ["edit:", "change:", "modify:", "update:", "adjust:", "tweak:", "revise:"])
    def test_edit_prefixes_with_colon(self, prefix):
        decision, feedback = _parse_review_intent(f"{prefix} add security")
        assert decision == ReviewDecision.EDIT
        assert "add security" in feedback

    @pytest.mark.parametrize("prefix", ["edit ", "change ", "modify "])
    def test_edit_prefixes_with_space(self, prefix):
        decision, feedback = _parse_review_intent(f"{prefix}add security")
        assert decision == ReviewDecision.EDIT
        assert "add security" in feedback

    # Unrecognized text defaults to REJECT with full text as feedback
    def test_unrecognized_text_defaults_to_reject(self):
        decision, feedback = _parse_review_intent("add a security epic")
        assert decision == ReviewDecision.REJECT
        assert feedback == "add a security epic"

    def test_whitespace_handling(self):
        decision, feedback = _parse_review_intent("  accept  ")
        assert decision == ReviewDecision.ACCEPT

    def test_case_insensitive(self):
        decision, feedback = _parse_review_intent("LOOKS GOOD")
        assert decision == ReviewDecision.ACCEPT


# ── Generation node review state tests ───────────────────────────────


class TestEpicGeneratorReview:
    """Tests for epic_generator review state handling."""

    def _make_state(self, **extras: object) -> dict:
        """Build a minimal state with project_analysis."""
        analysis = make_dummy_analysis()
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
        }
        state.update(extras)
        return state

    def test_returns_pending_review(self, monkeypatch):
        """epic_generator should set pending_review='epic_generator'."""
        fake_response = MagicMock()
        fake_response.content = VALID_EPICS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = epic_generator(self._make_state())
        assert result["pending_review"] == "epic_generator"

    def test_reject_feedback_in_prompt(self, monkeypatch):
        """When last_review_decision=REJECT, feedback should reach the LLM prompt."""
        captured_prompts = []

        def mock_llm_factory(**kw):
            mock = MagicMock()

            def capture_invoke(messages):
                captured_prompts.append(messages[0].content)
                resp = MagicMock()
                resp.content = VALID_EPICS_JSON
                return resp

            mock.invoke.side_effect = capture_invoke
            return mock

        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", mock_llm_factory)

        state = self._make_state(
            last_review_decision=ReviewDecision.REJECT,
            last_review_feedback="add a security epic",
        )
        epic_generator(state)
        assert len(captured_prompts) == 1
        assert "add a security epic" in captured_prompts[0]
        assert "User Feedback" in captured_prompts[0]

    def test_edit_feedback_with_previous_output_in_prompt(self, monkeypatch):
        """When last_review_decision=EDIT, both feedback and previous output should reach the prompt."""
        captured_prompts = []

        def mock_llm_factory(**kw):
            mock = MagicMock()

            def capture_invoke(messages):
                captured_prompts.append(messages[0].content)
                resp = MagicMock()
                resp.content = VALID_EPICS_JSON
                return resp

            mock.invoke.side_effect = capture_invoke
            return mock

        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", mock_llm_factory)

        state = self._make_state(
            last_review_decision=ReviewDecision.EDIT,
            last_review_feedback='rename E1\n\n---PREVIOUS OUTPUT---\n[{"id": "E1"}]',
        )
        epic_generator(state)
        assert len(captured_prompts) == 1
        assert "rename E1" in captured_prompts[0]
        assert "Edit Instructions" in captured_prompts[0]

    def test_no_feedback_normal_prompt(self, monkeypatch):
        """Without review state, the prompt should not contain feedback sections."""
        captured_prompts = []

        def mock_llm_factory(**kw):
            mock = MagicMock()

            def capture_invoke(messages):
                captured_prompts.append(messages[0].content)
                resp = MagicMock()
                resp.content = VALID_EPICS_JSON
                return resp

            mock.invoke.side_effect = capture_invoke
            return mock

        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", mock_llm_factory)

        state = self._make_state()
        epic_generator(state)
        assert "User Feedback" not in captured_prompts[0]
        assert "Edit Instructions" not in captured_prompts[0]


class TestStoryWriterReview:
    """Tests for story_writer review state handling."""

    def _make_state(self, **extras: object) -> dict:
        analysis = make_dummy_analysis()
        epics = [Epic(id="E1", title="Auth", description="Auth epic", priority=Priority.HIGH)]
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "epics": epics,
        }
        state.update(extras)
        return state

    def test_returns_pending_review(self, monkeypatch):
        """story_writer should set pending_review='story_writer'."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert result["pending_review"] == "story_writer"

    def test_reject_feedback_reaches_prompt(self, monkeypatch):
        """Reject feedback should appear in the LLM prompt."""
        captured_prompts = []

        def mock_llm_factory(**kw):
            mock = MagicMock()

            def capture_invoke(messages):
                captured_prompts.append(messages[0].content)
                resp = MagicMock()
                resp.content = VALID_STORIES_JSON
                return resp

            mock.invoke.side_effect = capture_invoke
            return mock

        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", mock_llm_factory)

        state = self._make_state(
            last_review_decision=ReviewDecision.REJECT,
            last_review_feedback="add more stories per epic",
        )
        story_writer(state)
        assert "add more stories per epic" in captured_prompts[0]


class TestTaskDecomposerReview:
    """Tests for task_decomposer review state handling."""

    def _make_state(self, **extras: object) -> dict:
        analysis = make_dummy_analysis()
        epics = [Epic(id="E1", title="Auth", description="Auth epic", priority=Priority.HIGH)]
        stories = [
            UserStory(
                id="US-E1-001",
                epic_id="E1",
                persona="user",
                goal="log in",
                benefit="access the system",
                acceptance_criteria=(
                    AcceptanceCriterion(given="a", when="b", then="c"),
                    AcceptanceCriterion(given="d", when="e", then="f"),
                    AcceptanceCriterion(given="g", when="h", then="i"),
                ),
                story_points=StoryPointValue.THREE,
                priority=Priority.HIGH,
            )
        ]
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "epics": epics,
            "stories": stories,
        }
        state.update(extras)
        return state

    def test_returns_pending_review(self, monkeypatch):
        """task_decomposer should set pending_review='task_decomposer'."""
        fake_response = MagicMock()
        fake_response.content = VALID_TASKS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = task_decomposer(self._make_state())
        assert result["pending_review"] == "task_decomposer"

    def test_reject_feedback_reaches_prompt(self, monkeypatch):
        """Reject feedback should appear in the LLM prompt."""
        captured_prompts = []

        def mock_llm_factory(**kw):
            mock = MagicMock()

            def capture_invoke(messages):
                captured_prompts.append(messages[0].content)
                resp = MagicMock()
                resp.content = VALID_TASKS_JSON
                return resp

            mock.invoke.side_effect = capture_invoke
            return mock

        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", mock_llm_factory)

        state = self._make_state(
            last_review_decision=ReviewDecision.REJECT,
            last_review_feedback="need more granular tasks",
        )
        task_decomposer(state)
        assert "need more granular tasks" in captured_prompts[0]


class TestSprintPlannerReview:
    """Tests for sprint_planner review state handling."""

    def _make_state(self, **extras: object) -> dict:
        analysis = make_dummy_analysis()
        epics = [Epic(id="E1", title="Auth", description="Auth epic", priority=Priority.HIGH)]
        stories = [
            UserStory(
                id="US-E1-001",
                epic_id="E1",
                persona="user",
                goal="log in",
                benefit="access",
                acceptance_criteria=(AcceptanceCriterion(given="a", when="b", then="c"),),
                story_points=StoryPointValue.FIVE,
                priority=Priority.HIGH,
            )
        ]
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "epics": epics,
            "stories": stories,
            "velocity_per_sprint": 20,
            "team_size": 3,
            "target_sprints": 2,
        }
        state.update(extras)
        return state

    def test_returns_pending_review(self, monkeypatch):
        """sprint_planner should set pending_review='sprint_planner'."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = sprint_planner(self._make_state())
        assert result["pending_review"] == "sprint_planner"

    def test_reject_feedback_reaches_prompt(self, monkeypatch):
        """Reject feedback should appear in the LLM prompt."""
        captured_prompts = []

        def mock_llm_factory(**kw):
            mock = MagicMock()

            def capture_invoke(messages):
                captured_prompts.append(messages[0].content)
                resp = MagicMock()
                resp.content = VALID_SPRINTS_JSON
                return resp

            mock.invoke.side_effect = capture_invoke
            return mock

        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", mock_llm_factory)

        state = self._make_state(
            last_review_decision=ReviewDecision.REJECT,
            last_review_feedback="spread stories more evenly",
        )
        sprint_planner(state)
        assert "spread stories more evenly" in captured_prompts[0]
