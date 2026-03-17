"""Integration tests for the Scrum Agent LangGraph graph.

Tests the full graph execution with mock LLM responses, verifying:
- Route entry correctly dispatches to the right node based on state
- The full pipeline: analyzer → epics → stories → tasks → sprints
- Questionnaire flow end-to-end (Q1 → Q26 → completed)
- Each node's output feeds correctly into the next

All LLM calls are mocked — no real API calls are made.
"""

from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage

from scrum_agent.agent.graph import create_graph
from scrum_agent.agent.nodes import (
    epic_generator,
    project_analyzer,
    project_intake,
    route_entry,
    sprint_planner,
    story_writer,
    task_decomposer,
)
from scrum_agent.agent.state import (
    TOTAL_QUESTIONS,
    AcceptanceCriterion,
    Epic,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)

# ---------------------------------------------------------------------------
# JSON fixtures (same as test_nodes.py — duplicated to keep integration
# tests self-contained and independent of unit test internals)
# ---------------------------------------------------------------------------

_VALID_ANALYSIS_JSON = """\
{
  "project_name": "Todo App",
  "project_description": "A full-stack todo application",
  "project_type": "greenfield",
  "goals": ["Task management", "User authentication"],
  "end_users": ["developers", "project managers"],
  "target_state": "Deployed to production with CI/CD",
  "tech_stack": ["React", "FastAPI", "PostgreSQL"],
  "integrations": ["GitHub API"],
  "constraints": ["Must use AWS"],
  "sprint_length_weeks": 2,
  "target_sprints": 4,
  "risks": ["Tight timeline"],
  "out_of_scope": ["Mobile app"],
  "assumptions": ["Default velocity assumed"]
}"""

_VALID_EPICS_JSON = """\
[
  {"id": "E1", "title": "User Authentication", "description": "Registration, login, JWT", "priority": "high"},
  {"id": "E2", "title": "Task Management", "description": "CRUD operations for tasks", "priority": "high"},
  {"id": "E3", "title": "Dashboard", "description": "Responsive dashboard", "priority": "medium"}
]"""

_VALID_STORIES_JSON = """\
[
  {
    "id": "US-E1-001",
    "epic_id": "E1",
    "persona": "end user",
    "goal": "register an account",
    "benefit": "I can access the application",
    "acceptance_criteria": [
      {"given": "I am on the registration page", "when": "I submit valid credentials", "then": "my account is created"},
      {"given": "I am on the registration page", "when": "I submit an existing email", "then": "I see an error"}
    ],
    "story_points": 5,
    "priority": "high"
  },
  {
    "id": "US-E1-002",
    "epic_id": "E1",
    "persona": "end user",
    "goal": "log in to my account",
    "benefit": "I can access my data",
    "acceptance_criteria": [
      {"given": "I have an account", "when": "I enter correct credentials", "then": "I am logged in"}
    ],
    "story_points": 3,
    "priority": "high"
  },
  {
    "id": "US-E2-001",
    "epic_id": "E2",
    "persona": "end user",
    "goal": "create a new task",
    "benefit": "I can track my work",
    "acceptance_criteria": [
      {"given": "I am logged in", "when": "I fill out the task form", "then": "the task is created"}
    ],
    "story_points": 3,
    "priority": "high"
  }
]"""

_VALID_TASKS_JSON = """\
[
  {
    "id": "T-US-E1-001-01",
    "story_id": "US-E1-001",
    "title": "Create user registration API endpoint",
    "description": "Build POST /api/auth/register endpoint"
  },
  {
    "id": "T-US-E1-001-02",
    "story_id": "US-E1-001",
    "title": "Write tests for registration endpoint",
    "description": "Unit and integration tests for registration"
  },
  {
    "id": "T-US-E1-002-01",
    "story_id": "US-E1-002",
    "title": "Create login API endpoint",
    "description": "Build POST /api/auth/login endpoint with JWT"
  },
  {
    "id": "T-US-E2-001-01",
    "story_id": "US-E2-001",
    "title": "Create task CRUD endpoints",
    "description": "Build CRUD endpoints for task management"
  }
]"""

