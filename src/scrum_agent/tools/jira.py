"""Jira Cloud tools — 1 read-only + 3 write (with user-confirmation guard in docstrings).

# See README: "Tools" — tool types, @tool decorator, risk levels
#
# Why jira (PyJira) instead of raw requests?
# PyJira wraps the REST API with typed objects and raises structured JIRAError
# exceptions (with status_code and text) on failures. This makes error handling
# predictable across all four tools — same pattern used by PyGithub and azure-devops.
#
# Auth: Jira Cloud uses HTTP Basic Auth with the user's email and an API token
# (not their password). Tokens are generated at id.atlassian.com → Security.
#
# Write tools carry a "only call after user confirms" note in their docstrings.
# The agent's ReAct loop reads these docstrings via bind_tools, so the agent
# knows to ask the user for confirmation before invoking any write operation.
# See README: "Guardrails" — human-in-the-loop pattern
"""

import json
import logging
import re

from jira import JIRA, JIRAError
from langchain_core.tools import tool

from scrum_agent.config import get_jira_base_url, get_jira_email, get_jira_project_key, get_jira_token

logger = logging.getLogger(__name__)

# Shown whenever Jira env vars are missing — single source of truth for the message.
_MISSING_CONFIG_MSG = (
    "Error: Jira is not configured. Add JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN to your .env file."
)


def _make_jira_client() -> JIRA | None:
    """Return an authenticated JIRA client, or None if any required config is missing.

    Uses HTTP Basic Auth: email + API token (Jira Cloud standard).
    Callers check for None and return _MISSING_CONFIG_MSG immediately.
    """
    base_url, email, token = get_jira_base_url(), get_jira_email(), get_jira_token()
    if not all([base_url, email, token]):
        logger.warning("Jira client not created — missing config (base_url/email/token)")
        return None
    logger.debug("Creating Jira client for %s", base_url)
    # JIRA(server, basic_auth=(email, token)) — PyJira Cloud auth pattern.
    client = JIRA(server=base_url, basic_auth=(email, token))
    logger.debug("Jira client created successfully")
    return client


def _jira_error_msg(e: JIRAError) -> str:
    """Return a user-friendly message for common Jira HTTP error codes."""
    code = getattr(e, "status_code", 0)
    if code == 401:
        return "Error: Jira authentication failed. Check JIRA_EMAIL and JIRA_API_TOKEN in .env."
    if code == 403:
        return "Error: Jira permission denied. Ensure your API token has the required project permissions."
    if code == 404:
        return f"Error: Jira resource not found — verify your project key or board ID. ({e.text})"
    if code == 429:
        return "Error: Jira rate limit reached. Wait a moment and try again."
    return f"Error: Jira API error {code}: {e.text}"


def _create_issue_with_epic_link(jira: JIRA, fields: dict, epic_key: str, link_method: str) -> tuple:
    """Create a Jira issue with an epic link, using the correct field for the project type.

    Jira Cloud has two project types with different epic-link fields:
      - Company-managed (classic): customfield_10014 (Epic Link)
      - Team-managed (next-gen):   parent: {"key": epic_key}

    link_method values:
      "auto"       — try customfield_10014; if Jira returns 400 (field not on screen),
                     fall back to parent field automatically.
      "epic_link"  — force customfield_10014 only (classic projects).
      "parent"     — force parent field only (next-gen projects).

    Returns (issue, link_field_used) so callers can report which method succeeded.
    """
    if link_method == "parent":
        fields["parent"] = {"key": epic_key}
        return jira.create_issue(fields=fields), "parent"

    # "epic_link" or "auto": start with the classic customfield_10014.
    fields["customfield_10014"] = epic_key
    if link_method == "epic_link":
        return jira.create_issue(fields=fields), "customfield_10014"

    # "auto": fall back to parent field when Jira rejects customfield_10014 (next-gen projects
    # return HTTP 400 because the field is not on the issue's create screen).
    try:
        return jira.create_issue(fields=fields), "customfield_10014"
    except JIRAError as e:
        if getattr(e, "status_code", 0) == 400:
            del fields["customfield_10014"]
            fields["parent"] = {"key": epic_key}
            return jira.create_issue(fields=fields), "parent"
        raise


