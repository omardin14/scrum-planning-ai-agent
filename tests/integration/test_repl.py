"""Tests for the REPL loop."""

import re
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from rich.console import Console

from scrum_agent.agent.state import (
    TOTAL_QUESTIONS,
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    ReviewDecision,
    Sprint,
    Task,
    UserStory,
)
from scrum_agent.repl import (
    _RATE_LIMIT_MAX_RETRIES,
    REVIEW_HINT,
    _build_spinner_message,
    _build_toolbar,
    _clear_downstream_artifacts,
    _export_plan_markdown,
    _get_active_suggestion,
    _handle_rate_limit,
    _is_md_file_path,
    _is_unrecognized_review_input,
    _predict_next_node,
    _render_choice_options,
    _render_dynamic_choices,
    _render_intake_mode_menu,
    _render_offline_submenu,
    _render_questionnaire_ui,
    _resolve_choice_input,
    _resolve_dynamic_choice,
    _resolve_intake_mode,
    _resolve_offline_choice,
    _resolve_review_choice,
    _serialize_artifacts_for_review,
    _split_intake_preamble,
    print_phase_header,
    run_repl,
    stream_response,
)

# ── Helpers ────────────────────────────────────────────────────────


def _make_console() -> tuple[Console, StringIO]:
    """Create a Console that writes to a StringIO buffer with theme support."""
    from scrum_agent.formatters import build_theme

    buf = StringIO()
    return Console(file=buf, force_terminal=True, theme=build_theme("dark")), buf


def _mock_session_factory(inputs: list[str]):
    """Return a PromptSession class whose prompt() yields inputs in order."""

    class FakeSession:
        def __init__(self, *args, **kwargs):
            self._iter = iter(inputs)

        def prompt(self):
            return next(self._iter)

    return FakeSession


def _mock_session_with_exception(exc: type[BaseException]):
    """Return a PromptSession class whose prompt() raises on first call."""

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def prompt(self):
            raise exc()

    return FakeSession


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes so assertions match plain text."""
    return re.sub(r"\x1b\[[^m]*m", "", text)


def _mock_graph_with_questionnaire(ai_content: str, questionnaire: QuestionnaireState):
    """Return a mock compiled graph that returns state including a QuestionnaireState.

    Used by TestQuestionnaireUI to simulate the intake flow. The returned
    graph mirrors the real graph's behaviour — spreading input state and
    appending the AI message, plus including the questionnaire in the result.
    When awaiting_confirmation is True, also sets pending_review: "project_intake"
    to match the nodes' new behaviour (unified with pipeline review checkpoint).
    """
    mock_graph = MagicMock()

    def _invoke(state):
        input_msgs = state["messages"]
        ai_msg = AIMessage(content=ai_content)
        result = {**state, "messages": [*input_msgs, ai_msg], "questionnaire": questionnaire}
        # Nodes set pending_review: "project_intake" only when awaiting confirmation
        # AND not in a re-ask (editing_question is None).
        confirming = questionnaire.awaiting_confirmation and not questionnaire.completed
        if confirming and questionnaire.editing_question is None:
            result["pending_review"] = "project_intake"
        return result

    mock_graph.invoke.side_effect = _invoke
    return mock_graph


def _mock_graph_factory(ai_content: str = "I'm your Scrum Master."):
    """Return a mock compiled graph whose invoke() returns a canned AIMessage.

    The mock tracks all .invoke() calls so tests can inspect what messages
    were passed to the graph on each turn. Returns the full state dict
    (not just messages) to match the real graph's behaviour — the REPL
    now tracks graph_state including questionnaire and other fields.
    """
    mock_graph = MagicMock()

    def _invoke(state):
        input_msgs = state["messages"]
        ai_msg = AIMessage(content=ai_content)
        # Return full state dict (spread input state + updated messages)
        return {**state, "messages": [*input_msgs, ai_msg]}

    mock_graph.invoke.side_effect = _invoke
    return mock_graph


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_deps(monkeypatch, tmp_path):
    """Avoid filesystem writes, network calls, and delays during tests.

    Monkeypatches create_graph to return a mock graph so tests never hit
    a real LLM. Individual tests can override the graph by patching
    create_graph again.
    """
    monkeypatch.setattr("scrum_agent.repl.HISTORY_DIR", tmp_path)
    monkeypatch.setattr("scrum_agent.repl.FileHistory", lambda path: None)
    monkeypatch.setattr("scrum_agent.repl.time.sleep", lambda _: None)
    monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: _mock_graph_factory())


# --- Graceful exit ---


class TestGracefulExit:
    def test_exit_command(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert "Goodbye!" in buf.getvalue()

    def test_quit_command(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["quit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert "Goodbye!" in buf.getvalue()

    def test_exit_case_insensitive(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["EXIT"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert "Goodbye!" in buf.getvalue()

    def test_ctrl_d_eof(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_with_exception(EOFError))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert "Goodbye!" in buf.getvalue()

    def test_ctrl_c_keyboard_interrupt(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_with_exception(KeyboardInterrupt))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert "Goodbye!" in buf.getvalue()


# --- Input handling ---


class TestInputHandling:
    def test_empty_input_skipped(self, monkeypatch):
        mock_graph = _mock_graph_factory()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["", "   ", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        mock_graph.invoke.assert_not_called()

    def test_user_input_sent_to_graph(self, monkeypatch):
        """User input should be wrapped in a HumanMessage and passed to graph.invoke()."""
        mock_graph = _mock_graph_factory()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello world", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")

        mock_graph.invoke.assert_called_once()
        call_args = mock_graph.invoke.call_args[0][0]
        assert len(call_args["messages"]) == 1
        assert isinstance(call_args["messages"][0], HumanMessage)
        assert call_args["messages"][0].content == "hello world"

    def test_ai_response_displayed(self, monkeypatch):
        """The AI response content should appear in the console output."""
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: _mock_graph_factory("Here are your features."))
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["plan my project", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "Here are your features." in output

    def test_multiple_inputs_before_exit(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: _mock_graph_factory("response"))
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["first", "second", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "response" in output
        assert "Goodbye!" in output

    def test_help_command(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["help", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "Available commands" in output

    def test_help_question_mark(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["?", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "Available commands" in output

    def test_markdown_rendering(self, monkeypatch):
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: _mock_graph_factory("**bold text**"))
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["describe project", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "bold text" in output


# --- REPL-Graph integration ---


class TestReplGraphIntegration:
    """Tests that the REPL correctly wires to the LangGraph agent."""

    def test_conversation_history_accumulates(self, monkeypatch):
        """On the second turn, graph.invoke() should receive all prior messages.

        Turn 1: [HumanMessage("first")] → AI responds
        Turn 2: [HumanMessage("first"), AIMessage(...), HumanMessage("second")] → AI responds

        See README: "Memory & State" — without a checkpointer, the REPL must
        manually accumulate and pass conversation history (via graph_state dict).
        """
        mock_graph = _mock_graph_factory("ok")
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["first", "second", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")

        assert mock_graph.invoke.call_count == 2

        # First call: only one HumanMessage
        first_call_msgs = mock_graph.invoke.call_args_list[0][0][0]["messages"]
        assert len(first_call_msgs) == 1
        assert first_call_msgs[0].content == "first"

        # Second call: the REPL now saves the full graph result as graph_state
        # and appends the new HumanMessage to graph_state["messages"].
        # So second call has: HumanMessage("first") + AIMessage("ok") + HumanMessage("second")
        second_call_msgs = mock_graph.invoke.call_args_list[1][0][0]["messages"]
        assert len(second_call_msgs) == 3
        assert second_call_msgs[0].content == "first"
        assert isinstance(second_call_msgs[1], AIMessage)
        assert second_call_msgs[1].content == "ok"
        assert second_call_msgs[2].content == "second"

    def test_api_error_displays_error_message(self, monkeypatch):
        """When graph.invoke() raises an exception, the REPL should display
        an error message and continue (not crash)."""
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = RuntimeError("API key invalid")
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "Unexpected error:" in output
        assert "API key invalid" in output
        # REPL should continue and exit gracefully
        assert "Goodbye!" in output

    def test_error_does_not_accumulate_in_history(self, monkeypatch):
        """When an invocation fails, the failed turn should not be added to history."""
        call_count = 0

        def _invoke_fail_then_succeed(state):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("network timeout")
            input_msgs = state["messages"]
            return {**state, "messages": [*input_msgs, AIMessage(content="recovered")]}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke_fail_then_succeed
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession", _mock_session_factory(["fail-input", "retry-input", "exit"])
        )
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")

        # Second call should only have the retry message (failed turn not accumulated)
        second_call_msgs = mock_graph.invoke.call_args_list[1][0][0]["messages"]
        assert len(second_call_msgs) == 1
        assert second_call_msgs[0].content == "retry-input"

    def test_graph_compiled_once(self, monkeypatch):
        """create_graph() should be called once, not on every input."""
        create_call_count = 0

        def _counting_factory():
            nonlocal create_call_count
            create_call_count += 1
            return _mock_graph_factory()

        monkeypatch.setattr("scrum_agent.repl.create_graph", _counting_factory)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["a", "b", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert create_call_count == 1


# --- Streaming ---


class TestStreaming:
    def test_stream_response_returns_accumulated_text(self):
        console, buf = _make_console()
        result = stream_response(console, iter(["Hello", " ", "world"]))
        assert result == "Hello world"

    def test_stream_response_renders_to_console(self):
        console, buf = _make_console()
        stream_response(console, iter(["Hello", " ", "world"]))
        output = buf.getvalue()
        assert "Hello" in output
        assert "world" in output

    def test_stream_empty_tokens(self):
        console, buf = _make_console()
        result = stream_response(console, iter([]))
        assert result == ""


# --- Phase headers ---


class TestPhaseHeader:
    def test_prints_title(self):
        console, buf = _make_console()
        print_phase_header(console, "Project Context")
        output = buf.getvalue()
        assert "Project Context" in output

    def test_custom_style(self):
        console, buf = _make_console()
        print_phase_header(console, "Done", style="green")
        output = buf.getvalue()
        assert "Done" in output

    def test_uses_rule_divider(self):
        console, buf = _make_console()
        print_phase_header(console, "Test")
        output = buf.getvalue()
        # rich.rule uses ─ characters for the divider line
        assert "─" in output


# --- Session setup ---


class TestSessionSetup:
    def test_history_dir_created(self, monkeypatch, tmp_path):
        history_dir = tmp_path / "custom-history"
        monkeypatch.setattr("scrum_agent.repl.HISTORY_DIR", history_dir)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert history_dir.exists()

    def test_conversational_opener_shown(self, monkeypatch):
        """The REPL should show a conversational opener before the first prompt."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Tell me about your project" in output
        assert "follow-up questions" in output

    def test_no_opener_with_preloaded_questionnaire(self, monkeypatch):
        """When a questionnaire is pre-loaded, the opener should NOT be shown (summary replaces it)."""
        qs = QuestionnaireState(
            current_question=TOTAL_QUESTIONS + 1,
            answers={i: f"Answer {i}" for i in range(1, TOTAL_QUESTIONS + 1)},
            awaiting_confirmation=True,
        )
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["exit"]))
        console, buf = _make_console()
        run_repl(console=console, questionnaire=qs)
        output = _strip_ansi(buf.getvalue())
        # The summary should be shown, not the conversational opener
        assert "Tell me about your project" not in output
        assert "Answer 1" in output


