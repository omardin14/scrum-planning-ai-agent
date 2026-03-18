"""Tests for task decomposer node and its helpers."""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from scrum_agent.agent.nodes import (
    _build_doc_context,
    _build_fallback_tasks,
    _format_stories_for_prompt,
    _format_tasks,
    _parse_tasks_response,
    task_decomposer,
)
from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Priority,
    QuestionnaireState,
    StoryPointValue,
    Task,
    TaskLabel,
    UserStory,
)
from scrum_agent.prompts.task_decomposer import get_task_decomposer_prompt
from tests._node_helpers import (
    VALID_TASKS_JSON,
    make_dummy_analysis,
    make_sample_features,
    make_sample_stories,
)

# ── _format_stories_for_prompt tests ──────────────────────────────────


class TestFormatStoriesForPrompt:
    """Tests for _format_stories_for_prompt() helper."""

    def test_returns_string(self):
        """Should return a non-empty string."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_prompt(stories, features)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_story_ids(self):
        """All story IDs should appear in the output."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_prompt(stories, features)
        assert "US-F1-001" in result
        assert "US-F1-002" in result

    def test_includes_story_points(self):
        """Story point values should appear in the output."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_prompt(stories, features)
        assert "5 pts" in result
        assert "3 pts" in result

    def test_includes_feature_headers(self):
        """Feature titles should appear as group headers."""
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_prompt(stories, features)
        assert "F1:" in result
        assert "User Authentication" in result

    def test_empty_stories_returns_empty(self):
        """Empty story list should return empty string."""
        result = _format_stories_for_prompt([], make_sample_features())
        assert result.strip() == ""

    def test_includes_documentation_in_dod_tag_when_applicable(self):
        """Stories with dod_applicable[1]=True should have [Documentation in DoD] tag."""
        # Default dod_applicable is all-True, so Documentation (index 1) is True
        stories = make_sample_stories()
        features = make_sample_features()
        result = _format_stories_for_prompt(stories, features)
        assert "[Documentation in DoD]" in result

    def test_excludes_documentation_tag_when_not_applicable(self):
        """Stories with dod_applicable[1]=False should NOT have [Documentation in DoD] tag."""
        story = UserStory(
            id="US-F1-001",
            feature_id="F1",
            persona="user",
            goal="do something",
            benefit="value",
            acceptance_criteria=(AcceptanceCriterion(given="ctx", when="act", then="out"),),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
            # Documentation (index 1) is False
            dod_applicable=(True, False, True, True, True, True, True),
        )
        features = make_sample_features()
        result = _format_stories_for_prompt([story], features)
        assert "[Documentation in DoD]" not in result


# ── _parse_tasks_response tests ──────────────────────────────────────


class TestParseTasksResponse:
    """Tests for _parse_tasks_response() helper."""

    def _stories(self):
        return make_sample_stories()

    def test_parses_valid_json(self):
        """Valid JSON array should produce a list of Task dataclasses."""
        result = _parse_tasks_response(VALID_TASKS_JSON, self._stories())
        assert isinstance(result, list)
        assert len(result) == 4
        assert all(isinstance(t, Task) for t in result)

    def test_handles_code_fence_wrapped_json(self):
        """JSON wrapped in markdown code fences should be handled."""
        fenced = f"```json\n{VALID_TASKS_JSON}\n```"
        result = _parse_tasks_response(fenced, self._stories())
        assert len(result) == 4
        assert result[0].id == "T-US-F1-001-01"

    def test_invalid_json_returns_fallback(self):
        """Invalid JSON should fall back to deterministic tasks."""
        result = _parse_tasks_response("this is not json", self._stories())
        assert isinstance(result, list)
        assert len(result) == 4  # fallback: 2 per story x 2 stories

    def test_empty_array_returns_fallback(self):
        """Empty JSON array should fall back."""
        result = _parse_tasks_response("[]", self._stories())
        assert len(result) == 4  # fallback: 2 per story x 2 stories

    def test_skips_items_with_unknown_story_id(self):
        """Tasks with invalid story_ids should be skipped."""
        json_str = (
            '[{"id": "T-US-X1-001-01", "story_id": "US-X1-001", '
            '"title": "Bad task", "description": "Invalid story ref"}]'
        )
        result = _parse_tasks_response(json_str, self._stories())
        # Task with invalid story_id "US-X1-001" is skipped, fallback produces 4 tasks
        assert len(result) == 4

    def test_auto_generates_missing_id(self):
        """Tasks with missing IDs should get auto-generated IDs."""
        json_str = '[{"story_id": "US-F1-001", "title": "Test task", "description": "Test desc"}]'
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].id == "T-US-F1-001-01"

    def test_defaults_empty_title(self):
        """Empty title should get a default value."""
        json_str = '[{"id": "T-US-F1-001-01", "story_id": "US-F1-001", "title": "", "description": "Some desc"}]'
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].title != ""
        assert "US-F1-001" in result[0].title

    def test_defaults_empty_description(self):
        """Empty description should get a default value."""
        json_str = '[{"id": "T-US-F1-001-01", "story_id": "US-F1-001", "title": "Task", "description": ""}]'
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].description != ""
        assert "US-F1-001" in result[0].description


# ── _build_fallback_tasks tests ──────────────────────────────────────


class TestBuildFallbackTasks:
    """Tests for _build_fallback_tasks() helper."""

    def test_returns_two_per_story(self):
        """Fallback should produce exactly 2 tasks per story."""
        stories = make_sample_stories()
        result = _build_fallback_tasks(stories)
        assert len(result) == 4  # 2 x 2 stories

    def test_task_ids_sequential(self):
        """Task IDs should follow T-{story_id}-01, T-{story_id}-02 format."""
        stories = make_sample_stories()
        result = _build_fallback_tasks(stories)
        assert result[0].id == "T-US-F1-001-01"
        assert result[1].id == "T-US-F1-001-02"
        assert result[2].id == "T-US-F1-002-01"
        assert result[3].id == "T-US-F1-002-02"

    def test_task_story_ids_valid(self):
        """Each task's story_id should match a story."""
        stories = make_sample_stories()
        story_ids = {s.id for s in stories}
        result = _build_fallback_tasks(stories)
        for task in result:
            assert task.story_id in story_ids

    def test_titles_non_empty(self):
        """All task titles should be non-empty strings."""
        stories = make_sample_stories()
        result = _build_fallback_tasks(stories)
        for task in result:
            assert isinstance(task.title, str)
            assert len(task.title) > 0

    def test_empty_stories_returns_empty(self):
        """Empty story list should produce no tasks."""
        result = _build_fallback_tasks([])
        assert result == []


