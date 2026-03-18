"""Tests for the feature generator prompt template."""

from scrum_agent.prompts.feature_generator import (
    _ALLOWED_PRIORITIES,
    MAX_FEATURES,
    MIN_FEATURES,
    get_feature_generator_prompt,
)  # noqa: I001 — _ALLOWED_PRIORITIES is a private constant needed for validation tests


def _make_prompt(**overrides: str) -> str:
    """Build a prompt with sensible defaults, overriding any kwargs."""
    defaults = {
        "project_name": "Todo App",
        "project_description": "A full-stack todo application",
        "project_type": "greenfield",
        "goals": "- Task management\n- User authentication",
        "end_users": "- developers\n- project managers",
        "target_state": "Deployed to production with CI/CD",
        "tech_stack": "- React\n- FastAPI\n- PostgreSQL",
        "constraints": "- Must use AWS",
        "risks": "- Tight timeline",
        "target_sprints": "4",
    }
    defaults.update(overrides)
    return get_feature_generator_prompt(**defaults)


class TestGetFeatureGeneratorPrompt:
    """Tests for get_feature_generator_prompt()."""

    def test_returns_string(self):
        """get_feature_generator_prompt should return a non-empty string."""
        result = _make_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_project_name(self):
        """The project name should appear in the prompt."""
        result = _make_prompt(project_name="Widget Builder")
        assert "Widget Builder" in result

    def test_includes_project_description(self):
        """The project description should appear in the prompt."""
        result = _make_prompt(project_description="Build amazing widgets")
        assert "Build amazing widgets" in result

    def test_includes_project_type(self):
        """The project type should appear in the prompt."""
        result = _make_prompt(project_type="existing codebase")
        assert "existing codebase" in result

    def test_includes_goals(self):
        """The goals should appear in the prompt."""
        result = _make_prompt(goals="- Build a REST API\n- Add auth")
        assert "Build a REST API" in result
        assert "Add auth" in result

    def test_includes_tech_stack(self):
        """The tech stack should appear in the prompt."""
        result = _make_prompt(tech_stack="- Python\n- Django")
        assert "Python" in result
        assert "Django" in result

    def test_includes_risks(self):
        """The risks should appear in the prompt."""
        result = _make_prompt(risks="- Tight timeline\n- New tech")
        assert "Tight timeline" in result

    def test_includes_json_schema(self):
        """The JSON schema with feature fields should be present."""
        result = _make_prompt()
        assert '"id"' in result
        assert '"title"' in result
        assert '"description"' in result
        assert '"priority"' in result

    def test_includes_feature_count_guidance(self):
        """The prompt should specify the min and max feature count."""
        result = _make_prompt()
        assert str(MIN_FEATURES) in result
        assert str(MAX_FEATURES) in result

    def test_includes_priority_values(self):
        """The prompt should list allowed priority values."""
        result = _make_prompt()
        for priority in _ALLOWED_PRIORITIES:
            assert priority in result

    def test_includes_json_only_instruction(self):
        """The prompt should instruct the LLM to return only JSON."""
        result = _make_prompt()
        assert "Return ONLY the JSON array" in result

    def test_includes_chain_of_thought(self):
        """The prompt should include chain-of-thought instructions."""
        result = _make_prompt()
        assert "Think step by step" in result

    def test_includes_target_sprints(self):
        """The target sprints value should appear in the prompt."""
        result = _make_prompt(target_sprints="6")
        assert "6" in result


class TestFeatureGeneratorPromptConstants:
    """Tests for prompt constants."""

    def test_min_features_is_3(self):
        assert MIN_FEATURES == 3

    def test_max_features_is_6(self):
        assert MAX_FEATURES == 6

    def test_allowed_priorities_match_enum(self):
        """Allowed priorities should match the Priority StrEnum values."""
        assert set(_ALLOWED_PRIORITIES) == {"critical", "high", "medium", "low"}


class TestFeatureGeneratorPromptImports:
    """Verify imports from the expected locations."""

    def test_importable_from_feature_generator_module(self):
        from scrum_agent.prompts.feature_generator import get_feature_generator_prompt as imported_fn

        assert imported_fn is get_feature_generator_prompt


class TestFeatureGeneratorPromptOutOfScope:
    """Tests for the out_of_scope parameter."""

    def test_out_of_scope_section_present_when_provided(self):
        """'Out of Scope' section should contain the provided items."""
        result = _make_prompt(out_of_scope="- Creating the EKS cluster\n- CI/CD pipeline setup")
        assert "Out of Scope" in result
        assert "Creating the EKS cluster" in result
        assert "CI/CD pipeline setup" in result

    def test_out_of_scope_exclusion_rule_present(self):
        """The prompt should include a rule about not creating features for out-of-scope items."""
        result = _make_prompt()
        assert "Do NOT create features for items listed under Out of Scope" in result

    def test_out_of_scope_defaults_to_empty(self):
        """Out of scope should be present as a section even when empty."""
        result = _make_prompt()
        assert "### Out of Scope" in result


class TestFeatureGeneratorPromptRepoContext:
    """Tests for the repo_context parameter."""

    def test_repo_context_section_present_when_provided(self):
        """'Repository Context' section should appear when repo_context is given."""
        repo_data = "## File Tree\n- src/\n- tests/"
        result = _make_prompt(repo_context=repo_data)
        assert "Repository Context" in result
        assert repo_data in result

    def test_repo_context_section_absent_when_none(self):
        """'Repository Context' section should be absent when repo_context is None."""
        result = _make_prompt(repo_context=None)
        assert "Repository Context" not in result

    def test_repo_context_section_absent_by_default(self):
        """'Repository Context' section should be absent when repo_context is omitted."""
        result = _make_prompt()
        assert "Repository Context" not in result