# --- Questionnaire UI ---


class TestQuestionnaireUI:
    """Tests for questionnaire-aware UI elements: progress bar, skip hint, phase headers."""

    def test_progress_shown_during_questionnaire(self, monkeypatch):
        """When the graph returns an active questionnaire, the progress percentage is shown."""
        qs = QuestionnaireState(current_question=6, answers={1: "a", 2: "b", 3: "c", 4: "d", 5: "e"})
        mock_graph = _mock_graph_with_questionnaire("Next question?", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        # 5 answered / 30 total = 16%
        assert "16% complete" in output

    def test_no_skip_hint_during_questionnaire(self, monkeypatch):
        """Skip hint is not shown automatically — discoverable via help only."""
        qs = QuestionnaireState(current_question=2, answers={1: "a"})
        mock_graph = _mock_graph_with_questionnaire("Next question?", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "skip" not in output

    def test_no_ui_after_questionnaire_completes(self, monkeypatch):
        """When the questionnaire is completed, no progress bar or skip hint is shown."""
        qs = QuestionnaireState(current_question=26, completed=True, answers={i: "a" for i in range(1, 27)})
        mock_graph = _mock_graph_with_questionnaire("Summary complete!", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "% complete" not in output
        assert "I don't know" not in output

    def test_no_ui_in_agent_mode(self, monkeypatch):
        """When the graph returns no questionnaire (main agent mode), no progress UI is shown."""
        # Default mock graph returns no questionnaire key
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: _mock_graph_factory("Agent response"))
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "% complete" not in output
        assert "I don't know" not in output

    def test_initial_phase_header_shown(self, monkeypatch):
        """The first questionnaire response should show the Phase 1 header."""
        qs = QuestionnaireState(current_question=1)
        mock_graph = _mock_graph_with_questionnaire("What is the project?", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Phase 1: Project Context" in output

    def test_phase_transition_shows_header(self, monkeypatch):
        """When the phase changes between turns, a phase header divider appears."""
        # Turn 1: questionnaire at Q5 (phase 1: project_context)
        qs_phase1 = QuestionnaireState(current_question=5, answers={i: "a" for i in range(1, 5)})
        # Turn 2: questionnaire at Q6 (phase 2: team_and_capacity)
        qs_phase2 = QuestionnaireState(current_question=6, answers={i: "a" for i in range(1, 6)})

        call_count = 0

        def _invoke(state):
            nonlocal call_count
            call_count += 1
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="question")
            qs = qs_phase1 if call_count == 1 else qs_phase2
            return {**state, "messages": [*input_msgs, ai_msg], "questionnaire": qs}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["first", "second", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        # Both Phase 1 (initial) and Phase 2 (transition) headers should appear
        assert "Phase 1: Project Context" in output
        assert "Phase 2: Team & Capacity" in output

    def test_skip_in_help_text(self, monkeypatch):
        """The help output should mention 'skip' as an available command."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["help", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "skip" in output.lower()
        assert "sensible default" in output

    def test_confirm_hint_shown_when_awaiting(self, monkeypatch):
        """When the questionnaire is awaiting confirmation, the confirm hint is displayed instead of progress."""
        qs = QuestionnaireState(current_question=27, answers={i: "a" for i in range(1, 27)})
        qs.awaiting_confirmation = True
        mock_graph = _mock_graph_with_questionnaire("Here is your summary.", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        # Confirm hint should be shown
        assert "accept" in output.lower()
        assert "edit" in output.lower()
        # Progress bar should NOT be shown
        assert "% complete" not in output
        assert "skip" not in output or "I don't know" not in output

    def test_edit_hint_shown_when_editing(self, monkeypatch):
        """When editing_question is set, the edit hint should be displayed instead of confirm or progress."""
        qs = QuestionnaireState(current_question=27, answers={i: "a" for i in range(1, 27)})
        qs.awaiting_confirmation = True
        qs.editing_question = 6
        mock_graph = _mock_graph_with_questionnaire("Enter your new answer:", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        # Edit hint should be shown
        assert "new answer" in output
        assert "skip" in output
        # Confirm hint should NOT be shown
        assert "what you'd like to change" not in output
        # Progress bar should NOT be shown
        assert "% complete" not in output

    def test_edit_in_help_text(self, monkeypatch):
        """The help output should mention 'edit' as an available command."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["help", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "edit" in output.lower()
        assert "Q6" in output

    def test_render_questionnaire_ui_directly(self):
        """Unit test for _render_questionnaire_ui output."""
        console, buf = _make_console()
        qs = QuestionnaireState(current_question=14, answers={i: "a" for i in range(1, 14)})
        _render_questionnaire_ui(console, qs)
        output = _strip_ansi(buf.getvalue())
        # 13/30 = 43%
        assert "43% complete" in output
        assert "━" in output

    def test_out_of_range_choice_rejected(self, monkeypatch):
        """Typing '5' on a 3-option choice question shows error and re-prompts."""
        # Q2 is a 3-option choice (Greenfield / Existing / Hybrid).
        # First invoke puts questionnaire at Q2, second records valid answer.
        qs = QuestionnaireState(current_question=2, answers={1: "a"})
        mock_graph = _mock_graph_with_questionnaire("Next question?", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # "5" is out of range → rejected, "1" resolves to Greenfield
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["hello", "5", "1", "exit"]),
        )
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Please pick 1" in output
        # "5" should NOT be sent to the graph — the graph should only see "hello" and "Greenfield"
        assert mock_graph.invoke.call_count == 2
        # Second call should have "Greenfield" (resolved from "1"), not "5"
        second_call_msgs = mock_graph.invoke.call_args_list[1][0][0]["messages"]
        assert second_call_msgs[-1].content == "Greenfield"


# --- Export command ---


class TestExportCommand:
    """Tests for the `export` REPL command."""

    def test_export_writes_file(self, monkeypatch, tmp_path):
        """The `export` command should write a questionnaire .md file."""
        monkeypatch.setattr("scrum_agent.repl.DEFAULT_EXPORT_FILENAME", str(tmp_path / "scrum-questionnaire.md"))
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["export", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "exported" in output.lower()
        assert (tmp_path / "scrum-questionnaire.md").exists()

    def test_export_with_partial_questionnaire(self, monkeypatch, tmp_path):
        """Export should include answers from the current questionnaire state."""
        qs = QuestionnaireState(current_question=6, answers={1: "My project", 2: "Greenfield"})
        mock_graph = _mock_graph_with_questionnaire("Next question?", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.DEFAULT_EXPORT_FILENAME", str(tmp_path / "export.md"))
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "export", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        content = (tmp_path / "export.md").read_text()
        assert "> My project" in content
        assert "> Greenfield" in content

    def test_export_in_help_text(self, monkeypatch):
        """The help output should mention 'export' as an available command."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["help", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "export" in output.lower()


# --- File-path auto-detect ---


class TestFilePathImport:
    """Tests for auto-detecting .md file paths during intake."""

    def test_md_file_detected_during_intake(self, monkeypatch, tmp_path):
        """Typing a .md file path during intake should import it."""
        qfile = tmp_path / "intake.md"
        qfile.write_text("**Q1.** What is the project?\n> My awesome app\n\n**Q6.** Engineers?\n> 5\n")

        # Start with an active questionnaire (intake phase)
        qs = QuestionnaireState(current_question=1)
        mock_graph = _mock_graph_with_questionnaire("What is the project?", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", str(qfile), "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        # Should show loaded message and the summary
        assert "Loaded 2 answers" in output
        assert "accept" in output.lower()

    def test_md_file_ignored_after_completion(self, monkeypatch, tmp_path):
        """A .md file path after the questionnaire completes should be passed to the graph."""
        qfile = tmp_path / "notes.md"
        qfile.write_text("**Q1.** What is the project?\n> My app\n")

        # Completed questionnaire — not in intake phase
        qs = QuestionnaireState(current_question=27, completed=True, answers={i: "a" for i in range(1, 27)})
        mock_graph = _mock_graph_with_questionnaire("Got it.", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory([str(qfile), "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        # The input should be passed to the graph, not intercepted
        mock_graph.invoke.assert_called_once()

    def test_nonexistent_md_file_passed_to_graph(self, monkeypatch, tmp_path):
        """A .md file that doesn't exist should be passed to the graph normally."""
        qs = QuestionnaireState(current_question=1)
        mock_graph = _mock_graph_with_questionnaire("What is the project?", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        fake_path = str(tmp_path / "nonexistent.md")
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", fake_path, "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        # Should be called twice: once for "hello", once for the nonexistent path
        assert mock_graph.invoke.call_count == 2


# --- _is_md_file_path unit tests ---


class TestIsMdFilePath:
    """Unit tests for the _is_md_file_path helper."""

    def test_simple_filename(self):
        assert _is_md_file_path("questionnaire.md") is True

    def test_relative_path(self):
        assert _is_md_file_path("./my-file.md") is True

    def test_absolute_path(self):
        assert _is_md_file_path("/tmp/intake.md") is True

    def test_home_path(self):
        assert _is_md_file_path("~/docs/q.md") is True

    def test_not_md(self):
        assert _is_md_file_path("hello world") is False

    def test_sentence_ending_md(self):
        """A sentence containing spaces should not match, even if it ends with .md."""
        assert _is_md_file_path("please load file.md") is False

    def test_empty_string(self):
        assert _is_md_file_path("") is False


# --- Pre-loaded questionnaire ---


class TestPreloadedQuestionnaire:
    """Tests for passing a pre-populated questionnaire to run_repl()."""

    def test_summary_shown_on_start(self, monkeypatch):
        """When a questionnaire is passed, the summary should be displayed immediately."""
        qs = QuestionnaireState(
            current_question=TOTAL_QUESTIONS + 1,
            answers={i: f"Answer {i}" for i in range(1, TOTAL_QUESTIONS + 1)},
            awaiting_confirmation=True,
        )
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["exit"]))
        console, buf = _make_console()
        run_repl(console=console, questionnaire=qs)
        output = _strip_ansi(buf.getvalue())
        # Summary should contain the answers
        assert "Answer 1" in output
        # Confirm hint should be shown
        assert "accept" in output.lower()

    def test_confirm_proceeds_normally(self, monkeypatch):
        """After pre-loaded questionnaire, user input goes through graph.invoke()."""
        qs = QuestionnaireState(
            current_question=TOTAL_QUESTIONS + 1,
            answers={i: f"Answer {i}" for i in range(1, TOTAL_QUESTIONS + 1)},
            awaiting_confirmation=True,
        )
        mock_graph = _mock_graph_factory("Confirmed! Generating plan...")
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["confirm", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, questionnaire=qs)
        # The confirm input should go to graph.invoke
        mock_graph.invoke.assert_called_once()
        # The graph should receive the questionnaire and summary message in state
        call_state = mock_graph.invoke.call_args[0][0]
        assert "questionnaire" in call_state
        assert isinstance(call_state["questionnaire"], QuestionnaireState)
        # Messages should include the AI summary + the user's "confirm"
        assert len(call_state["messages"]) == 2


# ── Review checkpoint tests ──────────────────────────────────────────


class TestClearDownstreamArtifacts:
    """Tests for _clear_downstream_artifacts()."""

    def test_clears_features_and_all_downstream(self):
        """Rejecting features should clear features, stories, tasks, and sprints."""
        state = {
            "features": [Feature(id="F1", title="Auth", description="Auth", priority=Priority.HIGH)],
            "stories": ["story1"],
            "tasks": ["task1"],
            "sprints": ["sprint1"],
        }
        _clear_downstream_artifacts(state, "feature_generator")
        assert "features" not in state
        assert "stories" not in state
        assert "tasks" not in state
        assert "sprints" not in state

    def test_clears_stories_and_downstream_only(self):
        """Rejecting stories should clear stories, tasks, sprints but NOT features."""
        state = {
            "features": ["feature1"],
            "stories": ["story1"],
            "tasks": ["task1"],
            "sprints": ["sprint1"],
        }
        _clear_downstream_artifacts(state, "story_writer")
        assert "features" in state  # preserved
        assert "stories" not in state
        assert "tasks" not in state
        assert "sprints" not in state

    def test_clears_sprints_only(self):
        """Rejecting sprints should only clear sprints."""
        state = {
            "features": ["feature1"],
            "stories": ["story1"],
            "tasks": ["task1"],
            "sprints": ["sprint1"],
        }
        _clear_downstream_artifacts(state, "sprint_planner")
        assert "features" in state
        assert "stories" in state
        assert "tasks" in state
        assert "sprints" not in state

    def test_unknown_node_does_nothing(self):
        """Unknown node names should not clear anything."""
        state = {"features": ["feature1"], "stories": ["story1"]}
        _clear_downstream_artifacts(state, "unknown_node")
        assert "features" in state
        assert "stories" in state


class TestSerializeArtifactsForReview:
    """Tests for _serialize_artifacts_for_review()."""

    def test_serializes_features(self):
        """Should serialize feature dataclasses to JSON text."""
        features = [Feature(id="F1", title="Auth", description="Auth feature", priority=Priority.HIGH)]
        state = {"features": features}
        result = _serialize_artifacts_for_review(state, "feature_generator")
        assert "F1" in result
        assert "Auth" in result

    def test_empty_artifacts_returns_empty(self):
        """Should return empty string when no artifacts present."""
        result = _serialize_artifacts_for_review({}, "feature_generator")
        assert result == ""


class TestReviewAcceptFlow:
    """Tests for the accept review flow in the REPL."""

    def test_accept_clears_pending_review(self, monkeypatch):
        """Accepting should clear pending_review and let the pipeline continue."""
        # Set up a graph that first returns pending_review, then returns normally
        call_count = [0]

        def _invoke(state):
            call_count[0] += 1
            input_msgs = state["messages"]
            if call_count[0] == 1:
                ai_msg = AIMessage(content="Here are your features...")
                return {**state, "messages": [*input_msgs, ai_msg], "pending_review": "feature_generator"}
            else:
                ai_msg = AIMessage(content="Here are your stories...")
                return {**state, "messages": [*input_msgs, ai_msg]}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["start", "accept", "exit"]))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Accepted" in output

    def test_accept_preserves_artifacts(self, monkeypatch):
        """Accepting should preserve the generated artifacts in state."""

        def _invoke(state):
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Done")
            result = {**state, "messages": [*input_msgs, ai_msg], "pending_review": "feature_generator"}
            if "features" not in state:
                result["features"] = ["fake_feature"]
            return result

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # First input triggers graph, "accept" triggers review, third triggers graph again
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["start", "accept", "exit"]))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        # The second graph.invoke call should still have features in state
        if mock_graph.invoke.call_count >= 2:
            second_call_state = mock_graph.invoke.call_args_list[1][0][0]
            assert "features" in second_call_state

    def test_numeric_1_accepts(self, monkeypatch):
        """Typing '1' should resolve to 'accept' and approve the output."""
        call_count = [0]

        def _invoke(state):
            call_count[0] += 1
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Here are your features...")
            if call_count[0] == 1:
                return {**state, "messages": [*input_msgs, ai_msg], "pending_review": "feature_generator"}
            return {**state, "messages": [*input_msgs, ai_msg]}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["start", "1", "exit"]))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Accepted" in output

    def test_accept_last_step_shows_completion(self, monkeypatch):
        """Accepting the last pipeline step should show completion message, not invoke agent."""

        def _invoke(state):
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Here are your sprints...")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "pending_review": "sprint_planner",
                "questionnaire": QuestionnaireState(completed=True),
                "project_analysis": "analysis",
                "features": ["feature"],
                "stories": ["story"],
                "tasks": ["task"],
                "sprints": ["sprint"],
            }

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["start", "accept", "exit"]))

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "plan complete" in output.lower()
        assert "export" in output.lower()
        # Should NOT invoke the graph a second time (no agent node dump)
        assert mock_graph.invoke.call_count == 1