# ── _format_tasks tests ──────────────────────────────────────────────


class TestFormatTasks:
    """Tests for _format_tasks() helper."""

    def _sample_tasks(self) -> list[Task]:
        return [
            Task(id="T-US-F1-001-01", story_id="US-F1-001", title="Create registration API", description="Build it"),
            Task(id="T-US-F1-001-02", story_id="US-F1-001", title="Write registration tests", description="Test it"),
        ]

    def test_returns_non_empty_string(self):
        """Should return a non-empty markdown string."""
        result = _format_tasks(self._sample_tasks(), make_sample_stories(), make_sample_features(), "Test Project")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_groups_by_feature(self):
        """Feature titles should appear as headers."""
        result = _format_tasks(self._sample_tasks(), make_sample_stories(), make_sample_features(), "Test Project")
        assert "F1:" in result
        assert "User Authentication" in result

    def test_includes_task_titles(self):
        """Task titles should appear in the output."""
        result = _format_tasks(self._sample_tasks(), make_sample_stories(), make_sample_features(), "Test Project")
        assert "Create registration API" in result
        assert "Write registration tests" in result

    def test_includes_review_footer(self):
        """The review prompt footer should be present."""
        result = _format_tasks(self._sample_tasks(), make_sample_stories(), make_sample_features(), "Test Project")
        assert "[Accept / Edit / Reject]" in result

    def test_includes_project_name(self):
        """The project name should appear in the header."""
        result = _format_tasks(self._sample_tasks(), make_sample_stories(), make_sample_features(), "Widget Builder")
        assert "Widget Builder" in result


# ── task_decomposer node tests ──────────────────────────────────────


