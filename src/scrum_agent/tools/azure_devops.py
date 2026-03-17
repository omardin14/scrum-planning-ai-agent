"""Azure DevOps read-only tools for fetching repo context.

# See README: "Tools" — tool types, @tool decorator, risk levels
#
# All three tools are read-only (low risk) — they fetch data from the Azure
# DevOps REST API and return it as a string for the LLM to reason about. The
# LLM uses these tools in the ReAct loop (Thought → Action → Observation) to
# ground its scrum planning in the actual codebase and backlog.
#
# Why azure-devops SDK instead of raw requests?
# The SDK wraps the REST API with typed objects, handles authentication via
# BasicAuthentication (PAT), and raises AzureDevOpsServiceError for API
# failures. This makes error handling predictable across all three tools.
#
# URL format supported (modern only):
#   https://dev.azure.com/{org}/{project}/_git/{repo}
"""

import logging

from azure.devops.exceptions import AzureDevOpsServiceError
from langchain_core.tools import tool

from scrum_agent.config import get_azure_devops_token

logger = logging.getLogger(__name__)

# Truncate file content at this many characters to avoid flooding the LLM context.
_MAX_CONTENT_CHARS = 8_000

# Key config/manifest files to highlight in the repo tree summary.
# See README: "Tools" — scoping tool output for LLM relevance
_KEY_FILES = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "README.md",
    "README.rst",
    "CONTRIBUTING.md",
    "Makefile",
    "requirements.txt",
    ".env.example",
    "tsconfig.json",
    "webpack.config.js",
    "vite.config.ts",
    "vite.config.js",
}


def _azdo_error_msg(e: Exception) -> str:
    """Return a user-friendly message for common AzDO HTTP error codes."""
    msg = str(e).lower()
    if "401" in msg or "unauthorized" in msg:
        return "Error: Authentication failed. Check your AZURE_DEVOPS_TOKEN in .env."
    if "403" in msg or "forbidden" in msg or "access denied" in msg:
        return "Error: Access denied. Ensure your PAT has Code=Read and Work Items=Read permissions."
    if "404" in msg or "not found" in msg:
        return f"Error: Resource not found — verify the repo URL. ({e})"
    if "429" in msg or "503" in msg or "throttl" in msg:
        return "Error: Azure DevOps is throttling requests. Wait a moment and try again."
    return f"Error: {e}"


def _parse_azdo_url(url: str) -> tuple[str, str, str]:
    """Parse 'https://dev.azure.com/{org}/{project}/_git/{repo}' into (org_url, project, repo).

    Returns:
        (org_url, project, repo) — e.g. ("https://dev.azure.com/myorg", "MyProject", "my-repo")

    Raises:
        ValueError: if URL does not match the expected format.
    """
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    if "dev.azure.com/" not in url:
        raise ValueError(
            f"URL must be a modern Azure DevOps URL (https://dev.azure.com/org/project/_git/repo). Got: {url!r}"
        )

    # Split off everything after "dev.azure.com/" → "org/project/_git/repo"
    after = url.split("dev.azure.com/", 1)[1]
    parts = after.split("/")

    # Expect exactly: [org, project, "_git", repo] (may have extra segments — we ignore them)
    if len(parts) < 4 or parts[2] != "_git":
        raise ValueError(
            f"URL must follow the pattern https://dev.azure.com/{{org}}/{{project}}/_git/{{repo}}. Got: {url!r}"
        )

    org, project, repo = parts[0], parts[1], parts[3]

    if not org or not project or not repo:
        raise ValueError(f"org, project, and repo must be non-empty. Got: {url!r}")

    return f"https://dev.azure.com/{org}", project, repo


def _make_connection(org_url: str, token: str | None):
    """Create an authenticated Azure DevOps Connection.

    Uses BasicAuthentication with a PAT (Personal Access Token). The convention
    for AzDO PATs is an empty username and the PAT as the password. Without a
    token the connection is unauthenticated — private projects return 401/403,
    caught by the caller's error handler.

    # See README: "Tools" — authentication pattern
    """
    from azure.devops.connection import Connection
    from msrest.authentication import BasicAuthentication

    if not token:
        logger.warning("No AZURE_DEVOPS_TOKEN set — private repos will fail")
    logger.debug("Creating AzDO connection for %s", org_url)
    creds = BasicAuthentication("", token or "")
    return Connection(base_url=org_url, creds=creds)


