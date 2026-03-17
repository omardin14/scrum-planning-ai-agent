"""Tests for the analyzer prompt template."""

from scrum_agent.prompts.analyzer import get_analyzer_prompt


class TestGetAnalyzerPrompt:
    """Tests for get_analyzer_prompt()."""

    def test_returns_string(self):
        """get_analyzer_prompt should return a non-empty string."""
        result = get_analyzer_prompt("Q1. What?\nA: A project\n", 3, 15)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_answers_block(self):
        """The answers block should be included in the prompt."""
        answers = "Q1. What is the project?\nA: Build a todo app\n"
        result = get_analyzer_prompt(answers, 3, 15)
        assert "Build a todo app" in result

    def test_includes_team_size(self):
        """Team size should be injected into the prompt."""
        result = get_analyzer_prompt("answers", 5, 25)
        assert "5 engineer(s)" in result

    def test_includes_velocity(self):
        """Velocity should be injected into the prompt."""
        result = get_analyzer_prompt("answers", 3, 15)
        assert "15 story points per sprint" in result

    def test_includes_json_schema(self):
        """The JSON schema should be present for the LLM to follow."""
        result = get_analyzer_prompt("answers", 3, 15)
        assert "project_name" in result
        assert "project_description" in result
        assert "sprint_length_weeks" in result
        assert "target_sprints" in result

    def test_includes_extraction_rules(self):
        """Key extraction rules should be in the prompt."""
        result = get_analyzer_prompt("answers", 3, 15)
        assert "Return ONLY the JSON object" in result
        assert "assumptions" in result

    def test_importable_from_analyzer_module(self):
        """get_analyzer_prompt should be importable from scrum_agent.prompts.analyzer."""
        from scrum_agent.prompts.analyzer import get_analyzer_prompt as imported_fn

        assert imported_fn is get_analyzer_prompt

    def test_repo_context_section_present_when_provided(self):
        """'Repository Scan' section should appear when repo_context is given."""
        repo_data = "## File Tree\n- src/\n- README.md"
        result = get_analyzer_prompt("answers", 3, 15, repo_context=repo_data)
        assert "Repository Scan" in result
        assert repo_data in result

    def test_repo_context_section_absent_when_none(self):
        """'Repository Scan' section should be absent when repo_context is None."""
        result = get_analyzer_prompt("answers", 3, 15, repo_context=None)
        assert "Repository Scan" not in result

    def test_repo_context_section_absent_by_default(self):
        """'Repository Scan' section should be absent when repo_context is omitted."""
        result = get_analyzer_prompt("answers", 3, 15)
        assert "Repository Scan" not in result

    def test_confluence_context_section_present_when_provided(self):
        """'Confluence Documentation' section should appear when confluence_context is given."""
        conf_data = "Sprint Planning (ID: 12345)\n  Our team uses 2-week sprints..."
        result = get_analyzer_prompt("answers", 3, 15, confluence_context=conf_data)
        assert "Confluence Documentation" in result
        assert conf_data in result

    def test_confluence_context_section_absent_when_none(self):
        """'Confluence Documentation' section should be absent when confluence_context is None."""
        result = get_analyzer_prompt("answers", 3, 15, confluence_context=None)
        assert "Confluence Documentation" not in result

    def test_confluence_context_section_absent_by_default(self):
        """'Confluence Documentation' section should be absent when confluence_context is omitted."""
        result = get_analyzer_prompt("answers", 3, 15)
        assert "Confluence Documentation" not in result

    def test_both_repo_and_confluence_context(self):
        """Both repo and Confluence sections should appear together when both are provided."""
        result = get_analyzer_prompt(
            "answers",
            3,
            15,
            repo_context="## File Tree\n- src/",
            confluence_context="Architecture Decision Records...",
        )
        assert "Repository Scan" in result
        assert "Confluence Documentation" in result

    def test_user_context_section_present_when_provided(self):
        """'User Context (SCRUM.md / scrum-docs)' section should appear when user_context is given."""
        scrum_data = "# My Project\nWe use React + FastAPI. Budget: £500k."
        result = get_analyzer_prompt("answers", 3, 15, user_context=scrum_data)
        assert "User Context (SCRUM.md / scrum-docs)" in result
        assert scrum_data in result

    def test_user_context_section_absent_when_none(self):
        """'User Context (SCRUM.md / scrum-docs)' section should be absent when user_context is None."""
        result = get_analyzer_prompt("answers", 3, 15, user_context=None)
        assert "User Context (SCRUM.md / scrum-docs)" not in result

    def test_user_context_section_absent_by_default(self):
        """'User Context (SCRUM.md / scrum-docs)' section should be absent when user_context is omitted."""
        result = get_analyzer_prompt("answers", 3, 15)
        assert "User Context (SCRUM.md / scrum-docs)" not in result

    def test_user_context_appears_after_confluence_and_before_questionnaire(self):
        """User context section must be ordered: confluence → user context → questionnaire."""
        result = get_analyzer_prompt(
            "answers",
            3,
            15,
            confluence_context="Confluence docs here.",
            user_context="SCRUM.md notes here.",
        )
        conf_pos = result.index("Confluence Documentation")
        user_pos = result.index("User Context (SCRUM.md / scrum-docs)")
        qa_pos = result.index("Questionnaire Answers")
        assert conf_pos < user_pos < qa_pos

    def test_all_three_context_sections_together(self):
        """Repo, Confluence, and user context sections should all appear together."""
        result = get_analyzer_prompt(
            "answers",
            3,
            15,
            repo_context="file tree data",
            confluence_context="architecture docs",
            user_context="SCRUM.md notes",
        )
        assert "Repository Scan" in result
        assert "Confluence Documentation" in result
        assert "User Context (SCRUM.md / scrum-docs)" in result
