"""End-to-end graph-level tests — drive the compiled graph through full pipelines.

Unlike test_integration.py (which tests individual nodes and route_entry in isolation),
these tests drive the compiled LangGraph graph with scripted inputs and multi-stage
LLM mocks. They verify routing, state flow, and node chaining end-to-end without
any REPL or UI interaction.

Key pattern: the graph processes one node per invoke() call (each pipeline node routes
to END). The REPL drives multi-step flows by calling graph.invoke() repeatedly. These
tests simulate that loop.

Review loops are REPL-driven, not graph-edge-driven: the REPL intercepts ``pending_review``,
collects user feedback, clears old artifacts, sets ``last_review_decision``/``last_review_feedback``
in state, and re-invokes the graph. We simulate this same pattern.

All LLM calls are mocked — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from scrum_agent.agent.graph import create_graph
from scrum_agent.agent.nodes import (
    feature_generator,
    route_entry,
    story_writer,
)
from scrum_agent.agent.state import (
    TOTAL_QUESTIONS,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    ReviewDecision,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)
from scrum_agent.sessions import SessionStore, _deserialize_state, _serialize_state

# ---------------------------------------------------------------------------
# JSON fixtures — valid LLM responses for each pipeline stage
# ---------------------------------------------------------------------------

_ANALYSIS_JSON = """\
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

_FEATURES_JSON = """\
[
  {"id": "F1", "title": "User Authentication", "description": "Registration, login, JWT", "priority": "high"},
  {"id": "F2", "title": "Task Management", "description": "CRUD operations for tasks", "priority": "high"},
  {"id": "F3", "title": "Dashboard", "description": "Responsive dashboard", "priority": "medium"}
]"""

_STORIES_JSON = """\
[
  {
    "id": "US-F1-001", "feature_id": "F1", "persona": "end user",
    "goal": "register an account", "benefit": "I can access the application",
    "acceptance_criteria": [
      {"given": "on registration page", "when": "submit valid credentials", "then": "account is created"},
      {"given": "on registration page", "when": "submit existing email", "then": "I see an error"},
      {"given": "on registration page", "when": "leave fields empty", "then": "validation errors show"}
    ],
    "story_points": 5, "priority": "high"
  },
  {
    "id": "US-F1-002", "feature_id": "F1", "persona": "end user",
    "goal": "log in to my account", "benefit": "I can access my data",
    "acceptance_criteria": [
      {"given": "have an account", "when": "enter correct credentials", "then": "I am logged in"}
    ],
    "story_points": 3, "priority": "high"
  },
  {
    "id": "US-F2-001", "feature_id": "F2", "persona": "end user",
    "goal": "create a new task", "benefit": "I can track my work",
    "acceptance_criteria": [
      {"given": "I am logged in", "when": "fill out the task form", "then": "the task is created"}
    ],
    "story_points": 3, "priority": "high"
  }
]"""

_TASKS_JSON = """\
[
  {
    "id": "T-US-F1-001-01", "story_id": "US-F1-001",
    "title": "Registration endpoint", "description": "Build POST /register"
  },
  {
    "id": "T-US-F1-001-02", "story_id": "US-F1-001",
    "title": "Registration tests", "description": "Unit tests for registration"
  },
  {
    "id": "T-US-F1-002-01", "story_id": "US-F1-002",
    "title": "Login endpoint", "description": "Build POST /login with JWT"
  },
  {
    "id": "T-US-F2-001-01", "story_id": "US-F2-001",
    "title": "Task CRUD endpoints", "description": "Build CRUD for tasks"
  }
]"""

_SPRINTS_JSON = """\
[
  {
    "id": "SP-1", "name": "Sprint 1", "goal": "Auth foundation",
    "capacity_points": 8, "story_ids": ["US-F1-001", "US-F1-002"]
  },
  {
    "id": "SP-2", "name": "Sprint 2", "goal": "Task management",
    "capacity_points": 3, "story_ids": ["US-F2-001"]
  }
]"""

