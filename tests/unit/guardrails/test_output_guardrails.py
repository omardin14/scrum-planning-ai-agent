"""Tests for output guardrails — programmatic validation of generated artifacts."""

from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Priority,
    Sprint,
    StoryPointValue,
    UserStory,
)
from scrum_agent.output_guardrails import (
    validate_ac_coverage,
    validate_output,
    validate_scope_vs_capacity,
    validate_sprint_capacity,
    validate_story_format,
)


def _story(
    id: str = "US-E1-001",
    persona: str = "developer",
    goal: str = "deploy the app",
    benefit: str = "users can access it",
    points: StoryPointValue = StoryPointValue.THREE,
    acs: tuple[AcceptanceCriterion, ...] | None = None,
) -> UserStory:
    if acs is None:
        acs = (
            AcceptanceCriterion(given="the app is built", when="I deploy", then="it runs"),
            AcceptanceCriterion(given="invalid config", when="I deploy", then="error is shown"),
        )
    return UserStory(
        id=id,
        epic_id="E1",
        persona=persona,
        goal=goal,
        benefit=benefit,
        acceptance_criteria=acs,
        story_points=points,
        priority=Priority.MEDIUM,
    )


def _sprint(id: str = "S1", name: str = "Sprint 1", story_ids: tuple[str, ...] = ("US-E1-001",)) -> Sprint:
    return Sprint(id=id, name=name, goal="Deliver features", capacity_points=0, story_ids=story_ids)


# ---------------------------------------------------------------------------
# validate_story_format
# ---------------------------------------------------------------------------


class TestValidateStoryFormat:
    def test_valid_story_no_warnings(self):
        assert validate_story_format([_story()]) == []

    def test_empty_persona_warns(self):
        warnings = validate_story_format([_story(persona="")])
        assert len(warnings) == 1
        assert "persona" in warnings[0]

    def test_empty_goal_warns(self):
        warnings = validate_story_format([_story(goal="")])
        assert len(warnings) == 1
        assert "goal" in warnings[0]

    def test_empty_benefit_warns(self):
        warnings = validate_story_format([_story(benefit="")])
        assert len(warnings) == 1
        assert "benefit" in warnings[0]

    def test_multiple_missing_fields(self):
        warnings = validate_story_format([_story(persona="", goal="x", benefit="")])
        assert len(warnings) == 1
        assert "persona" in warnings[0]
        assert "benefit" in warnings[0]

    def test_too_short_field(self):
        warnings = validate_story_format([_story(persona="x")])
        assert len(warnings) == 1
        assert "persona" in warnings[0]

    def test_empty_list_no_warnings(self):
        assert validate_story_format([]) == []


# ---------------------------------------------------------------------------
# validate_ac_coverage
# ---------------------------------------------------------------------------


class TestValidateAcCoverage:
    def test_happy_and_negative_no_warnings(self):
        """Story with both happy and negative ACs should pass."""
        assert validate_ac_coverage([_story()]) == []

    def test_single_ac_warns(self):
        acs = (AcceptanceCriterion(given="ok", when="I act", then="it works"),)
        warnings = validate_ac_coverage([_story(acs=acs)])
        assert len(warnings) == 1
        assert "only 1" in warnings[0]

    def test_all_happy_path_warns(self):
        acs = (
            AcceptanceCriterion(given="user is logged in", when="they click save", then="data is saved"),
            AcceptanceCriterion(given="user is on dashboard", when="they view stats", then="charts render"),
        )
        warnings = validate_ac_coverage([_story(acs=acs)])
        assert len(warnings) == 1
        assert "happy-path" in warnings[0]

    def test_negative_keyword_detected(self):
        acs = (
            AcceptanceCriterion(given="user is logged in", when="they save", then="data is saved"),
            AcceptanceCriterion(given="invalid input", when="they submit", then="error is shown"),
        )
        assert validate_ac_coverage([_story(acs=acs)]) == []

    def test_empty_list_no_warnings(self):
        assert validate_ac_coverage([]) == []


# ---------------------------------------------------------------------------
# validate_sprint_capacity
# ---------------------------------------------------------------------------


class TestValidateSprintCapacity:
    def test_within_capacity_no_warnings(self):
        stories = [_story(points=StoryPointValue.THREE)]
        sprints = [_sprint()]
        assert validate_sprint_capacity(sprints, stories, velocity=10) == []

    def test_over_capacity_warns(self):
        stories = [
            _story(id="US-E1-001", points=StoryPointValue.FIVE),
            _story(id="US-E1-002", points=StoryPointValue.EIGHT),
        ]
        sprints = [_sprint(story_ids=("US-E1-001", "US-E1-002"))]
        warnings = validate_sprint_capacity(sprints, stories, velocity=10)
        assert len(warnings) == 1
        assert "exceeds" in warnings[0]
        assert "13 pts" in warnings[0]

    def test_zero_velocity_skips(self):
        assert validate_sprint_capacity([_sprint()], [_story()], velocity=0) == []


# ---------------------------------------------------------------------------
# validate_scope_vs_capacity
# ---------------------------------------------------------------------------


class TestValidateScopeVsCapacity:
    def test_within_scope_no_warnings(self):
        stories = [_story(points=StoryPointValue.THREE)]
        sprints = [_sprint()]
        assert validate_scope_vs_capacity(sprints, stories, velocity=10) == []

    def test_over_scope_warns(self):
        stories = [_story(id=f"US-E1-{i:03d}", points=StoryPointValue.EIGHT) for i in range(5)]
        sprints = [_sprint(story_ids=tuple(s.id for s in stories))]
        # 5 stories × 8 pts = 40 pts, capacity = 1 sprint × 10 = 10
        warnings = validate_scope_vs_capacity(sprints, stories, velocity=10)
        assert len(warnings) == 1
        assert "exceeds capacity" in warnings[0]

    def test_small_overrun_ignored(self):
        """≤10% overage is tolerated."""
        stories = [_story(id="US-E1-001", points=StoryPointValue.FIVE)]
        sprints = [_sprint(story_ids=("US-E1-001",))]
        # 5 pts vs 5 capacity (1 sprint × 5 velocity) = exactly at capacity
        assert validate_scope_vs_capacity(sprints, stories, velocity=5) == []

    def test_zero_velocity_skips(self):
        assert validate_scope_vs_capacity([_sprint()], [_story()], velocity=0) == []

    def test_no_sprints_skips(self):
        assert validate_scope_vs_capacity([], [_story()], velocity=10) == []


# ---------------------------------------------------------------------------
# validate_output (combined)
# ---------------------------------------------------------------------------


class TestValidateOutput:
    def test_all_clean_no_warnings(self):
        stories = [_story()]
        sprints = [_sprint()]
        assert validate_output(stories=stories, sprints=sprints, velocity=10) == []

    def test_no_artifacts_no_warnings(self):
        assert validate_output() == []

    def test_stories_only(self):
        stories = [_story(persona="")]
        warnings = validate_output(stories=stories)
        assert any("persona" in w for w in warnings)

    def test_sprints_without_stories_no_crash(self):
        """Sprints alone (no stories) should not crash."""
        assert validate_output(sprints=[_sprint()], velocity=10) == []
