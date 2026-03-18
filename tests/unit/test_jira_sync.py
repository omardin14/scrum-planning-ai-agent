"""Tests for the jira_sync batch creation module.

Tests cover:
- _feature_title_to_label sanitisation edge cases
- _format_story_description / _format_task_description formatting
- Idempotency: pre-populated jira_*_keys are skipped
- Error accumulation: one failure doesn't stop others
- sync_stories_to_jira with mock JIRA client
- sync_tasks_to_jira cascades to create stories first
- sync_all_to_jira full pipeline
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    Sprint,
    StoryPointValue,
    Task,
    TaskLabel,
    UserStory,
)
from scrum_agent.jira_sync import (
    JiraSyncResult,
    _feature_title_to_label,
    _format_story_description,
    _format_task_description,
    is_jira_configured,
    sync_all_to_jira,
    sync_stories_to_jira,
    sync_tasks_to_jira,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_feature(id="feat-1", title="User Authentication"):
    return Feature(id=id, title=title, description="Auth feature", priority=Priority.HIGH)


def _make_story(id="story-1", feature_id="feat-1", title="Login endpoint"):
    return UserStory(
        id=id,
        feature_id=feature_id,
        persona="developer",
        goal="log in via API",
        benefit="access protected resources",
        acceptance_criteria=(AcceptanceCriterion(given="valid credentials", when="POST /login", then="return 200"),),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        title=title,
        discipline=Discipline.BACKEND,
    )


def _make_task(id="task-1", story_id="story-1", title="Implement login handler"):
    return Task(
        id=id,
        story_id=story_id,
        title=title,
        description="Implement the login endpoint",
        label=TaskLabel.CODE,
        test_plan="Test with valid and invalid credentials",
    )


def _make_sprint(id="sprint-1", story_ids=("story-1",)):
    return Sprint(
        id=id,
        name="Sprint 1",
        goal="Auth foundation",
        capacity_points=13,
        story_ids=story_ids,
    )


def _make_graph_state(**overrides):
    """Build a minimal graph state with defaults for testing."""
    state = {
        "messages": [],
        "features": [_make_feature()],
        "stories": [_make_story()],
        "tasks": [_make_task()],
        "sprints": [_make_sprint()],
        "project_name": "Test Project",
        "sprint_length_weeks": 2,
        "sprint_start_date": "2026-03-16",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# _feature_title_to_label tests
# ---------------------------------------------------------------------------


class TestFeatureTitleToLabel:
    def test_basic(self):
        assert _feature_title_to_label("User Authentication") == "User-Authentication"

    def test_special_chars(self):
        assert _feature_title_to_label("Feature #1 (beta)") == "Feature-1-beta"

    def test_empty(self):
        assert _feature_title_to_label("") == "Feature"

    def test_only_special_chars(self):
        assert _feature_title_to_label("@#$%") == "Feature"

    def test_multiple_spaces(self):
        assert _feature_title_to_label("  User   Auth  ") == "User-Auth"

    def test_long_title_truncated(self):
        label = _feature_title_to_label("A" * 100)
        assert len(label) <= 50


# ---------------------------------------------------------------------------
# _format_story_description tests
# ---------------------------------------------------------------------------


class TestFormatStoryDescription:
    def test_includes_user_story_text(self):
        story = _make_story()
        desc = _format_story_description(story)
        assert "developer" in desc
        assert "log in via API" in desc

    def test_includes_acceptance_criteria(self):
        story = _make_story()
        desc = _format_story_description(story)
        assert "Acceptance Criteria" in desc
        assert "valid credentials" in desc

    def test_includes_feature_context(self):
        story = _make_story()
        feature = _make_feature()
        desc = _format_story_description(story, feature)
        assert "User Authentication" in desc

    def test_no_feature(self):
        story = _make_story()
        desc = _format_story_description(story, None)
        assert "Feature:" not in desc


# ---------------------------------------------------------------------------
# _format_task_description tests
# ---------------------------------------------------------------------------


class TestFormatTaskDescription:
    def test_includes_description(self):
        task = _make_task()
        desc = _format_task_description(task)
        assert "Implement the login endpoint" in desc

    def test_includes_test_plan(self):
        task = _make_task()
        desc = _format_task_description(task)
        assert "Test Plan" in desc
        assert "valid and invalid credentials" in desc

    def test_no_test_plan(self):
        task = Task(id="t1", story_id="s1", title="Doc task", description="Write docs", label=TaskLabel.DOCUMENTATION)
        desc = _format_task_description(task)
        assert "Test Plan" not in desc


# ---------------------------------------------------------------------------
# is_jira_configured tests
# ---------------------------------------------------------------------------


class TestIsJiraConfigured:
    def test_returns_true_when_token_present(self, monkeypatch):
        monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
        assert is_jira_configured() is True

    def test_returns_false_when_token_missing(self, monkeypatch):
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
        assert is_jira_configured() is False


# ---------------------------------------------------------------------------
# sync_stories_to_jira tests
# ---------------------------------------------------------------------------


class TestSyncStoriesToJira:
    def test_returns_error_when_jira_not_configured(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.jira_sync.get_jira_token", lambda: None)
        result, state = sync_stories_to_jira(_make_graph_state())
        assert result.errors
        assert "not configured" in result.errors[0].lower() or "missing" in result.errors[0].lower()

    @patch("scrum_agent.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_creates_epic_and_stories(self, mock_key):
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_story_issue = MagicMock()
        mock_story_issue.key = "PROJ-2"

        mock_jira.create_issue.return_value = mock_epic

        with patch("scrum_agent.tools.jira._make_jira_client", return_value=mock_jira):
            with patch(
                "scrum_agent.tools.jira._create_issue_with_epic_link",
                return_value=(mock_story_issue, "parent"),
            ):
                result, state = sync_stories_to_jira(_make_graph_state())

        assert result.epic_key == "PROJ-1"
        assert state["jira_epic_key"] == "PROJ-1"
        assert "story-1" in result.stories_created
        assert result.stories_created["story-1"] == "PROJ-2"
        assert state["jira_story_keys"]["story-1"] == "PROJ-2"

    @patch("scrum_agent.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_skips_existing_stories(self, mock_key):
        """Stories already in jira_story_keys should be skipped."""
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.return_value = mock_epic

        state = _make_graph_state(
            jira_epic_key="PROJ-1",
            jira_story_keys={"story-1": "PROJ-2"},
        )

        with patch("scrum_agent.tools.jira._make_jira_client", return_value=mock_jira):
            result, new_state = sync_stories_to_jira(state)

        # Epic was skipped (already exists)
        assert result.skipped >= 1
        # Story was skipped (already exists)
        assert "story-1" not in result.stories_created
        # No Jira API calls for story creation
        assert not mock_jira.create_issue.called  # epic also skipped

    @patch("scrum_agent.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_error_accumulation(self, mock_key):
        """One failing story shouldn't prevent others from being created."""
        from jira import JIRAError

        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.return_value = mock_epic

        # Two stories — first fails, second succeeds
        story1 = _make_story(id="s1", title="Failing story")
        story2 = _make_story(id="s2", title="Good story")
        state = _make_graph_state(stories=[story1, story2])

        mock_good_issue = MagicMock()
        mock_good_issue.key = "PROJ-3"

        call_count = [0]

        def mock_create_with_epic(jira, fields, epic_key, method):
            call_count[0] += 1
            if call_count[0] == 1:
                raise JIRAError(status_code=500, text="Server error")
            return mock_good_issue, "parent"

        with patch("scrum_agent.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("scrum_agent.tools.jira._create_issue_with_epic_link", side_effect=mock_create_with_epic):
                result, new_state = sync_stories_to_jira(state)

        assert len(result.errors) == 1
        assert "s2" in result.stories_created
        assert "s1" not in result.stories_created

    @patch("scrum_agent.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_progress_callback_called(self, mock_key):
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.return_value = mock_epic

        mock_story_issue = MagicMock()
        mock_story_issue.key = "PROJ-2"

        progress_calls = []

        with patch("scrum_agent.tools.jira._make_jira_client", return_value=mock_jira):
            with patch(
                "scrum_agent.tools.jira._create_issue_with_epic_link",
                return_value=(mock_story_issue, "parent"),
            ):
                result, state = sync_stories_to_jira(
                    _make_graph_state(),
                    on_progress=lambda cur, tot, desc: progress_calls.append((cur, tot, desc)),
                )

        assert len(progress_calls) >= 2  # epic + at least 1 story


# ---------------------------------------------------------------------------
# sync_tasks_to_jira tests
# ---------------------------------------------------------------------------


class TestSyncTasksToJira:
    @patch("scrum_agent.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_cascades_to_create_stories_first(self, mock_key):
        """When no stories exist in Jira, tasks sync should create stories first."""
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.side_effect = [mock_epic]  # epic creation

        mock_story_issue = MagicMock()
        mock_story_issue.key = "PROJ-2"
        mock_task_issue = MagicMock()
        mock_task_issue.key = "PROJ-3"

        # Mock create_subtask separately from the module
        with patch("scrum_agent.tools.jira._make_jira_client", return_value=mock_jira):
            with patch(
                "scrum_agent.tools.jira._create_issue_with_epic_link",
                return_value=(mock_story_issue, "parent"),
            ):
                with patch("scrum_agent.tools.jira.create_subtask", return_value="PROJ-3"):
                    result, state = sync_tasks_to_jira(_make_graph_state())

        # Stories should have been created via cascade
        assert "story-1" in state.get("jira_story_keys", {})
        # Tasks should be created
        assert "task-1" in state.get("jira_task_keys", {})

    @patch("scrum_agent.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_skips_existing_tasks(self, mock_key):
        mock_jira = MagicMock()
        state = _make_graph_state(
            jira_epic_key="PROJ-1",
            jira_story_keys={"story-1": "PROJ-2"},
            jira_task_keys={"task-1": "PROJ-3"},
        )

        with patch("scrum_agent.tools.jira._make_jira_client", return_value=mock_jira):
            result, new_state = sync_tasks_to_jira(state)

        assert result.skipped >= 1
        assert "task-1" not in result.tasks_created


# ---------------------------------------------------------------------------
# sync_all_to_jira tests
# ---------------------------------------------------------------------------


class TestSyncAllToJira:
    @patch("scrum_agent.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_full_pipeline(self, mock_key):
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.return_value = mock_epic

        mock_story_issue = MagicMock()
        mock_story_issue.key = "PROJ-2"

        mock_sprint = MagicMock()
        mock_sprint.id = 42
        mock_jira.create_sprint.return_value = mock_sprint

        mock_boards = [MagicMock(id=10)]
        mock_jira.boards.return_value = mock_boards

        with patch("scrum_agent.tools.jira._make_jira_client", return_value=mock_jira):
            with patch(
                "scrum_agent.tools.jira._create_issue_with_epic_link",
                return_value=(mock_story_issue, "parent"),
            ):
                with patch("scrum_agent.tools.jira.create_subtask", return_value="PROJ-3"):
                    result, state = sync_all_to_jira(_make_graph_state())

        assert result.epic_key == "PROJ-1"
        assert len(result.stories_created) == 1
        assert len(result.tasks_created) == 1
        assert len(result.sprints_created) == 1


# ---------------------------------------------------------------------------
# JiraSyncResult tests
# ---------------------------------------------------------------------------


class TestJiraSyncResult:
    def test_defaults(self):
        r = JiraSyncResult()
        assert r.epic_key is None
        assert r.stories_created == {}
        assert r.tasks_created == {}
        assert r.sprints_created == {}
        assert r.errors == []
        assert r.skipped == 0
