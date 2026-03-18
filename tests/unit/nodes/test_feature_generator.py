"""Tests for feature generator node, feature_skip node, and their helpers."""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from scrum_agent.agent.nodes import (
    _build_fallback_features,
    _format_features,
    _parse_features_response,
    feature_generator,
    feature_skip,
)
from scrum_agent.agent.state import (
    Feature,
    Priority,
    QuestionnaireState,
)
from tests._node_helpers import VALID_FEATURES_JSON, make_dummy_analysis


class TestParseFeaturesResponse:
    """Tests for _parse_features_response() helper."""

    def _analysis(self):
        return make_dummy_analysis()

    def test_valid_json_returns_feature_list(self):
        """Valid JSON array should produce a list of Feature dataclasses."""
        result = _parse_features_response(VALID_FEATURES_JSON, self._analysis())
        assert isinstance(result, list)
        assert len(result) == 4
        assert all(isinstance(e, Feature) for e in result)

    def test_feature_fields_parsed_correctly(self):
        """Feature fields should match the JSON values."""
        result = _parse_features_response(VALID_FEATURES_JSON, self._analysis())
        assert result[0].id == "F1"
        assert result[0].title == "User Authentication"
        assert result[0].priority == Priority.HIGH

    def test_code_fence_stripping(self):
        """JSON wrapped in markdown code fences should be handled."""
        fenced = f"```json\n{VALID_FEATURES_JSON}\n```"
        result = _parse_features_response(fenced, self._analysis())
        assert len(result) == 4
        assert result[0].id == "F1"

    def test_bad_json_returns_fallback(self):
        """Invalid JSON should fall back to deterministic features."""
        result = _parse_features_response("this is not json", self._analysis())
        assert isinstance(result, list)
        assert len(result) == 3  # fallback produces exactly 3

    def test_empty_response_returns_fallback(self):
        """Empty response should fall back."""
        result = _parse_features_response("", self._analysis())
        assert isinstance(result, list)
        assert len(result) == 3

    def test_non_list_json_returns_fallback(self):
        """JSON that's not a list (e.g. object) should fall back."""
        result = _parse_features_response('{"feature": "not a list"}', self._analysis())
        assert len(result) == 3

    def test_empty_array_returns_fallback(self):
        """Empty JSON array should fall back."""
        result = _parse_features_response("[]", self._analysis())
        assert len(result) == 3

    def test_invalid_priority_defaults_to_medium(self):
        """Invalid priority value should default to MEDIUM."""
        json_with_bad_priority = '[{"id": "F1", "title": "Test", "description": "desc", "priority": "urgent"}]'
        result = _parse_features_response(json_with_bad_priority, self._analysis())
        assert result[0].priority == Priority.MEDIUM

    def test_valid_priorities_preserved(self):
        """All valid priority values should be preserved."""
        for prio in ("critical", "high", "medium", "low"):
            json_str = f'[{{"id": "F1", "title": "Test", "description": "d", "priority": "{prio}"}}]'
            result = _parse_features_response(json_str, self._analysis())
            assert result[0].priority == Priority(prio)

    def test_missing_fields_use_defaults(self):
        """Feature dicts with missing fields should use sensible defaults."""
        minimal = '[{"title": "Just a title"}]'
        result = _parse_features_response(minimal, self._analysis())
        assert len(result) == 1
        assert result[0].title == "Just a title"
        assert result[0].priority == Priority.MEDIUM