# Alternative features JSON for review re-generation (different titles)
_FEATURES_V2_JSON = """\
[
  {"id": "F1", "title": "Auth System", "description": "Full authentication with OAuth", "priority": "high"},
  {"id": "F2", "title": "Core Tasks", "description": "Task CRUD and filtering", "priority": "high"},
  {"id": "F3", "title": "Reporting", "description": "Analytics dashboard", "priority": "medium"}
]"""

# Alternative stories JSON for review re-generation
_STORIES_V2_JSON = """\
[
  {
    "id": "US-F1-001", "feature_id": "F1", "persona": "developer",
    "goal": "set up OAuth integration", "benefit": "secure authentication",
    "acceptance_criteria": [
      {"given": "on login page", "when": "click OAuth provider", "then": "redirected to provider"},
      {"given": "OAuth callback", "when": "valid token", "then": "session created"},
      {"given": "OAuth callback", "when": "invalid token", "then": "error shown"}
    ],
    "story_points": 8, "priority": "high"
  },
  {
    "id": "US-F2-001", "feature_id": "F2", "persona": "end user",
    "goal": "filter tasks by status", "benefit": "find relevant tasks quickly",
    "acceptance_criteria": [
      {"given": "on task list", "when": "select status filter", "then": "list updates"}
    ],
    "story_points": 3, "priority": "medium"
  }
]"""


# ---------------------------------------------------------------------------
# Multi-stage LLM mock — returns different JSON per pipeline stage
# ---------------------------------------------------------------------------


def _make_stage_llm(responses: list[str]) -> MagicMock:
    """Return a mock LLM that returns responses in order (one per call).

    Each call to invoke() returns the next response from the list.
    After all responses are consumed, returns empty JSON.

    Args:
        responses: List of response texts, consumed in order.
    """
    call_idx = {"n": 0}

    def invoke(messages, **_kwargs):
        idx = call_idx["n"]
        call_idx["n"] += 1
        text = responses[idx] if idx < len(responses) else "{}"
        mock_resp = MagicMock()
        mock_resp.content = text
        return mock_resp

    mock = MagicMock()
    mock.invoke = invoke
    return mock


def _make_simple_llm(response_text: str) -> MagicMock:
    """Return a mock LLM that always returns the same text."""
    mock_resp = MagicMock()
    mock_resp.content = response_text
    mock = MagicMock()
    mock.invoke.return_value = mock_resp
    return mock


# ---------------------------------------------------------------------------
# State builders
# ---------------------------------------------------------------------------


def _completed_questionnaire() -> QuestionnaireState:
    """Create a completed questionnaire with all 26 answers."""
    qs = QuestionnaireState(completed=True, current_question=TOTAL_QUESTIONS + 1)
    for i in range(1, TOTAL_QUESTIONS + 1):
        qs.answers[i] = f"Answer for Q{i}"
    return qs


def _dummy_analysis() -> ProjectAnalysis:
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


def _patch_external_lookups(monkeypatch):
    """Disable repo scanning, Confluence, and SCRUM.md loading."""
    monkeypatch.setattr("scrum_agent.agent.nodes._scan_repo_context", lambda *a, **kw: (None, {}))
    monkeypatch.setattr("scrum_agent.agent.nodes._fetch_confluence_context", lambda *a, **kw: (None, {}))
    monkeypatch.setattr("scrum_agent.agent.nodes._load_user_context", lambda *a, **kw: (None, {}))


# ---------------------------------------------------------------------------
# Test 1: Full questionnaire → pipeline flow
# ---------------------------------------------------------------------------