class TestTaskDecomposer:
    """Tests for the task_decomposer() node function."""

    def _make_state(self, **extras: object) -> dict:
        """Build a minimal state with project_analysis, features, and stories for task decomposer tests."""
        analysis = make_dummy_analysis()
        features = make_sample_features()
        stories = make_sample_stories()
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
        }
        state.update(extras)
        return state

    def test_returns_tasks_and_messages(self, monkeypatch):
        """task_decomposer should return both 'tasks' and 'messages' keys."""
        fake_response = MagicMock()
        fake_response.content = VALID_TASKS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = task_decomposer(self._make_state())
        assert "tasks" in result
        assert "messages" in result
        assert isinstance(result["tasks"], list)
        assert all(isinstance(t, Task) for t in result["tasks"])
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)

    def test_llm_failure_uses_fallback(self, monkeypatch):
        """When the LLM call raises an exception, the fallback should be used."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("API down")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = task_decomposer(self._make_state())
        assert isinstance(result["tasks"], list)
        assert len(result["tasks"]) == 4  # fallback: 2 per story x 2 stories
        assert "messages" in result

    def test_tasks_have_valid_story_ids(self, monkeypatch):
        """All returned tasks should have story_ids that reference actual stories."""
        fake_response = MagicMock()
        fake_response.content = VALID_TASKS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = self._make_state()
        story_ids = {s.id for s in state["stories"]}
        result = task_decomposer(state)
        for task in result["tasks"]:
            assert task.story_id in story_ids

    def test_display_message_contains_project_name(self, monkeypatch):
        """The formatted AIMessage should include the project name."""
        fake_response = MagicMock()
        fake_response.content = VALID_TASKS_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = task_decomposer(self._make_state())
        content = result["messages"][0].content
        assert "Test Project" in content

    def test_passes_doc_context_to_prompt(self, monkeypatch):
        """When Q14 has documentation refs, the LLM prompt should include them."""
        captured_prompts = []

        fake_response = MagicMock()
        fake_response.content = VALID_TASKS_JSON
        mock_llm = MagicMock()

        def capture_invoke(messages):
            captured_prompts.append(messages[0].content)
            return fake_response

        mock_llm.invoke.side_effect = capture_invoke
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        qs = QuestionnaireState(completed=True)
        q14_text = "See our API docs at https://docs.example.com/api"
        qs.answers[14] = q14_text
        state = self._make_state(questionnaire=qs)

        task_decomposer(state)
        assert len(captured_prompts) == 1
        assert q14_text in captured_prompts[0]

    def test_no_doc_context_when_default_q14(self, monkeypatch):
        """When Q14 is the default 'no existing documentation', no doc context section."""
        captured_prompts = []

        fake_response = MagicMock()
        fake_response.content = VALID_TASKS_JSON
        mock_llm = MagicMock()

        def capture_invoke(messages):
            captured_prompts.append(messages[0].content)
            return fake_response

        mock_llm.invoke.side_effect = capture_invoke
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        qs = QuestionnaireState(completed=True)
        qs.answers[14] = "No existing documentation to reference"
        state = self._make_state(questionnaire=qs)

        task_decomposer(state)
        assert len(captured_prompts) == 1
        # The "### Documentation References" header should NOT appear as a section
        # (it's only mentioned generically in the rules text)
        assert "### Documentation References" not in captured_prompts[0]


# ── _build_doc_context tests ──────────────────────────────────────


class TestBuildDocContext:
    """Tests for _build_doc_context() helper."""

    def test_returns_none_when_no_context(self):
        """Should return None when there's no documentation context."""
        state = {
            "messages": [],
            "questionnaire": QuestionnaireState(completed=True),
        }
        result = _build_doc_context(state)
        assert result is None

    def test_includes_q14_answer(self):
        """Should include Q14 answer when it's not the default."""
        qs = QuestionnaireState(completed=True)
        q14_text = "API docs at https://wiki.example.com/api-reference"
        qs.answers[14] = q14_text
        state = {"messages": [], "questionnaire": qs}
        result = _build_doc_context(state)
        assert result is not None
        assert q14_text in result
        assert "Existing docs" in result

    def test_skips_default_q14(self):
        """Should skip Q14 when it's the default 'no existing documentation'."""
        qs = QuestionnaireState(completed=True)
        qs.answers[14] = "No existing documentation to reference"
        state = {"messages": [], "questionnaire": qs}
        result = _build_doc_context(state)
        assert result is None

    def test_includes_confluence_context(self):
        """Should include Confluence context when present."""
        confluence_text = "Confluence space: https://wiki.company.com/project"
        state = {
            "messages": [],
            "questionnaire": QuestionnaireState(completed=True),
            "confluence_context": confluence_text,
        }
        result = _build_doc_context(state)
        assert result is not None
        assert "Confluence" in result
        assert confluence_text in result

    def test_includes_user_context(self):
        """Should include SCRUM.md user context when present."""
        user_ctx_text = "README: https://github.com/org/repo/blob/main/README.md"
        state = {
            "messages": [],
            "questionnaire": QuestionnaireState(completed=True),
            "user_context": user_ctx_text,
        }
        result = _build_doc_context(state)
        assert result is not None
        assert "SCRUM.md" in result
        assert user_ctx_text in result

    def test_combines_multiple_sources(self):
        """Should combine Q14, Confluence, and user context."""
        qs = QuestionnaireState(completed=True)
        qs.answers[14] = "PRD at https://docs.google.com/prd"
        state = {
            "messages": [],
            "questionnaire": qs,
            "confluence_context": "Design docs in Confluence",
            "user_context": "See README for setup",
        }
        result = _build_doc_context(state)
        assert result is not None
        assert "Existing docs" in result
        assert "Confluence" in result
        assert "SCRUM.md" in result

    def test_skips_empty_confluence_context(self):
        """Should skip Confluence context when it's empty or whitespace."""
        state = {
            "messages": [],
            "questionnaire": QuestionnaireState(completed=True),
            "confluence_context": "   ",
        }
        result = _build_doc_context(state)
        assert result is None


