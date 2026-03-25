"""Azure DevOps tools for fetching repo context and creating work items.

# See README: "Tools" — tool types, @tool decorator, risk levels
#
# Read tools (low risk) — fetch data from the Azure DevOps REST API and return
# it as a string for the LLM to reason about. Write tools (high risk) — create
# work items and require user confirmation before invocation.
#
# Why azure-devops SDK instead of raw requests?
# The SDK wraps the REST API with typed objects, handles authentication via
# BasicAuthentication (PAT), and raises AzureDevOpsServiceError for API
# failures. This makes error handling predictable across all tools.
#
# URL format supported (modern only):
#   https://dev.azure.com/{org}/{project}/_git/{repo}
"""

import logging
from datetime import UTC

from azure.devops.exceptions import AzureDevOpsServiceError
from langchain_core.tools import tool

from scrum_agent.config import (
    get_azure_devops_org_url,
    get_azure_devops_project,
    get_azure_devops_team,
    get_azure_devops_token,
)

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


# ---------------------------------------------------------------------------
# Board / Velocity / Iteration tools (use org-level config, not repo URL)
# ---------------------------------------------------------------------------


def _make_azdo_clients(org_url: str | None = None, token: str | None = None):
    """Create authenticated WIT and Work clients from a single connection.

    Returns (wit_client, work_client). Uses config defaults when args are None.
    # See README: "Tools" — authentication pattern
    """
    org_url = org_url or get_azure_devops_org_url()
    token = token or get_azure_devops_token()
    if not org_url:
        raise ValueError("AZURE_DEVOPS_ORG_URL is not set. Add it to your .env file.")
    conn = _make_connection(org_url, token)
    wit_client = conn.clients.get_work_item_tracking_client()
    work_client = conn.clients.get_work_client()
    return wit_client, work_client


@tool
def azdevops_read_board(project: str = "") -> str:
    """Read board info from an Azure DevOps project: active iteration, backlog count, and average velocity.

    Returns the current iteration name, number of backlog items, and average velocity
    computed from the last 3 completed iterations. Use this to understand the team's
    current capacity and throughput before planning sprints.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_read_board called: project=%r", project)
    try:
        from azure.devops.v7_1.work.models import TeamContext

        _, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)

        lines: list[str] = [f"Azure DevOps Board: {project}", f"Team: {team}", ""]

        # Fetch all team iterations and classify by date
        from datetime import datetime as _dt

        all_iterations = work_client.get_team_iterations(team_context) or []
        now = _dt.now(UTC)
        current_iter = None
        past_iters: list = []

        for it in all_iterations:
            attrs = getattr(it, "attributes", None)
            start = getattr(attrs, "start_date", None) if attrs else None
            end = getattr(attrs, "finish_date", None) if attrs else None
            if start and end:
                if start <= now <= end:
                    current_iter = it
                elif end < now:
                    past_iters.append(it)

        # Current iteration
        if current_iter:
            attrs = current_iter.attributes
            start = getattr(attrs, "start_date", None)
            end = getattr(attrs, "finish_date", None)
            start_str = start.strftime("%Y-%m-%d") if start else "?"
            end_str = end.strftime("%Y-%m-%d") if end else "?"
            lines.append(f"Active iteration: {current_iter.name} ({start_str} to {end_str})")
        else:
            lines.append("Active iteration: None")

        # Past iterations for velocity (last 3)
        try:
            recent = past_iters[-3:]
            total_points = 0.0
            iter_count = 0

            wit_client = _make_azdo_clients()[0]
            for iteration in recent:
                iter_id = iteration.id
                try:
                    work_items = work_client.get_iteration_work_items(team_context, iter_id)
                    wi_ids = []
                    for relation in getattr(work_items, "work_item_relations", []) or []:
                        target = getattr(relation, "target", None)
                        if target:
                            wi_ids.append(target.id)
                    if wi_ids:
                        items = wit_client.get_work_items(
                            wi_ids,
                            fields=[
                                "System.State",
                                "Microsoft.VSTS.Scheduling.StoryPoints",
                            ],
                        )
                        for item in items or []:
                            state = item.fields.get("System.State", "")
                            if state in ("Closed", "Done", "Resolved", "Completed"):
                                pts = item.fields.get("Microsoft.VSTS.Scheduling.StoryPoints")
                                if pts:
                                    total_points += float(pts)
                        iter_count += 1
                except Exception as e:
                    logger.warning("Could not fetch iteration %s work items: %s", iteration.name, e)

            if iter_count > 0:
                avg_velocity = total_points / iter_count
                lines.append(f"Average velocity (last {iter_count} iterations): {avg_velocity:.1f} points")
                lines.append(f"Total completed points: {total_points:.0f}")
            else:
                lines.append("Velocity: No completed iteration data available")
        except Exception as e:
            logger.warning("Could not fetch past iterations: %s", e)
            lines.append(f"Velocity: Error ({e})")

        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_read_board: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_read_board: %s", e)
        return f"Error: {e}"


@tool
def azdevops_fetch_velocity(project: str = "") -> str:
    """Fetch team velocity data from Azure DevOps: average points, team size, per-developer velocity.

    Computes velocity from the last 3 completed iterations and team size from unique
    assignees on completed items. Returns structured data for capacity planning.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_fetch_velocity called: project=%r", project)
    try:
        from azure.devops.v7_1.work.models import TeamContext

        wit_client, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)

        # Fetch all iterations and filter to past (finished before now) by date.
        # The timeframe="past" parameter is not supported by all AzDO API versions.
        from datetime import datetime as _dt

        all_iterations = work_client.get_team_iterations(team_context) or []
        now = _dt.now(UTC)
        past_iterations = [
            it
            for it in all_iterations
            if getattr(getattr(it, "attributes", None), "finish_date", None) and it.attributes.finish_date < now
        ]
        recent = past_iterations[-3:]

        total_points = 0.0
        iter_count = 0
        assignees: set[str] = set()

        for iteration in recent:
            iter_id = iteration.id
            try:
                work_items = work_client.get_iteration_work_items(team_context, iter_id)
                wi_ids = []
                for relation in getattr(work_items, "work_item_relations", []) or []:
                    target = getattr(relation, "target", None)
                    if target:
                        wi_ids.append(target.id)
                if wi_ids:
                    items = wit_client.get_work_items(
                        wi_ids,
                        fields=[
                            "System.State",
                            "Microsoft.VSTS.Scheduling.StoryPoints",
                            "System.AssignedTo",
                        ],
                    )
                    for item in items or []:
                        state = item.fields.get("System.State", "")
                        if state in ("Closed", "Done", "Resolved", "Completed"):
                            pts = item.fields.get("Microsoft.VSTS.Scheduling.StoryPoints")
                            if pts:
                                total_points += float(pts)
                            assigned = item.fields.get("System.AssignedTo")
                            if isinstance(assigned, dict):
                                name = assigned.get("uniqueName") or assigned.get("displayName", "")
                            elif assigned:
                                name = str(assigned)
                            else:
                                name = ""
                            if name:
                                assignees.add(name)
                    iter_count += 1
            except Exception as e:
                logger.warning("Could not fetch iteration %s: %s", iteration.name, e)

        if iter_count == 0:
            return "No completed iteration data available for velocity calculation."

        avg_velocity = total_points / iter_count
        team_size = len(assignees) or 1
        per_dev = avg_velocity / team_size

        lines = [
            f"Team velocity: {avg_velocity:.1f} points/iteration (avg of {iter_count} iterations)",
            f"Team size: {team_size} (unique assignees on completed items)",
            f"Per-developer velocity: {per_dev:.1f} points/iteration",
        ]
        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_fetch_velocity: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_fetch_velocity: %s", e)
        return f"Error: {e}"


