"""Tests for the task decomposer prompt template."""

from scrum_agent.prompts.task_decomposer import (
    MAX_TASKS_PER_STORY,
    MIN_TASKS_PER_STORY,
    get_task_decomposer_prompt,
)


def _make_prompt(**overrides: str) -> str:
    """Build a prompt with sensible defaults, overriding any kwargs."""
    defaults = {
        "project_name": "Todo App",
        "project_type": "greenfield",
        "tech_stack": "- React\n- FastAPI\n- PostgreSQL",
        "stories_block": (
            "### E1: User Authentication\n\n"
            "**US-E1-001** (5 pts, backend)\n"
            "  As a user, I want to register, so that I can access the app.\n"
            "  ACs:\n"
            "    - Given on registration page, When submit valid data, Then account created\n"
        ),
    }
    defaults.update(overrides)
    return get_task_decomposer_prompt(**defaults)


class TestGetTaskDecomposerPrompt:
    """Tests for get_task_decomposer_prompt()."""

    def test_returns_string(self):
        """get_task_decomposer_prompt should return a non-empty string."""
        result = _make_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_project_name(self):
        """The project name should appear in the prompt."""
        result = _make_prompt(project_name="Widget Builder")
        assert "Widget Builder" in result

    def test_includes_project_type(self):
        """The project type should appear in the prompt."""
        result = _make_prompt(project_type="existing codebase")
        assert "existing codebase" in result

    def test_includes_tech_stack(self):
        """The tech stack should appear in the prompt."""
        result = _make_prompt(tech_stack="- Python\n- Django")
        assert "Python" in result
        assert "Django" in result

    def test_includes_stories_block(self):
        """The stories block should appear in the prompt."""
        result = _make_prompt(stories_block="**US-E1-001** (5 pts, backend)\n  As a user, I want to register")
        assert "US-E1-001" in result
        assert "register" in result

    def test_includes_json_schema_with_all_fields(self):
        """The JSON schema should include all Task fields."""
        result = _make_prompt()
        assert '"id"' in result
        assert '"story_id"' in result
        assert '"title"' in result
        assert '"description"' in result

    def test_includes_task_count_guidance(self):
        """The prompt should specify the min and max task count per story."""
        result = _make_prompt()
        assert str(MIN_TASKS_PER_STORY) in result
        assert str(MAX_TASKS_PER_STORY) in result

    def test_includes_imperative_instruction(self):
        """The prompt should instruct imperative (verb-first) task titles."""
        result = _make_prompt()
        assert "imperative" in result.lower()

    def test_includes_chain_of_thought(self):
        """The prompt should include chain-of-thought instructions."""
        result = _make_prompt()
        assert "Think step by step" in result

    def test_includes_json_only_instruction(self):
        """The prompt should instruct the LLM to return only JSON."""
        result = _make_prompt()
        assert "Return ONLY the JSON array" in result

    def test_includes_sequential_id_rule(self):
        """The prompt should specify the sequential ID format."""
        result = _make_prompt()
        assert "T-US-E1-001-01" in result

    def test_includes_concrete_artifacts_rule(self):
        """The prompt should mention referencing concrete technical artifacts."""
        result = _make_prompt()
        assert "files" in result.lower() or "endpoints" in result.lower() or "components" in result.lower()


class TestTaskDecomposerPromptConstants:
    """Tests for prompt constants."""

    def test_min_tasks_per_story_is_2(self):
        assert MIN_TASKS_PER_STORY == 2

    def test_max_tasks_per_story_is_5(self):
        assert MAX_TASKS_PER_STORY == 5


class TestTaskDecomposerPromptImports:
    """Verify imports from the expected locations."""

    def test_importable_from_task_decomposer_module(self):
        from scrum_agent.prompts.task_decomposer import get_task_decomposer_prompt as imported_fn

        assert imported_fn is get_task_decomposer_prompt