class TestBuildFallbackFeatures:
    """Tests for _build_fallback_features() helper."""

    def test_returns_three_features(self):
        """Fallback should always return exactly 3 features."""
        analysis = make_dummy_analysis()
        result = _build_fallback_features(analysis)
        assert len(result) == 3

    def test_all_are_feature_instances(self):
        """All returned items should be Feature dataclasses."""
        analysis = make_dummy_analysis()
        result = _build_fallback_features(analysis)
        assert all(isinstance(e, Feature) for e in result)

    def test_sequential_ids(self):
        """Fallback features should have sequential IDs F1, F2, F3."""
        analysis = make_dummy_analysis()
        result = _build_fallback_features(analysis)
        assert [e.id for e in result] == ["F1", "F2", "F3"]

    def test_first_feature_uses_first_goal(self):
        """First feature should derive its title from the first goal."""
        analysis = make_dummy_analysis(goals=("Build a REST API", "Add auth"))
        result = _build_fallback_features(analysis)
        assert "Build a REST API" in result[0].title

    def test_handles_empty_goals(self):
        """Empty goals should produce a generic 'Core Functionality' feature."""
        analysis = make_dummy_analysis(goals=())
        result = _build_fallback_features(analysis)
        assert result[0].title == "Core Functionality"

    def test_second_feature_is_infrastructure(self):
        """Second feature should be infrastructure & setup."""
        analysis = make_dummy_analysis()
        result = _build_fallback_features(analysis)
        assert "Infrastructure" in result[1].title

    def test_third_feature_is_integrations(self):
        """Third feature should be integrations & extensions."""
        analysis = make_dummy_analysis()
        result = _build_fallback_features(analysis)
        assert "Integrations" in result[2].title

    def test_includes_project_name_in_infra_description(self):
        """Infrastructure feature description should reference the project name."""
        analysis = make_dummy_analysis(project_name="Widget Builder")
        result = _build_fallback_features(analysis)
        assert "Widget Builder" in result[1].description


class TestFormatFeatures:
    """Tests for _format_features() helper."""

    def _sample_features(self) -> list[Feature]:
        return [
            Feature(id="F1", title="Authentication", description="User auth", priority=Priority.HIGH),
            Feature(id="F2", title="Dashboard", description="Main UI", priority=Priority.MEDIUM),
        ]

    def test_returns_string(self):
        """Should return a non-empty markdown string."""
        result = _format_features(self._sample_features(), "Test Project")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_project_name(self):
        """The project name should appear in the header."""
        result = _format_features(self._sample_features(), "Widget Builder")
        assert "Widget Builder" in result

    def test_includes_feature_count(self):
        """The feature count should be mentioned."""
        result = _format_features(self._sample_features(), "Test")
        assert "2 feature(s)" in result

    def test_includes_feature_ids(self):
        """All feature IDs should appear in the output."""
        result = _format_features(self._sample_features(), "Test")
        assert "F1" in result
        assert "F2" in result

    def test_includes_feature_titles(self):
        """All feature titles should appear in the output."""
        result = _format_features(self._sample_features(), "Test")
        assert "Authentication" in result
        assert "Dashboard" in result

    def test_includes_priorities(self):
        """Priority values should appear in the output."""
        result = _format_features(self._sample_features(), "Test")
        assert "high" in result
        assert "medium" in result

    def test_includes_review_footer(self):
        """The review prompt footer should be present."""
        result = _format_features(self._sample_features(), "Test")
        assert "[Accept / Edit / Reject]" in result