class TestReviewRejectFlow:
    """Tests for the reject/edit review flow in the REPL.

    Reject was removed from the menu — 'reject:' keyword now goes through the
    same EDIT path (last_review_decision=EDIT, previous output included as context).
    """

    def test_reject_keyword_clears_artifacts(self, monkeypatch):
        """'reject:' keyword should clear the node's artifacts and re-run, same as edit."""
        call_count = [0]

        def _invoke(state):
            call_count[0] += 1
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Output")
            result = {**state, "messages": [*input_msgs, ai_msg], "pending_review": "feature_generator"}
            if "features" not in state:
                result["features"] = ["fake_feature"]
                result["stories"] = ["fake_story"]
            return result

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # "reject: more detail" → goes through EDIT path (Reject removed from menu)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["start", "reject: more detail", "exit"]),
        )
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Regenerating" in output

    def test_reject_keyword_sets_edit_decision(self, monkeypatch):
        """'reject:' keyword should set last_review_decision=EDIT (merged into edit path)."""
        call_count = [0]

        def _invoke(state):
            call_count[0] += 1
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Output")
            result = {**state, "messages": [*input_msgs, ai_msg]}
            if call_count[0] == 1:
                result["pending_review"] = "feature_generator"
                result["features"] = ["fake_feature"]
            return result

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["start", "reject: add security", "exit"]),
        )
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        # The second graph call should use EDIT decision (Reject merged into Edit)
        if mock_graph.invoke.call_count >= 2:
            second_call = mock_graph.invoke.call_args_list[1][0][0]
            assert second_call.get("last_review_decision") == ReviewDecision.EDIT
            assert "add security" in second_call.get("last_review_feedback", "")

    def test_numeric_2_edits(self, monkeypatch):
        """Typing '2' should resolve to 'edit' and trigger the edit flow."""
        call_count = [0]

        def _invoke(state):
            call_count[0] += 1
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Output")
            result = {**state, "messages": [*input_msgs, ai_msg]}
            if call_count[0] == 1:
                result["pending_review"] = "feature_generator"
                result["features"] = ["fake_feature"]
            return result

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # "2" → edit (no feedback), then "more detail" as feedback prompt
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["start", "2", "more detail", "exit"]),
        )
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Regenerating" in output


# ── Choice question rendering and input resolution ───────────────────


class TestResolveChoiceInput:
    """Tests for _resolve_choice_input()."""

    def test_valid_number_resolves(self):
        """Typing '1' for Q2 should resolve to 'Greenfield'."""
        assert _resolve_choice_input("1", 2) == "Greenfield"

    def test_second_option(self):
        """Typing '2' for Q2 should resolve to 'Existing codebase'."""
        assert _resolve_choice_input("2", 2) == "Existing codebase"

    def test_out_of_range_returns_original(self):
        """A number beyond the option count should return the original input."""
        assert _resolve_choice_input("99", 2) == "99"

    def test_zero_returns_original(self):
        """Zero is not a valid option number."""
        assert _resolve_choice_input("0", 2) == "0"

    def test_non_number_returns_original(self):
        """Non-numeric input should pass through unchanged."""
        assert _resolve_choice_input("Greenfield", 2) == "Greenfield"

    def test_non_choice_question_returns_original(self):
        """For a free-text question, numbers should pass through unchanged."""
        assert _resolve_choice_input("1", 1) == "1"

    def test_q8_sprint_length(self):
        """Q8: typing '2' should resolve to '2 weeks'."""
        assert _resolve_choice_input("2", 8) == "2 weeks"

    def test_q26_output_format(self):
        """Q26: typing '3' should resolve to 'Both'."""
        assert _resolve_choice_input("3", 26) == "Both"


class TestRenderChoiceOptions:
    """Tests for _render_choice_options()."""

    def test_renders_options_for_q2(self):
        """Q2 should render numbered options."""
        console, buf = _make_console()
        _render_choice_options(console, 2)
        output = _strip_ansi(buf.getvalue())
        assert "[1]" in output
        assert "Greenfield" in output
        assert "[2]" in output
        assert "Existing codebase" in output
        assert "[3]" in output
        assert "Hybrid" in output

    def test_no_render_for_free_text(self):
        """Free-text questions should not render any options."""
        console, buf = _make_console()
        _render_choice_options(console, 1)
        output = buf.getvalue()
        assert output == ""

    def test_default_marked(self):
        """The default option should be marked with *(default)*."""
        console, buf = _make_console()
        _render_choice_options(console, 8)
        output = _strip_ansi(buf.getvalue())
        assert "(default)" in output
        # "2 weeks" is the default for Q8
        assert "2 weeks" in output

    def test_options_shown_in_questionnaire_ui(self):
        """_render_questionnaire_ui should show options for choice questions."""
        console, buf = _make_console()
        qs = QuestionnaireState(current_question=2, answers={1: "A todo app"})
        _render_questionnaire_ui(console, qs)
        output = _strip_ansi(buf.getvalue())
        assert "[1]" in output
        assert "Greenfield" in output

    def test_options_not_shown_for_free_text(self):
        """_render_questionnaire_ui should NOT show options for free-text questions."""
        console, buf = _make_console()
        qs = QuestionnaireState(current_question=1)
        _render_questionnaire_ui(console, qs)
        output = _strip_ansi(buf.getvalue())
        assert "[1]" not in output