# ---------------------------------------------------------------------------
# Non-@tool helpers for batch sync (called by jira_sync.py, not the ReAct agent)
# ---------------------------------------------------------------------------


def create_subtask(
    jira: JIRA,
    summary: str,
    parent_key: str,
    description: str = "",
    project_key: str = "",
    labels: list[str] | None = None,
    issue_type_name: str = "Sub-task",
) -> str:
    """Create a Jira Sub-task linked to a parent issue.

    Returns the new sub-task's issue key (e.g. "PROJ-99").
    Used by the batch sync module to create tasks as Jira Sub-tasks
    under their parent Story.

    issue_type_name defaults to "Sub-task" but can be overridden for
    projects that use "Subtask" or other names (discovered at runtime).

    # See README: "Tools" — tool types, write tools
    """
    key = project_key.strip() or (get_jira_project_key() or "")
    fields: dict = {
        "project": {"key": key},
        "summary": summary,
        "description": description,
        "issuetype": {"name": issue_type_name},
        "parent": {"key": parent_key},
    }
    if labels:
        fields["labels"] = labels
    issue = jira.create_issue(fields=fields)
    logger.debug("Created sub-task %s under %s", issue.key, parent_key)
    return issue.key


def add_issues_to_sprint(jira: JIRA, sprint_id: int, issue_keys: list[str]) -> None:
    """Move issues into a sprint by their keys.

    Calls the Jira Agile REST API to assign existing issues to a sprint.
    Used by the batch sync module to populate sprints after creation.

    # See README: "Tools" — tool types, write tools
    """
    if not issue_keys:
        return
    jira.add_issues_to_sprint(sprint_id, issue_keys)
    logger.debug("Added %d issues to sprint %d", len(issue_keys), sprint_id)


@tool
def jira_read_board(project_key: str = "") -> str:
    """Read the current state of a Jira board: active sprint, backlog size, and velocity.

    Discovers the board automatically from the project key. Falls back to
    JIRA_PROJECT_KEY env var when project_key is not provided. Returns a
    formatted summary with board name, active sprint, backlog count, and
    average velocity from the last 3 closed sprints.
    """
    # See README: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("jira_read_board called with project_key=%r", project_key)
    jira = _make_jira_client()
    if jira is None:
        return _MISSING_CONFIG_MSG

    key = project_key.strip() or (get_jira_project_key() or "")
    if not key:
        return "Error: No project key provided and JIRA_PROJECT_KEY is not set in .env."

    try:
        # boards() returns all boards visible to the authenticated user for this project.
        boards = jira.boards(projectKeyOrID=key)
        if not boards:
            logger.debug("No board found for project %s", key)
            return f"Error: No Jira board found for project '{key}'."

        board = boards[0]
        logger.debug("Found board %s (ID: %s) for project %s", board.name, board.id, key)
        lines: list[str] = [
            f"Board: {board.name} (ID: {board.id})",
            f"Project: {key}",
            "",
        ]

        # Active sprint — state="active" filters to currently running sprints.
        try:
            active_sprints = jira.sprints(board.id, state="active")
            if active_sprints:
                sp = active_sprints[0]
                lines.append(f"Active sprint: {sp.name}")
                if hasattr(sp, "startDate") and sp.startDate:
                    lines.append(f"  Start: {sp.startDate[:10]}")
                if hasattr(sp, "endDate") and sp.endDate:
                    lines.append(f"  End:   {sp.endDate[:10]}")
            else:
                lines.append("Active sprint: None")
        except JIRAError:
            lines.append("Active sprint: (could not retrieve)")

        # Backlog count — issues in the project with no sprint and not Done.
        try:
            backlog = jira.search_issues(
                f'project = "{key}" AND sprint is EMPTY AND status != Done',
                maxResults=1,
                fields="summary",
            )
            lines.append(f"Backlog issues: {backlog.total}")
        except JIRAError:
            lines.append("Backlog issues: (could not retrieve)")

        # Velocity — average story points across last 3 closed sprints.
        try:
            closed_sprints = jira.sprints(board.id, state="closed")
            sample = list(closed_sprints)[-3:] if len(closed_sprints) >= 3 else list(closed_sprints)
            if sample:
                totals: list[float] = []
                for sp in sample:
                    # sprint_info returns a dict with completed/notCompleted points.
                    info = jira.sprint_info(board.id, sp.id)
                    completed = info.get("completedPoints", 0)
                    # completedPoints may be a string ("0") — normalise.
                    try:
                        totals.append(float(completed))
                    except (TypeError, ValueError):
                        totals.append(0.0)
                avg = sum(totals) / len(totals) if totals else 0.0
                lines.append(f"Avg velocity (last {len(sample)} sprints): {avg:.1f} pts")
            else:
                lines.append("Avg velocity: no closed sprints found")
        except JIRAError:
            lines.append("Avg velocity: (could not retrieve)")

        logger.debug("jira_read_board completed for project %s", key)
        return "\n".join(lines)

    except JIRAError as e:
        logger.error("Jira API error in jira_read_board: %s", e)
        return _jira_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in jira_read_board: %s", e)
        return f"Error: {e}"