class TestFullQuestionnaireToSprintPipeline:
    """Drive all 26 questions through intake, then run the full pipeline.

    Simulates the REPL loop: invoke → get question → answer → invoke again.
    After Q26, confirm, then run analyzer → features → stories → tasks → sprints.
    """

    def test_full_flow_produces_all_artifacts(self, monkeypatch):
        """Q1–Q26 → confirm → analyzer → features → stories → tasks → sprints."""
        _patch_external_lookups(monkeypatch)

        # Phase 1 uses a no-op LLM (intake extraction / vague checks get empty JSON).
        # Phase 2 switches to ordered pipeline responses after questionnaire completes.
        intake_llm = _make_simple_llm("{}")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: intake_llm)

        graph = create_graph(tools=[])

        # --- Phase 1: Questionnaire ---
        state = graph.invoke({"messages": [HumanMessage(content="Build a todo app")]})
        assert state.get("questionnaire") is not None

        for _attempt in range(TOTAL_QUESTIONS + 7):
            qs = state.get("questionnaire")
            if qs and qs.completed:
                break
            if qs and qs._awaiting_leave_input:
                # PTO sub-loop — answer "No" to skip planned leave
                state = graph.invoke({**state, "messages": [HumanMessage(content="2")]})
            elif qs and qs.awaiting_confirmation:
                state = graph.invoke({**state, "messages": [HumanMessage(content="yes")]})
            else:
                q_num = qs.current_question if qs else 1
                state = graph.invoke(
                    {
                        **state,
                        "messages": [HumanMessage(content=f"Answer for Q{q_num}")],
                    }
                )
        else:
            pytest.fail("Questionnaire did not complete within expected iterations")

        assert state["questionnaire"].completed is True

        # --- Phase 2: Pipeline stages ---
        # Switch to ordered pipeline LLM
        pipeline_llm = _make_stage_llm(
            [
                _ANALYSIS_JSON,
                _FEATURES_JSON,
                _STORIES_JSON,
                _TASKS_JSON,
                _SPRINTS_JSON,
            ]
        )
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: pipeline_llm)

        # Analyzer
        assert route_entry(state) == "project_analyzer"
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})
        assert state.get("project_analysis") is not None
        assert isinstance(state["project_analysis"], ProjectAnalysis)

        # Features
        assert route_entry(state) == "feature_generator"
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})
        assert len(state.get("features", [])) == 3

        # Stories
        assert route_entry(state) == "story_writer"
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})
        assert len(state.get("stories", [])) == 3

        # Tasks
        assert route_entry(state) == "task_decomposer"
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})
        assert len(state.get("tasks", [])) == 4

        # Sprint planner (sprint selection + capacity now handled during intake)
        assert route_entry(state) == "sprint_planner"
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})

        # Sprint planner may return a capacity override warning — handle it
        if not state.get("sprints"):
            state = graph.invoke({**state, "messages": [HumanMessage(content="yes")]})

        assert len(state.get("sprints", [])) >= 1

        # Final routing should point to agent
        assert route_entry(state) == "agent"

        # All stories allocated to sprints
        allocated = set()
        for sp in state["sprints"]:
            allocated.update(sp.story_ids)
        story_ids = {s.id for s in state["stories"]}
        assert story_ids == allocated


# ---------------------------------------------------------------------------
# Test 2: Quick intake mode
# ---------------------------------------------------------------------------


class TestQuickIntakeMode:
    """Quick intake auto-defaults non-essential questions, only asking essentials."""

    def test_quick_mode_fills_defaults_and_completes_faster(self, monkeypatch):
        """Quick mode should auto-default most questions, leaving only essential gaps."""
        # Provide many empty-JSON responses for any extraction/vague-check calls
        stage_llm = _make_stage_llm(["{}"] * 30)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: stage_llm)

        graph = create_graph(tools=[])

        # First invocation with quick mode
        state = graph.invoke(
            {
                "messages": [HumanMessage(content="Build a todo app with React and FastAPI")],
                "_intake_mode": "quick",
            }
        )

        qs = state["questionnaire"]
        assert qs.intake_mode == "quick"
        # Quick mode should have auto-defaulted many questions
        assert len(qs.defaulted_questions) > 0

        # Answer remaining gaps until done
        iterations = 0
        for _attempt in range(15):  # quick mode should be much faster
            qs = state.get("questionnaire")
            if qs and qs.completed:
                break
            if qs and qs.awaiting_confirmation:
                state = graph.invoke({**state, "messages": [HumanMessage(content="yes")]})
            else:
                q_num = qs.current_question if qs else 1
                state = graph.invoke({**state, "messages": [HumanMessage(content=f"Quick answer for Q{q_num}")]})
            iterations += 1

        # Quick mode should complete in fewer iterations than the full 26
        assert state["questionnaire"].completed is True
        assert iterations < TOTAL_QUESTIONS  # strictly fewer than standard mode