_VALID_SPRINTS_JSON = """\
[
  {
    "id": "SP-1",
    "name": "Sprint 1",
    "goal": "Establish authentication foundation",
    "capacity_points": 8,
    "story_ids": ["US-E1-001", "US-E1-002"]
  },
  {
    "id": "SP-2",
    "name": "Sprint 2",
    "goal": "Implement task management",
    "capacity_points": 3,
    "story_ids": ["US-E2-001"]
  }
]"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_questionnaire() -> QuestionnaireState:
    """Create a completed questionnaire with all answers."""
    qs = QuestionnaireState(completed=True, current_question=TOTAL_QUESTIONS + 1)
    for i in range(1, TOTAL_QUESTIONS + 1):
        qs.answers[i] = f"Answer for Q{i}"
    return qs


def _make_dummy_analysis() -> ProjectAnalysis:
    """Create a ProjectAnalysis with sensible defaults."""
    return ProjectAnalysis(
        project_name="Todo App",
        project_description="A full-stack todo application",
        project_type="greenfield",
        goals=("Task management", "User authentication"),
        end_users=("developers",),
        target_state="Deployed to production",
        tech_stack=("React", "FastAPI", "PostgreSQL"),
        integrations=("GitHub API",),
        constraints=("Must use AWS",),
        sprint_length_weeks=2,
        target_sprints=4,
        risks=("Tight timeline",),
        out_of_scope=("Mobile app",),
        assumptions=("Default velocity assumed",),
    )


def _make_sample_epics() -> list[Epic]:
    """Create sample epics matching _VALID_EPICS_JSON."""
    return [
        Epic(id="E1", title="User Authentication", description="Registration, login, JWT", priority=Priority.HIGH),
        Epic(id="E2", title="Task Management", description="CRUD operations for tasks", priority=Priority.HIGH),
        Epic(id="E3", title="Dashboard", description="Responsive dashboard", priority=Priority.MEDIUM),
    ]


def _make_sample_stories() -> list[UserStory]:
    """Create sample stories matching _VALID_STORIES_JSON."""
    return [
        UserStory(
            id="US-E1-001",
            epic_id="E1",
            persona="end user",
            goal="register an account",
            benefit="I can access the application",
            acceptance_criteria=(
                AcceptanceCriterion(given="on registration page", when="submit valid data", then="account created"),
            ),
            story_points=StoryPointValue.FIVE,
            priority=Priority.HIGH,
        ),
        UserStory(
            id="US-E1-002",
            epic_id="E1",
            persona="end user",
            goal="log in to my account",
            benefit="I can access my data",
            acceptance_criteria=(
                AcceptanceCriterion(given="have an account", when="enter correct credentials", then="logged in"),
            ),
            story_points=StoryPointValue.THREE,
            priority=Priority.HIGH,
        ),
        UserStory(
            id="US-E2-001",
            epic_id="E2",
            persona="end user",
            goal="create a new task",
            benefit="I can track my work",
            acceptance_criteria=(
                AcceptanceCriterion(given="logged in", when="fill out task form", then="task created"),
            ),
            story_points=StoryPointValue.THREE,
            priority=Priority.HIGH,
        ),
    ]


def _make_sample_tasks() -> list[Task]:
    """Create sample tasks matching _VALID_TASKS_JSON."""
    return [
        Task(id="T-US-E1-001-01", story_id="US-E1-001", title="Registration endpoint", description="Build endpoint"),
        Task(id="T-US-E1-001-02", story_id="US-E1-001", title="Registration tests", description="Write tests"),
        Task(id="T-US-E1-002-01", story_id="US-E1-002", title="Login endpoint", description="Build endpoint"),
        Task(id="T-US-E2-001-01", story_id="US-E2-001", title="Task CRUD", description="Build CRUD endpoints"),
    ]


def _mock_llm(response_text: str) -> MagicMock:
    """Return a mock LLM that returns the given text on invoke()."""
    mock_response = MagicMock()
    mock_response.content = response_text
    mock_instance = MagicMock()
    mock_instance.invoke.return_value = mock_response
    return mock_instance


# ---------------------------------------------------------------------------
# Test 1: Full graph execution with mock LLM responses
# ---------------------------------------------------------------------------


class TestFullGraphExecution:
    """Test that the compiled graph routes correctly through all stages."""

    def test_route_entry_dispatches_intake_when_no_questionnaire(self):
        """Empty state should route to project_intake."""
        state = {"messages": [HumanMessage(content="hi")]}
        assert route_entry(state) == "project_intake"

    def test_route_entry_dispatches_analyzer_after_questionnaire(self):
        """Completed questionnaire should route to project_analyzer."""
        state = {"messages": [], "questionnaire": _make_completed_questionnaire()}
        assert route_entry(state) == "project_analyzer"

    def test_route_entry_dispatches_epic_generator_after_analysis(self):
        """Analysis present should route to epic_generator."""
        state = {
            "messages": [],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
        }
        assert route_entry(state) == "epic_generator"

    def test_route_entry_dispatches_story_writer_after_epics(self):
        """Epics present should route to story_writer."""
        state = {
            "messages": [],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
        }
        assert route_entry(state) == "story_writer"

    def test_route_entry_dispatches_task_decomposer_after_stories(self):
        """Stories present should route to task_decomposer."""
        state = {
            "messages": [],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
            "stories": _make_sample_stories(),
        }
        assert route_entry(state) == "task_decomposer"

    def test_route_entry_dispatches_sprint_planner_after_tasks(self):
        """Tasks present with no sprints should route to sprint_planner."""
        state = {
            "messages": [],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
            "stories": _make_sample_stories(),
            "tasks": _make_sample_tasks(),
        }
        assert route_entry(state) == "sprint_planner"

    def test_route_entry_dispatches_sprint_planner_with_sprint_number(self):
        """Tasks + starting_sprint_number set should route to sprint_planner."""
        state = {
            "messages": [],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
            "stories": _make_sample_stories(),
            "tasks": _make_sample_tasks(),
            "starting_sprint_number": -1,
        }
        assert route_entry(state) == "sprint_planner"

    def test_route_entry_dispatches_sprint_planner_with_positive_sprint(self):
        """Tasks + positive starting_sprint_number should route to sprint_planner."""
        state = {
            "messages": [],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
            "stories": _make_sample_stories(),
            "tasks": _make_sample_tasks(),
            "starting_sprint_number": 105,
        }
        assert route_entry(state) == "sprint_planner"

    def test_route_entry_dispatches_agent_after_sprints(self):
        """All artifacts present should route to agent (ReAct loop)."""
        state = {
            "messages": [],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
            "stories": _make_sample_stories(),
            "tasks": _make_sample_tasks(),
            "sprints": [
                Sprint(id="SP-1", name="Sprint 1", goal="Auth", capacity_points=8, story_ids=("US-E1-001",)),
            ],
        }
        assert route_entry(state) == "agent"

    def test_graph_compiles_with_no_tools(self):
        """Graph should compile successfully with an empty tool list."""
        graph = create_graph(tools=[])
        assert graph is not None

    def test_graph_invocation_routes_to_intake(self, monkeypatch):
        """First invocation with empty state should route to project_intake."""
        # Patch LLM to avoid real calls (intake doesn't call LLM on Q1, but
        # adaptive extraction may be triggered)
        mock_llm = _mock_llm("{}")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        graph = create_graph(tools=[])
        result = graph.invoke({"messages": [HumanMessage(content="Build a todo app")]})

        # Should have a questionnaire initialized and an AI message asking Q1
        assert result.get("questionnaire") is not None
        assert len(result["messages"]) > 0

    def test_graph_invocation_routes_to_analyzer(self, monkeypatch):
        """Invocation with completed questionnaire should route to project_analyzer."""
        mock_llm = _mock_llm(_VALID_ANALYSIS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)
        # Disable repo scanning / confluence / user context to simplify
        monkeypatch.setattr("scrum_agent.agent.nodes._scan_repo_context", lambda *a, **kw: (None, {}))
        monkeypatch.setattr("scrum_agent.agent.nodes._fetch_confluence_context", lambda *a, **kw: (None, {}))
        monkeypatch.setattr("scrum_agent.agent.nodes._load_user_context", lambda *a, **kw: (None, {}))

        graph = create_graph(tools=[])
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="continue")],
                "questionnaire": _make_completed_questionnaire(),
            }
        )

        assert result.get("project_analysis") is not None
        assert result["project_analysis"].project_name == "Todo App"


# ---------------------------------------------------------------------------
# Test 2: Questionnaire flow end-to-end
# ---------------------------------------------------------------------------


class TestQuestionnaireFlowE2E:
    """Test the intake questionnaire from Q1 through Q26 completion."""

    def test_first_invocation_initializes_questionnaire(self, monkeypatch):
        """First call with no questionnaire should initialize and ask Q1."""
        mock_llm = _mock_llm("{}")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = {"messages": [HumanMessage(content="Build a todo app")]}
        result = project_intake(state)

        assert "questionnaire" in result
        qs = result["questionnaire"]
        assert isinstance(qs, QuestionnaireState)
        # Q1 answer should be pre-populated from the initial description
        assert qs.current_question >= 1

    def test_answering_advances_question(self, monkeypatch):
        """Providing an answer should advance current_question."""
        mock_llm = _mock_llm("{}")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        qs = QuestionnaireState()
        qs.current_question = 2
        qs.answers[1] = "Todo App"

        state = {
            "messages": [HumanMessage(content="React and FastAPI")],
            "questionnaire": qs,
        }
        result = project_intake(state)

        updated_qs = result["questionnaire"]
        # Should have recorded the answer and advanced
        assert updated_qs.answers.get(2) is not None
        assert updated_qs.current_question > 2

    def test_last_question_triggers_confirmation(self, monkeypatch):
        """Answering the last question should set awaiting_confirmation=True."""
        mock_llm = _mock_llm("{}")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        # Set up questionnaire at the last question
        qs = QuestionnaireState()
        qs.current_question = TOTAL_QUESTIONS
        for i in range(1, TOTAL_QUESTIONS):
            qs.answers[i] = f"Answer for Q{i}"

        state = {
            "messages": [HumanMessage(content="Final answer")],
            "questionnaire": qs,
        }
        result = project_intake(state)

        updated_qs = result["questionnaire"]
        # After Q26, questionnaire awaits user confirmation before marking completed
        assert TOTAL_QUESTIONS in updated_qs.answers
        assert updated_qs.awaiting_confirmation is True

    def test_confirmation_marks_completed(self, monkeypatch):
        """Confirming answers after Q26 should set completed=True."""
        mock_llm = _mock_llm("{}")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        # Set up questionnaire awaiting confirmation (all answers filled)
        qs = QuestionnaireState()
        qs.current_question = TOTAL_QUESTIONS + 1
        qs.awaiting_confirmation = True
        for i in range(1, TOTAL_QUESTIONS + 1):
            qs.answers[i] = f"Answer for Q{i}"

        state = {
            "messages": [HumanMessage(content="yes")],
            "questionnaire": qs,
        }
        result = project_intake(state)

        updated_qs = result["questionnaire"]
        assert updated_qs.completed is True

    def test_completed_questionnaire_routes_away_from_intake(self):
        """After completion, route_entry should NOT return project_intake."""
        qs = _make_completed_questionnaire()
        state = {"messages": [], "questionnaire": qs}
        assert route_entry(state) != "project_intake"

    def test_skip_intent_uses_default(self, monkeypatch):
        """Typing 'skip' should use the question's default answer."""
        mock_llm = _mock_llm("{}")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        qs = QuestionnaireState()
        qs.current_question = 5  # A question with a default
        for i in range(1, 5):
            qs.answers[i] = f"Answer for Q{i}"

        state = {
            "messages": [HumanMessage(content="skip")],
            "questionnaire": qs,
        }
        result = project_intake(state)

        updated_qs = result["questionnaire"]
        # Answer should be populated (default or "skipped")
        assert updated_qs.answers.get(5) is not None
        assert updated_qs.current_question > 5


