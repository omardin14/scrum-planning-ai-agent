"""Tests for sprint planner node and its helper functions."""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from scrum_agent.agent.nodes import (
    _build_fallback_sprints,
    _format_sprints,
    _format_stories_for_sprint_planner,
    _merge_sprints_to_target,
    _parse_sprints_response,
    _validate_sprint_capacity,
    sprint_planner,
)
from scrum_agent.agent.state import (
    Priority,
    QuestionnaireState,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)
from tests._node_helpers import (
    VALID_SPRINTS_JSON,
    make_dummy_analysis,
    make_sample_features,
    make_sample_sprints,
    make_sample_stories,
)

# ── _format_stories_for_sprint_planner tests ──────────────────────────


class TestFormatStoriesForSprintPlanner:
    """Tests for _format_stories_for_sprint_planner() helper."""

    def test_returns_string(self):
        """Should return a non-empty string."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_sprint_planner(stories, features)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_story_ids(self):
        """All story IDs should appear in the output."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_sprint_planner(stories, features)
        assert "US-F1-001" in result
        assert "US-F1-002" in result

    def test_includes_points_and_priority(self):
        """Story points and priority should appear in the output."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_sprint_planner(stories, features)
        assert "5 pts" in result
        assert "3 pts" in result
        assert "high" in result

    def test_includes_feature_headers(self):
        """Feature titles should appear as group headers."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_sprint_planner(stories, features)
        assert "F1:" in result
        assert "User Authentication" in result

    def test_does_not_include_acceptance_criteria(self):
        """Sprint planner format should NOT include AC details (compact format)."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_sprint_planner(stories, features)
        assert "Given" not in result
        assert "When" not in result
        assert "Then" not in result


# ── _parse_sprints_response tests ──────────────────────────────────────


class TestParseSprintsResponse:
    """Tests for _parse_sprints_response() helper."""

    def _stories(self) -> list[UserStory]:
        return make_sample_stories()

    def test_parses_valid_json(self):
        """Valid JSON array should produce a list of Sprint dataclasses."""
        result = _parse_sprints_response(VALID_SPRINTS_JSON, self._stories(), velocity=20)
        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(s, Sprint) for s in result)

    def test_handles_code_fence_wrapped_json(self):
        """JSON wrapped in markdown code fences should be handled."""
        fenced = f"```json\n{VALID_SPRINTS_JSON}\n```"
        result = _parse_sprints_response(fenced, self._stories(), velocity=20)
        assert len(result) == 2
        assert result[0].id == "SP-1"

    def test_validates_story_ids(self):
        """Only stories with valid IDs should be included."""
        json_str = (
            '[{"id": "SP-1", "name": "Sprint 1", "goal": "Test", '
            '"capacity_points": 5, "story_ids": ["US-F1-001", "INVALID-999"]}]'
        )
        result = _parse_sprints_response(json_str, self._stories(), velocity=20)
        # Invalid story ID should be filtered out
        all_sids = set()
        for sp in result:
            all_sids.update(sp.story_ids)
        assert "INVALID-999" not in all_sids

    def test_skips_non_dict_items(self):
        """Non-dict items in the JSON array should be skipped."""
        json_str = (
            '[42, "string", {"id": "SP-1", "name": "Sprint 1", "goal": "Test", '
            '"capacity_points": 5, "story_ids": ["US-F1-001"]}]'
        )
        result = _parse_sprints_response(json_str, self._stories(), velocity=20)
        assert len(result) >= 1

    def test_auto_generates_ids(self):
        """Sprints with missing IDs should get auto-generated IDs."""
        json_str = '[{"name": "Sprint 1", "goal": "Test", "capacity_points": 5, "story_ids": ["US-F1-001"]}]'
        result = _parse_sprints_response(json_str, self._stories(), velocity=20)
        assert result[0].id == "SP-1"

    def test_invalid_json_returns_fallback(self):
        """Invalid JSON should fall back to greedy bin-packing."""
        result = _parse_sprints_response("this is not json", self._stories(), velocity=20)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(s, Sprint) for s in result)

    def test_non_list_json_returns_fallback(self):
        """Non-list JSON should fall back."""
        result = _parse_sprints_response('{"not": "a list"}', self._stories(), velocity=20)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_empty_array_returns_fallback(self):
        """Empty JSON array should fall back."""
        result = _parse_sprints_response("[]", self._stories(), velocity=20)
        assert isinstance(result, list)
        assert len(result) >= 1


# ── _validate_sprint_capacity tests ──────────────────────────────────


class TestValidateSprintCapacity:
    """Tests for _validate_sprint_capacity() helper."""

    def _stories(self) -> list[UserStory]:
        return make_sample_stories()

    def test_no_change_when_under_capacity(self):
        """Sprints under velocity should pass through unchanged (except recalculated capacity)."""
        sprints = [
            Sprint(id="SP-1", name="Sprint 1", goal="Auth", capacity_points=8, story_ids=("US-F1-001", "US-F1-002")),
        ]
        result = _validate_sprint_capacity(sprints, self._stories(), velocity=20)
        assert len(result) == 1
        assert set(result[0].story_ids) == {"US-F1-001", "US-F1-002"}

    def test_recalculates_capacity_points(self):
        """capacity_points should be recalculated from actual story points, not trusted from LLM."""
        sprints = [
            Sprint(
                id="SP-1",
                name="Sprint 1",
                goal="Auth",
                capacity_points=999,  # intentionally wrong
                story_ids=("US-F1-001", "US-F1-002"),
            ),
        ]
        result = _validate_sprint_capacity(sprints, self._stories(), velocity=20)
        # US-F1-001=5pts + US-F1-002=3pts = 8pts, not 999
        assert result[0].capacity_points == 8

    def test_redistributes_over_capacity(self):
        """If a sprint exceeds velocity, excess stories should be moved to the next sprint."""
        # Both stories in one sprint with velocity=5 → only US-F1-001 (5pts) fits
        sprints = [
            Sprint(id="SP-1", name="Sprint 1", goal="Auth", capacity_points=8, story_ids=("US-F1-001", "US-F1-002")),
        ]
        result = _validate_sprint_capacity(sprints, self._stories(), velocity=5)
        # US-F1-001 is 5pts (fits exactly), US-F1-002 is 3pts (overflows)
        assert len(result) == 2
        assert result[0].capacity_points <= 5
        assert result[1].capacity_points <= 5

    def test_no_duplicate_stories(self):
        """If a story appears in multiple sprints, only the first occurrence should be kept."""
        sprints = [
            Sprint(id="SP-1", name="Sprint 1", goal="Auth", capacity_points=5, story_ids=("US-F1-001",)),
            Sprint(id="SP-2", name="Sprint 2", goal="More", capacity_points=8, story_ids=("US-F1-001", "US-F1-002")),
        ]
        result = _validate_sprint_capacity(sprints, self._stories(), velocity=20)
        all_sids = []
        for sp in result:
            all_sids.extend(sp.story_ids)
        assert len(all_sids) == len(set(all_sids))

    def test_no_orphaned_stories(self):
        """All stories should appear in at least one sprint after validation."""
        # Only one story assigned — the other should be added as orphan
        sprints = [
            Sprint(id="SP-1", name="Sprint 1", goal="Auth", capacity_points=5, story_ids=("US-F1-001",)),
        ]
        result = _validate_sprint_capacity(sprints, self._stories(), velocity=20)
        all_sids = set()
        for sp in result:
            all_sids.update(sp.story_ids)
        story_ids = {s.id for s in self._stories()}
        assert all_sids == story_ids


# ── _build_fallback_sprints tests ──────────────────────────────────────


class TestBuildFallbackSprints:
    """Tests for _build_fallback_sprints() helper."""

    def test_returns_sprint_instances(self):
        """Fallback should produce Sprint dataclass instances."""
        stories = make_sample_stories()
        result = _build_fallback_sprints(stories, velocity=20)
        assert isinstance(result, list)
        assert all(isinstance(s, Sprint) for s in result)

    def test_respects_velocity_cap(self):
        """No sprint should exceed velocity (unless single story exceeds it)."""
        stories = make_sample_stories()
        result = _build_fallback_sprints(stories, velocity=5)
        for sp in result:
            # Allow exceeding only if the sprint has a single story
            if len(sp.story_ids) > 1:
                assert sp.capacity_points <= 5

    def test_priority_ordering(self):
        """Higher-priority stories should appear in earlier sprints."""
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="low priority thing",
                benefit="value",
                acceptance_criteria=(),
                story_points=StoryPointValue.THREE,
                priority=Priority.LOW,
            ),
            UserStory(
                id="US-F1-002",
                feature_id="F1",
                persona="user",
                goal="critical thing",
                benefit="value",
                acceptance_criteria=(),
                story_points=StoryPointValue.THREE,
                priority=Priority.CRITICAL,
            ),
        ]
        result = _build_fallback_sprints(stories, velocity=3)
        # Critical story should be in the first sprint
        assert "US-F1-002" in result[0].story_ids

    def test_all_stories_allocated(self):
        """Every story should appear in exactly one sprint."""
        stories = make_sample_stories()
        result = _build_fallback_sprints(stories, velocity=5)
        all_sids = set()
        for sp in result:
            all_sids.update(sp.story_ids)
        assert all_sids == {s.id for s in stories}

    def test_sequential_ids(self):
        """Sprint IDs should follow SP-1, SP-2, ... format."""
        stories = make_sample_stories()
        result = _build_fallback_sprints(stories, velocity=5)
        for i, sp in enumerate(result):
            assert sp.id == f"SP-{i + 1}"

    def test_empty_stories_returns_empty(self):
        """Empty story list should produce no sprints."""
        result = _build_fallback_sprints([], velocity=20)
        assert result == []

    def test_single_story_single_sprint(self):
        """A single story should produce a single sprint."""
        stories = [make_sample_stories()[0]]
        result = _build_fallback_sprints(stories, velocity=20)
        assert len(result) == 1
        assert "US-F1-001" in result[0].story_ids

    def test_story_larger_than_velocity_gets_own_sprint(self):
        """A story whose points exceed velocity should still get its own sprint."""
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="big story",
                benefit="value",
                acceptance_criteria=(),
                story_points=StoryPointValue.EIGHT,
                priority=Priority.HIGH,
            ),
        ]
        result = _build_fallback_sprints(stories, velocity=5)
        assert len(result) == 1
        assert "US-F1-001" in result[0].story_ids
        assert result[0].capacity_points == 8


# ── Auto-split / boundary tests ───────────────────────────────────────


class TestAutoSplitBoundary:
    """Test story point boundary enforcement and sprint capacity auto-redistribution.

    The 8-point maximum is enforced at the prompt level (StoryPointValue IntEnum
    only allows 1,2,3,5,8). These tests verify the downstream handling: sprint
    capacity redistribution for all-8-point stories, mixed sizes, and edge cases.
    """

    def test_all_eight_point_stories_one_per_sprint(self):
        """When all stories are 8pts and velocity=8, each story gets its own sprint."""
        stories = [
            UserStory(
                id=f"US-F1-{i:03d}",
                feature_id="F1",
                persona="user",
                goal=f"feature {i}",
                benefit="value",
                acceptance_criteria=(),
                story_points=StoryPointValue.EIGHT,
                priority=Priority.MEDIUM,
            )
            for i in range(1, 4)
        ]
        result = _build_fallback_sprints(stories, velocity=8)
        assert len(result) == 3
        for sp in result:
            assert len(sp.story_ids) == 1
            assert sp.capacity_points == 8

    def test_mixed_sizes_pack_efficiently(self):
        """Stories of mixed sizes should be packed without exceeding velocity."""
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="g1",
                benefit="b",
                acceptance_criteria=(),
                story_points=StoryPointValue.FIVE,
                priority=Priority.HIGH,
            ),
            UserStory(
                id="US-F1-002",
                feature_id="F1",
                persona="user",
                goal="g2",
                benefit="b",
                acceptance_criteria=(),
                story_points=StoryPointValue.THREE,
                priority=Priority.HIGH,
            ),
            UserStory(
                id="US-F1-003",
                feature_id="F1",
                persona="user",
                goal="g3",
                benefit="b",
                acceptance_criteria=(),
                story_points=StoryPointValue.TWO,
                priority=Priority.MEDIUM,
            ),
            UserStory(
                id="US-F1-004",
                feature_id="F1",
                persona="user",
                goal="g4",
                benefit="b",
                acceptance_criteria=(),
                story_points=StoryPointValue.ONE,
                priority=Priority.LOW,
            ),
        ]
        result = _build_fallback_sprints(stories, velocity=8)
        # 5+3=8 fits in one sprint, 2+1=3 fits in another
        assert len(result) == 2
        for sp in result:
            assert sp.capacity_points <= 8

    def test_validate_redistribution_creates_new_sprints(self):
        """Overloaded sprint should be split, creating additional sprints as needed."""
        stories = [
            UserStory(
                id=f"US-F1-{i:03d}",
                feature_id="F1",
                persona="user",
                goal=f"g{i}",
                benefit="b",
                acceptance_criteria=(),
                story_points=StoryPointValue.FIVE,
                priority=Priority.MEDIUM,
            )
            for i in range(1, 5)
        ]
        # All 4 stories (5pts each = 20pts) crammed into 1 sprint with velocity=10
        sprints = [
            Sprint(
                id="SP-1",
                name="Sprint 1",
                goal="Everything",
                capacity_points=20,
                story_ids=tuple(s.id for s in stories),
            ),
        ]
        result = _validate_sprint_capacity(sprints, stories, velocity=10)
        assert len(result) == 2
        assert result[0].capacity_points == 10
        assert result[1].capacity_points == 10

    def test_validate_single_oversized_story_gets_own_sprint(self):
        """A single 8pt story with velocity=5 should get its own sprint (not lost)."""
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="big",
                benefit="b",
                acceptance_criteria=(),
                story_points=StoryPointValue.EIGHT,
                priority=Priority.HIGH,
            ),
            UserStory(
                id="US-F1-002",
                feature_id="F1",
                persona="user",
                goal="small",
                benefit="b",
                acceptance_criteria=(),
                story_points=StoryPointValue.TWO,
                priority=Priority.MEDIUM,
            ),
        ]
        sprints = [
            Sprint(
                id="SP-1",
                name="Sprint 1",
                goal="Mix",
                capacity_points=10,
                story_ids=("US-F1-001", "US-F1-002"),
            ),
        ]
        result = _validate_sprint_capacity(sprints, stories, velocity=5)
        all_sids = set()
        for sp in result:
            all_sids.update(sp.story_ids)
        assert all_sids == {"US-F1-001", "US-F1-002"}
        assert any("US-F1-001" in sp.story_ids for sp in result)

    def test_story_point_value_enum_max_is_eight(self):
        """StoryPointValue enum should have 8 as the maximum — auto-split is prompt-enforced."""
        assert max(v.value for v in StoryPointValue) == 8

    def test_story_point_value_enum_is_fibonacci(self):
        """StoryPointValue should only contain Fibonacci values 1,2,3,5,8."""
        values = sorted(v.value for v in StoryPointValue)
        assert values == [1, 2, 3, 5, 8]


# ── _format_sprints tests ──────────────────────────────────────────────


class TestFormatSprints:
    """Tests for _format_sprints() helper."""

    def test_returns_non_empty_string(self):
        """Should return a non-empty markdown string."""
        sprints = make_sample_sprints()
        result = _format_sprints(sprints, make_sample_stories(), make_sample_features(), "Test Project", 20)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_project_name(self):
        """The project name should appear in the header."""
        sprints = make_sample_sprints()
        result = _format_sprints(sprints, make_sample_stories(), make_sample_features(), "Widget Builder", 20)
        assert "Widget Builder" in result

    def test_includes_velocity(self):
        """The velocity should appear in the output."""
        sprints = make_sample_sprints()
        result = _format_sprints(sprints, make_sample_stories(), make_sample_features(), "Test", 15)
        assert "15" in result

    def test_includes_sprint_goals(self):
        """Sprint goals should appear in the output."""
        sprints = make_sample_sprints()
        result = _format_sprints(sprints, make_sample_stories(), make_sample_features(), "Test", 20)
        assert "Auth foundation" in result
        assert "Login flow" in result

    def test_includes_story_ids(self):
        """Story IDs should appear in the output."""
        sprints = make_sample_sprints()
        result = _format_sprints(sprints, make_sample_stories(), make_sample_features(), "Test", 20)
        assert "US-F1-001" in result
        assert "US-F1-002" in result

    def test_includes_review_footer(self):
        """The review prompt footer should be present."""
        sprints = make_sample_sprints()
        result = _format_sprints(sprints, make_sample_stories(), make_sample_features(), "Test", 20)
        assert "[Accept / Edit / Reject]" in result


# ── sprint_planner node tests ──────────────────────────────────────────


class TestSprintPlanner:
    """Tests for the sprint_planner() node function."""

    def _make_state(self, **extras: object) -> dict:
        """Build a minimal state for sprint planner tests."""
        analysis = make_dummy_analysis()
        features = make_sample_features()
        stories = make_sample_stories()
        tasks = [
            Task(id="T-US-F1-001-01", story_id="US-F1-001", title="Impl registration", description="Build it"),
            Task(id="T-US-F1-002-01", story_id="US-F1-002", title="Impl login", description="Build it"),
        ]
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
            "tasks": tasks,
            "velocity_per_sprint": 20,
            "team_size": 4,
        }
        state.update(extras)
        return state

    def test_returns_sprints_and_messages(self, monkeypatch):
        """sprint_planner should return both 'sprints' and 'messages' keys."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = sprint_planner(self._make_state())
        assert "sprints" in result
        assert "messages" in result
        assert isinstance(result["sprints"], list)
        assert all(isinstance(s, Sprint) for s in result["sprints"])
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)

    def test_valid_story_ids_in_sprints(self, monkeypatch):
        """All story_ids in returned sprints should reference actual stories."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = self._make_state()
        story_ids = {s.id for s in state["stories"]}
        result = sprint_planner(state)
        for sp in result["sprints"]:
            for sid in sp.story_ids:
                assert sid in story_ids

    def test_llm_failure_uses_fallback(self, monkeypatch):
        """When the LLM call raises an exception, the fallback should be used."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("API down")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = sprint_planner(self._make_state())
        assert isinstance(result["sprints"], list)
        assert len(result["sprints"]) >= 1
        assert "messages" in result

    def test_no_sprint_exceeds_velocity(self, monkeypatch):
        """No sprint should exceed velocity (unless a single story exceeds it)."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = self._make_state(velocity_per_sprint=20)
        result = sprint_planner(state)
        for sp in result["sprints"]:
            if len(sp.story_ids) > 1:
                assert sp.capacity_points <= 20

    def test_all_stories_allocated(self, monkeypatch):
        """Every story should appear in exactly one sprint."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = self._make_state()
        story_ids = {s.id for s in state["stories"]}
        result = sprint_planner(state)
        allocated = set()
        for sp in result["sprints"]:
            allocated.update(sp.story_ids)
        assert allocated == story_ids

    def test_display_includes_project_name(self, monkeypatch):
        """The formatted AIMessage should include the project name."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = sprint_planner(self._make_state())
        content = result["messages"][0].content
        assert "Test Project" in content

    def test_uses_default_velocity_when_not_provided(self, monkeypatch):
        """When velocity_per_sprint is not in state, default should be team_size * 5."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        # Remove velocity_per_sprint, set team_size=3 → default velocity = 15
        state = self._make_state()
        del state["velocity_per_sprint"]
        state["team_size"] = 3
        result = sprint_planner(state)
        # Should still produce valid sprints
        assert isinstance(result["sprints"], list)
        assert len(result["sprints"]) >= 1