# ---------------------------------------------------------------------------
# Test 3: Smart intake mode
# ---------------------------------------------------------------------------


class TestSmartIntakeMode:
    """Smart intake extracts answers from description and skips answered questions."""

    def test_smart_mode_extracts_and_reduces_questions(self, monkeypatch):
        """Smart mode should extract answers from description, reducing gap count."""
        stage_llm = _make_stage_llm(["{}"] * 30)
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: stage_llm)

        graph = create_graph(tools=[])

        # Rich description that should trigger extraction
        description = (
            "Build a todo app using React frontend with FastAPI backend and PostgreSQL. "
            "Team of 3 engineers, 2-week sprints, greenfield project. "
            "Must deploy on AWS with CI/CD pipeline."
        )

        state = graph.invoke(
            {
                "messages": [HumanMessage(content=description)],
                "_intake_mode": "smart",
            }
        )

        qs = state["questionnaire"]
        assert qs.intake_mode == "smart"

        # Smart mode should have extracted some answers from the description
        total_answered = len(qs.answers) + len(qs.suggested_answers)
        assert total_answered > 1  # At least Q1 + some extracted

        # Complete remaining gaps
        for _attempt in range(20):
            qs = state.get("questionnaire")
            if qs and qs.completed:
                break
            if qs and qs._awaiting_leave_input:
                # PTO sub-loop — answer "No" to skip planned leave
                state = graph.invoke({**state, "messages": [HumanMessage(content="2")]})
            elif qs and qs.awaiting_confirmation:
                state = graph.invoke({**state, "messages": [HumanMessage(content="yes")]})
            else:
                q_num = qs.current_question if qs else 1
                state = graph.invoke({**state, "messages": [HumanMessage(content=f"Smart answer for Q{q_num}")]})

        assert state["questionnaire"].completed is True


# ---------------------------------------------------------------------------
# Test 4: Review loop — reject features → re-generate with feedback
# ---------------------------------------------------------------------------


class TestReviewRejectFeatures:
    """Simulate the REPL reject flow: clear features, set feedback, re-invoke."""

    def test_reject_features_triggers_regeneration(self, monkeypatch):
        """Rejecting features with feedback should re-generate different features."""
        _patch_external_lookups(monkeypatch)

        # First call returns original features, second returns v2
        stage_llm = _make_stage_llm([_FEATURES_JSON, _FEATURES_V2_JSON])
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: stage_llm)

        # Build state with completed questionnaire + analysis (skip intake)
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _completed_questionnaire(),
            "project_analysis": _dummy_analysis(),
        }

        # --- First generation ---
        result = feature_generator(state)
        features_v1 = result["features"]
        assert result["pending_review"] == "feature_generator"
        assert features_v1[0].title == "User Authentication"

        # --- REPL reject flow simulation ---
        # The REPL: (1) clears old features, (2) sets review decision + feedback
        state.update(result)
        state["features"] = []  # Clear for re-routing
        state["last_review_decision"] = ReviewDecision.REJECT
        state["last_review_feedback"] = "Focus more on OAuth and security"
        state["messages"] = [HumanMessage(content="continue")]

        # route_entry should now route back to feature_generator (features cleared)
        assert route_entry(state) == "feature_generator"

        # --- Re-generation ---
        result_v2 = feature_generator(state)
        features_v2 = result_v2["features"]
        assert len(features_v2) >= 1
        # V2 features should be different (our mock returns different JSON)
        assert features_v2[0].title == "Auth System"


# ---------------------------------------------------------------------------
# Test 5: Review loop — edit stories → re-generate with edits
# ---------------------------------------------------------------------------