class TestDefaultsInHelp:
    """Tests that 'defaults' appears in help text."""

    def test_defaults_in_help_text(self, monkeypatch):
        """The help output should mention 'defaults' as an available command."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["help", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "defaults" in output.lower()


# ── Suggestion confirmation tests ────────────────────────────────────


class TestGetActiveSuggestion:
    """Tests for _get_active_suggestion()."""

    def test_returns_suggestion_when_present(self):
        """Should return the suggestion for the current question."""
        qs = QuestionnaireState(current_question=1)
        qs.suggested_answers = {1: "A todo app"}
        state = {"questionnaire": qs}
        assert _get_active_suggestion(state) == "A todo app"

    def test_returns_none_when_no_suggestion(self):
        """Should return None when the current question has no suggestion."""
        qs = QuestionnaireState(current_question=2)
        qs.suggested_answers = {1: "A todo app"}
        state = {"questionnaire": qs}
        assert _get_active_suggestion(state) is None

    def test_returns_none_when_completed(self):
        """Should return None when the questionnaire is completed."""
        qs = QuestionnaireState(current_question=1, completed=True)
        qs.suggested_answers = {1: "A todo app"}
        state = {"questionnaire": qs}
        assert _get_active_suggestion(state) is None

    def test_returns_none_when_awaiting_confirmation(self):
        """Should return None during the confirmation phase."""
        qs = QuestionnaireState(current_question=27, awaiting_confirmation=True)
        qs.suggested_answers = {1: "A todo app"}
        state = {"questionnaire": qs}
        assert _get_active_suggestion(state) is None

    def test_returns_none_when_editing(self):
        """Should return None when editing a question."""
        qs = QuestionnaireState(current_question=1, editing_question=6)
        qs.suggested_answers = {1: "A todo app"}
        state = {"questionnaire": qs}
        assert _get_active_suggestion(state) is None

    def test_returns_none_when_no_questionnaire(self):
        """Should return None when no questionnaire in state."""
        assert _get_active_suggestion({}) is None


class TestSuggestionHint:
    """Tests that the suggestion hint is shown when appropriate."""

    def test_suggest_hint_shown_for_suggested_question(self):
        """When the current question has a suggestion, show the suggestion hint."""
        console, buf = _make_console()
        qs = QuestionnaireState(current_question=1)
        qs.suggested_answers = {1: "A todo app"}
        _render_questionnaire_ui(console, qs)
        output = _strip_ansi(buf.getvalue())
        assert "press Enter or Y to accept" in output

    def test_no_hint_for_non_suggested_question(self):
        """When no suggestion, no hint is shown (skip hint is in help only)."""
        console, buf = _make_console()
        qs = QuestionnaireState(current_question=3)
        qs.suggested_answers = {1: "A todo app"}  # suggestion on Q1, not Q3
        _render_questionnaire_ui(console, qs)
        output = _strip_ansi(buf.getvalue())
        assert "press Enter or Y to accept" not in output
        assert "skip" not in output


# ── Dynamic follow-up choice rendering and resolution ─────────────────


class TestRenderDynamicChoices:
    """Tests for _render_dynamic_choices()."""

    def test_renders_numbered_options(self):
        """Dynamic choices should be rendered as numbered options."""
        console, buf = _make_console()
        _render_dynamic_choices(console, ("Task management", "E-commerce", "Social platform"))
        output = _strip_ansi(buf.getvalue())
        assert "[1]" in output
        assert "Task management" in output
        assert "[2]" in output
        assert "E-commerce" in output
        assert "[3]" in output
        assert "Social platform" in output

    def test_shows_hint(self):
        """Dynamic choices should show the 'pick a number, or type your own' hint."""
        console, buf = _make_console()
        _render_dynamic_choices(console, ("Option A", "Option B"))
        output = _strip_ansi(buf.getvalue())
        assert "pick a number" in output
        assert "type your own" in output

    def test_shown_in_questionnaire_ui_during_probe(self):
        """_render_questionnaire_ui should show dynamic choices when probed with choices."""
        console, buf = _make_console()
        qs = QuestionnaireState(current_question=1, answers={1: "A web app"})
        qs.probed_questions.add(1)
        qs._follow_up_choices[1] = ("Task management", "E-commerce")
        _render_questionnaire_ui(console, qs)
        output = _strip_ansi(buf.getvalue())
        assert "[1]" in output
        assert "Task management" in output
        assert "pick a number" in output

    def test_not_shown_when_no_choices(self):
        """_render_questionnaire_ui should NOT show dynamic choices when probed but no choices stored."""
        console, buf = _make_console()
        qs = QuestionnaireState(current_question=1, answers={1: "A web app"})
        qs.probed_questions.add(1)
        _render_questionnaire_ui(console, qs)
        output = _strip_ansi(buf.getvalue())
        assert "pick a number" not in output


class TestResolveDynamicChoice:
    """Tests for _resolve_dynamic_choice()."""

    def test_valid_number_resolves(self):
        """Typing '1' should resolve to first choice."""
        assert _resolve_dynamic_choice("1", ("Task management", "E-commerce")) == "Task management"

    def test_second_option(self):
        """Typing '2' should resolve to second choice."""
        assert _resolve_dynamic_choice("2", ("Task management", "E-commerce")) == "E-commerce"

    def test_out_of_range_returns_original(self):
        """A number beyond the choice count should return the original input."""
        assert _resolve_dynamic_choice("5", ("A", "B", "C")) == "5"

    def test_zero_returns_original(self):
        """Zero is not a valid choice number."""
        assert _resolve_dynamic_choice("0", ("A", "B")) == "0"

    def test_free_text_passes_through(self):
        """Non-numeric input should pass through unchanged."""
        assert _resolve_dynamic_choice("My custom answer", ("A", "B")) == "My custom answer"

    def test_all_expands_to_all_choices(self):
        """'all' should expand to all options joined with semicolons."""
        choices = ("Tracking", "Rate comparison", "Fulfillment")
        assert _resolve_dynamic_choice("all", choices) == "Tracking; Rate comparison; Fulfillment"

    def test_all_of_the_above(self):
        """'all of the above' variant should also expand."""
        choices = ("A", "B", "C")
        assert _resolve_dynamic_choice("all of the above", choices) == "A; B; C"

    def test_all_case_insensitive(self):
        """'ALL', 'All' should work too."""
        choices = ("X", "Y")
        assert _resolve_dynamic_choice("All of them", choices) == "X; Y"

    def test_multiple_numbers(self):
        """'1 and 3' should expand to those specific options."""
        choices = ("Tracking", "Rates", "Fulfillment", "Returns")
        assert _resolve_dynamic_choice("1 and 3", choices) == "Tracking; Fulfillment"

    def test_comma_separated_numbers(self):
        """'1, 2, 4' should expand to those options."""
        choices = ("Tracking", "Rates", "Fulfillment", "Returns")
        assert _resolve_dynamic_choice("1, 2, 4", choices) == "Tracking; Rates; Returns"

    def test_multiple_numbers_skips_out_of_range(self):
        """Out-of-range numbers in a multi-select are silently skipped."""
        choices = ("A", "B")
        assert _resolve_dynamic_choice("1, 5", choices) == "A"


# ── Chat attribution labels ─────────────────────────────────────────


class TestChatAttribution:
    """Tests that user/AI attribution labels appear in the REPL output."""

    def test_user_label_shown(self, monkeypatch):
        """'You:' should appear in output when the user sends a normal message."""
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: _mock_graph_factory("response"))
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello world", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "You:" in output
        assert "You: hello world" in output

    def test_ai_label_shown(self, monkeypatch):
        """'Scrum AI:' should appear before the AI response."""
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: _mock_graph_factory("Here are your features."))
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["plan my project", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Scrum AI:" in output

    def test_no_user_label_for_help(self, monkeypatch):
        """Commands like 'help' should NOT get a 'You:' label — they short-circuit."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["help", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "You: help" not in output
        assert "Available commands" in output

    def test_question_label_during_intake(self, monkeypatch):
        """'(question)' should appear in the AI label during active intake."""
        qs = QuestionnaireState(current_question=2)
        mock_graph = _mock_graph_with_questionnaire("What is your project type?", qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["describe project", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "(question)" in output


# ── TestIntakeSummaryFormatter ────────────────────────────────────


class TestIntakeSummaryFormatter:
    """Tests that the REPL uses Rich table rendering for intake summaries."""

    def test_preloaded_questionnaire_uses_formatter(self, monkeypatch):
        """Pre-loaded questionnaire should render phase labels as table titles, not markdown."""
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: _mock_graph_factory())
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["exit"]))
        # Suppress filesystem writes for history
        monkeypatch.setattr("scrum_agent.repl.HISTORY_DIR", Path("/tmp/scrum-test-hist"))

        qs = QuestionnaireState(
            current_question=26,
            answers={i: f"Answer {i}" for i in range(1, 27)},
            awaiting_confirmation=True,
        )
        console, buf = _make_console()
        run_repl(console=console, questionnaire=qs)
        output = _strip_ansi(buf.getvalue())
        # Phase label should appear as a table title, not as a markdown header (##)
        assert "Phase 1: Project Context" in output
        # Should NOT contain the markdown "##" headers from _build_intake_summary
        assert "## Phase" not in output


# ── TestIntakeModeMenu ──────────────────────────────────────────