@tool
def azdevops_fetch_active_iteration(project: str = "") -> str:
    """Fetch the active (current) iteration from Azure DevOps.

    Returns sprint number, sprint name, and start date of the current iteration.
    Use this to determine the team's current sprint for planning purposes.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_fetch_active_iteration called: project=%r", project)
    try:
        import re as _re

        from azure.devops.v7_1.work.models import TeamContext

        _, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)

        # Find the current iteration by date (timeframe="current" not supported
        # by all AzDO API versions).
        from datetime import datetime as _dt

        all_iterations = work_client.get_team_iterations(team_context) or []
        now = _dt.now(UTC)
        current_iterations = [
            it
            for it in all_iterations
            if getattr(getattr(it, "attributes", None), "start_date", None)
            and getattr(it.attributes, "finish_date", None)
            and it.attributes.start_date <= now <= it.attributes.finish_date
        ]
        if not current_iterations:
            return "No active iteration found."

        cur = current_iterations[0]
        attrs = cur.attributes
        start = getattr(attrs, "start_date", None)
        start_str = start.strftime("%Y-%m-%d") if start else ""

        # Extract sprint number from name (e.g. "Sprint 42" → 42)
        match = _re.search(r"(\d+)\s*$", cur.name or "")
        sprint_number = int(match.group(1)) if match else 0

        lines = [
            f"Sprint name: {cur.name}",
            f"Sprint number: {sprint_number}",
            f"Start date: {start_str}",
        ]
        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_fetch_active_iteration: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_fetch_active_iteration: %s", e)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Write tools — create work items (require user confirmation)
# ---------------------------------------------------------------------------


@tool
def azdevops_create_epic(title: str, description: str = "", project: str = "") -> str:
    """Create an Epic work item in Azure DevOps. Only call after user confirms.

    Creates a top-level Epic with the given title and description. Returns the
    work item ID on success.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_create_epic called: title=%r, project=%r", title, project)
    try:
        from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

        wit_client = _make_azdo_clients()[0]

        document = [
            JsonPatchOperation(op="add", path="/fields/System.Title", value=title),
            JsonPatchOperation(op="add", path="/fields/System.Description", value=description),
        ]

        work_item = wit_client.create_work_item(document=document, project=project, type="Epic")
        wi_id = str(work_item.id)
        logger.info("Created AzDO Epic: %s (ID: %s)", title, wi_id)
        return f"Created Epic '{title}' — Work Item ID: {wi_id}"

    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_create_epic: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_create_epic: %s", e)
        return f"Error: {e}"