class TestReviewEditStories:
    """Simulate the REPL edit flow: set feedback with previous output, re-invoke."""

    def test_edit_stories_includes_previous_output(self, monkeypatch):
        """Editing stories should re-generate with user feedback and previous output."""
        _patch_external_lookups(monkeypatch)

        stage_llm = _make_stage_llm([_STORIES_JSON, _STORIES_V2_JSON])
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: stage_llm)

        # Build state with features already generated
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _completed_questionnaire(),
            "project_analysis": _dummy_analysis(),
            "features": [
                Feature(id="F1", title="Auth", description="Auth system", priority=Priority.HIGH),
                Feature(id="F2", title="Tasks", description="Task management", priority=Priority.HIGH),
            ],
        }

        # --- First generation ---
        result = story_writer(state)
        stories_v1 = result["stories"]
        assert result["pending_review"] == "story_writer"
        assert stories_v1[0].goal == "register an account"

        # --- REPL edit flow simulation ---
        # The REPL packs feedback + previous output into last_review_feedback
        previous_output = "US-F1-001: register an account\nUS-F1-002: log in"
        state.update(result)
        state["stories"] = []  # Clear for re-routing
        state["last_review_decision"] = ReviewDecision.EDIT
        state["last_review_feedback"] = f"Add OAuth support\n\n---PREVIOUS OUTPUT---\n{previous_output}"
        state["messages"] = [HumanMessage(content="continue")]

        assert route_entry(state) == "story_writer"

        # --- Re-generation ---
        result_v2 = story_writer(state)
        stories_v2 = result_v2["stories"]
        assert len(stories_v2) >= 1
        # V2 stories should differ
        assert stories_v2[0].goal == "set up OAuth integration"


# ---------------------------------------------------------------------------
# Test 6: Fallback path — garbage LLM at every stage via graph.invoke()
# ---------------------------------------------------------------------------


class TestFallbackPathViaGraph:
    """All stages should produce valid artifacts even when LLM returns garbage.

    Unlike test_integration.py's TestPipelineE2E.test_pipeline_with_llm_fallback
    (which calls nodes directly), this test drives the compiled graph to verify
    fallback logic works through the full graph routing.
    """

    def test_garbage_llm_produces_valid_artifacts_through_graph(self, monkeypatch):
        """Every pipeline stage should fall back to deterministic defaults."""
        _patch_external_lookups(monkeypatch)

        garbage_llm = _make_simple_llm("This is absolute garbage, not JSON!")
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: garbage_llm)

        graph = create_graph(tools=[])

        # Start after questionnaire (skip the Q1-Q26 loop)
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _completed_questionnaire(),
        }

        # Analyzer fallback
        state = graph.invoke(state)
        assert state.get("project_analysis") is not None
        assert isinstance(state["project_analysis"], ProjectAnalysis)

        # Feature fallback
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})
        assert len(state.get("features", [])) >= 1
        assert all(isinstance(e, Feature) for e in state["features"])

        # Story fallback
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})
        assert len(state.get("stories", [])) >= 1
        assert all(isinstance(s, UserStory) for s in state["stories"])

        # Task fallback
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})
        assert len(state.get("tasks", [])) >= 1
        assert all(isinstance(t, Task) for t in state["tasks"])

        # Sprint planner (sprint selection + capacity now handled during intake)
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})

        # Sprint planner may return a capacity override warning — handle it
        if not state.get("sprints"):
            state = graph.invoke({**state, "messages": [HumanMessage(content="yes")]})

        assert len(state.get("sprints", [])) >= 1
        assert all(isinstance(s, Sprint) for s in state["sprints"])

        # Pipeline complete — should route to agent
        assert route_entry(state) == "agent"

        # Verify pipeline integrity
        story_ids = {s.id for s in state["stories"]}
        for task in state["tasks"]:
            assert task.story_id in story_ids
        allocated = set()
        for sp in state["sprints"]:
            allocated.update(sp.story_ids)
        assert story_ids == allocated


# ---------------------------------------------------------------------------
# Test 7: Session resume — save state mid-pipeline, reload, continue
# ---------------------------------------------------------------------------