# ---------------------------------------------------------------------------
# Test 3: Epic → story → task → sprint pipeline
# ---------------------------------------------------------------------------


class TestPipelineE2E:
    """Test the full generation pipeline: analyzer → epics → stories → tasks → sprints.

    Each node is called directly (not via graph.invoke) with the previous node's
    output merged into state. This verifies the data flows correctly through the
    entire pipeline without requiring full graph compilation.
    """

    def test_analyzer_produces_project_analysis(self, monkeypatch):
        """project_analyzer should produce a ProjectAnalysis from questionnaire answers."""
        mock_llm = _mock_llm(_VALID_ANALYSIS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)
        monkeypatch.setattr("scrum_agent.agent.nodes._scan_repo_context", lambda *a, **kw: (None, {}))
        monkeypatch.setattr("scrum_agent.agent.nodes._fetch_confluence_context", lambda *a, **kw: (None, {}))
        monkeypatch.setattr("scrum_agent.agent.nodes._load_user_context", lambda *a, **kw: (None, {}))

        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _make_completed_questionnaire(),
        }
        result = project_analyzer(state)

        assert "project_analysis" in result
        analysis = result["project_analysis"]
        assert isinstance(analysis, ProjectAnalysis)
        assert analysis.project_name == "Todo App"
        assert analysis.tech_stack == ("React", "FastAPI", "PostgreSQL")

    def test_epic_generator_produces_epics_from_analysis(self, monkeypatch):
        """epic_generator should produce Epic list from ProjectAnalysis."""
        mock_llm = _mock_llm(_VALID_EPICS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
        }
        result = epic_generator(state)

        assert "epics" in result
        epics = result["epics"]
        assert isinstance(epics, list)
        assert len(epics) == 3
        assert all(isinstance(e, Epic) for e in epics)
        assert epics[0].title == "User Authentication"

    def test_story_writer_produces_stories_from_epics(self, monkeypatch):
        """story_writer should produce UserStory list from epics + analysis."""
        mock_llm = _mock_llm(_VALID_STORIES_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
        }
        result = story_writer(state)

        assert "stories" in result
        stories = result["stories"]
        assert isinstance(stories, list)
        assert len(stories) == 3
        assert all(isinstance(s, UserStory) for s in stories)
        # Verify story fields
        assert stories[0].id == "US-E1-001"
        assert stories[0].epic_id == "E1"
        assert isinstance(stories[0].story_points, StoryPointValue)

    def test_task_decomposer_produces_tasks_from_stories(self, monkeypatch):
        """task_decomposer should produce Task list from stories + analysis."""
        mock_llm = _mock_llm(_VALID_TASKS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
            "stories": _make_sample_stories(),
        }
        result = task_decomposer(state)

        assert "tasks" in result
        tasks = result["tasks"]
        assert isinstance(tasks, list)
        assert len(tasks) == 4
        assert all(isinstance(t, Task) for t in tasks)
        # Verify task references valid story IDs
        story_ids = {s.id for s in _make_sample_stories()}
        for task in tasks:
            assert task.story_id in story_ids

    def test_sprint_planner_produces_sprints_from_stories(self, monkeypatch):
        """sprint_planner should produce Sprint list from stories + analysis."""
        mock_llm = _mock_llm(_VALID_SPRINTS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _make_completed_questionnaire(),
            "project_analysis": _make_dummy_analysis(),
            "epics": _make_sample_epics(),
            "stories": _make_sample_stories(),
            "tasks": _make_sample_tasks(),
            "team_size": 2,
            "velocity_per_sprint": 10,
            "target_sprints": 4,
        }
        result = sprint_planner(state)

        assert "sprints" in result
        sprints = result["sprints"]
        assert isinstance(sprints, list)
        assert len(sprints) >= 1
        assert all(isinstance(s, Sprint) for s in sprints)
        # All stories should be allocated to some sprint
        all_allocated = set()
        for sp in sprints:
            all_allocated.update(sp.story_ids)
        story_ids = {s.id for s in _make_sample_stories()}
        assert story_ids == all_allocated

    def test_full_pipeline_sequential(self, monkeypatch):
        """Run all pipeline nodes in sequence, feeding each output to the next.

        This is the closest we can get to an end-to-end test without running
        the graph (which requires REPL interaction between nodes).
        """
        # Patch external lookups — each returns (context, status_dict) tuple
        monkeypatch.setattr("scrum_agent.agent.nodes._scan_repo_context", lambda *a, **kw: (None, {}))
        monkeypatch.setattr("scrum_agent.agent.nodes._fetch_confluence_context", lambda *a, **kw: (None, {}))
        monkeypatch.setattr("scrum_agent.agent.nodes._load_user_context", lambda *a, **kw: (None, {}))

        # --- Stage 1: Analyzer ---
        mock_llm = _mock_llm(_VALID_ANALYSIS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _make_completed_questionnaire(),
        }
        analyzer_result = project_analyzer(state)
        analysis = analyzer_result["project_analysis"]
        assert isinstance(analysis, ProjectAnalysis)

        # --- Stage 2: Epic generator ---
        mock_llm = _mock_llm(_VALID_EPICS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state["project_analysis"] = analysis
        epic_result = epic_generator(state)
        epics = epic_result["epics"]
        assert len(epics) == 3

        # --- Stage 3: Story writer ---
        mock_llm = _mock_llm(_VALID_STORIES_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state["epics"] = epics
        story_result = story_writer(state)
        stories = story_result["stories"]
        assert len(stories) == 3

        # --- Stage 4: Task decomposer ---
        mock_llm = _mock_llm(_VALID_TASKS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state["stories"] = stories
        task_result = task_decomposer(state)
        tasks = task_result["tasks"]
        assert len(tasks) == 4

        # --- Stage 5: Sprint planner ---
        mock_llm = _mock_llm(_VALID_SPRINTS_JSON)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state["tasks"] = tasks
        state["team_size"] = 2
        state["velocity_per_sprint"] = 10
        state["target_sprints"] = 4
        sprint_result = sprint_planner(state)
        sprints = sprint_result["sprints"]
        assert len(sprints) >= 1

        # Verify pipeline integrity: all story IDs from sprints reference real stories
        all_allocated = set()
        for sp in sprints:
            all_allocated.update(sp.story_ids)
        story_ids = {s.id for s in stories}
        assert all_allocated.issubset(story_ids)

        # Verify pipeline integrity: all task story_ids reference real stories
        for task in tasks:
            assert task.story_id in story_ids

        # Route entry should now point to "agent" since all artifacts are populated
        state["sprints"] = sprints
        assert route_entry(state) == "agent"

    def test_pipeline_with_llm_fallback(self, monkeypatch):
        """Pipeline should still produce valid output when LLM returns bad JSON.

        All nodes have deterministic fallback logic — this verifies that even
        with garbage LLM responses, the pipeline produces usable artifacts.
        """
        monkeypatch.setattr("scrum_agent.agent.nodes._scan_repo_context", lambda *a, **kw: (None, {}))
        monkeypatch.setattr("scrum_agent.agent.nodes._fetch_confluence_context", lambda *a, **kw: (None, {}))
        monkeypatch.setattr("scrum_agent.agent.nodes._load_user_context", lambda *a, **kw: (None, {}))

        # All LLM calls return garbage — fallback logic should kick in
        mock_llm = _mock_llm("This is not JSON at all!")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _make_completed_questionnaire(),
        }

        # Analyzer fallback
        analyzer_result = project_analyzer(state)
        analysis = analyzer_result["project_analysis"]
        assert isinstance(analysis, ProjectAnalysis)

        # Epic fallback
        state["project_analysis"] = analysis
        epic_result = epic_generator(state)
        epics = epic_result["epics"]
        assert len(epics) >= 1
        assert all(isinstance(e, Epic) for e in epics)

        # Story fallback
        state["epics"] = epics
        story_result = story_writer(state)
        stories = story_result["stories"]
        assert len(stories) >= 1
        assert all(isinstance(s, UserStory) for s in stories)

        # Task fallback
        state["stories"] = stories
        task_result = task_decomposer(state)
        tasks = task_result["tasks"]
        assert len(tasks) >= 1
        assert all(isinstance(t, Task) for t in tasks)

        # Sprint fallback
        state["tasks"] = tasks
        state["team_size"] = 2
        state["velocity_per_sprint"] = 10
        state["target_sprints"] = 3
        sprint_result = sprint_planner(state)
        sprints = sprint_result["sprints"]
        assert len(sprints) >= 1
        assert all(isinstance(s, Sprint) for s in sprints)

        # All stories should still be allocated
        all_allocated = set()
        for sp in sprints:
            all_allocated.update(sp.story_ids)
        story_ids = {s.id for s in stories}
        assert story_ids == all_allocated