@tool
def jira_create_epic(
    title: str,
    description: str = "",
    project_key: str = "",
    issue_type: str = "Epic",
    internal_id: str = "",
) -> str:
    """Create a single project-level epic in Jira.

    Each project gets one Jira Epic that acts as the container for all features
    and stories. This is NOT called per-feature — features are internal planning
    artifacts that map to stories under the single epic.

    Only call this after the user has explicitly confirmed they want to create issues in Jira.
    Falls back to JIRA_PROJECT_KEY env var when project_key is not provided.
    Pass internal_id (e.g. 'epic-1') to record the mapping between the internal artifact
    ID and the created Jira key — the response will include a 'Mapping:' line for tracking.
    Returns the new epic's key and URL on success.
    issue_type defaults to 'Epic' but can be overridden for non-standard Jira configurations.
    """
    logger.debug("jira_create_epic called: title=%r, project_key=%r, type=%s", title, project_key, issue_type)
    jira = _make_jira_client()
    if jira is None:
        return _MISSING_CONFIG_MSG

    key = project_key.strip() or (get_jira_project_key() or "")
    if not key:
        return "Error: No project key provided and JIRA_PROJECT_KEY is not set in .env."

    try:
        fields = {
            "project": {"key": key},
            "summary": title,
            "description": description,
            "issuetype": {"name": issue_type},
        }
        issue = jira.create_issue(fields=fields)
        logger.debug("Created epic %s in project %s", issue.key, key)
        base_url = (get_jira_base_url() or "").rstrip("/")
        lines = [
            f"Created {issue_type}: {issue.key} — {title}",
            f"URL: {base_url}/browse/{issue.key}",
        ]
        # Include the internal→Jira mapping so the agent and downstream nodes can
        # record which internal epic ID corresponds to this Jira key.
        if internal_id:
            lines.append(f"Mapping: {internal_id} → {issue.key}")
        return "\n".join(lines)

    except JIRAError as e:
        logger.error("Jira API error in jira_create_epic: %s", e)
        return _jira_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in jira_create_epic: %s", e)
        return f"Error: {e}"