class TestIntakeModeMenu:
    """Tests for the interactive intake mode selection menu."""

    # --- Unit tests for helpers ---

    def test_resolve_intake_mode_1_is_smart(self):
        assert _resolve_intake_mode("1") == "smart"

    def test_resolve_intake_mode_2_is_standard(self):
        assert _resolve_intake_mode("2") == "standard"

    def test_resolve_intake_mode_3_is_offline(self):
        assert _resolve_intake_mode("3") == "offline"

    def test_resolve_intake_mode_invalid_number(self):
        assert _resolve_intake_mode("4") is None
        assert _resolve_intake_mode("0") is None

    def test_resolve_intake_mode_non_numeric(self):
        assert _resolve_intake_mode("smart") is None
        assert _resolve_intake_mode("") is None

    def test_render_menu_contains_all_modes(self):
        console, buf = _make_console()
        _render_intake_mode_menu(console)
        output = _strip_ansi(buf.getvalue())
        assert "Smart intake (recommended)" in output
        assert "Full intake" in output
        assert "Quick intake" not in output
        assert "Offline questionnaire" in output

    def test_render_menu_no_offline_tip(self):
        """Old offline tip text should no longer appear — it's a full menu option now."""
        console, buf = _make_console()
        _render_intake_mode_menu(console)
        output = _strip_ansi(buf.getvalue())
        assert "--export-questionnaire" not in output
        assert "Tip:" not in output

    # --- Integration tests with run_repl ---

    def test_mode_menu_shown_when_no_flag(self, monkeypatch):
        """Menu text appears when intake_mode=None."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["1", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        output = _strip_ansi(buf.getvalue())
        assert "How would you like to get started?" in output
        assert "Smart intake (recommended)" in output

    def test_mode_1_selects_smart(self, monkeypatch):
        """Typing '1' at the menu sets smart mode and proceeds to opener."""
        mock_graph = _mock_graph_factory()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["1", "my project", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        output = _strip_ansi(buf.getvalue())
        # Opener should appear after mode selection
        assert "Tell me about your project" in output
        # Graph should have been invoked (user typed "my project")
        mock_graph.invoke.assert_called_once()
        # _intake_mode should be "smart" in the state passed to graph
        call_state = mock_graph.invoke.call_args[0][0]
        assert call_state["_intake_mode"] == "smart"

    def test_mode_2_selects_standard(self, monkeypatch):
        """Typing '2' at the menu sets standard mode."""
        mock_graph = _mock_graph_factory()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["2", "my project", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        call_state = mock_graph.invoke.call_args[0][0]
        assert call_state["_intake_mode"] == "standard"

    def test_mode_3_selects_offline(self, monkeypatch):
        """Typing '3' at the menu enters the offline questionnaire flow."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["3", "1", "exit"]))
        monkeypatch.setattr("scrum_agent.repl.export_questionnaire_md", lambda qs, path: path)
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        output = _strip_ansi(buf.getvalue())
        assert "Export blank questionnaire" in output

    def test_menu_skipped_with_cli_flag(self, monkeypatch):
        """When intake_mode='smart', no menu is shown — straight to opener."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "How would you like to get started?" not in output
        assert "Tell me about your project" in output

    def test_invalid_input_reprompts(self, monkeypatch):
        """Invalid input shows 'pick 1, 2, or 3' then accepts a valid choice."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["foo", "7", "2", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        output = _strip_ansi(buf.getvalue())
        # Should have shown the reprompt message twice (for "foo" and "7")
        assert output.count("Please pick 1, 2, or 3.") == 2
        # Should still proceed to the opener after valid input
        assert "Tell me about your project" in output

    def test_ctrl_c_during_menu_exits_gracefully(self, monkeypatch):
        """KeyboardInterrupt during menu selection should exit with Goodbye."""

        class FakeSession:
            _call_count = 0

            def __init__(self, *args, **kwargs):
                pass

            def prompt(self):
                FakeSession._call_count += 1
                if FakeSession._call_count == 1:
                    raise KeyboardInterrupt()
                return "exit"

        FakeSession._call_count = 0
        monkeypatch.setattr("scrum_agent.repl.PromptSession", FakeSession)
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        output = _strip_ansi(buf.getvalue())
        assert "Goodbye!" in output

    # --- Offline (option 3) integration tests ---

    def test_mode_3_export_writes_file_and_exits(self, monkeypatch, tmp_path):
        """Picking [3] then [1] exports the questionnaire and exits."""
        export_path = tmp_path / "scrum-questionnaire.md"
        monkeypatch.setattr("scrum_agent.repl.DEFAULT_EXPORT_FILENAME", str(export_path))
        # "3" selects offline, "1" selects export — should return immediately
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["3", "1"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        output = _strip_ansi(buf.getvalue())
        assert "Questionnaire exported to" in output
        assert "scrum-agent --questionnaire" in output
        # Should NOT show the conversational opener
        assert "Tell me about your project" not in output

    def test_mode_3_import_loads_and_shows_summary(self, monkeypatch, tmp_path):
        """Picking [3] then [2] then a valid path loads and shows the summary table."""
        # Create a minimal questionnaire file
        md_file = tmp_path / "filled.md"
        md_file.write_text("## Q1\nMy project\n\n## Q2\nGreenfield\n")
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["3", "2", str(md_file), "exit"]),
        )
        # Mock _import_questionnaire_file to avoid needing real parse logic
        imported = False

        def fake_import(console, path, state):
            nonlocal imported
            imported = True
            qs = QuestionnaireState()
            qs.answers = {1: "My project", 2: "Greenfield"}
            qs.awaiting_confirmation = True
            return {**state, "questionnaire": qs}

        monkeypatch.setattr("scrum_agent.repl._import_questionnaire_file", fake_import)
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        assert imported
        output = _strip_ansi(buf.getvalue())
        # Should NOT show the conversational opener
        assert "Tell me about your project" not in output

    def test_mode_3_import_defaults_to_export_filename(self, monkeypatch, tmp_path):
        """Pressing Enter at the import prompt uses DEFAULT_EXPORT_FILENAME."""
        md_file = tmp_path / "scrum-questionnaire.md"
        md_file.write_text("## Q1\nMy project\n")
        monkeypatch.setattr("scrum_agent.repl.DEFAULT_EXPORT_FILENAME", str(md_file))
        # "3" → offline, "2" → import, "" → press Enter (default path), "exit" → quit
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["3", "2", "", "exit"]),
        )
        imported_path = None

        def fake_import(console, path, state):
            nonlocal imported_path
            imported_path = path
            qs = QuestionnaireState()
            qs.awaiting_confirmation = True
            return {**state, "questionnaire": qs}

        monkeypatch.setattr("scrum_agent.repl._import_questionnaire_file", fake_import)
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        assert imported_path == md_file

    def test_mode_3_import_invalid_path_reprompts(self, monkeypatch, tmp_path):
        """Bad path shows error and re-prompts for a valid one."""
        md_file = tmp_path / "filled.md"
        md_file.write_text("## Q1\nMy project\n")
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["3", "2", "/no/such/file.md", str(md_file), "exit"]),
        )

        def fake_import(console, path, state):
            qs = QuestionnaireState()
            qs.awaiting_confirmation = True
            return {**state, "questionnaire": qs}

        monkeypatch.setattr("scrum_agent.repl._import_questionnaire_file", fake_import)
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        output = _strip_ansi(buf.getvalue())
        assert "File not found or not a .md file" in output

    def test_offline_submenu_invalid_input_reprompts(self, monkeypatch, tmp_path):
        """Non-1/2 input at the sub-menu shows 'pick 1 or 2' and re-prompts."""
        export_path = tmp_path / "scrum-questionnaire.md"
        monkeypatch.setattr("scrum_agent.repl.DEFAULT_EXPORT_FILENAME", str(export_path))
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["3", "foo", "5", "1"]),
        )
        console, buf = _make_console()
        run_repl(console=console, intake_mode=None)
        output = _strip_ansi(buf.getvalue())
        assert output.count("Please pick 1 or 2.") == 2


class TestOfflineSubmenu:
    """Unit tests for the offline sub-menu helpers."""

    def test_resolve_offline_choice_1_is_export(self):
        assert _resolve_offline_choice("1") == "export"

    def test_resolve_offline_choice_2_is_import(self):
        assert _resolve_offline_choice("2") == "import"

    def test_resolve_offline_choice_invalid(self):
        assert _resolve_offline_choice("3") is None
        assert _resolve_offline_choice("0") is None
        assert _resolve_offline_choice("foo") is None
        assert _resolve_offline_choice("") is None

    def test_render_offline_submenu_content(self):
        console, buf = _make_console()
        _render_offline_submenu(console)
        output = _strip_ansi(buf.getvalue())
        assert "Export blank questionnaire" in output
        assert "Import filled questionnaire" in output


# ── Spinner & Progress Indicators ─────────────────────────────────


class TestSpinnerProgress:
    """Tests for spinner helpers: _predict_next_node and _build_spinner_message."""

    def test_predict_next_node_intake_no_questionnaire(self):
        """No questionnaire at all → project_intake."""
        assert _predict_next_node({}) == "project_intake"

    def test_predict_next_node_intake_incomplete(self):
        """Questionnaire exists but not completed → project_intake."""
        qs = QuestionnaireState()
        assert _predict_next_node({"questionnaire": qs}) == "project_intake"

    def test_predict_next_node_analyzer(self):
        """Completed questionnaire, no analysis → project_analyzer."""
        qs = QuestionnaireState(completed=True)
        assert _predict_next_node({"questionnaire": qs}) == "project_analyzer"

    def test_predict_next_node_feature_generator(self):
        """Analysis present, no features → feature_generator."""
        qs = QuestionnaireState(completed=True)
        state = {"questionnaire": qs, "project_analysis": "some analysis"}
        assert _predict_next_node(state) == "feature_generator"

    def test_predict_next_node_story_writer(self):
        """Features present, no stories → story_writer."""
        qs = QuestionnaireState(completed=True)
        feature = Feature(id="F-1", title="t", priority=Priority.HIGH, description="d")
        state = {"questionnaire": qs, "project_analysis": "x", "features": [feature]}
        assert _predict_next_node(state) == "story_writer"

    def test_predict_next_node_full_pipeline(self):
        """All artifacts present → agent."""
        qs = QuestionnaireState(completed=True)
        state = {
            "questionnaire": qs,
            "project_analysis": "x",
            "features": ["e"],
            "stories": ["s"],
            "tasks": ["t"],
            "sprints": ["sp"],
        }
        assert _predict_next_node(state) == "agent"

    def test_build_spinner_message_intake(self):
        """Intake messages have no step prefix."""
        msg = _build_spinner_message("project_intake")
        assert msg == "Processing your answer"
        assert "[" not in msg

    def test_build_spinner_message_pipeline_step(self):
        """Pipeline steps get [N/6] prefix."""
        msg = _build_spinner_message("feature_generator")
        assert msg == "[3/6] Generating features"

    def test_build_spinner_message_first_pipeline_step(self):
        """First pipeline step is [1/6]."""
        msg = _build_spinner_message("project_analyzer")
        assert msg == "[1/6] Analysing project"

    def test_build_spinner_message_last_pipeline_step(self):
        """Last pipeline step is [6/6]."""
        msg = _build_spinner_message("sprint_planner")
        assert msg == "[6/6] Planning sprints"

    def test_build_spinner_message_agent(self):
        """Agent node has no step prefix."""
        msg = _build_spinner_message("agent")
        assert msg == "Thinking"
        assert "[" not in msg

    def test_build_spinner_message_unknown_node(self):
        """Unknown node falls back to 'Working'."""
        assert _build_spinner_message("unknown_node") == "Working"

    def test_elapsed_time_shown_for_pipeline(self, monkeypatch):
        """Pipeline steps show elapsed time after completion."""
        console, buf = _make_console()
        qs = QuestionnaireState(completed=True, awaiting_confirmation=True, answers={i: f"a{i}" for i in range(1, 27)})

        # Mock graph that returns analysis on invoke (pipeline step).
        mock_graph = MagicMock()

        def _invoke(state):
            return {
                **state,
                "project_analysis": "done",
                "messages": [*state["messages"], AIMessage(content="Analysed.")],
            }

        mock_graph.invoke.side_effect = _invoke

        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # First prompt: "confirm" triggers the confirmation path → graph.invoke
        # Second prompt: EOFError exits the REPL
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["confirm", "exit"]))

        # Pre-loaded completed questionnaire → skips mode menu, shows summary
        run_repl(console=console, questionnaire=qs)

        output = buf.getvalue()
        # The pipeline step completion line should include "took"
        assert "took" in output

    def test_no_elapsed_for_intake(self, monkeypatch):
        """Intake invocations don't show elapsed timing."""
        console, buf = _make_console()
        # No questionnaire → intake node
        mock_graph = MagicMock()

        def _invoke(state):
            return {
                **state,
                "questionnaire": QuestionnaireState(current_question=1),
                "messages": [*state["messages"], AIMessage(content="Tell me more.")],
            }

        mock_graph.invoke.side_effect = _invoke

        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["hello", "exit"]))

        run_repl(console=console, intake_mode="smart")

        output = buf.getvalue()
        assert "took" not in output