# ── Documentation sub-task prompt tests ──────────────────────────────


class TestDocumentationSubTaskPrompt:
    """Tests for documentation sub-task rules in the task decomposer prompt."""

    def test_prompt_includes_documentation_subtask_rule(self):
        """The prompt should include the Documentation Sub-Task Rule section."""
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="**US-F1-001** (5 pts, backend) [Documentation in DoD]",
        )
        assert "Documentation Sub-Task Rule" in prompt
        assert "exactly one" in prompt
        assert "No other task" in prompt

    def test_prompt_includes_doc_context_when_provided(self):
        """When doc_context is provided, it should appear in the prompt."""
        doc_ctx = "- **Confluence:** https://wiki.example.com/project"
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="some stories",
            doc_context=doc_ctx,
        )
        assert "### Documentation References" in prompt
        assert doc_ctx in prompt

    def test_prompt_omits_doc_references_section_when_no_context(self):
        """When doc_context is None, no Documentation References section header."""
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="some stories",
            doc_context=None,
        )
        # The "### Documentation References" header should not appear
        assert "### Documentation References" not in prompt


# ── Task label tests ──────────────────────────────────────────────


class TestTaskLabelParsing:
    """Tests for task label parsing in _parse_tasks_response."""

    def _stories(self):
        return make_sample_stories()

    def test_parses_valid_labels(self):
        """Tasks with valid labels should have the correct TaskLabel."""
        json_str = (
            "["
            '{"id":"T-US-F1-001-01","story_id":"US-F1-001","title":"Build API","description":"d","label":"Code"},'
            '{"id":"T-US-F1-001-02","story_id":"US-F1-001","title":"Write tests","description":"d","label":"Testing"},'
            '{"id":"T-US-F1-002-01","story_id":"US-F1-002","title":"Deploy","description":"d","label":"Infrastructure"},'
            '{"id":"T-US-F1-002-02","story_id":"US-F1-002",'
            '"title":"Write docs","description":"d","label":"Documentation"}'
            "]"
        )
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].label == TaskLabel.CODE
        assert result[1].label == TaskLabel.TESTING
        assert result[2].label == TaskLabel.INFRASTRUCTURE
        assert result[3].label == TaskLabel.DOCUMENTATION

    def test_missing_label_defaults_to_code(self):
        """Tasks without a label field should default to Code."""
        json_str = '[{"id":"T-US-F1-001-01","story_id":"US-F1-001","title":"Task","description":"d"}]'
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].label == TaskLabel.CODE

    def test_invalid_label_defaults_to_code(self):
        """Tasks with an invalid label value should default to Code."""
        json_str = (
            '[{"id":"T-US-F1-001-01","story_id":"US-F1-001","title":"Task","description":"d","label":"InvalidLabel"}]'
        )
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].label == TaskLabel.CODE

    def test_empty_label_defaults_to_code(self):
        """Tasks with an empty label value should default to Code."""
        json_str = '[{"id":"T-US-F1-001-01","story_id":"US-F1-001","title":"Task","description":"d","label":""}]'
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].label == TaskLabel.CODE