# ── Capacity warning tests ───────────────────────────────────────────


class TestCapacityWarning:
    """Tests for the sprint_planner capacity overflow detection.

    When total story points exceed velocity × target_sprints, the node should
    return early with a warning message and a negative capacity_override_target
    (encoding the recommended sprint count) instead of calling the LLM.
    """

    def _make_state(self, **extras: object) -> dict:
        """Build a minimal state for capacity warning tests."""
        analysis = make_dummy_analysis()
        features = make_sample_features()
        stories = make_sample_stories()  # 5 + 3 = 8 total points
        tasks = [
            Task(id="T-US-F1-001-01", story_id="US-F1-001", title="Impl", description="Build"),
        ]
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
            "tasks": tasks,
            "velocity_per_sprint": 3,  # Low velocity to trigger overflow (8 pts / 3 = 3 sprints needed)
            "team_size": 1,
            "target_sprints": 1,  # Target 1 sprint — can't fit 8 points at velocity 3
        }
        state.update(extras)
        return state

    def test_overflow_returns_warning_not_sprints(self):
        """When scope exceeds target, should return warning without calling LLM."""
        state = self._make_state()
        result = sprint_planner(state)
        # Should NOT have sprints (no LLM call)
        assert "sprints" not in result
        # Should have the capacity warning marker
        assert result["capacity_override_target"] < -1
        # Should have a warning message
        assert len(result["messages"]) == 1
        assert "story points" in result["messages"][0].content.lower()
        # Should include the original target so the TUI can show "Keep N sprints"
        assert result["_original_target_sprints"] == state["target_sprints"]

    def test_overflow_recommended_sprints_encoding(self):
        """The recommended sprint count should be encoded as -abs(recommended)."""
        state = self._make_state()  # 8 pts, velocity 3, target 1 → needs ceil(8/3)=3 sprints
        result = sprint_planner(state)
        recommended = abs(result["capacity_override_target"])
        assert recommended == 3  # ceil(8/3)

    def test_no_overflow_proceeds_normally(self, monkeypatch):
        """When scope fits in target, should call LLM and return sprints normally."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        # 8 pts, velocity 20, target 1 → 1 sprint fits (ceil(8/20)=1)
        state = self._make_state(velocity_per_sprint=20, target_sprints=1)
        result = sprint_planner(state)
        assert "sprints" in result
        assert "capacity_override_target" not in result

    def test_override_accepted_uses_new_target(self, monkeypatch):
        """When user accepted the recommendation, sprint_planner should use it as target."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        # capacity_override_target > 0 means user accepted
        state = self._make_state(capacity_override_target=3)
        result = sprint_planner(state)
        # Should proceed to LLM call and return sprints
        assert "sprints" in result

    def test_override_rejected_uses_original_target(self, monkeypatch):
        """When user rejected the recommendation, sprint_planner should proceed with original."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        # capacity_override_target = -1 means user rejected
        state = self._make_state(capacity_override_target=-1)
        result = sprint_planner(state)
        # Should proceed to LLM call and return sprints
        assert "sprints" in result

    def test_no_target_skips_capacity_check(self, monkeypatch):
        """When target_sprints is 0 (auto-calculate), skip capacity check."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = self._make_state(target_sprints=0)
        result = sprint_planner(state)
        # Should proceed without capacity warning
        assert "sprints" in result

    def test_review_mode_skips_capacity_check(self):
        """During a review re-run (edit/reject), capacity check should be skipped."""
        from scrum_agent.agent.state import ReviewDecision

        state = self._make_state(
            last_review_decision=ReviewDecision.REJECT,
            last_review_feedback="Make sprints shorter",
        )
        # Even though scope overflows, review mode should skip the check.
        # The node will call the LLM — we just need to verify it doesn't
        # short-circuit with a capacity warning.
        # We expect an LLM call error here (no mock), but NOT a capacity warning return.
        try:
            result = sprint_planner(state)
        except Exception:
            # LLM call fails without mock — that's fine, it means we got past the check
            pass
        else:
            # If it returns, it should have sprints (fallback) not a capacity warning
            assert "capacity_override_target" not in result or result.get("capacity_override_target", 0) >= 0

    def test_warning_message_includes_recommendation(self):
        """Warning message should mention the recommended sprint count."""
        state = self._make_state()  # 8 pts, velocity 3, target 1 → recommends 3
        result = sprint_planner(state)
        msg = result["messages"][0].content
        assert "3 sprints" in msg

    def test_overflow_returns_recommended_team_size(self):
        """When scope overflows, should return _recommended_team_size in state."""
        state = self._make_state()  # 8 pts, velocity 3 (1 eng), target 1
        result = sprint_planner(state)
        # velocity_per_engineer = 3 // 1 = 3
        # min_team_size = ceil(8 / (3 * 1)) = 3
        assert result["_recommended_team_size"] == 3

    def test_team_override_recalculates_velocity(self, monkeypatch):
        """When _capacity_team_override is set, velocity should scale with new team size."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        # capacity_override_target = -1, _capacity_team_override = 3
        # Original: velocity 3, team 1 → vel_per_eng = 3
        # Override: velocity = 3 * 3 = 9, which fits 8 pts in 1 sprint
        state = self._make_state(
            capacity_override_target=-1,
            _capacity_team_override=3,
        )
        result = sprint_planner(state)
        # Should proceed to LLM call and return sprints (velocity now covers scope)
        assert "sprints" in result
        # Verify the LLM was called (not short-circuited by capacity warning)
        assert mock_llm.invoke.called
        # Verify updated velocity and team size are persisted back to state
        assert result["velocity_per_sprint"] == 9  # 3 pts/eng × 3 engineers
        assert result["team_size"] == 3
        assert result["net_velocity_per_sprint"] == 9

    def test_overflow_team_size_capped_by_jira_q9_text(self):
        """When Jira team size is in Q9 text, min_team_size should not exceed it."""
        qs = QuestionnaireState(completed=True)
        # Q9 with Jira provenance — 2 team members on Jira board
        qs.answers[9] = "5 pts/dev/sprint (from Jira: 10 pts team avg, 2 team member(s))"
        state = self._make_state(questionnaire=qs)
        # 8 pts, velocity 3, target 1 → uncapped min_team_size = ceil(8 / (3*1)) = 3
        # But Jira team is 2, so should be capped to 2
        result = sprint_planner(state)
        assert result["_recommended_team_size"] == 2

    def test_overflow_team_size_capped_by_jira_org_field(self):
        """When _jira_org_team_size is set (even with zero velocity), cap applies."""
        qs = QuestionnaireState(completed=True)
        # No Jira provenance in Q9 — velocity was zero so Q9 is the default
        qs.answers[9] = "No historical velocity — will use default of 5 points per engineer per sprint"
        # But _jira_org_team_size was set from the team size data
        qs._jira_org_team_size = 2
        state = self._make_state(questionnaire=qs)
        # 8 pts, velocity 3, target 1 → uncapped min_team_size = ceil(8 / (3*1)) = 3
        # But Jira org has 2 members, so should be capped to 2
        result = sprint_planner(state)
        assert result["_recommended_team_size"] == 2

    def test_overflow_team_size_uncapped_without_jira(self):
        """Without Jira data, min_team_size is not capped."""
        qs = QuestionnaireState(completed=True)
        qs.answers[9] = "5 pts/sprint (estimated)"  # No Jira provenance
        qs._jira_org_team_size = None  # No Jira data
        state = self._make_state(questionnaire=qs)
        result = sprint_planner(state)
        # Should be uncapped: ceil(8 / (3*1)) = 3
        assert result["_recommended_team_size"] == 3

    def test_team_override_does_not_enforce_target(self, monkeypatch):
        """When team override is set, enforce_target should NOT be True."""
        fake_response = MagicMock()
        fake_response.content = VALID_SPRINTS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = self._make_state(
            capacity_override_target=-1,
            _capacity_team_override=3,
        )
        sprint_planner(state)
        # The prompt should NOT contain "HARD DEADLINE" (which enforce_target adds)
        prompt_text = mock_llm.invoke.call_args[0][0][0].content
        assert "HARD DEADLINE" not in prompt_text


# ── _merge_sprints_to_target tests ───────────────────────────────────


class TestMergeSprintsToTarget:
    """Tests for _merge_sprints_to_target() — merging sprints down to a deadline."""

    def _make_sprints(self, n: int, stories_per: int = 2) -> list[Sprint]:
        """Build n sprints with stories_per stories each."""
        sprints = []
        sid = 1
        for i in range(n):
            story_ids = tuple(f"US-F1-{sid + j:03d}" for j in range(stories_per))
            sprints.append(
                Sprint(
                    id=f"SP-{i + 1}",
                    name=f"Sprint {i + 1}",
                    goal=f"Sprint {i + 1} goal",
                    capacity_points=10,
                    story_ids=story_ids,
                )
            )
            sid += stories_per
        return sprints

    def _make_stories_for_sprints(self, sprints: list[Sprint]) -> list[UserStory]:
        """Build UserStory objects for all story_ids in the given sprints."""
        stories = []
        for sp in sprints:
            for sid in sp.story_ids:
                stories.append(
                    UserStory(
                        id=sid,
                        feature_id="F1",
                        persona="user",
                        goal="do something",
                        benefit="value",
                        acceptance_criteria=(),
                        story_points=StoryPointValue.FIVE,
                        priority=Priority.HIGH,
                    )
                )
        return stories

    def test_no_merge_when_under_target(self):
        """Should return sprints unchanged if count <= target."""
        sprints = self._make_sprints(2)
        stories = self._make_stories_for_sprints(sprints)
        result = _merge_sprints_to_target(sprints, 3, stories)
        assert len(result) == 2

    def test_merges_4_to_2(self):
        """4 sprints merged to 2 should produce exactly 2 sprints."""
        sprints = self._make_sprints(4)
        stories = self._make_stories_for_sprints(sprints)
        result = _merge_sprints_to_target(sprints, 2, stories)
        assert len(result) == 2

    def test_all_stories_preserved(self):
        """All story IDs must be present after merging."""
        sprints = self._make_sprints(4)
        stories = self._make_stories_for_sprints(sprints)
        original_ids = {sid for sp in sprints for sid in sp.story_ids}
        result = _merge_sprints_to_target(sprints, 2, stories)
        merged_ids = {sid for sp in result for sid in sp.story_ids}
        assert merged_ids == original_ids

    def test_no_duplicate_stories(self):
        """No story should appear in more than one merged sprint."""
        sprints = self._make_sprints(4)
        stories = self._make_stories_for_sprints(sprints)
        result = _merge_sprints_to_target(sprints, 2, stories)
        all_ids = [sid for sp in result for sid in sp.story_ids]
        assert len(all_ids) == len(set(all_ids))

    def test_sprint_numbering_with_starting_number(self):
        """Merged sprints should use starting_sprint_number for naming."""
        sprints = self._make_sprints(4)
        stories = self._make_stories_for_sprints(sprints)
        result = _merge_sprints_to_target(sprints, 2, stories, starting_sprint_number=3)
        assert result[0].id == "SP-3"
        assert result[0].name == "Sprint 3"
        assert result[1].id == "SP-4"
        assert result[1].name == "Sprint 4"

    def test_capacity_points_reflect_actual_points(self):
        """capacity_points should equal the sum of story points in each merged sprint."""
        sprints = self._make_sprints(4)
        stories = self._make_stories_for_sprints(sprints)
        result = _merge_sprints_to_target(sprints, 2, stories)
        points_map = {s.id: s.story_points.value for s in stories}
        for sp in result:
            expected = sum(points_map.get(sid, 0) for sid in sp.story_ids)
            assert sp.capacity_points == expected

    def test_even_distribution(self):
        """Stories should be distributed roughly evenly across merged sprints."""
        sprints = self._make_sprints(4, stories_per=2)  # 8 stories, 5 pts each = 40 pts
        stories = self._make_stories_for_sprints(sprints)
        result = _merge_sprints_to_target(sprints, 2, stories)
        # 40 pts split across 2 → each should have ~20 pts
        for sp in result:
            assert sp.capacity_points == 20  # 4 stories × 5 pts each

    def test_zero_target_returns_unchanged(self):
        """Target of 0 should return sprints unchanged (no merge)."""
        sprints = self._make_sprints(3)
        stories = self._make_stories_for_sprints(sprints)
        result = _merge_sprints_to_target(sprints, 0, stories)
        assert len(result) == 3