# ── Interactive review menu (Phase 6E) ───────────────────────────────


class TestResolveReviewChoice:
    """Tests for _resolve_review_choice — numeric menu resolution."""

    def test_1_maps_to_accept(self):
        assert _resolve_review_choice("1") == "accept"

    def test_2_maps_to_edit(self):
        assert _resolve_review_choice("2") == "edit"

    def test_3_maps_to_export(self):
        """3 now maps to Export — Reject was removed from the menu."""
        assert _resolve_review_choice("3") == "export"

    def test_0_passes_through(self):
        """Out-of-range number passes through unchanged."""
        assert _resolve_review_choice("0") == "0"

    def test_4_passes_through(self):
        """4 is now out-of-range (Export moved to 3)."""
        assert _resolve_review_choice("4") == "4"

    def test_5_passes_through(self):
        """Out-of-range number passes through unchanged."""
        assert _resolve_review_choice("5") == "5"

    def test_keyword_passthrough(self):
        """Non-numeric text passes through unchanged."""
        assert _resolve_review_choice("accept") == "accept"

    def test_edit_prefix_passthrough(self):
        """Edit prefix with feedback passes through unchanged."""
        assert _resolve_review_choice("edit: add more detail") == "edit: add more detail"

    def test_typo_passthrough(self):
        """Typos pass through unchanged (caught later by unrecognized check)."""
        assert _resolve_review_choice("accpet") == "accpet"


class TestIsUnrecognizedReviewInput:
    """Tests for _is_unrecognized_review_input — typo detection."""

    def test_fallback_reject_detected(self):
        """Unrecognized text triggers fallback REJECT with full text as feedback."""
        assert _is_unrecognized_review_input("accpet", ReviewDecision.REJECT, "accpet") is True

    def test_intentional_reject_not_flagged(self):
        """Bare 'reject' keyword returns empty feedback — not a typo."""
        assert _is_unrecognized_review_input("reject", ReviewDecision.REJECT, "") is False

    def test_reject_with_feedback_not_flagged(self):
        """'reject: reason' returns stripped feedback (≠ resolved) — not a typo."""
        assert _is_unrecognized_review_input("reject: more detail", ReviewDecision.REJECT, "more detail") is False

    def test_accept_not_flagged(self):
        """Accept decisions are never flagged."""
        assert _is_unrecognized_review_input("accept", ReviewDecision.ACCEPT, "") is False

    def test_edit_not_flagged(self):
        """Edit decisions are never flagged."""
        assert _is_unrecognized_review_input("edit: fix typo", ReviewDecision.EDIT, "fix typo") is False


class TestReviewHintContent:
    """Tests that REVIEW_HINT contains expected menu items."""

    def test_contains_option_1(self):
        assert "[1]" in REVIEW_HINT

    def test_contains_option_2(self):
        assert "[2]" in REVIEW_HINT

    def test_contains_option_3(self):
        assert "[3]" in REVIEW_HINT

    def test_contains_accept(self):
        assert "Accept" in REVIEW_HINT

    def test_contains_edit(self):
        assert "Edit" in REVIEW_HINT

    def test_contains_export(self):
        assert "Export" in REVIEW_HINT


class TestReviewUnrecognizedInput:
    """Integration test: typos reprompt instead of silently rejecting."""

    def test_typo_reprompts(self, monkeypatch):
        """A typo like 'accpet' should show reprompt message, not trigger rejection."""
        call_count = [0]

        def _invoke(state):
            call_count[0] += 1
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Here are your features...")
            if call_count[0] == 1:
                return {**state, "messages": [*input_msgs, ai_msg], "pending_review": "feature_generator"}
            return {**state, "messages": [*input_msgs, ai_msg]}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # "accpet" is a typo → reprompt, then "1" → accept, then exit
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["start", "accpet", "1", "exit"]),
        )
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "didn't recognise" in output
        # After the reprompt, "1" should accept successfully
        assert "Accepted" in output


# ── Actionable error messages ─────────────────────────────────────


class TestActionableErrors:
    """Tests for specific API error handling with actionable messages."""

    def test_auth_error_shows_api_key_hint(self, monkeypatch):
        """AuthenticationError should mention ANTHROPIC_API_KEY."""
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=401, request=req)

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = anthropic.AuthenticationError(
            message="Invalid API key", response=resp, body=None
        )
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["hello", "exit"]),
        )
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Authentication failed" in output
        assert "ANTHROPIC_API_KEY" in output

    def test_connection_error_shows_network_hint(self, monkeypatch):
        """APIConnectionError should mention internet connection."""
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = anthropic.APIConnectionError(request=req)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["hello", "exit"]),
        )
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "Network error" in output
        assert "internet connection" in output

    def test_api_status_error_shows_code(self, monkeypatch):
        """APIStatusError should show the HTTP status code."""
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=500, request=req)

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = anthropic.APIStatusError(
            message="Internal server error", response=resp, body=None
        )
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["hello", "exit"]),
        )
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "500" in output
        assert "API error" in output

    def test_rate_limit_retries_and_succeeds(self, monkeypatch):
        """RateLimitError should trigger retry; success should print confirmation."""
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=429, request=req)
        rate_err = anthropic.RateLimitError(message="Rate limited", response=resp, body=None)

        console, buf = _make_console()
        mock_graph = MagicMock()
        # First call raises, second succeeds
        success_result = {"messages": [AIMessage(content="ok")]}
        mock_graph.invoke.side_effect = [rate_err, success_result]
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        result = _handle_rate_limit(console, mock_graph, {"messages": []})
        output = _strip_ansi(buf.getvalue())
        assert result is not None
        assert "Rate limited" in output
        assert "Retry succeeded" in output

    def test_rate_limit_exhausted(self, monkeypatch):
        """All retries failing should print exhaustion message and return None."""
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=429, request=req)
        rate_err = anthropic.RateLimitError(message="Rate limited", response=resp, body=None)

        console, buf = _make_console()
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = rate_err
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        result = _handle_rate_limit(console, mock_graph, {"messages": []})
        output = _strip_ansi(buf.getvalue())
        assert result is None
        assert "retries exhausted" in output
        assert mock_graph.invoke.call_count == _RATE_LIMIT_MAX_RETRIES


# ── Export-only mode ──────────────────────────────────────────────


def _make_completed_questionnaire() -> QuestionnaireState:
    """Build a QuestionnaireState that has completed the intake flow."""
    qs = QuestionnaireState()
    qs.completed = True
    qs.intake_mode = "quick"
    qs.answers = {i: f"answer {i}" for i in range(1, TOTAL_QUESTIONS + 1)}
    return qs


def _make_full_graph_state() -> dict:
    """Build a graph state dict with all pipeline artifacts populated."""
    qs = _make_completed_questionnaire()
    analysis = ProjectAnalysis(
        project_name="Test Project",
        project_description="A test project",
        project_type="greenfield",
        goals=("goal 1",),
        end_users=("developers",),
        target_state="MVP launched",
        tech_stack=("Python",),
        integrations=(),
        constraints=(),
        sprint_length_weeks=2,
        target_sprints=3,
        risks=(),
        out_of_scope=(),
        assumptions=(),
    )
    features = [Feature(id="F-1", title="Feature 1", description="First feature", priority=Priority.HIGH)]
    stories = [
        UserStory(
            id="S-1",
            feature_id="F-1",
            persona="developer",
            goal="do something",
            benefit="value delivered",
            acceptance_criteria=(AcceptanceCriterion(given="setup", when="action", then="result"),),
            story_points=3,
            priority=Priority.HIGH,
            discipline=Discipline.FULLSTACK,
        )
    ]
    tasks = [Task(id="T-1", story_id="S-1", title="Task 1", description="Do it")]
    sprints = [Sprint(id="SP-1", name="Sprint 1", goal="Deliver MVP", capacity_points=10, story_ids=("S-1",))]
    return {
        "messages": [AIMessage(content="done")],
        "questionnaire": qs,
        "project_analysis": analysis,
        "features": features,
        "stories": stories,
        "tasks": tasks,
        "sprints": sprints,
        "velocity_per_sprint": 10,
    }


