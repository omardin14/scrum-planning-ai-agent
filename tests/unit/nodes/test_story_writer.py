"""Tests for story writer node and related helpers.

Extracted from test_nodes.py during the test reorganisation (Phase 12).
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from scrum_agent.agent.nodes import (
    _build_fallback_stories,
    _format_epics_for_prompt,
    _format_stories,
    _infer_discipline,
    _parse_stories_response,
    _snap_to_fibonacci,
    _validate_stories,
    story_writer,
)
from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Epic,
    Priority,
    QuestionnaireState,
    StoryPointValue,
    UserStory,
)
from tests._node_helpers import (
    VALID_STORIES_JSON,
    make_dummy_analysis,
    make_sample_epics,
    make_story_for_inference,
    make_valid_story,
)


class TestSnapToFibonacci:
    """Tests for _snap_to_fibonacci() helper."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, StoryPointValue.ONE),
            (2, StoryPointValue.TWO),
            (3, StoryPointValue.THREE),
            (5, StoryPointValue.FIVE),
            (8, StoryPointValue.EIGHT),
        ],
    )
    def test_exact_fibonacci_values(self, value, expected):
        """Exact Fibonacci values should return as-is."""
        assert _snap_to_fibonacci(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (4, StoryPointValue.THREE),  # 4 is closer to 3 than to 5
            (6, StoryPointValue.FIVE),  # 6 is closer to 5 than to 8
            (7, StoryPointValue.EIGHT),  # 7 is closer to 8 than to 5
        ],
    )
    def test_rounds_to_nearest_fibonacci(self, value, expected):
        """Non-Fibonacci values should snap to the nearest Fibonacci."""
        assert _snap_to_fibonacci(value) == expected

    def test_caps_above_eight(self):
        """Values above 8 should clamp to 8."""
        assert _snap_to_fibonacci(10) == StoryPointValue.EIGHT
        assert _snap_to_fibonacci(13) == StoryPointValue.EIGHT
        assert _snap_to_fibonacci(100) == StoryPointValue.EIGHT

    def test_clamps_below_one(self):
        """Values below 1 should clamp to 1."""
        assert _snap_to_fibonacci(0) == StoryPointValue.ONE
        assert _snap_to_fibonacci(-5) == StoryPointValue.ONE

    def test_return_type(self):
        """Return value should be a StoryPointValue instance."""
        result = _snap_to_fibonacci(3)
        assert isinstance(result, StoryPointValue)


class TestFormatEpicsForPrompt:
    """Tests for _format_epics_for_prompt() helper."""

    def test_returns_string(self):
        """Should return a non-empty string."""
        epics = make_sample_epics()
        result = _format_epics_for_prompt(epics)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_epic_ids(self):
        """All epic IDs should appear in the output."""
        epics = make_sample_epics()
        result = _format_epics_for_prompt(epics)
        assert "E1" in result
        assert "E2" in result
        assert "E3" in result

    def test_includes_titles(self):
        """All epic titles should appear in the output."""
        epics = make_sample_epics()
        result = _format_epics_for_prompt(epics)
        assert "User Authentication" in result
        assert "Task Management" in result

    def test_includes_priorities(self):
        """Epic priorities should appear in the output."""
        epics = make_sample_epics()
        result = _format_epics_for_prompt(epics)
        assert "high" in result
        assert "medium" in result

    def test_includes_descriptions(self):
        """Epic descriptions should appear in the output."""
        epics = make_sample_epics()
        result = _format_epics_for_prompt(epics)
        assert "Registration, login, JWT" in result

    def test_empty_epics_returns_empty(self):
        """Empty epic list should return empty string."""
        result = _format_epics_for_prompt([])
        assert result == ""


