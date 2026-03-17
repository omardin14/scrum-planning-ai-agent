"""Snapshot tests for Rich formatter output in src/scrum_agent/formatters.py.

# See README: "Architecture" — REPL-side formatter layer

Why snapshot tests?
-------------------
The formatters produce Rich Tables, Panels, and Groups whose visual structure
is hard to assert with traditional equality checks (column widths, word-wrap,
border characters, multi-line cells). Snapshot tests capture the rendered
output once and then catch any visual regression automatically on the next run.

How snapshots are stored:
- syrupy writes `.ambr` files to tests/unit/__snapshots__/
- Each ``assert rendered == snapshot`` call creates a named entry
- Run ``make snapshot-update`` (or ``pytest --snapshot-update``) to accept
  intentional format changes and rewrite the stored baselines

Every formatter is tested in two modes:
- **full** (compact=False) — all columns/sections visible, default mode
- **compact** (compact=True) — secondary columns omitted, for narrow terminals

Additional edge cases:
- **empty** input lists — no crash, graceful empty-state rendering
- **overcapacity sprint** — bar turns red, border turns red

Rich rendering to plain text:
``Console(force_terminal=False, width=80)`` renders without ANSI codes so
snapshots are human-readable diffs in the .ambr files.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console, ConsoleRenderable

from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Epic,
    Priority,
    ProjectAnalysis,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)

# ---------------------------------------------------------------------------
# Render helper
# ---------------------------------------------------------------------------


def _render(obj: ConsoleRenderable, width: int = 80) -> str:
    """Render a Rich renderable to a plain-text string (no ANSI codes).

    Using force_terminal=False strips colour/style codes so snapshots contain
    only the structural content (borders, columns, text). This makes the .ambr
    files human-readable and produces stable diffs when layout changes.
    """
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=width, highlight=False)
    console.print(obj)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

_EPIC_AUTH = Epic(id="E1", title="User Authentication", description="OAuth2 and JWT flow", priority=Priority.HIGH)
_EPIC_TASKS = Epic(id="E2", title="Task Management", description="CRUD for tasks", priority=Priority.MEDIUM)

_AC_LOGIN = AcceptanceCriterion(
    given="a valid email and password",
    when="the user submits the login form",
    then="they are redirected to the dashboard",
)
_AC_LOGOUT = AcceptanceCriterion(
    given="an authenticated session",
    when="the user clicks logout",
    then="the session is invalidated",
)

_STORY_LOGIN = UserStory(
    id="US-1",
    epic_id="E1",
    persona="developer",
    goal="log in with email and password",
    benefit="I can access my personal task list",
    acceptance_criteria=(_AC_LOGIN, _AC_LOGOUT),
    story_points=StoryPointValue.THREE,
    priority=Priority.HIGH,
    discipline=Discipline.BACKEND,
    dod_applicable=(True, True, True, True, False, True, False),
)

_STORY_CREATE_TASK = UserStory(
    id="US-2",
    epic_id="E2",
    persona="user",
    goal="create a new task",
    benefit="I can track my work",
    acceptance_criteria=(
        AcceptanceCriterion(given="I am logged in", when="I submit the task form", then="the task is saved"),
    ),
    story_points=StoryPointValue.TWO,
    priority=Priority.MEDIUM,
    discipline=Discipline.FULLSTACK,
)

_TASK_1 = Task(id="T1", story_id="US-1", title="Implement JWT middleware", description="Add JWT validation to FastAPI")
_TASK_2 = Task(
    id="T2", story_id="US-1", title="Write login endpoint", description="POST /auth/login with rate limiting"
)
_TASK_3 = Task(id="T3", story_id="US-2", title="Create task model", description="SQLAlchemy Task model with due date")

_SPRINT_1 = Sprint(id="SP1", name="Sprint 1", goal="Establish auth foundation", capacity_points=5, story_ids=("US-1",))
_SPRINT_2 = Sprint(id="SP2", name="Sprint 2", goal="Core task CRUD", capacity_points=2, story_ids=("US-2",))

_ANALYSIS = ProjectAnalysis(
    project_name="TodoApp",
    project_description="Full-stack todo app with auth",
    project_type="greenfield",
    goals=("Ship MVP in 6 weeks", "Support 100 concurrent users"),
    end_users=("individual developers", "small teams"),
    target_state="Production deployment on AWS",
    tech_stack=("React", "FastAPI", "PostgreSQL"),
    integrations=("GitHub Actions",),
    constraints=("Must use existing CI pipeline",),
    sprint_length_weeks=2,
    target_sprints=3,
    risks=("Third-party OAuth provider downtime",),
    out_of_scope=("Mobile app",),
    assumptions=("Team has React experience",),
)


# ---------------------------------------------------------------------------
# render_analysis_panel
# ---------------------------------------------------------------------------


class TestRenderAnalysisPanel:
    """Snapshot tests for the project analysis Rich Panel.

    # See README: "Architecture" — project_analyzer node output display
    """

    def test_full(self, snapshot):
        from scrum_agent.formatters import render_analysis_panel

        assert _render(render_analysis_panel(_ANALYSIS)) == snapshot

    def test_compact(self, snapshot):
        from scrum_agent.formatters import render_analysis_panel

        assert _render(render_analysis_panel(_ANALYSIS, compact=True)) == snapshot

    def test_no_assumptions_or_contributions(self, snapshot):
        """Analysis without optional fields renders cleanly (no extra section)."""
        from scrum_agent.formatters import render_analysis_panel

        analysis = ProjectAnalysis(
            project_name="Minimal",
            project_description="Simple project",
            project_type="greenfield",
            goals=("Launch",),
            end_users=("developers",),
            target_state="Deployed",
            tech_stack=("Python",),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=2,
            risks=(),
            out_of_scope=(),
            assumptions=(),  # no assumptions → no yellow section
        )
        assert _render(render_analysis_panel(analysis)) == snapshot


# ---------------------------------------------------------------------------
# render_epics_table
# ---------------------------------------------------------------------------


class TestRenderEpicsTable:
    """Snapshot tests for the epics Rich Table.

    # See README: "Scrum Standards" — epic decomposition
    """

    def test_full(self, snapshot):
        from scrum_agent.formatters import render_epics_table

        assert _render(render_epics_table([_EPIC_AUTH, _EPIC_TASKS])) == snapshot

    def test_compact(self, snapshot):
        from scrum_agent.formatters import render_epics_table

        assert _render(render_epics_table([_EPIC_AUTH, _EPIC_TASKS], compact=True)) == snapshot

    def test_empty_list(self, snapshot):
        """Empty epics list produces a table with headers but no rows."""
        from scrum_agent.formatters import render_epics_table

        assert _render(render_epics_table([])) == snapshot

    def test_all_priorities_represented(self, snapshot):
        """All four Priority levels are rendered with their colour-coded styles."""
        from scrum_agent.formatters import render_epics_table

        epics = [
            Epic(id="E1", title="Critical Epic", description="Urgent", priority=Priority.CRITICAL),
            Epic(id="E2", title="High Epic", description="High prio", priority=Priority.HIGH),
            Epic(id="E3", title="Medium Epic", description="Med prio", priority=Priority.MEDIUM),
            Epic(id="E4", title="Low Epic", description="Low prio", priority=Priority.LOW),
        ]
        assert _render(render_epics_table(epics)) == snapshot


# ---------------------------------------------------------------------------
# render_stories_table
# ---------------------------------------------------------------------------


class TestRenderStoriesTable:
    """Snapshot tests for the stories Rich Group (one Table per epic).

    # See README: "Scrum Standards" — user story format, acceptance criteria
    """

    def test_full(self, snapshot):
        from scrum_agent.formatters import render_stories_table

        stories = [_STORY_LOGIN, _STORY_CREATE_TASK]
        assert _render(render_stories_table(stories, [_EPIC_AUTH, _EPIC_TASKS])) == snapshot

    def test_compact(self, snapshot):
        from scrum_agent.formatters import render_stories_table

        stories = [_STORY_LOGIN, _STORY_CREATE_TASK]
        assert _render(render_stories_table(stories, [_EPIC_AUTH, _EPIC_TASKS], compact=True)) == snapshot

    def test_empty_list(self, snapshot):
        """Empty stories list returns an empty Group (no tables rendered)."""
        from scrum_agent.formatters import render_stories_table

        assert _render(render_stories_table([], [])) == snapshot

    def test_multiple_stories_per_epic(self, snapshot):
        """Two stories under the same epic are rendered in a single table."""
        from scrum_agent.formatters import render_stories_table

        story_b = UserStory(
            id="US-3",
            epic_id="E1",
            persona="admin",
            goal="reset a user password",
            benefit="account recovery is possible",
            acceptance_criteria=(_AC_LOGIN,),
            story_points=StoryPointValue.TWO,
            priority=Priority.MEDIUM,
        )
        assert _render(render_stories_table([_STORY_LOGIN, story_b], [_EPIC_AUTH])) == snapshot


# ---------------------------------------------------------------------------
# render_tasks_table
# ---------------------------------------------------------------------------


class TestRenderTasksTable:
    """Snapshot tests for the tasks Rich Group (one Table per epic).

    # See README: "Scrum Standards" — task decomposition
    """

    def test_full(self, snapshot):
        from scrum_agent.formatters import render_tasks_table

        tasks = [_TASK_1, _TASK_2, _TASK_3]
        stories = [_STORY_LOGIN, _STORY_CREATE_TASK]
        epics = [_EPIC_AUTH, _EPIC_TASKS]
        assert _render(render_tasks_table(tasks, stories, epics)) == snapshot

    def test_compact(self, snapshot):
        from scrum_agent.formatters import render_tasks_table

        assert (
            _render(
                render_tasks_table(
                    [_TASK_1, _TASK_2, _TASK_3],
                    [_STORY_LOGIN, _STORY_CREATE_TASK],
                    [_EPIC_AUTH, _EPIC_TASKS],
                    compact=True,
                )
            )
            == snapshot
        )

    def test_empty_list(self, snapshot):
        """Empty tasks list returns an empty Group."""
        from scrum_agent.formatters import render_tasks_table

        assert _render(render_tasks_table([], [], [])) == snapshot

    def test_multiple_tasks_per_story(self, snapshot):
        """Two tasks under the same story both appear under the story header row."""
        from scrum_agent.formatters import render_tasks_table

        assert _render(render_tasks_table([_TASK_1, _TASK_2], [_STORY_LOGIN], [_EPIC_AUTH])) == snapshot


# ---------------------------------------------------------------------------
# render_sprint_plan
# ---------------------------------------------------------------------------


class TestRenderSprintPlan:
    """Snapshot tests for the sprint plan Rich Group (summary + per-sprint Panels).

    # See README: "Scrum Standards" — sprint planning, capacity allocation
    """

    def test_full(self, snapshot):
        from scrum_agent.formatters import render_sprint_plan

        assert (
            _render(
                render_sprint_plan(
                    [_SPRINT_1, _SPRINT_2],
                    [_STORY_LOGIN, _STORY_CREATE_TASK],
                    [_EPIC_AUTH, _EPIC_TASKS],
                    velocity=10,
                )
            )
            == snapshot
        )

    def test_compact(self, snapshot):
        from scrum_agent.formatters import render_sprint_plan

        assert (
            _render(
                render_sprint_plan(
                    [_SPRINT_1, _SPRINT_2],
                    [_STORY_LOGIN, _STORY_CREATE_TASK],
                    [_EPIC_AUTH, _EPIC_TASKS],
                    velocity=10,
                    compact=True,
                )
            )
            == snapshot
        )

    def test_overcapacity_sprint(self, snapshot):
        """Sprint with more points than velocity gets a red capacity bar and border."""
        from scrum_agent.formatters import render_sprint_plan

        overcapacity = Sprint(
            id="SP1", name="Sprint 1", goal="Too much work", capacity_points=15, story_ids=("US-1", "US-2")
        )
        assert (
            _render(
                render_sprint_plan(
                    [overcapacity],
                    [_STORY_LOGIN, _STORY_CREATE_TASK],
                    [_EPIC_AUTH, _EPIC_TASKS],
                    velocity=5,
                )
            )
            == snapshot
        )

    def test_empty_sprints(self, snapshot):
        """Empty sprint list renders only the summary line."""
        from scrum_agent.formatters import render_sprint_plan

        assert _render(render_sprint_plan([], [], [], velocity=10)) == snapshot