class TestExportOnly:
    """Tests for --export-only auto-drive mode."""

    def test_auto_accepts_review(self, monkeypatch):
        """In export-only mode, pending_review should be auto-accepted without prompting."""
        full_state = _make_full_graph_state()

        call_count = 0

        def _invoke(state):
            nonlocal call_count
            call_count += 1
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="generated output")
            if call_count == 1:
                # First invoke ("continue"): return all artifacts but with pending_review
                return {
                    **full_state,
                    "messages": [*input_msgs, ai_msg],
                    "pending_review": "sprint_planner",
                }
            else:
                # Second invoke (after accept): return all artifacts, no pending_review
                return {
                    **full_state,
                    "messages": [*input_msgs, ai_msg],
                }

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory([]))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, questionnaire=full_state["questionnaire"], intake_mode="quick", export_only=True)
        output = _strip_ansi(buf.getvalue())
        # Should auto-accept without printing "You: accept"
        assert "Accepted" in output

    def test_exits_after_pipeline_complete(self, monkeypatch):
        """run_repl should return when all artifacts have been generated in export-only mode."""
        full_state = _make_full_graph_state()

        # Simulate: first invoke returns complete state with all artifacts
        def _invoke(state):
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="confirmed")
            return {**full_state, "messages": [*input_msgs, ai_msg]}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory([]))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, questionnaire=full_state["questionnaire"], intake_mode="quick", export_only=True)
        output = _strip_ansi(buf.getvalue())
        assert "Goodbye!" in output

    def test_exports_markdown_file(self, tmp_path):
        """_export_plan_markdown should write a .md file with artifact content."""
        full_state = _make_full_graph_state()
        out_path = tmp_path / "scrum-plan.md"
        result = _export_plan_markdown(full_state, path=out_path)
        assert result == out_path
        assert out_path.exists()
        content = out_path.read_text()
        assert "Test Project" in content
        assert "F-1" in content
        assert "S-1" in content
        assert "T-1" in content
        assert "Sprint 1" in content


# --- Intake message styling ---


class TestIntakeMessageStyling:
    """Tests for _split_intake_preamble() — splitting preamble from question text."""

    def test_split_extracts_preamble_and_question(self):
        """Smart mode first invocation: extraction summary + remaining count + question."""
        content = (
            "I **8** extracted from your description and **18** filled with defaults.\n\n"
            "A few more questions (5 remaining):\n\n"
            "What problem does this project solve?"
        )
        preamble, question = _split_intake_preamble(content)
        assert len(preamble) == 2
        assert "extracted" in preamble[0]
        assert "remaining" in preamble[1]
        assert question == "What problem does this project solve?"

    def test_split_no_preamble(self):
        """Plain question text returns empty preamble."""
        content = "What is the primary programming language for this project?"
        preamble, question = _split_intake_preamble(content)
        assert preamble == []
        assert question == content

    def test_split_follow_up_probe(self):
        """Follow-up probe label is extracted as preamble."""
        content = "**Follow-up on Q3:**\n\nCan you be more specific about the target users?"
        preamble, question = _split_intake_preamble(content)
        assert len(preamble) == 1
        assert "Follow-up on Q3" in preamble[0]
        assert question == "Can you be more specific about the target users?"

    def test_split_phase_header(self):
        """Standard mode bold phase header is extracted as preamble."""
        content = "**Project Scope**\n\nHow many developers will work on this project?"
        preamble, question = _split_intake_preamble(content)
        assert len(preamble) == 1
        assert "Project Scope" in preamble[0]
        assert question == "How many developers will work on this project?"

    def test_preamble_rendered_dim_in_repl(self, monkeypatch):
        """Integration: preamble lines appear dim, question is streamed normally."""
        ai_content = (
            "I **5** extracted from your description and **21** filled with defaults.\n\n"
            "A few more questions (3 remaining):\n\n"
            "What is your deployment target?"
        )
        qs = QuestionnaireState()
        qs.current_question = 4
        qs.intake_mode = "quick"
        mock_graph = _mock_graph_with_questionnaire(ai_content, qs)
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["cloud", "exit"]))
        monkeypatch.setattr("scrum_agent.repl.time", MagicMock())

        console, buf = _make_console()
        run_repl(console=console, intake_mode="quick")
        output = buf.getvalue()
        plain = _strip_ansi(output)

        # The preamble text should be present (rendered dim)
        assert "extracted" in plain
        assert "remaining" in plain
        # The question should also be present (streamed). Rich Live rendering
        # may wrap or truncate at the console width, so check a short fragment.
        assert "target" in plain


# ── Terminal bell tests ──────────────────────────────────────────


class TestTerminalBell:
    """Tests for console.bell() after pipeline steps."""

    def test_bell_called_after_pipeline_step(self, monkeypatch):
        """Bell should ring after a pipeline step completes."""
        completed_qs = QuestionnaireState(
            current_question=TOTAL_QUESTIONS + 1,
            answers={i: f"A{i}" for i in range(1, TOTAL_QUESTIONS + 1)},
            completed=True,
        )
        analysis = ProjectAnalysis(
            project_name="Test",
            project_description="A test",
            project_type="greenfield",
            goals=("g",),
            end_users=("u",),
            target_state="done",
            tech_stack=("Python",),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=3,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )

        call_count = [0]

        def _invoke(state):
            call_count[0] += 1
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Analysis complete.")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "questionnaire": completed_qs,
                "project_analysis": analysis,
            }

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl._build_intake_summary", lambda qs: "Summary")
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["go", "exit"]),
        )

        console, buf = _make_console()
        bell_calls = []
        console.bell = lambda: bell_calls.append(True)

        run_repl(console=console, questionnaire=completed_qs, bell=True)
        assert mock_graph.invoke.call_count >= 1, "Graph should have been invoked"
        assert len(bell_calls) >= 1, "Bell should have been called at least once"

    def test_bell_not_called_when_disabled(self, monkeypatch):
        """Bell should NOT ring when bell=False."""
        completed_qs = QuestionnaireState(
            current_question=TOTAL_QUESTIONS + 1,
            answers={i: f"A{i}" for i in range(1, TOTAL_QUESTIONS + 1)},
            completed=True,
        )
        analysis = ProjectAnalysis(
            project_name="Test",
            project_description="A test",
            project_type="greenfield",
            goals=("g",),
            end_users=("u",),
            target_state="done",
            tech_stack=("Python",),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=3,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )

        def _invoke(state):
            input_msgs = state["messages"]
            ai_msg = AIMessage(content="Analysis complete.")
            return {
                **state,
                "messages": [*input_msgs, ai_msg],
                "questionnaire": completed_qs,
                "project_analysis": analysis,
            }

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl._build_intake_summary", lambda qs: "Summary")
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["go", "exit"]),
        )

        console, buf = _make_console()
        bell_calls = []
        console.bell = lambda: bell_calls.append(True)

        run_repl(console=console, questionnaire=completed_qs, bell=False)
        assert len(bell_calls) == 0, "Bell should not have been called"

    def test_bell_not_called_during_intake(self, monkeypatch):
        """Bell should NOT ring for intake questions (only pipeline steps)."""
        mock_graph = _mock_graph_factory("What is your project?")
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["my project", "exit"]))

        console, buf = _make_console()
        bell_calls = []
        console.bell = lambda: bell_calls.append(True)

        run_repl(console=console, intake_mode="smart", bell=True)
        assert len(bell_calls) == 0, "Bell should not ring for intake questions"


# ── Compact/Verbose toggle tests ─────────────────────────────────


class TestCompactVerboseToggle:
    """Tests for /compact and /verbose REPL commands."""

    def test_compact_acknowledged(self, monkeypatch):
        """Typing /compact should print a confirmation and not invoke the graph."""
        mock_graph = _mock_graph_factory()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["/compact", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "compact" in output.lower()
        mock_graph.invoke.assert_not_called()

    def test_verbose_acknowledged(self, monkeypatch):
        """Typing /verbose should print a confirmation and not invoke the graph."""
        mock_graph = _mock_graph_factory()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["/verbose", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = buf.getvalue()
        assert "verbose" in output.lower()
        mock_graph.invoke.assert_not_called()

    def test_compact_case_insensitive(self, monkeypatch):
        """Commands should be case-insensitive."""
        mock_graph = _mock_graph_factory()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["/COMPACT", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        assert "compact" in buf.getvalue().lower()
        mock_graph.invoke.assert_not_called()

    def test_help_shows_compact_verbose(self, monkeypatch):
        """Help text should document /compact and /verbose."""
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["help", "exit"]))
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart")
        output = _strip_ansi(buf.getvalue())
        assert "/compact" in output
        assert "/verbose" in output


# ── Status bar tests ──────────────────────────────────────────────


class TestStatusBar:
    """Tests for _build_toolbar() — status bar content based on graph state."""

    def test_empty_state_shows_default(self):
        """Empty state should show the default agent label."""
        toolbar = _build_toolbar({})
        assert "Scrum AI Agent" in toolbar.value

    def test_intake_in_progress(self):
        """During intake, should show intake progress percentage."""
        qs = QuestionnaireState()
        qs.current_question = 7
        qs.total_questions = 26
        toolbar = _build_toolbar({"questionnaire": qs})
        assert "Intake" in toolbar.value
        assert "%" in toolbar.value

    def test_intake_awaiting_confirmation(self):
        """When awaiting confirmation, should show that label."""
        qs = QuestionnaireState()
        qs.awaiting_confirmation = True
        toolbar = _build_toolbar({"questionnaire": qs})
        assert "confirmation" in toolbar.value.lower()

    def test_pipeline_step_shown(self):
        """When questionnaire is complete, should show pipeline step."""
        qs = QuestionnaireState(
            current_question=TOTAL_QUESTIONS + 1,
            completed=True,
        )
        toolbar = _build_toolbar({"questionnaire": qs})
        assert "Pipeline" in toolbar.value
        assert "1/" in toolbar.value

    def test_pipeline_complete(self):
        """When all artifacts exist, should show 'Pipeline complete'."""
        qs = QuestionnaireState(completed=True)
        toolbar = _build_toolbar(
            {
                "questionnaire": qs,
                "project_analysis": "analysis",
                "features": ["e"],
                "stories": ["s"],
                "tasks": ["t"],
                "sprints": ["sp"],
            }
        )
        assert "complete" in toolbar.value.lower()

    def test_project_name_shown(self):
        """When project_analysis has a project_name, it should appear in the toolbar."""
        qs = QuestionnaireState(completed=True)
        analysis = ProjectAnalysis(
            project_name="MyApp",
            project_description="desc",
            project_type="greenfield",
            goals=(),
            end_users=(),
            target_state="done",
            tech_stack=(),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=3,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )
        toolbar = _build_toolbar({"questionnaire": qs, "project_analysis": analysis})
        assert "MyApp" in toolbar.value


# ── Edit flow (awaiting_edit_q_num) tests ─────────────────────────


class TestEditFlowRepl:
    """Tests for the _awaiting_edit_q_num flag in the intake confirmation intercept.

    Verifies that when the user picks [2] Edit from the confirmation menu and
    then types a bare question number (e.g. "25"), the REPL normalises it to
    "Q25" before passing it to the graph, so single-digit numbers like "3"
    are not mis-resolved to "reject".
    """

    def _make_confirming_questionnaire(self) -> QuestionnaireState:
        """A questionnaire in awaiting_confirmation state with all answers filled."""
        qs = QuestionnaireState()
        qs.intake_mode = "smart"
        qs.completed = False
        qs.awaiting_confirmation = True
        for i in range(1, TOTAL_QUESTIONS + 1):
            qs.answers[i] = f"answer to Q{i}"
        return qs

    def test_bare_number_edit_normalised_to_q_prefix(self, monkeypatch):
        """Typing '25' after '[2] Edit' sends 'Q25' to the graph, not '25'."""
        qs = self._make_confirming_questionnaire()
        captured = {}

        def _invoke(state):
            captured["last_msg"] = state["messages"][-1].content
            # Simulate graph resetting awaiting_confirmation so REPL exits cleanly
            qs_copy = QuestionnaireState()
            qs_copy.intake_mode = "smart"
            qs_copy.completed = False
            qs_copy.awaiting_confirmation = True
            qs_copy.editing_question = 25
            ai_msg = AIMessage(content="Q25 question text")
            return {**state, "messages": [*state["messages"], ai_msg], "questionnaire": qs_copy}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # "2" picks Edit → REPL shows "Which question?" without invoking graph
        # "25" is the question number → should be normalised to "Q25"
        # "exit" cleanly exits
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["2", "25", "exit"]),
        )
        console, _ = _make_console()
        run_repl(console=console, intake_mode="smart", questionnaire=qs)

        # Graph should have been called once with "Q25"
        assert mock_graph.invoke.call_count == 1
        assert captured["last_msg"] == "Q25"

    def test_answer_during_reask_not_intercepted_as_edit(self, monkeypatch):
        """Regression: typing an answer like '2' while editing_question is set must
        NOT be intercepted as '[2] Edit'.  The intercept should skip when the user
        is in mid-edit (editing_question is not None), so the answer goes to the graph.
        """
        qs = self._make_confirming_questionnaire()

        # Two graph calls expected:
        #   1. "Q6"  → graph sets editing_question=6, asks Q6 question
        #   2. "2"   → graph records "2" as Q6's new answer, editing_question=None
        invoke_args: list[str] = []

        def _invoke(state):
            last = state["messages"][-1].content
            invoke_args.append(last)
            # First call: set editing_question
            if last == "Q6":
                qs_copy = QuestionnaireState()
                qs_copy.intake_mode = "smart"
                qs_copy.completed = False
                qs_copy.awaiting_confirmation = True
                qs_copy.editing_question = 6
                ai_msg = AIMessage(content="Q6. How many engineers?\n\nCurrent answer: 1\n\nEnter your new answer:")
                return {**state, "messages": [*state["messages"], ai_msg], "questionnaire": qs_copy}
            # Second call: record answer, clear editing_question
            qs_copy = QuestionnaireState()
            qs_copy.intake_mode = "smart"
            qs_copy.completed = False
            qs_copy.awaiting_confirmation = True
            qs_copy.editing_question = None
            ai_msg = AIMessage(content="Updated Q6.\n\n# Project Intake Summary")
            return {**state, "messages": [*state["messages"], ai_msg], "questionnaire": qs_copy}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # "2" selects Edit; "6" is question number; "2" is the new answer; "exit" quits
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["2", "6", "2", "exit"]),
        )
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart", questionnaire=qs)

        # Graph should have been invoked exactly twice: once for "Q6", once for "2"
        assert mock_graph.invoke.call_count == 2
        assert invoke_args[0] == "Q6"
        assert invoke_args[1] == "2"
        # Should NOT have shown "Which question?" after the user typed "2" as the answer
        output = _strip_ansi(buf.getvalue())
        assert output.count("Which question") == 1  # only shown once (after "2" → Edit menu)