class TestSessionResume:
    """Save state mid-pipeline, deserialize, and verify continuation works."""

    def test_save_and_restore_mid_pipeline(self, tmp_path, monkeypatch):
        """State saved after features should resume at story_writer."""
        _patch_external_lookups(monkeypatch)

        # Build state as if feature_generator just completed
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": _completed_questionnaire(),
            "project_analysis": _dummy_analysis(),
            "features": [
                Feature(id="F1", title="Auth", description="Authentication", priority=Priority.HIGH),
                Feature(id="F2", title="Tasks", description="Task management", priority=Priority.HIGH),
            ],
            "pending_review": "feature_generator",
        }

        # Save to SessionStore
        db_path = tmp_path / "test_resume.db"
        with SessionStore(db_path) as store:
            store.create_session("test-session")
            store.save_state("test-session", state)

            # Load it back
            restored = store.load_state("test-session")

        assert restored is not None
        assert restored["questionnaire"].completed is True
        assert isinstance(restored["project_analysis"], ProjectAnalysis)
        assert len(restored["features"]) == 2
        assert all(isinstance(e, Feature) for e in restored["features"])

        # Route entry should point to story_writer (features present, no stories)
        assert route_entry(restored) == "story_writer"

        # Verify we can continue the pipeline from here
        stage_llm = _make_stage_llm([_STORIES_JSON, _TASKS_JSON, _SPRINTS_JSON])
        monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: stage_llm)

        graph = create_graph(tools=[])

        # Stories
        restored["messages"] = [HumanMessage(content="continue")]
        state = graph.invoke(restored)
        assert len(state.get("stories", [])) == 3

        # Tasks
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})
        assert len(state.get("tasks", [])) == 4

        # Sprint planner (sprint selection + capacity now handled during intake)
        state = graph.invoke({**state, "messages": [HumanMessage(content="continue")]})

        # Sprint planner may return a capacity override warning — handle it
        if not state.get("sprints"):
            state = graph.invoke({**state, "messages": [HumanMessage(content="yes")]})

        assert len(state.get("sprints", [])) >= 1
        assert route_entry(state) == "agent"

    def test_roundtrip_serialization_preserves_all_artifacts(self):
        """Serialize → deserialize should preserve all artifact types exactly."""
        state = {
            "messages": [HumanMessage(content="test")],  # skipped in serialization
            "questionnaire": _completed_questionnaire(),
            "project_analysis": _dummy_analysis(),
            "features": [
                Feature(id="F1", title="Auth", description="Auth system", priority=Priority.HIGH),
            ],
            "stories": [
                UserStory(
                    id="US-F1-001",
                    feature_id="F1",
                    persona="user",
                    goal="register",
                    benefit="access",
                    acceptance_criteria=(),
                    story_points=StoryPointValue.FIVE,
                    priority=Priority.HIGH,
                ),
            ],
            "tasks": [
                Task(id="T-1", story_id="US-F1-001", title="Build endpoint", description="POST /register"),
            ],
            "sprints": [
                Sprint(id="SP-1", name="Sprint 1", goal="Auth", capacity_points=5, story_ids=("US-F1-001",)),
            ],
            "team_size": 3,
            "pending_review": "sprint_planner",
            "last_review_decision": ReviewDecision.ACCEPT,
        }

        json_str = _serialize_state(state)
        restored = _deserialize_state(json_str)

        # Messages are not serialized — restored gets empty list
        assert restored["messages"] == []

        # Questionnaire
        assert restored["questionnaire"].completed is True
        assert len(restored["questionnaire"].answers) == TOTAL_QUESTIONS

        # Analysis
        assert restored["project_analysis"].project_name == "Todo App"
        assert restored["project_analysis"].tech_stack == ("React", "FastAPI", "PostgreSQL")

        # Features
        assert len(restored["features"]) == 1
        assert isinstance(restored["features"][0], Feature)
        assert restored["features"][0].priority == Priority.HIGH

        # Stories
        assert len(restored["stories"]) == 1
        assert isinstance(restored["stories"][0], UserStory)
        assert restored["stories"][0].story_points == StoryPointValue.FIVE

        # Tasks
        assert len(restored["tasks"]) == 1
        assert isinstance(restored["tasks"][0], Task)

        # Sprints
        assert len(restored["sprints"]) == 1
        assert isinstance(restored["sprints"][0], Sprint)
        assert restored["sprints"][0].story_ids == ("US-F1-001",)

        # Scalars
        assert restored["team_size"] == 3
        assert restored["pending_review"] == "sprint_planner"
        assert restored["last_review_decision"] == ReviewDecision.ACCEPT