class TestParseStoriesResponse:
    """Tests for _parse_stories_response() helper."""

    def _epics(self) -> list[Epic]:
        return make_sample_epics()

    def _analysis(self):
        return make_dummy_analysis()

    def test_valid_json_returns_story_list(self):
        """Valid JSON array should produce a list of UserStory dataclasses."""
        result = _parse_stories_response(VALID_STORIES_JSON, self._epics(), self._analysis())
        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(s, UserStory) for s in result)

    def test_story_fields_parsed_correctly(self):
        """Story fields should match the JSON values."""
        result = _parse_stories_response(VALID_STORIES_JSON, self._epics(), self._analysis())
        story = result[0]
        assert story.id == "US-E1-001"
        assert story.epic_id == "E1"
        assert story.persona == "end user"
        assert story.goal == "register an account"
        assert story.benefit == "I can access the application"
        assert story.priority == Priority.HIGH
        assert story.story_points == StoryPointValue.FIVE

    def test_nested_acs_parsed(self):
        """Nested acceptance criteria should be parsed into AcceptanceCriterion tuples."""
        result = _parse_stories_response(VALID_STORIES_JSON, self._epics(), self._analysis())
        story = result[0]
        assert len(story.acceptance_criteria) == 3
        ac = story.acceptance_criteria[0]
        assert isinstance(ac, AcceptanceCriterion)
        assert ac.given == "I am on the registration page"
        assert ac.when == "I submit valid credentials"
        assert ac.then == "my account is created"

    def test_code_fence_stripping(self):
        """JSON wrapped in markdown code fences should be handled."""
        fenced = f"```json\n{VALID_STORIES_JSON}\n```"
        result = _parse_stories_response(fenced, self._epics(), self._analysis())
        assert len(result) == 3
        assert result[0].id == "US-E1-001"

    def test_bad_json_returns_fallback(self):
        """Invalid JSON should fall back to deterministic stories."""
        result = _parse_stories_response("this is not json", self._epics(), self._analysis())
        assert isinstance(result, list)
        assert len(result) == 6  # fallback: 2 per epic x 3 epics

    def test_invalid_priority_defaults_to_medium(self):
        """Invalid priority value should default to MEDIUM."""
        json_str = (
            '[{"id": "US-E1-001", "epic_id": "E1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "urgent"}]'
        )
        result = _parse_stories_response(json_str, self._epics(), self._analysis())
        assert result[0].priority == Priority.MEDIUM

    def test_invalid_points_snap_to_fibonacci(self):
        """Non-Fibonacci story points should snap to nearest Fibonacci."""
        json_str = (
            '[{"id": "US-E1-001", "epic_id": "E1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 4, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._epics(), self._analysis())
        assert result[0].story_points == StoryPointValue.THREE  # 4 snaps to 3

    def test_missing_ac_adds_fallback(self):
        """Stories with no valid acceptance criteria should get a generic fallback AC."""
        json_str = (
            '[{"id": "US-E1-001", "epic_id": "E1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [], '
            '"story_points": 3, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._epics(), self._analysis())
        assert len(result[0].acceptance_criteria) == 1
        assert "successfully" in result[0].acceptance_criteria[0].then

    def test_auto_id_generation(self):
        """Stories with missing IDs should get auto-generated IDs."""
        json_str = (
            '[{"epic_id": "E1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._epics(), self._analysis())
        assert result[0].id == "US-E1-001"

    def test_invalid_epic_id_skipped(self):
        """Stories with invalid epic_ids should be skipped."""
        json_str = (
            '[{"id": "US-X1-001", "epic_id": "X1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._epics(), self._analysis())
        # Story with invalid epic_id "X1" is skipped, fallback produces 6 stories
        assert len(result) == 6  # fallback: 2 per epic x 3 epics

    def test_empty_array_returns_fallback(self):
        """Empty JSON array should fall back."""
        result = _parse_stories_response("[]", self._epics(), self._analysis())
        assert len(result) == 6

    def test_non_list_json_returns_fallback(self):
        """JSON that's not a list should fall back."""
        result = _parse_stories_response('{"story": "not a list"}', self._epics(), self._analysis())
        assert len(result) == 6


class TestBuildFallbackStories:
    """Tests for _build_fallback_stories() helper."""

    def test_two_per_epic(self):
        """Fallback should produce exactly 2 stories per epic."""
        epics = make_sample_epics()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(epics, analysis)
        assert len(result) == 6  # 2 x 3 epics

    def test_all_are_user_story_instances(self):
        """All returned items should be UserStory dataclasses."""
        epics = make_sample_epics()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(epics, analysis)
        assert all(isinstance(s, UserStory) for s in result)

    def test_story_ids_follow_format(self):
        """Story IDs should follow US-{epic_id}-{NNN} format."""
        epics = make_sample_epics()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(epics, analysis)
        assert result[0].id == "US-E1-001"
        assert result[1].id == "US-E1-002"
        assert result[2].id == "US-E2-001"
        assert result[3].id == "US-E2-002"

    def test_epic_id_linkage(self):
        """Each story's epic_id should match the parent epic."""
        epics = make_sample_epics()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(epics, analysis)
        assert result[0].epic_id == "E1"
        assert result[1].epic_id == "E1"
        assert result[2].epic_id == "E2"
        assert result[3].epic_id == "E2"

    def test_default_points_and_priority(self):
        """Fallback stories should have reasonable default points and inherit priority."""
        epics = make_sample_epics()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(epics, analysis)
        # First story (core) has 5 points, second (setup) has 3 points
        assert result[0].story_points == StoryPointValue.FIVE
        assert result[1].story_points == StoryPointValue.THREE
        # Priority inherited from epic
        assert result[0].priority == Priority.HIGH  # E1 is HIGH

    def test_ac_presence(self):
        """Every fallback story should have at least one acceptance criterion."""
        epics = make_sample_epics()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(epics, analysis)
        for story in result:
            assert len(story.acceptance_criteria) >= 1
            ac = story.acceptance_criteria[0]
            assert isinstance(ac, AcceptanceCriterion)
            assert ac.given
            assert ac.when
            assert ac.then

    def test_empty_epics_returns_empty(self):
        """Empty epic list should produce no stories."""
        analysis = make_dummy_analysis()
        result = _build_fallback_stories([], analysis)
        assert result == []

    def test_uses_first_end_user(self):
        """Core stories should use the first end_user from analysis as persona."""
        epics = [Epic(id="E1", title="Auth", description="Auth features", priority=Priority.HIGH)]
        analysis = make_dummy_analysis(end_users=("admin", "customer"))
        result = _build_fallback_stories(epics, analysis)
        assert result[0].persona == "admin"

    def test_single_epic_produces_2_stories(self):
        """Single epic should produce 2 fallback stories (same as any other epic)."""
        single = [Epic(id="E1", title="My Project", description="desc", priority=Priority.HIGH)]
        analysis = make_dummy_analysis(skip_epics=True, goals=("Build API",))
        result = _build_fallback_stories(single, analysis)
        assert len(result) == 2

    def test_single_epic_all_link_to_epic(self):
        """All fallback stories for a single epic should reference that epic's ID."""
        single = [Epic(id="E1", title="My Project", description="desc", priority=Priority.HIGH)]
        analysis = make_dummy_analysis(skip_epics=True)
        result = _build_fallback_stories(single, analysis)
        assert all(s.epic_id == "E1" for s in result)

    def test_single_epic_ids_sequential(self):
        """Fallback stories for a single epic should have sequential IDs."""
        single = [Epic(id="E1", title="My Project", description="desc", priority=Priority.HIGH)]
        analysis = make_dummy_analysis(skip_epics=True)
        result = _build_fallback_stories(single, analysis)
        expected_ids = ["US-E1-001", "US-E1-002"]
        assert [s.id for s in result] == expected_ids


class TestFormatStories:
    """Tests for _format_stories() helper."""

    def _sample_stories(self) -> list[UserStory]:
        return [
            UserStory(
                id="US-E1-001",
                epic_id="E1",
                persona="end user",
                goal="register an account",
                benefit="I can access the app",
                acceptance_criteria=(
                    AcceptanceCriterion(given="on registration page", when="submit valid data", then="account created"),
                ),
                story_points=StoryPointValue.FIVE,
                priority=Priority.HIGH,
            ),
            UserStory(
                id="US-E2-001",
                epic_id="E2",
                persona="developer",
                goal="create a task",
                benefit="I can track work",
                acceptance_criteria=(AcceptanceCriterion(given="logged in", when="fill form", then="task saved"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.MEDIUM,
            ),
        ]

    def _sample_epics(self) -> list[Epic]:
        return [
            Epic(id="E1", title="Authentication", description="Auth features", priority=Priority.HIGH),
            Epic(id="E2", title="Task Management", description="Task CRUD", priority=Priority.MEDIUM),
        ]

    def test_returns_string(self):
        """Should return a non-empty markdown string."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test Project")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_project_name(self):
        """The project name should appear in the header."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Widget Builder")
        assert "Widget Builder" in result

    def test_includes_count(self):
        """The story count should be mentioned."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "2 user story(ies)" in result

    def test_includes_story_ids(self):
        """All story IDs should appear in the output."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "US-E1-001" in result
        assert "US-E2-001" in result

    def test_includes_persona_goal_benefit(self):
        """Story text elements should appear in the output."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "end user" in result
        assert "register an account" in result
        assert "I can access the app" in result

    def test_includes_priorities(self):
        """Priority values should appear in the output."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "high" in result
        assert "medium" in result

    def test_includes_points(self):
        """Story point values should appear in the output."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "5" in result
        assert "3" in result

    def test_includes_acceptance_criteria(self):
        """Acceptance criteria should appear with Given/When/Then format."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "Given" in result
        assert "When" in result
        assert "Then" in result

    def test_includes_review_footer(self):
        """The review prompt footer should be present."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "[Accept / Edit / Reject]" in result

    def test_grouped_by_epic(self):
        """Stories should be grouped under their parent epic headers."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        # Epic headers should appear
        assert "E1: Authentication" in result
        assert "E2: Task Management" in result
        # E1 header should come before E2 header
        assert result.index("E1: Authentication") < result.index("E2: Task Management")


class TestStoryWriter:
    """Tests for the story_writer() node function."""

    def _make_state(self, **extras: object) -> dict:
        """Build a minimal state with project_analysis and epics for story writer tests."""
        analysis = make_dummy_analysis()
        epics = make_sample_epics()
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "epics": epics,
        }
        state.update(extras)
        return state

    def test_returns_stories_list(self, monkeypatch):
        """story_writer should return a list of UserStory instances."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert "stories" in result
        assert isinstance(result["stories"], list)
        assert all(isinstance(s, UserStory) for s in result["stories"])

    def test_returns_ai_message(self, monkeypatch):
        """story_writer should return an AIMessage with the formatted stories."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)

    def test_ai_message_contains_story_info(self, monkeypatch):
        """The AIMessage should contain story IDs and content."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        content = result["messages"][0].content
        assert "US-E1-001" in content
        assert "register an account" in content

    def test_bad_json_uses_fallback(self, monkeypatch):
        """When LLM returns bad JSON, the fallback should produce valid stories."""
        fake_response = MagicMock()
        fake_response.content = "not valid json at all"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert isinstance(result["stories"], list)
        assert len(result["stories"]) == 6  # fallback: 2 per epic x 3 epics

    def test_llm_exception_uses_fallback(self, monkeypatch):
        """When the LLM call raises an exception, the fallback should be used."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("API down")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert isinstance(result["stories"], list)
        assert len(result["stories"]) == 6
        assert "messages" in result

    def test_calls_llm_with_temperature_zero(self, monkeypatch):
        """story_writer should use temperature=0.0 for deterministic output."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response

        captured_kwargs = {}

        def capture_get_llm(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_llm

        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", capture_get_llm)

        story_writer(self._make_state())
        assert captured_kwargs.get("temperature") == 0.0


# -- Discipline inference helpers ------------------------------------------


class TestInferDiscipline:
    """Tests for _infer_discipline() helper.

    Keyword-based discipline inference from story text fields.
    """

    def test_frontend_keywords(self):
        """Goal with frontend keywords -> FRONTEND."""
        story = make_story_for_inference(goal="build a UI component with responsive layout")
        assert _infer_discipline(story) == Discipline.FRONTEND

    def test_backend_keywords(self):
        """Goal with backend keywords -> BACKEND."""
        story = make_story_for_inference(goal="create an API endpoint for user data")
        assert _infer_discipline(story) == Discipline.BACKEND

    def test_infrastructure_keywords(self):
        """Goal with infrastructure keywords -> INFRASTRUCTURE."""
        story = make_story_for_inference(goal="set up CI pipeline and deploy to staging")
        assert _infer_discipline(story) == Discipline.INFRASTRUCTURE

    def test_design_keywords(self):
        """Goal with design keywords -> DESIGN."""
        story = make_story_for_inference(goal="create wireframe for the dashboard")
        assert _infer_discipline(story) == Discipline.DESIGN

    def test_testing_keywords(self):
        """Goal with testing keywords -> TESTING."""
        story = make_story_for_inference(goal="improve test coverage for authentication")
        assert _infer_discipline(story) == Discipline.TESTING

    def test_mixed_frontend_backend_returns_fullstack(self):
        """Both frontend and backend keywords -> FULLSTACK."""
        story = make_story_for_inference(goal="build a form UI component that calls the API endpoint")
        assert _infer_discipline(story) == Discipline.FULLSTACK

    def test_no_keywords_returns_fullstack(self):
        """Generic goal with no discipline keywords -> FULLSTACK (default)."""
        story = make_story_for_inference(goal="implement the main feature")
        assert _infer_discipline(story) == Discipline.FULLSTACK

    def test_keywords_in_acceptance_criteria(self):
        """Keywords in AC text should also be detected."""
        acs = (
            AcceptanceCriterion(given="the database is seeded", when="querying the endpoint", then="data is returned"),
        )
        story = make_story_for_inference(goal="implement data retrieval", acs=acs)
        assert _infer_discipline(story) == Discipline.BACKEND

    def test_keywords_in_persona(self):
        """Keywords in persona should be detected."""
        story = make_story_for_inference(persona="frontend developer", goal="add new feature")
        assert _infer_discipline(story) == Discipline.FRONTEND


# -- Story validation helpers ----------------------------------------------


class TestValidateStories:
    """Tests for _validate_stories() helper.

    Deterministic validation + auto-fix of story fields.
    """

    def _sample_epics(self) -> list[Epic]:
        return [
            Epic(id="E1", title="Auth", description="Auth features", priority=Priority.HIGH),
            Epic(id="E2", title="Tasks", description="Task features", priority=Priority.MEDIUM),
        ]

    def test_valid_stories_pass_through(self):
        """Stories meeting all checks should pass through unchanged with no warnings."""
        stories = [
            make_valid_story("US-E1-001", "E1"),
            make_valid_story("US-E1-002", "E1"),
            make_valid_story("US-E2-001", "E2"),
            make_valid_story("US-E2-002", "E2"),
        ]
        epics = self._sample_epics()
        validated, warnings = _validate_stories(stories, epics)
        assert len(validated) == 4
        assert len(warnings) == 0

    def test_adds_acs_when_fewer_than_3(self):
        """A story with fewer than 3 ACs should be padded to 3."""
        story = make_valid_story(num_acs=1)
        epics = self._sample_epics()
        validated, warnings = _validate_stories([story], epics)
        assert len(validated[0].acceptance_criteria) == 3

    def test_warning_when_acs_added(self):
        """Adding generic ACs should produce a warning."""
        story = make_valid_story(num_acs=1)
        epics = self._sample_epics()
        _, warnings = _validate_stories([story], epics)
        assert any("AC" in w for w in warnings)

    def test_empty_persona_gets_default(self):
        """Story with empty persona should default to 'user'."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-E1-001",
            epic_id="E1",
            persona="",
            goal="do something",
            benefit="value",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )
        epics = self._sample_epics()
        validated, warnings = _validate_stories([story], epics)
        assert validated[0].persona == "user"
        assert any("persona" in w for w in warnings)

    def test_empty_goal_gets_default(self):
        """Story with empty goal should get a default."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-E1-001",
            epic_id="E1",
            persona="dev",
            goal="",
            benefit="value",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )
        epics = self._sample_epics()
        validated, warnings = _validate_stories([story], epics)
        assert validated[0].goal != ""
        assert any("goal" in w for w in warnings)

    def test_empty_benefit_gets_default(self):
        """Story with empty benefit should get a default."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-E1-001",
            epic_id="E1",
            persona="dev",
            goal="do something",
            benefit="  ",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )
        epics = self._sample_epics()
        validated, warnings = _validate_stories([story], epics)
        assert validated[0].benefit.strip() != ""
        assert any("benefit" in w for w in warnings)

    def test_epic_count_warning_below_min(self):
        """Epic with fewer than MIN_STORIES_PER_EPIC stories should produce a warning."""
        # E1 has 1 story, E2 has 0 -- both under the minimum of 2
        stories = [make_valid_story("US-E1-001", "E1")]
        epics = self._sample_epics()
        _, warnings = _validate_stories(stories, epics)
        epic_warnings = [w for w in warnings if "minimum" in w.lower()]
        assert len(epic_warnings) >= 1

    def test_epic_count_warning_above_max(self):
        """Epic with more than MAX_STORIES_PER_EPIC stories should produce a warning."""
        stories = [make_valid_story(f"US-E1-{i:03d}", "E1") for i in range(1, 8)]
        epics = self._sample_epics()
        _, warnings = _validate_stories(stories, epics)
        epic_warnings = [w for w in warnings if "maximum" in w.lower()]
        assert len(epic_warnings) >= 1

    def test_returns_new_instances_when_fixed(self):
        """Validated stories with fixes should be new objects (frozen rebuild)."""
        story = make_valid_story(num_acs=1)
        epics = self._sample_epics()
        validated, _ = _validate_stories([story], epics)
        assert validated[0] is not story

    def test_preserves_discipline(self):
        """Discipline field should be preserved through validation."""
        story = make_valid_story(discipline=Discipline.FRONTEND)
        epics = self._sample_epics()
        validated, _ = _validate_stories([story], epics)
        assert validated[0].discipline == Discipline.FRONTEND

    def test_single_epic_uses_standard_bounds(self):
        """Single epic should use the same MIN/MAX bounds as any other epic."""
        single = [Epic(id="E1", title="My Project", description="desc", priority=Priority.HIGH)]
        stories = [make_valid_story(f"US-E1-{i:03d}", "E1") for i in range(1, 4)]
        _, warnings = _validate_stories(stories, single)
        epic_warnings = [w for w in warnings if "minimum" in w.lower() or "maximum" in w.lower()]
        assert len(epic_warnings) == 0

    def test_single_epic_warns_below_min(self):
        """Single epic with fewer than MIN stories should produce a warning."""
        single = [Epic(id="E1", title="My Project", description="desc", priority=Priority.HIGH)]
        stories = [make_valid_story("US-E1-001", "E1")]
        _, warnings = _validate_stories(stories, single)
        epic_warnings = [w for w in warnings if "minimum" in w.lower()]
        assert len(epic_warnings) >= 1

    def test_single_epic_warns_above_max(self):
        """Single epic with more than MAX stories should produce a warning."""
        single = [Epic(id="E1", title="My Project", description="desc", priority=Priority.HIGH)]
        stories = [make_valid_story(f"US-E1-{i:03d}", "E1") for i in range(1, 8)]
        _, warnings = _validate_stories(stories, single)
        epic_warnings = [w for w in warnings if "maximum" in w.lower()]
        assert len(epic_warnings) >= 1

    def test_empty_title_generated_from_goal(self):
        """Story with empty title should get a title derived from goal."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-E1-001",
            epic_id="E1",
            persona="developer",
            goal="create a bookmark endpoint for the API",
            benefit="value",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
            title="",
        )
        epics = self._sample_epics()
        validated, _ = _validate_stories([story], epics)
        assert validated[0].title != ""
        # Title should be title-cased and derived from goal words
        assert validated[0].title == "Create A Bookmark Endpoint For The Api"

    def test_existing_title_preserved(self):
        """Story with a title should keep it unchanged."""
        story = make_valid_story()
        epics = self._sample_epics()
        validated, _ = _validate_stories([story], epics)
        # make_valid_story doesn't set title, so it's "" — but let's test with an explicit title
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story_with_title = UserStory(
            id="US-E1-001",
            epic_id="E1",
            persona="developer",
            goal="do something",
            benefit="value",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
            title="My Custom Title",
        )
        validated, _ = _validate_stories([story_with_title], epics)
        assert validated[0].title == "My Custom Title"


# -- Discipline parsing in _parse_stories_response -------------------------


class TestParseStoriesDiscipline:
    """Tests for discipline parsing in _parse_stories_response()."""

    def _epics(self) -> list[Epic]:
        return make_sample_epics()

    def _analysis(self):
        return make_dummy_analysis()

    def test_parses_discipline_field(self):
        """Valid discipline in JSON should be parsed correctly."""
        json_str = (
            '[{"id": "US-E1-001", "epic_id": "E1", "persona": "user", "goal": "build a form", '
            '"benefit": "better UX", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high", "discipline": "frontend"}]'
        )
        result = _parse_stories_response(json_str, self._epics(), self._analysis())
        assert result[0].discipline == Discipline.FRONTEND

    def test_invalid_discipline_infers(self):
        """Invalid discipline value should trigger inference."""
        json_str = (
            '[{"id": "US-E1-001", "epic_id": "E1", "persona": "user", '
            '"goal": "create an API endpoint", '
            '"benefit": "data access", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high", "discipline": "invalid_value"}]'
        )
        result = _parse_stories_response(json_str, self._epics(), self._analysis())
        # Should infer BACKEND from "API endpoint" keywords
        assert result[0].discipline == Discipline.BACKEND

    def test_missing_discipline_infers(self):
        """Missing discipline field should trigger inference."""
        json_str = (
            '[{"id": "US-E1-001", "epic_id": "E1", "persona": "user", '
            '"goal": "build a responsive page layout", '
            '"benefit": "better UX", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._epics(), self._analysis())
        # Should infer FRONTEND from "page layout responsive" keywords
        assert result[0].discipline == Discipline.FRONTEND


# -- Fallback stories discipline -------------------------------------------


class TestFallbackStoriesDiscipline:
    """Tests for discipline tagging in _build_fallback_stories()."""

    def test_core_story_is_fullstack(self):
        """Fallback core-functionality story should be tagged FULLSTACK."""
        epics = [Epic(id="E1", title="Auth", description="Auth features", priority=Priority.HIGH)]
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(epics, analysis)
        assert result[0].discipline == Discipline.FULLSTACK

    def test_setup_story_is_testing(self):
        """Fallback setup/testing story should be tagged TESTING."""
        epics = [Epic(id="E1", title="Auth", description="Auth features", priority=Priority.HIGH)]
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(epics, analysis)
        assert result[1].discipline == Discipline.TESTING


# -- Format stories with discipline and warnings --------------------------


class TestFormatStoriesDiscipline:
    """Tests for discipline display and warnings in _format_stories()."""

    def _sample_stories(self) -> list[UserStory]:
        return [
            UserStory(
                id="US-E1-001",
                epic_id="E1",
                persona="end user",
                goal="register an account",
                benefit="I can access the app",
                acceptance_criteria=(
                    AcceptanceCriterion(given="on reg page", when="submit data", then="account created"),
                ),
                story_points=StoryPointValue.FIVE,
                priority=Priority.HIGH,
                discipline=Discipline.BACKEND,
            ),
        ]

    def _sample_epics(self) -> list[Epic]:
        return [Epic(id="E1", title="Auth", description="Auth features", priority=Priority.HIGH)]

    def test_shows_discipline_in_output(self):
        """Formatted output should include the discipline tag."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "Discipline:** backend" in result

    def test_shows_warnings_when_provided(self):
        """Warnings should appear in a Validation Notes section."""
        warnings = ["Story US-E1-001 had only 1 AC — added 2 generic ACs."]
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test", warnings=warnings)
        assert "Validation Notes" in result
        assert "Story US-E1-001" in result

    def test_no_warnings_section_when_empty(self):
        """No warnings -> no Validation Notes section."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test", warnings=[])
        assert "Validation Notes" not in result

    def test_no_warnings_section_when_none(self):
        """Default (None warnings) -> no Validation Notes section."""
        result = _format_stories(self._sample_stories(), self._sample_epics(), "Test")
        assert "Validation Notes" not in result


# -- Story writer validates stories ----------------------------------------


class TestStoryWriterValidation:
    """Tests for validation integration in the story_writer() node."""

    def _make_state(self) -> dict:
        analysis = make_dummy_analysis()
        epics = make_sample_epics()
        return {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "epics": epics,
        }

    def test_stories_are_validated(self, monkeypatch):
        """story_writer should validate stories (e.g. pad ACs to 3)."""
        # JSON with a story that has only 1 AC -- validation should pad to 3
        single_ac_json = (
            '[{"id": "US-E1-001", "epic_id": "E1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high", "discipline": "backend"}]'
        )
        fake_response = MagicMock()
        fake_response.content = single_ac_json
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        # The single AC should have been padded to 3 by validation
        assert len(result["stories"][0].acceptance_criteria) == 3
