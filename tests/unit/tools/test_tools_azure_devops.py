"""Tests for Azure DevOps read-only tools.

All Azure DevOps API calls are mocked via unittest.mock.patch on _make_connection
so no real network requests are made. Tests cover happy paths, error cases, and
edge cases for each tool and the _parse_azdo_url helper.
"""

from unittest.mock import MagicMock, patch

from azure.devops.exceptions import AzureDevOpsServiceError

from scrum_agent.tools import get_tools
from scrum_agent.tools.azure_devops import (
    _azdo_error_msg,
    _parse_azdo_url,
    azdevops_list_work_items,
    azdevops_read_file,
    azdevops_read_repo,
)


class _FakeAzdoError(AzureDevOpsServiceError):
    """Test-only subclass that bypasses the complex __init__.

    AzureDevOpsServiceError normally requires a wrapped SDK exception object.
    This subclass lets us instantiate with a plain string for testing.
    """

    def __init__(self, message: str):
        Exception.__init__(self, message)
        self.inner_exception = None
        self.message = message
        self.exception_id = None
        self.type_name = None
        self.type_key = None
        self.error_code = None
        self.event_id = None
        self.custom_properties = {}

    def __str__(self) -> str:
        return self.message


_VALID_URL = "https://dev.azure.com/myorg/MyProject/_git/my-repo"

# ---------------------------------------------------------------------------
# _parse_azdo_url
# ---------------------------------------------------------------------------


class TestParseAzdoUrl:
    def test_valid_url(self):
        org_url, project, repo = _parse_azdo_url(_VALID_URL)
        assert org_url == "https://dev.azure.com/myorg"
        assert project == "MyProject"
        assert repo == "my-repo"

    def test_trailing_slash(self):
        org_url, project, repo = _parse_azdo_url(_VALID_URL + "/")
        assert org_url == "https://dev.azure.com/myorg"
        assert project == "MyProject"
        assert repo == "my-repo"

    def test_git_suffix(self):
        org_url, project, repo = _parse_azdo_url(_VALID_URL + ".git")
        assert org_url == "https://dev.azure.com/myorg"
        assert project == "MyProject"
        assert repo == "my-repo"

    def test_invalid_url_raises(self):
        import pytest

        with pytest.raises(ValueError, match="dev.azure.com"):
            _parse_azdo_url("https://github.com/owner/repo")

    def test_missing_git_segment_raises(self):
        import pytest

        with pytest.raises(ValueError):
            _parse_azdo_url("https://dev.azure.com/myorg/MyProject/my-repo")


# ---------------------------------------------------------------------------
# Helpers — build mock AzDO objects
# ---------------------------------------------------------------------------


def _make_item(path: str, obj_type: str = "blob") -> MagicMock:
    item = MagicMock()
    item.path = path
    item.git_object_type = obj_type
    return item


def _make_work_item(wi_id: int, wi_type: str, title: str, state: str, assignee: str | None = None) -> MagicMock:
    wi = MagicMock()
    wi.id = wi_id
    assigned_value = {"displayName": assignee} if assignee else None
    wi.fields = {
        "System.Id": wi_id,
        "System.WorkItemType": wi_type,
        "System.Title": title,
        "System.State": state,
        "System.AssignedTo": assigned_value,
    }
    return wi


# ---------------------------------------------------------------------------
# azdevops_read_repo
# ---------------------------------------------------------------------------