@tool
def jira_create_story(
    summary: str,
    epic_key: str,
    story_points: int = 0,
    priority: str = "Medium",
    description: str = "",
    project_key: str = "",
    issue_type: str = "Story",
    internal_id: str = "",
    labels: list[str] | None = None,
    link_method: str = "auto",
) -> str:
    """Create a user story in Jira linked to an epic.

    Only call this after the user has explicitly confirmed they want to create issues in Jira.
    Falls back to JIRA_PROJECT_KEY env var when project_key is not provided.
    story_points maps to customfield_10016 (Story Points — Jira Cloud next-gen standard).
    Pass internal_id (e.g. 'story-3') to record the mapping between the internal artifact
    ID and the created Jira key — the response will include a 'Mapping:' line for tracking.

    labels: Jira labels to attach. Use ["Code"] for backend/frontend/fullstack/infrastructure
    stories, ["Design"] for design stories, ["Testing"] for QA stories. Multiple labels are
    supported: e.g. ["Code", "Auth"].

    link_method: how to link to the parent epic.
      "auto"      — try customfield_10014 (classic Jira); fall back to parent field
                    automatically if Jira rejects it (team-managed/next-gen projects).
      "epic_link" — force customfield_10014 only.
      "parent"    — force parent field only (team-managed projects).

    Returns the new story's key, epic link, labels used, and URL on success.
    """
    logger.debug("jira_create_story called: summary=%r, epic=%s, pts=%d", summary, epic_key, story_points)
    jira = _make_jira_client()
    if jira is None:
        return _MISSING_CONFIG_MSG

    key = project_key.strip() or (get_jira_project_key() or "")
    if not key:
        return "Error: No project key provided and JIRA_PROJECT_KEY is not set in .env."

    try:
        fields: dict = {
            "project": {"key": key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type},
            "priority": {"name": priority},
        }
        # Only set story points when a non-zero value is provided.
        # customfield_10016: Story Points — standard next-gen Jira Cloud field.
        if story_points:
            fields["customfield_10016"] = story_points
        # Attach labels when provided — Jira accepts a list of plain strings.
        if labels:
            fields["labels"] = labels

        # Link the story to its parent epic. _create_issue_with_epic_link handles
        # the classic (customfield_10014) vs next-gen (parent field) difference.
        issue, link_field_used = _create_issue_with_epic_link(jira, fields, epic_key, link_method)
        logger.debug("Created story %s linked to %s via %s", issue.key, epic_key, link_field_used)

        base_url = (get_jira_base_url() or "").rstrip("/")
        lines = [
            f"Created {issue_type}: {issue.key}",
            f"Epic: {epic_key} (linked via {link_field_used})",
            f"URL: {base_url}/browse/{issue.key}",
        ]
        if labels:
            lines.append(f"Labels: {', '.join(labels)}")
        if internal_id:
            lines.append(f"Mapping: {internal_id} → {issue.key}")
        return "\n".join(lines)

    except JIRAError as e:
        logger.error("Jira API error in jira_create_story: %s", e)
        return _jira_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in jira_create_story: %s", e)
        return f"Error: {e}"


@tool
def jira_create_sprint(
    sprint_name: str,
    board_id: int,
    goal: str = "",
    start_date: str = "",
    end_date: str = "",
) -> str:
    """Create a new sprint on a Jira board.

    Only call this after the user has explicitly confirmed they want to create issues in Jira.
    board_id can be obtained from the jira_read_board output (shown as 'Board: Name (ID: <id>)').
    start_date and end_date should be ISO 8601 format (e.g. '2024-01-15').
    goal is a short description of the sprint's focus.
    Returns the new sprint's ID and name on success.
    """
    logger.debug("jira_create_sprint called: name=%r, board_id=%d", sprint_name, board_id)
    jira = _make_jira_client()
    if jira is None:
        return _MISSING_CONFIG_MSG

    try:
        # create_sprint accepts keyword arguments; optional fields are only passed when non-empty.
        kwargs: dict = {"name": sprint_name, "board_id": board_id}
        if goal:
            kwargs["goal"] = goal
        if start_date:
            kwargs["startDate"] = start_date
        if end_date:
            kwargs["endDate"] = end_date

        sprint = jira.create_sprint(**kwargs)
        logger.debug("Created sprint %s (ID: %s) on board %d", sprint.name, sprint.id, board_id)
        return f"Created sprint '{sprint.name}' (ID: {sprint.id}) on board {board_id}"

    except JIRAError as e:
        logger.error("Jira API error in jira_create_sprint: %s", e)
        return _jira_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in jira_create_sprint: %s", e)
        return f"Error: {e}"