class TestTaskLabelDisplay:
    """Tests for task label display in _format_tasks."""

    def test_labels_appear_in_markdown_output(self):
        """Task labels should appear in the formatted markdown output."""
        tasks = [
            Task(id="T-US-F1-001-01", story_id="US-F1-001", title="Build API", description="d", label=TaskLabel.CODE),
            Task(
                id="T-US-F1-001-02",
                story_id="US-F1-001",
                title="Write docs",
                description="d",
                label=TaskLabel.DOCUMENTATION,
            ),
        ]
        result = _format_tasks(tasks, make_sample_stories(), make_sample_features(), "Test")
        assert "[Code]" in result
        assert "[Documentation]" in result

    def test_fallback_tasks_default_to_code_label(self):
        """Fallback tasks should all have the default Code label."""
        stories = make_sample_stories()
        result = _build_fallback_tasks(stories)
        for task in result:
            assert task.label == TaskLabel.CODE


class TestTaskLabelPrompt:
    """Tests for task label rules in the task decomposer prompt."""

    def test_prompt_includes_label_rule(self):
        """The prompt should include label assignment rules."""
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="some stories",
        )
        assert "label" in prompt
        assert "Code" in prompt
        assert "Documentation" in prompt
        assert "Infrastructure" in prompt
        assert "Testing" in prompt

    def test_json_schema_includes_label_field(self):
        """The JSON schema in the prompt should include the label field."""
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="some stories",
        )
        assert '"label"' in prompt


# ── Test plan tests ──────────────────────────────────────────────


class TestTestPlanParsing:
    """Tests for test_plan parsing in _parse_tasks_response."""

    def _stories(self):
        return make_sample_stories()

    def test_parses_test_plan_from_json(self):
        """Tasks with test_plan in JSON should have it populated."""
        json_str = (
            '[{"id":"T-US-F1-001-01","story_id":"US-F1-001","title":"Build API",'
            '"description":"d","label":"Code",'
            '"test_plan":"Unit: test endpoint returns 201"}]'
        )
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].test_plan == "Unit: test endpoint returns 201"

    def test_missing_test_plan_defaults_to_empty(self):
        """Tasks without test_plan should default to empty string."""
        json_str = '[{"id":"T-US-F1-001-01","story_id":"US-F1-001","title":"Task","description":"d","label":"Code"}]'
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].test_plan == ""

    def test_testing_label_has_empty_test_plan(self):
        """Tasks with Testing label should have empty test_plan."""
        result = _parse_tasks_response(VALID_TASKS_JSON, self._stories())
        testing_tasks = [t for t in result if t.label == TaskLabel.TESTING]
        for task in testing_tasks:
            assert task.test_plan == ""

    def test_code_label_has_test_plan(self):
        """Tasks with Code label in fixture should have non-empty test_plan."""
        result = _parse_tasks_response(VALID_TASKS_JSON, self._stories())
        code_tasks = [t for t in result if t.label == TaskLabel.CODE]
        for task in code_tasks:
            assert task.test_plan != ""

    def test_fallback_tasks_have_empty_test_plan(self):
        """Fallback tasks should have empty test_plan."""
        stories = make_sample_stories()
        result = _build_fallback_tasks(stories)
        for task in result:
            assert task.test_plan == ""


class TestTestPlanDisplay:
    """Tests for test_plan display in _format_tasks."""

    def test_test_plan_appears_in_markdown(self):
        """Tasks with test_plan should show it in markdown output."""
        tasks = [
            Task(
                id="T-US-F1-001-01",
                story_id="US-F1-001",
                title="Build API",
                description="d",
                label=TaskLabel.CODE,
                test_plan="Unit: test POST /register returns 201",
            ),
        ]
        result = _format_tasks(tasks, make_sample_stories(), make_sample_features(), "Test")
        assert "Test plan:" in result
        assert "POST /register returns 201" in result

    def test_no_test_plan_line_when_empty(self):
        """Tasks without test_plan should not show test plan line."""
        tasks = [
            Task(
                id="T-US-F1-001-01",
                story_id="US-F1-001",
                title="Write docs",
                description="d",
                label=TaskLabel.DOCUMENTATION,
            ),
        ]
        result = _format_tasks(tasks, make_sample_stories(), make_sample_features(), "Test")
        assert "Test plan:" not in result