class TestAzdevopsReadRepo:
    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_normal_tree_returned(self, mock_make_conn):
        items = [
            _make_item("/src", "tree"),
            _make_item("/src/main.py", "blob"),
            _make_item("/pyproject.toml", "blob"),
            _make_item("/README.md", "blob"),
        ]
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.return_value = items

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "MyProject/my-repo" in result
        assert "pyproject.toml" in result
        assert "README.md" in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_empty_repo(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.return_value = []

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "MyProject/my-repo" in result
        assert "Key files" not in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_service_error(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.side_effect = RuntimeError(
            "TF401019: The Git repository was not found"
        )

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "Error" in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_generic_error(self, mock_make_conn):
        mock_make_conn.side_effect = RuntimeError("connection refused")

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "Error" in result

    def test_invalid_url_returns_error(self):
        result = azdevops_read_repo.invoke({"repo_url": "https://github.com/owner/repo"})

        assert "Error" in result


# ---------------------------------------------------------------------------
# azdevops_read_file
# ---------------------------------------------------------------------------


class TestAzdevopsReadFile:
    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_file_found_and_decoded(self, mock_make_conn):
        content = b"name = 'my-project'\nversion = '1.0'\n"
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.return_value = iter([content])

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/pyproject.toml"})

        assert "pyproject.toml" in result
        assert "name = 'my-project'" in result
        assert "[Truncated" not in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_file_not_found(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.side_effect = RuntimeError(
            "TF401019: File not found"
        )

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/missing.py"})

        assert "Error" in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_truncation_at_8000_chars(self, mock_make_conn):
        long_content = ("x" * 10_000).encode()
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.return_value = iter(
            [long_content]
        )

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/big.py"})

        assert "[Truncated at 8000 characters]" in result
        assert "x" * 8000 in result
        assert "x" * 8001 not in result

    def test_invalid_url_returns_error(self):
        result = azdevops_read_file.invoke({"repo_url": "not-a-url", "file_path": "/any.py"})

        assert "Error" in result


# ---------------------------------------------------------------------------
# azdevops_list_work_items
# ---------------------------------------------------------------------------


class TestAzdevopsListWorkItems:
    def _setup_wit_client(self, mock_make_conn, work_items: list) -> MagicMock:
        wit_client = MagicMock()
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value = wit_client

        # query_by_wiql returns a result with .work_items = list of lightweight refs (id only)
        query_result = MagicMock()
        query_result.work_items = [MagicMock(id=wi.id) for wi in work_items]
        wit_client.query_by_wiql.return_value = query_result

        # get_work_items returns the full work item objects
        wit_client.get_work_items.return_value = work_items
        return wit_client

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_items_returned(self, mock_make_conn):
        work_items = [
            _make_work_item(1, "Bug", "Fix login crash", "Active", "Jane Smith"),
            _make_work_item(2, "Task", "Update docs", "Active"),
        ]
        self._setup_wit_client(mock_make_conn, work_items)

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        assert "#1" in result
        assert "Bug" in result
        assert "Fix login crash" in result
        assert "Jane Smith" in result
        assert "#2" in result
        assert "Unassigned" in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_empty_list(self, mock_make_conn):
        wit_client = MagicMock()
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value = wit_client
        query_result = MagicMock()
        query_result.work_items = []
        wit_client.query_by_wiql.return_value = query_result

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        assert "No work items found" in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_state_all_skips_filter(self, mock_make_conn):
        """state='All' must omit the state clause from the WIQL query."""
        work_items = [_make_work_item(1, "Story", "Add feature", "Closed")]
        wit_client = self._setup_wit_client(mock_make_conn, work_items)

        azdevops_list_work_items.invoke({"repo_url": _VALID_URL, "state": "All"})

        # Inspect the Wiql object passed to query_by_wiql
        wiql_obj = wit_client.query_by_wiql.call_args[0][0]
        assert "System.State" not in wiql_obj.query

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_service_error(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_work_item_tracking_client.side_effect = RuntimeError(
            "TF401001: Access denied"
        )

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        assert "Error" in result

    def test_invalid_url_returns_error(self):
        result = azdevops_list_work_items.invoke({"repo_url": "bad-url", "state": "Active"})

        assert "Error" in result


# ---------------------------------------------------------------------------
# get_tools() — now returns 7 tools
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _azdo_error_msg — user-friendly HTTP error messages
# ---------------------------------------------------------------------------


class TestAzdoErrorMessages:
    def test_401_authentication_failed(self):
        e = _FakeAzdoError("401 Unauthorized")
        assert "Authentication failed" in _azdo_error_msg(e)
        assert "AZURE_DEVOPS_TOKEN" in _azdo_error_msg(e)

    def test_403_access_denied(self):
        e = _FakeAzdoError("403 Forbidden")
        assert "Access denied" in _azdo_error_msg(e)
        assert "PAT" in _azdo_error_msg(e)

    def test_403_access_denied_text(self):
        e = _FakeAzdoError("access denied to resource")
        assert "Access denied" in _azdo_error_msg(e)

    def test_429_throttling(self):
        e = _FakeAzdoError("429 Too Many Requests")
        assert "throttling" in _azdo_error_msg(e)

    def test_503_throttling(self):
        e = _FakeAzdoError("503 Service Unavailable")
        assert "throttling" in _azdo_error_msg(e)

    def test_404_not_found(self):
        e = _FakeAzdoError("404 Not Found")
        assert "not found" in _azdo_error_msg(e).lower()

    def test_unknown_error_returns_str(self):
        e = _FakeAzdoError("something weird happened")
        result = _azdo_error_msg(e)
        assert result.startswith("Error:")

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_azdo_service_error_401_in_read_repo(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.side_effect = _FakeAzdoError(
            "401 Unauthorized"
        )

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "Authentication failed" in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_azdo_service_error_403_in_read_file(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.side_effect = _FakeAzdoError(
            "403 Forbidden"
        )

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/any.py"})

        assert "Access denied" in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_azdo_service_error_429_in_work_items(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value.query_by_wiql.side_effect = (
            _FakeAzdoError("429 Too Many Requests")
        )

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        assert "throttling" in result


# ---------------------------------------------------------------------------
# azdevops_list_work_items — truncation note
# ---------------------------------------------------------------------------


class TestWorkItemsTruncationNote:
    def _setup_wit_client(self, mock_make_conn, work_items: list) -> MagicMock:
        wit_client = MagicMock()
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value = wit_client
        query_result = MagicMock()
        query_result.work_items = [MagicMock(id=wi.id) for wi in work_items]
        wit_client.query_by_wiql.return_value = query_result
        wit_client.get_work_items.return_value = work_items
        return wit_client

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_truncation_note_when_at_cap(self, mock_make_conn):
        # Exactly max_items returned — note should appear
        work_items = [_make_work_item(i, "Task", f"Task {i}", "Active") for i in range(1, 6)]
        self._setup_wit_client(mock_make_conn, work_items)

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL, "max_items": 5})

        assert "increase max_items to see more" in result

    @patch("scrum_agent.tools.azure_devops._make_connection")
    def test_no_truncation_note_when_under_cap(self, mock_make_conn):
        # Fewer items than max_items — no note expected
        work_items = [_make_work_item(i, "Task", f"Task {i}", "Active") for i in range(1, 4)]
        self._setup_wit_client(mock_make_conn, work_items)

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL, "max_items": 10})

        assert "increase max_items" not in result


class TestGetTools:
    def test_returns_twenty_four_tools(self):
        tools = get_tools()
        assert len(tools) == 24

    def test_all_are_base_tools(self):
        from langchain_core.tools import BaseTool

        tools = get_tools()
        for t in tools:
            assert isinstance(t, BaseTool), f"{t} is not a BaseTool"

    def test_correct_names(self):
        tools = get_tools()
        names = {t.name for t in tools}
        assert names == {
            "github_read_repo",
            "github_read_file",
            "github_list_issues",
            "github_read_readme",
            "azdevops_read_repo",
            "azdevops_read_file",
            "azdevops_list_work_items",
            "read_codebase",
            "read_local_file",
            "detect_bank_holidays",
            "estimate_complexity",
            "generate_acceptance_criteria",
            "jira_read_board",
            "jira_create_epic",
            "jira_create_story",
            "jira_create_sprint",
            "confluence_search_docs",
            "confluence_read_page",
            "confluence_read_space",
            "confluence_create_page",
            "confluence_update_page",
            "jira_fetch_velocity",
            "jira_fetch_active_sprint",
            "load_project_context",
        }
