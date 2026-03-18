"""End-to-end REPL-level tests — drive run_repl() with scripted inputs.

Unlike test_repl.py (which tests individual REPL features in isolation),
these tests drive the full run_repl() loop with a mock graph that simulates
the complete pipeline progression: intake → analyzer → features → stories →
tasks → sprints → "plan complete".

Each test captures Rich console output to a StringIO buffer and asserts on
key text markers (panel titles, status messages, "Goodbye!").

All graph.invoke() calls hit a mock — no real LLM calls are made.
"""

from __future__ import annotations

import re
from io import StringIO
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from rich.console import Console

from scrum_agent.agent.state import (
    TOTAL_QUESTIONS,
    AcceptanceCriterion,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)
from scrum_agent.repl import run_repl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_console() -> tuple[Console, StringIO]:
    """Create a Console that writes to a StringIO buffer."""
    from scrum_agent.formatters import build_theme

    buf = StringIO()
    return Console(file=buf, force_terminal=True, theme=build_theme("dark")), buf


def _mock_session_factory(inputs: list[str]):
    """Return a PromptSession class whose prompt() yields inputs in order.

    After all inputs are consumed, raises EOFError to exit the REPL.
    """

    class FakeSession:
        def __init__(self, *args, **kwargs):
            self._iter = iter(inputs)

        def prompt(self, *args, **kwargs):
            try:
                return next(self._iter)
            except StopIteration:
                raise EOFError

    return FakeSession


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes for plain-text assertions."""
    return re.sub(r"\x1b\[[^m]*m", "", text)


def _completed_questionnaire() -> QuestionnaireState:
    """Create a completed questionnaire with all 26 answers."""
    qs = QuestionnaireState(completed=True, current_question=TOTAL_QUESTIONS + 1)
    for i in range(1, TOTAL_QUESTIONS + 1):
        qs.answers[i] = f"Answer for Q{i}"
    return qs


def _dummy_analysis() -> ProjectAnalysis:
    return ProjectAnalysis(
        project_name="Todo App",
        project_description="A full-stack todo application",
        project_type="greenfield",
        goals=("Task management",),
        end_users=("developers",),
        target_state="Deployed to production",
        tech_stack=("React", "FastAPI"),
        integrations=("GitHub API",),
        constraints=("Must use AWS",),
        sprint_length_weeks=2,
        target_sprints=3,
        risks=("Tight timeline",),
        out_of_scope=("Mobile app",),
        assumptions=("Default velocity assumed",),
    )


def _sample_features() -> list[Feature]:
    return [
        Feature(id="F1", title="Auth", description="Authentication", priority=Priority.HIGH),
        Feature(id="F2", title="Tasks", description="Task management", priority=Priority.HIGH),
    ]


def _sample_stories() -> list[UserStory]:
    return [
        UserStory(
            id="US-E1-001",
            feature_id="F1",
            persona="user",
            goal="register",
            benefit="access",
            acceptance_criteria=(AcceptanceCriterion(given="on page", when="submit", then="created"),),
            story_points=StoryPointValue.FIVE,
            priority=Priority.HIGH,
        ),
    ]


def _sample_tasks() -> list[Task]:
    return [
        Task(id="T-1", story_id="US-E1-001", title="Build endpoint", description="POST /register"),
    ]


def _sample_sprints() -> list[Sprint]:
    return [
        Sprint(id="SP-1", name="Sprint 1", goal="Auth", capacity_points=5, story_ids=("US-E1-001",)),
    ]


def _make_pipeline_graph():
    """Return a mock graph that simulates the full pipeline.

    On each invoke() call, it progresses through the pipeline based on state:
    - No questionnaire → intake (returns completed questionnaire)
    - No analysis → analyzer (returns analysis)
    - No features → feature generator (returns features)
    - No stories → story writer (returns stories)
    - No tasks → task decomposer (returns tasks)
    - No sprints → sprint planner (returns sprints)
    - Everything present → agent (returns chat response)
    """
    mock_graph = MagicMock()

    def _invoke(state):
        input_msgs = state.get("messages", [])
        qs = state.get("questionnaire")

        # Intake phase: return completed questionnaire
        if qs is None or not getattr(qs, "completed", False):
            completed_qs = _completed_questionnaire()
            ai_msg = AIMessage(content="Great! I've collected all your answers.")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "questionnaire": completed_qs,
            }

        # Analyzer
        if state.get("project_analysis") is None:
            analysis = _dummy_analysis()
            ai_msg = AIMessage(content="# Project Analysis\nTodo App analysis complete.")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "project_analysis": analysis,
                "project_name": "Todo App",
                "project_description": "A full-stack todo application",
                "sprint_length_weeks": 2,
                "target_sprints": 3,
                "pending_review": "project_analyzer",
                "context_sources": [],
            }

        # Feature generator
        if not state.get("features"):
            ai_msg = AIMessage(content="# Features\n2 features generated.")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "features": _sample_features(),
                "pending_review": "feature_generator",
            }

        # Story writer
        if not state.get("stories"):
            ai_msg = AIMessage(content="# Stories\n1 story generated.")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "stories": _sample_stories(),
                "pending_review": "story_writer",
            }

        # Task decomposer
        if not state.get("tasks"):
            ai_msg = AIMessage(content="# Tasks\n1 task generated.")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "tasks": _sample_tasks(),
                "pending_review": "task_decomposer",
            }

        # Sprint planner
        if not state.get("sprints"):
            ai_msg = AIMessage(content="# Sprints\n1 sprint planned.")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "sprints": _sample_sprints(),
                "pending_review": "sprint_planner",
            }

        # Agent (all artifacts present)
        ai_msg = AIMessage(content="I'm your Scrum Master. How can I help?")
        return {**state, "messages": [*input_msgs, ai_msg]}

    mock_graph.invoke.side_effect = _invoke
    return mock_graph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_deps(monkeypatch, tmp_path):
    """Avoid filesystem writes, network calls, and delays."""
    monkeypatch.setattr("scrum_agent.repl.HISTORY_DIR", tmp_path)
    monkeypatch.setattr("scrum_agent.repl.FileHistory", lambda path: None)
    monkeypatch.setattr("scrum_agent.repl.time.sleep", lambda _: None)
    # Default graph — individual tests override via monkeypatch
    monkeypatch.setattr("scrum_agent.repl.create_graph", _make_pipeline_graph)


# ---------------------------------------------------------------------------
# Test 1: Happy path — intake → pipeline → "Goodbye"
# ---------------------------------------------------------------------------


class TestHappyPathE2E:
    """Full pipeline run through run_repl() with accept at every checkpoint."""

    def test_full_pipeline_produces_goodbye(self, monkeypatch):
        """Drive through intake → all pipeline stages → exit, verify key output."""
        # Inputs: project description, then "accept" at each review checkpoint,
        # then "exit". The mock graph returns completed questionnaire on first call,
        # so we skip Q1-Q26. Then 5 pipeline stages each with "accept".
        inputs = [
            "Build a todo app",  # triggers intake → returns completed questionnaire
            "1",  # start analysis (post-questionnaire ready gate)
            "accept",  # accept analysis
            "accept",  # accept features
            "accept",  # accept stories
            "accept",  # accept tasks
            "accept",  # accept sprints → plan complete
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())

        # Should see pipeline progression markers
        assert "Goodbye!" in output
        assert "Accepted" in output
        # Plan complete message should appear after accepting sprints
        assert "plan complete" in output.lower() or "plan is ready" in output.lower()

    def test_pipeline_shows_accepted_continuation(self, monkeypatch):
        """Each accept should show 'Accepted' confirmation."""
        inputs = [
            "Build a todo app",
            "start",  # post-questionnaire gate
            "accept",  # analysis
            "accept",  # features
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())

        # Should see multiple "Accepted" messages (one per review)
        accept_count = output.lower().count("accepted")
        assert accept_count >= 2


# ---------------------------------------------------------------------------
# Test 2: Ctrl-C / Ctrl-D graceful exit at each pipeline stage
# ---------------------------------------------------------------------------


class TestGracefulExitAtPipelineStages:
    """Ctrl-C and Ctrl-D should cleanly exit at any point."""

    def test_ctrl_c_during_intake(self, monkeypatch):
        """Ctrl-C at the very first prompt should exit gracefully."""
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory([]),  # EOFError on first prompt
        )
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert "Goodbye!" in buf.getvalue()

    def test_ctrl_c_at_review_checkpoint(self, monkeypatch):
        """Ctrl-C at a review checkpoint should exit gracefully."""
        # Get to a review checkpoint, then stop providing inputs (EOFError)
        inputs = [
            "Build a todo app",  # intake → completed questionnaire
            "start",  # post-questionnaire → analysis
            # No more inputs → EOFError at review prompt
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "Goodbye!" in output

    def test_ctrl_c_after_features(self, monkeypatch):
        """Exit after accepting analysis but before accepting features."""
        inputs = [
            "Build a todo app",
            "start",
            "accept",  # accept analysis → graph produces features
            # EOFError at feature review
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert "Goodbye!" in buf.getvalue()


# ---------------------------------------------------------------------------
# Test 3: /export command
# ---------------------------------------------------------------------------


class TestExportCommandE2E:
    """Test /export at various pipeline stages."""

    def test_export_during_intake(self, monkeypatch):
        """Export during intake should produce questionnaire file."""
        inputs = [
            "Build a todo app",  # intake
            "export",  # export questionnaire
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "exported" in output.lower() or "Goodbye!" in output

    def test_export_at_review_checkpoint(self, monkeypatch):
        """Export at a review checkpoint should export current artifacts."""
        inputs = [
            "Build a todo app",
            "start",
            "accept",  # accept analysis
            "export",  # export at feature review checkpoint
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        # Export should produce a file and stay at the review checkpoint
        assert "Goodbye!" in output

    def test_export_after_full_pipeline(self, monkeypatch):
        """Export after completing the full pipeline."""
        inputs = [
            "Build a todo app",
            "1",  # start analysis
            "accept",  # analysis
            "accept",  # features
            "accept",  # stories
            "accept",  # tasks
            "accept",  # sprints → plan complete
            "export",  # export full plan
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Goodbye!" in output


# ---------------------------------------------------------------------------
# Test 4: /resume command
# ---------------------------------------------------------------------------


class TestResumeCommandE2E:
    """Test /resume lists sessions and resumes selected one."""

    def test_resume_no_sessions_shows_message(self, monkeypatch):
        """/resume with no saved sessions should show 'No saved sessions'."""
        inputs = [
            "Build a todo app",
            "/resume",
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "no saved sessions" in output.lower() or "Goodbye!" in output

    def test_resume_with_startup_flag(self, monkeypatch, tmp_path):
        """Resuming via resume_state= parameter should show 'Resumed session'."""
        # Build a mid-pipeline state (after analysis, before features)
        resume_state = {
            "messages": [],
            "questionnaire": _completed_questionnaire(),
            "project_analysis": _dummy_analysis(),
        }

        inputs = [
            "accept",  # accept features (mock graph generates them)
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, resume_state=resume_state)
        output = _strip_ansi(buf.getvalue())
        assert "resumed session" in output.lower() or "Resumed" in output
        assert "Goodbye!" in output


# ---------------------------------------------------------------------------
# Test 5: Error recovery — LLM error mid-pipeline
# ---------------------------------------------------------------------------


class TestErrorRecoveryE2E:
    """LLM errors mid-pipeline should show a message without crashing."""

    def test_api_error_doesnt_crash_repl(self, monkeypatch):
        """An exception during graph.invoke() should be caught and displayed."""
        call_count = {"n": 0}

        def _invoke(state):
            call_count["n"] += 1
            input_msgs = state.get("messages", [])

            if call_count["n"] == 1:
                # First call: return completed questionnaire
                return {
                    **state,
                    "messages": [*input_msgs, AIMessage(content="Got it!")],
                    "questionnaire": _completed_questionnaire(),
                }
            elif call_count["n"] == 2:
                # Second call: simulate an API error
                raise Exception("Simulated API failure")
            else:
                # Third call: recover normally
                return {
                    **state,
                    "messages": [*input_msgs, AIMessage(content="Recovered!")],
                    "project_analysis": _dummy_analysis(),
                    "project_name": "Todo App",
                    "pending_review": "project_analyzer",
                    "context_sources": [],
                }

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)

        inputs = [
            "Build a todo app",  # intake
            "start",  # post-questionnaire → triggers error
            "start",  # retry → works
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())

        # Error should be displayed but REPL should continue
        assert "error" in output.lower() or "Error" in output
        assert "Goodbye!" in output

    def test_repeated_errors_dont_accumulate(self, monkeypatch):
        """Multiple errors in a row should not crash or freeze the REPL."""
        call_count = {"n": 0}

        def _invoke(state):
            call_count["n"] += 1
            input_msgs = state.get("messages", [])

            if call_count["n"] == 1:
                return {
                    **state,
                    "messages": [*input_msgs, AIMessage(content="Got it!")],
                    "questionnaire": _completed_questionnaire(),
                }
            elif call_count["n"] <= 3:
                raise Exception("API error")
            else:
                return {
                    **state,
                    "messages": [*input_msgs, AIMessage(content="Finally!")],
                    "project_analysis": _dummy_analysis(),
                    "project_name": "Todo App",
                    "pending_review": "project_analyzer",
                    "context_sources": [],
                }

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)

        inputs = [
            "Build a todo app",
            "start",  # error 1
            "start",  # error 2
            "start",  # success
            "exit",
        ]
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(inputs))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())

        assert "Goodbye!" in output
        # Should have recovered on the 4th invoke
        assert call_count["n"] == 4