@tool
def azdevops_read_repo(repo_url: str, max_depth: int = 2) -> str:
    """Read the repository file tree from an Azure DevOps repository.

    Returns top-level directory structure (up to max_depth), detected tech stack
    files (package.json, pyproject.toml, Dockerfile, etc.), and repo stats.
    Use this first to understand a project's structure before reading individual files.
    """
    # See README: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("azdevops_read_repo called: repo_url=%r, max_depth=%d", repo_url, max_depth)
    try:
        org_url, project, repo = _parse_azdo_url(repo_url)
        conn = _make_connection(org_url, get_azure_devops_token())
        git_client = conn.clients.get_git_client()

        # get_items with recursion_level="full" fetches the entire tree in one API call.
        # Each GitItem has .path (e.g. "/src/main.py") and .git_object_type ("blob"/"tree").
        items = git_client.get_items(repository_id=repo, project=project, recursion_level="full") or []

        lines: list[str] = [f"Repository: {project}/{repo}", f"Organization: {org_url}", ""]

        key_files_found: list[str] = []
        top_level_entries: set[str] = set()

        for item in items:
            path = item.path.lstrip("/")
            if not path:
                continue  # Skip the root entry that AzDO includes

            parts = path.split("/")
            name = parts[-1]

            if len(parts) == 1:
                top_level_entries.add(path)

            # Highlight key config/manifest files regardless of depth
            if name in _KEY_FILES or path in _KEY_FILES:
                key_files_found.append(path)

        lines.append("File tree (top level):")
        for entry in sorted(top_level_entries)[:50]:  # cap at 50 top-level entries
            lines.append(f"  {entry}")

        if key_files_found:
            lines.append("")
            lines.append("Key files detected:")
            for kf in sorted(key_files_found):
                lines.append(f"  {kf}")

        total_files = sum(1 for i in items if i.git_object_type == "blob")
        lines.append("")
        lines.append(f"Total files: {total_files}")

        logger.debug("azdevops_read_repo completed for %s/%s (%d files)", project, repo, total_files)
        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_read_repo: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_read_repo: %s", e)
        return f"Error: {e}"


@tool
def azdevops_read_file(repo_url: str, file_path: str) -> str:
    """Fetch the raw contents of a specific file from an Azure DevOps repository.

    Use this after azdevops_read_repo identifies an important file. Truncates at
    8 000 characters with a note if the file is larger.
    """
    logger.debug("azdevops_read_file called: repo=%r, path=%r", repo_url, file_path)
    try:
        org_url, project, repo = _parse_azdo_url(repo_url)
        conn = _make_connection(org_url, get_azure_devops_token())
        git_client = conn.clients.get_git_client()

        # get_item_content returns a generator of bytes chunks — join and decode.
        chunks = git_client.get_item_content(repository_id=repo, project=project, path=file_path)
        raw = b"".join(chunks)
        content = raw.decode("utf-8", errors="replace")

        truncated = False
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS]
            truncated = True

        logger.debug("azdevops_read_file fetched %s (%d bytes)", file_path, len(raw))
        header = f"File: {file_path} ({len(raw)} bytes)\n\n"
        suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
        return header + content + suffix

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_read_file: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_read_file: %s", e)
        return f"Error: {e}"


@tool
def azdevops_list_work_items(repo_url: str, max_items: int = 20, state: str = "Active") -> str:
    """List work items (tasks, bugs, user stories) from an Azure DevOps project.

    Returns work item ID, type, title, state, and assigned-to for up to max_items.
    Use this to understand current backlog and in-progress work to inform the scrum plan.
    state: 'Active' (default), 'New', 'Resolved', 'Closed', or 'All' (skips state filter).
    """
    logger.debug("azdevops_list_work_items called: repo=%r, state=%s", repo_url, state)
    try:
        # Wiql is the Azure DevOps query language — SQL-like syntax for querying work items.
        # Imported here (lazy) to follow the same pattern as other tool imports.
        # See README: "Tools" — tool types, read-only tool pattern
        from azure.devops.v7_1.work_item_tracking.models import Wiql

        org_url, project, _ = _parse_azdo_url(repo_url)
        conn = _make_connection(org_url, get_azure_devops_token())
        wit_client = conn.clients.get_work_item_tracking_client()

        # Omit the state clause when state='All' so all states are returned.
        state_clause = f" AND [System.State] = '{state}'" if state != "All" else ""
        wiql = Wiql(
            query=(
                f"SELECT [System.Id] FROM WorkItems"
                f" WHERE [System.TeamProject] = '{project}'{state_clause}"
                f" ORDER BY [System.ChangedDate] DESC"
            )
        )

        # query_by_wiql returns a WorkItemQueryResult with .work_items = list of refs (id + url only).
        result = wit_client.query_by_wiql(wiql, top=max_items)

        if not result.work_items:
            return f"No work items found in project '{project}' with state='{state}'."

        ids = [wi.id for wi in result.work_items]
        fields = ["System.Id", "System.WorkItemType", "System.Title", "System.State", "System.AssignedTo"]

        # get_work_items fetches full field data for each ID in one batch call.
        work_items = wit_client.get_work_items(ids, fields=fields)

        lines: list[str] = [f"Work items for project '{project}' (state={state}):", ""]
        for item in work_items:
            f = item.fields
            wi_id = f.get("System.Id", "?")
            wi_type = f.get("System.WorkItemType", "?")
            wi_title = f.get("System.Title", "?")
            wi_state = f.get("System.State", "?")
            assigned_raw = f.get("System.AssignedTo")

            # AssignedTo is a dict with displayName in newer API versions, or a plain string/None.
            if isinstance(assigned_raw, dict):
                assignee = assigned_raw.get("displayName", "Unassigned")
            elif assigned_raw:
                assignee = str(assigned_raw)
            else:
                assignee = "Unassigned"

            lines.append(f"#{wi_id} [{wi_type}] {wi_title} | State: {wi_state} | Assigned: {assignee}")

        logger.debug("azdevops_list_work_items returned %d items for %s", len(work_items), project)
        note = "; increase max_items to see more" if len(work_items) >= max_items else ""
        lines.append("")
        lines.append(f"({len(work_items)} work items shown{note})")
        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_list_work_items: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_list_work_items: %s", e)
        return f"Error: {e}"