# ── Export checkpoint tests ───────────────────────────────────────


class TestExportCheckpoint:
    """Tests for the [3] Export option at review checkpoints and intake confirmation.

    Export writes HTML + Markdown but does NOT consume the review/confirm gate —
    the user can still Accept or Edit after exporting (Reject was removed from menu).
    """

    # ── Helpers ─────────────────────────────────────────────────

    def _make_confirming_questionnaire(self) -> QuestionnaireState:
        qs = QuestionnaireState()
        qs.intake_mode = "smart"
        qs.completed = False
        qs.awaiting_confirmation = True
        for i in range(1, TOTAL_QUESTIONS + 1):
            qs.answers[i] = f"answer to Q{i}"
        return qs

    def _make_review_graph(self) -> MagicMock:
        """Mock graph that returns pending_review on first invoke."""
        call_count = [0]

        def _invoke(state):
            call_count[0] += 1
            ai_msg = AIMessage(content="Here is the analysis.")
            return {
                **state,
                "messages": [*state["messages"], ai_msg],
                "pending_review": "project_analyzer",
            }

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        return mock_graph

    # ── Export tests (intake confirmation) ──────────────────────

    def test_export_at_confirm_gate_does_not_invoke_graph(self, monkeypatch, tmp_path):
        """Typing '3' (Export) at the confirm gate writes files but stays on the gate."""
        qs = self._make_confirming_questionnaire()
        mock_graph = MagicMock()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # "3" → Export; "exit" to quit
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["3", "exit"]))
        monkeypatch.chdir(tmp_path)
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart", questionnaire=qs)

        output = _strip_ansi(buf.getvalue())
        # Should mention file paths
        assert ".html" in output or "html" in output.lower()
        assert ".md" in output
        # Graph must NOT have been invoked — export doesn't proceed the pipeline
        mock_graph.invoke.assert_not_called()

    def test_export_at_confirm_gate_writes_files(self, monkeypatch, tmp_path):
        """Export at the confirm gate creates scrum-plan.html and scrum-plan.md."""
        qs = self._make_confirming_questionnaire()
        mock_graph = MagicMock()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["3", "exit"]))
        monkeypatch.chdir(tmp_path)
        run_repl(console=_make_console()[0], intake_mode="smart", questionnaire=qs)

        assert (tmp_path / "scrum-plan.html").exists()
        assert (tmp_path / "scrum-plan.md").exists()

    def test_export_at_confirm_gate_reshows_hint(self, monkeypatch, tmp_path):
        """After export, the confirm hint is re-shown so user can still accept/edit."""
        qs = self._make_confirming_questionnaire()
        mock_graph = MagicMock()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["3", "exit"]))
        monkeypatch.chdir(tmp_path)
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart", questionnaire=qs)

        output = _strip_ansi(buf.getvalue())
        assert "accept" in output.lower()
        assert "edit" in output.lower()

    # ── Review checkpoint ────────────────────────────────────────

    def test_export_at_review_checkpoint_writes_files(self, monkeypatch, tmp_path):
        """Typing '3' at a review checkpoint writes HTML + Markdown files."""
        mock_graph = self._make_review_graph()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        # First input drives intake → graph returns pending_review
        # "3" → Export (intercepted, no graph invoke)
        # "exit" → quit
        qs = QuestionnaireState()
        qs.completed = True
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["start", "3", "exit"]))
        monkeypatch.chdir(tmp_path)
        run_repl(console=_make_console()[0], intake_mode="smart")

        assert (tmp_path / "scrum-plan.html").exists()

    def test_export_at_review_does_not_advance_pipeline(self, monkeypatch, tmp_path):
        """Export at a review checkpoint does not invoke the graph again."""
        mock_graph = self._make_review_graph()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["start", "3", "exit"]))
        monkeypatch.chdir(tmp_path)
        run_repl(console=_make_console()[0], intake_mode="smart")

        # Graph invoked once for "start", NOT again for "4" (export)
        assert mock_graph.invoke.call_count == 1

    def test_export_keyword_works_at_review(self, monkeypatch, tmp_path):
        """Typing 'export' (word) at a review checkpoint also triggers export."""
        mock_graph = self._make_review_graph()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr("scrum_agent.repl.PromptSession", _mock_session_factory(["start", "export", "exit"]))
        monkeypatch.chdir(tmp_path)
        run_repl(console=_make_console()[0], intake_mode="smart")

        assert (tmp_path / "scrum-plan.html").exists()
        assert mock_graph.invoke.call_count == 1

    def test_single_digit_edit_not_rejected(self, monkeypatch):
        """Typing '3' after '[2] Edit' normalises to 'Q3', not 'reject'."""
        qs = self._make_confirming_questionnaire()
        captured = {}

        def _invoke(state):
            captured["last_msg"] = state["messages"][-1].content
            qs_copy = QuestionnaireState()
            qs_copy.intake_mode = "smart"
            qs_copy.completed = False
            qs_copy.awaiting_confirmation = True
            qs_copy.editing_question = 3
            ai_msg = AIMessage(content="Q3 question text")
            return {**state, "messages": [*state["messages"], ai_msg], "questionnaire": qs_copy}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["2", "3", "exit"]),
        )
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart", questionnaire=qs)

        # "3" should NOT have triggered a restart ("Starting over")
        assert "starting over" not in buf.getvalue().lower()
        # Graph should have been called with "Q3"
        assert mock_graph.invoke.call_count == 1
        assert captured["last_msg"] == "Q3"

    def test_out_of_range_number_shows_warning(self, monkeypatch):
        """Typing '99' after '[2] Edit' should print a warning, not invoke graph."""
        qs = self._make_confirming_questionnaire()
        mock_graph = MagicMock()
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["2", "99", "exit"]),
        )
        console, buf = _make_console()
        run_repl(console=console, intake_mode="smart", questionnaire=qs)

        output = _strip_ansi(buf.getvalue())
        assert "out of range" in output or "range" in output
        mock_graph.invoke.assert_not_called()

    def test_q_prefix_still_works(self, monkeypatch):
        """Typing 'Q25' or 'q25' directly still passes through to graph unchanged."""
        qs = self._make_confirming_questionnaire()
        captured = {}

        def _invoke(state):
            captured["last_msg"] = state["messages"][-1].content
            ai_msg = AIMessage(content="Q25 question text")
            qs_copy = QuestionnaireState()
            qs_copy.intake_mode = "smart"
            qs_copy.completed = False
            qs_copy.awaiting_confirmation = True
            qs_copy.editing_question = 25
            return {**state, "messages": [*state["messages"], ai_msg], "questionnaire": qs_copy}

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        monkeypatch.setattr("scrum_agent.repl.create_graph", lambda: mock_graph)
        monkeypatch.setattr(
            "scrum_agent.repl.PromptSession",
            _mock_session_factory(["Q25", "exit"]),
        )
        console, _ = _make_console()
        run_repl(console=console, intake_mode="smart", questionnaire=qs)

        assert mock_graph.invoke.call_count == 1
        assert captured["last_msg"] == "Q25"