@tool
def jira_fetch_velocity(project_key: str = "") -> str:
    """Fetch average team velocity and team size from the last 3 closed sprints.

    Connects to Jira, finds the first board for the project, samples the last
    3 closed sprints, and computes average completed story points plus unique
    assignees. Per-developer velocity is derived by dividing team velocity by
    the number of unique assignees.

    Returns a JSON string with keys: team_velocity, jira_team_size, per_dev_velocity.
    Returns an error string starting with "Error:" on failure.

    # See README: "Scrum Standards" — capacity planning
    #
    # The whole-team velocity from Jira must be normalised to per-developer
    # because the feature team may be a subset of the full Jira team.
    # E.g. team avg = 25 pts with 5 devs → 5 pts/dev. If 2 devs work on
    # the feature → feature velocity = 10 pts, not 25.
    """
    logger.debug("jira_fetch_velocity called with project_key=%r", project_key)
    jira = _make_jira_client()
    if jira is None:
        return _MISSING_CONFIG_MSG

    key = project_key.strip() or (get_jira_project_key() or "")
    if not key:
        return "Error: No project key provided and JIRA_PROJECT_KEY is not set in .env."

    try:
        boards = jira.boards(projectKeyOrID=key)
        if not boards:
            return f"Error: No Jira board found for project '{key}'."

        board = boards[0]
        closed_sprints = jira.sprints(board.id, state="closed")
        sample = list(closed_sprints)[-3:] if closed_sprints else []
        if not sample:
            logger.debug("No closed sprints found for project %s", key)
            return "Error: No closed sprints found — velocity cannot be computed."

        # Compute avg velocity from completed points and count unique assignees.
        # Team size is determined by counting unique assignees from sub-tasks
        # across the sampled closed sprints — sub-tasks better reflect who
        # actually did the work (stories are often assigned to a single lead).
        #
        # Two velocity sources are tried per sprint:
        #   1. sprint_info() → completedPoints (Jira Agile sprint report)
        #   2. JQL sum of story_points for Done issues (fallback when
        #      completedPoints is missing/zero — common with certain board
        #      configs or Jira Cloud plans)
        totals: list[float] = []
        assignees: set[str] = set()
        logger.debug("Sampling %d closed sprint(s): %s", len(sample), [s.name for s in sample])
        for sp in sample:
            # --- Velocity: try sprint report first, then JQL fallback ---
            info = jira.sprint_info(board.id, sp.id)
            completed = info.get("completedPoints", 0)
            try:
                completed = float(completed)
            except (TypeError, ValueError):
                completed = 0.0
            logger.debug("Sprint %s (id=%s): completedPoints=%s", sp.name, sp.id, completed)

            # Fallback: sum story points from Done issues via JQL when the
            # sprint report has no completedPoints.  This happens when the
            # board's estimation statistic is not configured or uses a
            # different field than what sprint_info() expects.
            #
            # We read customfield_10016 (Jira Cloud next-gen standard for
            # "Story Points") — the same field jira_create_story writes to.
            # The jira-python alias "story_points" is also checked as a
            # fallback for classic project boards that map it differently.
            if completed <= 0:
                try:
                    done_issues = jira.search_issues(
                        f'project = "{key}" AND sprint = {sp.id} AND status = Done',
                        maxResults=500,
                        fields="customfield_10016,story_points,assignee",
                    )
                    jql_total = 0.0
                    for issue in done_issues:
                        # Try customfield_10016 first (Jira Cloud standard),
                        # then fall back to story_points alias.
                        sp_val = getattr(issue.fields, "customfield_10016", None)
                        if sp_val is None:
                            sp_val = getattr(issue.fields, "story_points", None)
                        if sp_val is not None:
                            try:
                                jql_total += float(sp_val)
                            except (TypeError, ValueError):
                                pass
                    if jql_total > 0:
                        logger.debug("Sprint %s: JQL fallback story_points=%s", sp.name, jql_total)
                        completed = jql_total
                    else:
                        logger.debug(
                            "Sprint %s: JQL fallback found %d Done issues but 0 story points", sp.name, len(done_issues)
                        )
                except JIRAError as exc:
                    logger.debug("Sprint %s: JQL fallback failed: %s", sp.name, exc)

            totals.append(completed)

            # --- Team size: count unique assignees from sub-tasks ---
            # Sub-tasks better reflect who actually did the work (stories are
            # often assigned to a lead while multiple engineers work on sub-tasks).
            try:
                subtasks = jira.search_issues(
                    f'project = "{key}" AND sprint = {sp.id} AND status = Done AND issuetype = Sub-task',
                    maxResults=500,
                    fields="assignee",
                )
                for issue in subtasks:
                    assignee = getattr(issue.fields, "assignee", None)
                    if assignee:
                        assignees.add(assignee.accountId)
                # Fall back to story-level assignees if no sub-tasks found
                if not assignees:
                    stories = jira.search_issues(
                        f'project = "{key}" AND sprint = {sp.id} AND status = Done',
                        maxResults=200,
                        fields="assignee",
                    )
                    for issue in stories:
                        assignee = getattr(issue.fields, "assignee", None)
                        if assignee:
                            assignees.add(assignee.accountId)
            except JIRAError:
                pass  # Non-fatal: assignee count may be incomplete

        team_velocity = sum(totals) / len(totals) if totals else 0.0
        logger.debug("Velocity totals=%s, avg=%.1f, assignees=%d", totals, team_velocity, len(assignees))
        jira_team_size = max(len(assignees), 1)

        if team_velocity <= 0:
            # Return team size even when velocity is zero — the org headcount
            # is still useful for capping "increase team" recommendations.
            return json.dumps(
                {
                    "team_velocity": 0,
                    "jira_team_size": jira_team_size,
                    "per_dev_velocity": 0,
                    "velocity_error": "Computed velocity is zero — no completed points in sampled sprints.",
                }
            )
        per_dev = team_velocity / jira_team_size

        logger.debug("Velocity result: team=%.1f, size=%d, per_dev=%.1f", team_velocity, jira_team_size, per_dev)
        return json.dumps(
            {
                "team_velocity": round(team_velocity),
                "jira_team_size": jira_team_size,
                "per_dev_velocity": per_dev,
            }
        )

    except JIRAError as e:
        logger.error("Jira API error in jira_fetch_velocity: %s", e)
        return _jira_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in jira_fetch_velocity: %s", e)
        return f"Error: {e}"