@tool
def azdevops_create_story(
    summary: str,
    epic_id: str = "",
    story_points: int = 0,
    priority: int = 3,
    description: str = "",
    project: str = "",
) -> str:
    """Create a User Story work item in Azure DevOps. Only call after user confirms.

    Creates a User Story linked to a parent Epic (if epic_id is provided).
    Priority: 1=Critical, 2=High, 3=Medium, 4=Low.
    Returns the work item ID on success.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_create_story called: summary=%r, epic_id=%r, project=%r", summary, epic_id, project)
    try:
        from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

        wit_client = _make_azdo_clients()[0]

        document = [
            JsonPatchOperation(op="add", path="/fields/System.Title", value=summary),
            JsonPatchOperation(op="add", path="/fields/System.Description", value=description),
            JsonPatchOperation(
                op="add",
                path="/fields/Microsoft.VSTS.Common.Priority",
                value=priority,
            ),
        ]

        if story_points > 0:
            document.append(
                JsonPatchOperation(
                    op="add",
                    path="/fields/Microsoft.VSTS.Scheduling.StoryPoints",
                    value=float(story_points),
                )
            )

        # Link to parent Epic via System.LinkTypes.Hierarchy-Reverse
        if epic_id:
            org_url = get_azure_devops_org_url() or ""
            document.append(
                JsonPatchOperation(
                    op="add",
                    path="/relations/-",
                    value={
                        "rel": "System.LinkTypes.Hierarchy-Reverse",
                        "url": f"{org_url}/{project}/_apis/wit/workItems/{epic_id}",
                    },
                )
            )

        work_item = wit_client.create_work_item(document=document, project=project, type="User Story")
        wi_id = str(work_item.id)
        logger.info("Created AzDO User Story: %s (ID: %s)", summary, wi_id)
        return f"Created User Story '{summary}' — Work Item ID: {wi_id}"

    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_create_story: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_create_story: %s", e)
        return f"Error: {e}"


@tool
def azdevops_create_iteration(name: str, start_date: str = "", finish_date: str = "", project: str = "") -> str:
    """Create an iteration (sprint) in Azure DevOps. Only call after user confirms.

    Creates an iteration classification node with optional start and finish dates.
    start_date and finish_date are ISO date strings (e.g. "2026-03-16").
    Returns the iteration path on success.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_create_iteration called: name=%r, project=%r", name, project)
    try:
        from scrum_agent.azdevops_sync import _create_iteration_node

        org_url = get_azure_devops_org_url() or ""
        token = get_azure_devops_token() or ""
        if not org_url:
            return "Error: AZURE_DEVOPS_ORG_URL is not set."

        iteration_path = _create_iteration_node(org_url, token, project, name, start_date, finish_date)
        logger.info("Created AzDO Iteration: %s → %s", name, iteration_path)
        return f"Created Iteration '{name}' — Path: {iteration_path}"
    except Exception as e:
        logger.error("Unexpected error in azdevops_create_iteration: %s", e)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Non-@tool helpers (used by azdevops_sync.py for batch operations)
# ---------------------------------------------------------------------------


def create_task(title: str, description: str, story_id: str, project: str = "") -> str:
    """Create a Task work item linked to a parent User Story.

    Not a @tool — called directly by azdevops_sync.py during batch sync.
    Returns the work item ID string.
    """
    project = project or get_azure_devops_project() or ""
    from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

    wit_client = _make_azdo_clients()[0]
    org_url = get_azure_devops_org_url() or ""

    # Area path = "{project}\{team}" — assigns task to the team's board area.
    team = get_azure_devops_team() or ""
    area_path = f"{project}\\{team}" if team else project

    document = [
        JsonPatchOperation(op="add", path="/fields/System.Title", value=title),
        JsonPatchOperation(op="add", path="/fields/System.Description", value=description),
        JsonPatchOperation(op="add", path="/fields/System.AreaPath", value=area_path),
        JsonPatchOperation(
            op="add",
            path="/relations/-",
            value={
                "rel": "System.LinkTypes.Hierarchy-Reverse",
                "url": f"{org_url}/{project}/_apis/wit/workItems/{story_id}",
            },
        ),
    ]

    work_item = wit_client.create_work_item(document=document, project=project, type="Task")
    return str(work_item.id)


def add_work_items_to_iteration(work_item_ids: list[str], iteration_path: str, project: str = "") -> None:
    """Assign work items to an iteration by setting their System.IterationPath field.

    Not a @tool — called directly by azdevops_sync.py during batch sync.
    """
    project = project or get_azure_devops_project() or ""
    from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

    wit_client = _make_azdo_clients()[0]

    for wi_id in work_item_ids:
        document = [
            JsonPatchOperation(op="add", path="/fields/System.IterationPath", value=iteration_path),
        ]
        wit_client.update_work_item(document=document, id=int(wi_id), project=project)
