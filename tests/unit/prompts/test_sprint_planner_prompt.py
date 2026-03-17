"""Tests for the sprint planner prompt template."""

from scrum_agent.prompts.sprint_planner import (
    MAX_SPRINTS,
    get_sprint_planner_prompt,
)


def _make_prompt(**overrides: object) -> str:
    """Build a prompt with sensible defaults, overriding any kwargs."""
    defaults = {
        "project_name": "Todo App",
        "project_description": "A full-stack todo application",
        "velocity": 20,
        "target_sprints": 3,
        "stories_block": (
            "### E1: User Authentication (high)\n"
            "- **US-E1-001** | 5 pts | high | backend — register an account\n"
            "- **US-E1-002** | 3 pts | high | backend — log in to my account\n"
        ),
    }
    defaults.update(overrides)
    return get_sprint_planner_prompt(**defaults)


class TestGetSprintPlannerPrompt:
    """Tests for get_sprint_planner_prompt()."""

    def test_returns_string(self):
        """get_sprint_planner_prompt should return a non-empty string."""
        result = _make_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_project_name(self):
        """The project name should appear in the prompt."""
        result = _make_prompt(project_name="Widget Builder")
        assert "Widget Builder" in result

    def test_includes_project_description(self):
        """The project description should appear in the prompt."""
        result = _make_prompt(project_description="A widget management platform")
        assert "A widget management platform" in result

    def test_includes_velocity(self):
        """The velocity should appear in the prompt."""
        result = _make_prompt(velocity=15)
        assert "15" in result

    def test_includes_target_sprints(self):
        """The target sprint count should appear in the prompt."""
        result = _make_prompt(target_sprints=4)
        assert "4" in result

    def test_includes_stories_block(self):
        """The stories block should appear in the prompt."""
        result = _make_prompt(stories_block="- **US-E1-001** | 5 pts | high | backend — register")
        assert "US-E1-001" in result
        assert "register" in result

    def test_includes_json_schema_with_all_fields(self):
        """The JSON schema should include all Sprint fields."""
        result = _make_prompt()
        assert '"id"' in result
        assert '"name"' in result
        assert '"goal"' in result
        assert '"capacity_points"' in result
        assert '"story_ids"' in result

    def test_includes_priority_ordering_rule(self):
        """The prompt should mention priority ordering for earlier sprints."""
        result = _make_prompt()
        assert "Critical" in result
        assert "High" in result

    def test_includes_spike_early_rule(self):
        """The prompt should instruct scheduling spike stories early."""
        result = _make_prompt()
        assert "spike" in result.lower()

    def test_includes_capacity_constraint_rule(self):
        """The prompt should mention the velocity capacity constraint."""
        result = _make_prompt()
        assert "capacity" in result.lower() or "velocity" in result.lower()

    def test_includes_chain_of_thought(self):
        """The prompt should include chain-of-thought instructions."""
        result = _make_prompt()
        assert "Think step by step" in result

    def test_includes_json_only_instruction(self):
        """The prompt should instruct the LLM to return only JSON."""
        result = _make_prompt()
        assert "Return ONLY the JSON array" in result

    def test_auto_calculate_when_target_zero(self):
        """When target_sprints is 0, the prompt should say to auto-calculate."""
        result = _make_prompt(target_sprints=0)
        assert "Calculate" in result


class TestSprintPlannerPromptConstants:
    """Tests for prompt constants."""

    def test_max_sprints_is_12(self):
        assert MAX_SPRINTS == 12


class TestSprintPlannerPromptImports:
    """Verify imports from the expected locations."""

    def test_importable_from_sprint_planner_module(self):
        from scrum_agent.prompts.sprint_planner import get_sprint_planner_prompt as imported_fn

        assert imported_fn is get_sprint_planner_prompt