class TestFeatureGenerator:
    """Tests for the feature_generator() node function."""

    def _make_state(self, **extras: object) -> dict:
        """Build a minimal state with project_analysis for feature generator tests."""
        analysis = make_dummy_analysis()
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
        }
        state.update(extras)
        return state

    def test_returns_features_list(self, monkeypatch):
        """feature_generator should return a list of Feature instances."""
        fake_response = MagicMock()
        fake_response.content = VALID_FEATURES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = feature_generator(self._make_state())
        assert "features" in result
        assert isinstance(result["features"], list)
        assert all(isinstance(e, Feature) for e in result["features"])

    def test_returns_ai_message(self, monkeypatch):
        """feature_generator should return an AIMessage with the formatted features."""
        fake_response = MagicMock()
        fake_response.content = VALID_FEATURES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = feature_generator(self._make_state())
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)

    def test_ai_message_contains_feature_info(self, monkeypatch):
        """The AIMessage should contain feature IDs and titles."""
        fake_response = MagicMock()
        fake_response.content = VALID_FEATURES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = feature_generator(self._make_state())
        content = result["messages"][0].content
        assert "F1" in content
        assert "User Authentication" in content

    def test_bad_json_uses_fallback(self, monkeypatch):
        """When LLM returns bad JSON, the fallback should produce valid features."""
        fake_response = MagicMock()
        fake_response.content = "not valid json at all"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = feature_generator(self._make_state())
        assert isinstance(result["features"], list)
        assert len(result["features"]) == 3  # fallback produces 3

    def test_llm_exception_uses_fallback(self, monkeypatch):
        """When the LLM call raises an exception, the fallback should be used."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("API down")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = feature_generator(self._make_state())
        assert isinstance(result["features"], list)
        assert len(result["features"]) == 3
        assert "messages" in result

    def test_calls_llm_with_temperature_zero(self, monkeypatch):
        """feature_generator should use temperature=0.0 for deterministic output."""
        fake_response = MagicMock()
        fake_response.content = VALID_FEATURES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response

        captured_kwargs = {}

        def capture_get_llm(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_llm

        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", capture_get_llm)

        feature_generator(self._make_state())
        assert captured_kwargs.get("temperature") == 0.0


class TestFeatureGeneratorRepoContextIntegration:
    """Tests that feature_generator reads repo_context from state and passes it to prompt."""

    def _make_state(self, **extras: object) -> dict:
        analysis = make_dummy_analysis()
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
        }
        state.update(extras)
        return state

    def test_passes_repo_context_to_prompt(self, monkeypatch):
        """feature_generator passes repo_context from state into get_feature_generator_prompt."""
        fake_response = MagicMock()
        fake_response.content = VALID_FEATURES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        captured: dict = {}

        def mock_prompt(**kwargs):
            captured.update(kwargs)
            return "mock prompt"

        monkeypatch.setattr("scrum_agent.agent.nodes.get_feature_generator_prompt", mock_prompt)

        state = self._make_state(repo_context="## File Tree\n- src/")
        feature_generator(state)

        assert captured.get("repo_context") == "## File Tree\n- src/"

    def test_passes_none_when_no_repo_context(self, monkeypatch):
        """feature_generator passes repo_context=None when not in state."""
        fake_response = MagicMock()
        fake_response.content = VALID_FEATURES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        captured: dict = {}

        def mock_prompt(**kwargs):
            captured.update(kwargs)
            return "mock prompt"

        monkeypatch.setattr("scrum_agent.agent.nodes.get_feature_generator_prompt", mock_prompt)

        state = self._make_state()  # no repo_context key
        feature_generator(state)

        assert captured.get("repo_context") is None


class TestFeatureSkip:
    """Tests for the feature_skip node — sentinel feature for small projects."""

    def _make_state(self, **overrides: object) -> dict:
        analysis = make_dummy_analysis(skip_features=True, target_sprints=1, goals=("Build API",))
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
        }
        state.update(overrides)
        return state

    def test_returns_single_feature(self):
        """feature_skip should create a single F1 feature named after the project."""
        state = self._make_state()
        result = feature_skip(state)
        assert "features" in result
        assert len(result["features"]) == 1
        sentinel = result["features"][0]
        assert sentinel.id == "F1"
        assert sentinel.title == "Test Project"  # from make_dummy_analysis default
        assert sentinel.priority == Priority.HIGH

    def test_feature_description_from_analysis(self):
        """Feature description should come from the project analysis."""
        analysis = make_dummy_analysis(project_description="A tiny REST API", skip_features=True)
        state = self._make_state(project_analysis=analysis)
        result = feature_skip(state)
        assert result["features"][0].description == "A tiny REST API"

    def test_sets_pending_review(self):
        """feature_skip should set pending_review so the review checkpoint fires."""
        state = self._make_state()
        result = feature_skip(state)
        assert result["pending_review"] == "feature_generator"

    def test_returns_ai_message(self):
        """feature_skip should return an AIMessage with display text."""
        state = self._make_state()
        result = feature_skip(state)
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)
        assert "1 feature" in result["messages"][0].content.lower()