class TestTestPlanPrompt:
    """Tests for test_plan rules in the task decomposer prompt."""

    def test_prompt_includes_test_plan_rule(self):
        """The prompt should include test_plan assignment rules."""
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="some stories",
        )
        assert "test_plan" in prompt
        assert "Code" in prompt
        assert "Infrastructure" in prompt

    def test_json_schema_includes_test_plan_field(self):
        """The JSON schema in the prompt should include the test_plan field."""
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="some stories",
        )
        assert '"test_plan"' in prompt


# ── AI prompt tests ──────────────────────────────────────────────


class TestAiPromptParsing:
    """Tests for ai_prompt parsing in _parse_tasks_response."""

    def _stories(self):
        return make_sample_stories()

    def test_parses_ai_prompt_from_json(self):
        """Tasks with ai_prompt in JSON should have it populated."""
        result = _parse_tasks_response(VALID_TASKS_JSON, self._stories())
        assert result[0].ai_prompt != ""
        assert "backend engineer" in result[0].ai_prompt
        assert "FastAPI" in result[0].ai_prompt

    def test_missing_ai_prompt_defaults_to_empty(self):
        """Tasks without ai_prompt should default to empty string."""
        json_str = '[{"id":"T-US-F1-001-01","story_id":"US-F1-001","title":"Task","description":"d","label":"Code"}]'
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].ai_prompt == ""

    def test_all_labels_can_have_ai_prompt(self):
        """All 4 task label types should retain their ai_prompt."""
        json_str = (
            "["
            '{"id":"T-US-F1-001-01","story_id":"US-F1-001","title":"Build","description":"d",'
            '"label":"Code","ai_prompt":"You are a backend engineer."},'
            '{"id":"T-US-F1-001-02","story_id":"US-F1-001","title":"Docs","description":"d",'
            '"label":"Documentation","ai_prompt":"You are a technical writer."},'
            '{"id":"T-US-F1-002-01","story_id":"US-F1-002","title":"Deploy","description":"d",'
            '"label":"Infrastructure","ai_prompt":"You are a DevOps engineer."},'
            '{"id":"T-US-F1-002-02","story_id":"US-F1-002","title":"Test","description":"d",'
            '"label":"Testing","ai_prompt":"You are a QA engineer."}'
            "]"
        )
        result = _parse_tasks_response(json_str, self._stories())
        assert result[0].ai_prompt == "You are a backend engineer."
        assert result[1].ai_prompt == "You are a technical writer."
        assert result[2].ai_prompt == "You are a DevOps engineer."
        assert result[3].ai_prompt == "You are a QA engineer."

    def test_fallback_tasks_have_empty_ai_prompt(self):
        """Fallback tasks should have empty ai_prompt."""
        stories = make_sample_stories()
        result = _build_fallback_tasks(stories)
        for task in result:
            assert task.ai_prompt == ""


class TestAiPromptDisplay:
    """Tests for ai_prompt display in _format_tasks."""

    def test_ai_prompt_in_markdown_format(self):
        """Tasks with ai_prompt should show it in markdown output."""
        tasks = [
            Task(
                id="T-US-F1-001-01",
                story_id="US-F1-001",
                title="Build API",
                description="d",
                label=TaskLabel.CODE,
                ai_prompt="You are a backend engineer. Implement the registration endpoint.",
            ),
        ]
        result = _format_tasks(tasks, make_sample_stories(), make_sample_features(), "Test")
        assert "AI prompt:" in result
        assert "backend engineer" in result

    def test_no_ai_prompt_line_when_empty(self):
        """Tasks without ai_prompt should not show AI prompt line."""
        tasks = [
            Task(
                id="T-US-F1-001-01",
                story_id="US-F1-001",
                title="Build API",
                description="d",
                label=TaskLabel.CODE,
            ),
        ]
        result = _format_tasks(tasks, make_sample_stories(), make_sample_features(), "Test")
        assert "AI prompt:" not in result


class TestAiPromptPrompt:
    """Tests for ai_prompt rules in the task decomposer prompt."""

    def test_prompt_includes_ai_prompt_rule(self):
        """The prompt should include the ARC-structured ai_prompt rule."""
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="some stories",
        )
        assert "ai_prompt" in prompt
        assert "ARC" in prompt
        assert "Actor" in prompt

    def test_json_schema_includes_ai_prompt_field(self):
        """The JSON schema in the prompt should include the ai_prompt field."""
        prompt = get_task_decomposer_prompt(
            project_name="Test",
            project_type="greenfield",
            tech_stack="- Python",
            stories_block="some stories",
        )
        assert '"ai_prompt"' in prompt