@tool
def jira_fetch_active_sprint(project_key: str = "") -> str:
    """Fetch the currently active sprint number and name from Jira.

    Connects to Jira, finds the first board for the project, and retrieves the
    active sprint. Parses the sprint number from the sprint name (e.g. "Sprint 104"
    → 104).

    Returns a JSON string with keys: sprint_number, sprint_name.
    Returns an error string starting with "Error:" on failure.

    # See README: "Scrum Standards" — sprint planning
    """
    logger.debug("jira_fetch_active_sprint called with project_key=%r", project_key)
    jira = _make_jira_client()
    if jira is None:
        return _MISSING_CONFIG_MSG

    key = project_key.strip() or (get_jira_project_key() or "")
    if not key:
        return "Error: No project key provided and JIRA_PROJECT_KEY is not set in .env."

    try:
        boards = jira.boards(projectKeyOrID=key)
        if not boards:
            return f"Error: No Jira boards found for project {key}"

        board = boards[0]
        active_sprints = jira.sprints(board.id, state="active")
        if not active_sprints:
            logger.debug("No active sprint on board %s", board.name)
            return f"Error: No active sprint on board '{board.name}'"

        active_sprint = active_sprints[0]
        sprint_name = active_sprint.name
        match = re.search(r"(\d+)", sprint_name)
        if not match:
            logger.warning("Could not parse sprint number from %r", sprint_name)
            return f"Error: Could not parse sprint number from '{sprint_name}'"

        # Extract start date — Jira returns ISO datetime strings
        start_date = getattr(active_sprint, "startDate", None) or ""
        if start_date:
            start_date = start_date[:10]  # "2026-03-02T..." → "2026-03-02"

        logger.debug("Active sprint found: %s (start=%s)", sprint_name, start_date)
        result_data = {
            "sprint_number": int(match.group(1)),
            "sprint_name": sprint_name,
        }
        if start_date:
            result_data["start_date"] = start_date
        return json.dumps(result_data)

    except JIRAError as e:
        logger.error("Jira API error in jira_fetch_active_sprint: %s", e)
        return _jira_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in jira_fetch_active_sprint: %s", e)
        return f"Error: {e}"
