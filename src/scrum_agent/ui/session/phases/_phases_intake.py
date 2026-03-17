"""Description input and intake question phases for the TUI session."""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage
from rich.console import Console
from rich.live import Live

from scrum_agent.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from scrum_agent.prompts.intake import PHASE_LABELS, QUESTION_METADATA, is_choice_question
from scrum_agent.repl._io import _get_active_suggestion
from scrum_agent.repl._questionnaire import (
    _SUGGEST_CONFIRM,
    _resolve_choice_input,
    _resolve_dynamic_choice,
    _split_intake_preamble,
)
from scrum_agent.repl._ui import _predict_next_node
from scrum_agent.ui.session._utils import _invoke_with_animation
from scrum_agent.ui.session.screens._accordion import _build_accordion_question_screen
from scrum_agent.ui.session.screens._screens_input import _build_description_screen, _build_question_screen

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase A: Description Input
# ---------------------------------------------------------------------------


def _phase_description_input(
    live: Live, console: Console, _key, *, dry_run: bool = False
) -> tuple[str, list[str], int, int] | None:
    """Multi-line text input for the project description.

    Returns (description, input_lines, cursor_row, cursor_col) on submit,
    or None if the user pressed Esc. The extra state is used by the caller
    to animate the input box border during LLM processing.

    When dry_run=True, pre-fills an example description so the developer
    can just hit Enter twice to move on quickly.
    """
    logger.debug("_phase_description_input: dry_run=%s", dry_run)
    if dry_run:
        _example = (
            "We're building a mobile app for restaurant reservations. "
            "The team is 4 developers, we use React Native and Node.js, "
            "and we need to launch an MVP in 3 months."
        )
        input_lines = [_example]
        cursor_row = 0
        cursor_col = len(_example)
    else:
        input_lines = [""]
        cursor_row = 0
        cursor_col = 0

    w, h = console.size
    live.update(_build_description_screen(input_lines, cursor_row, cursor_col, width=w, height=h))

    while True:
        key = _key()

        if key == "esc":
            return None
        elif key == "enter":
            # Submit if current line is empty and there's content above
            if not input_lines[cursor_row].strip() and cursor_row > 0:
                # Remove trailing empty lines and join
                while input_lines and not input_lines[-1].strip():
                    input_lines.pop()
                if input_lines:
                    desc = "\n".join(input_lines)
                    logger.info("Description submitted: len=%d", len(desc))
                    return desc, input_lines, cursor_row, cursor_col
                continue
            # Otherwise add a new line
            # Split current line at cursor
            current = input_lines[cursor_row]
            input_lines[cursor_row] = current[:cursor_col]
            input_lines.insert(cursor_row + 1, current[cursor_col:])
            cursor_row += 1
            cursor_col = 0
        elif key == "backspace":
            if cursor_col > 0:
                line = input_lines[cursor_row]
                input_lines[cursor_row] = line[: cursor_col - 1] + line[cursor_col:]
                cursor_col -= 1
            elif cursor_row > 0:
                # Merge with previous line
                prev_len = len(input_lines[cursor_row - 1])
                input_lines[cursor_row - 1] += input_lines[cursor_row]
                input_lines.pop(cursor_row)
                cursor_row -= 1
                cursor_col = prev_len
        elif key == "clear":
            input_lines = [""]
            cursor_row = 0
            cursor_col = 0
        elif key == "up":
            if cursor_row > 0:
                cursor_row -= 1
                cursor_col = min(cursor_col, len(input_lines[cursor_row]))
        elif key == "down":
            if cursor_row < len(input_lines) - 1:
                cursor_row += 1
                cursor_col = min(cursor_col, len(input_lines[cursor_row]))
        elif key == "left":
            if cursor_col > 0:
                cursor_col -= 1
            elif cursor_row > 0:
                cursor_row -= 1
                cursor_col = len(input_lines[cursor_row])
        elif key == "right":
            if cursor_col < len(input_lines[cursor_row]):
                cursor_col += 1
            elif cursor_row < len(input_lines) - 1:
                cursor_row += 1
                cursor_col = 0
        elif key == "shift+left":
            from scrum_agent.ui.session.editor._editor_core import _word_boundary_left

            cursor_col = _word_boundary_left(input_lines[cursor_row], cursor_col)
        elif key == "shift+right":
            from scrum_agent.ui.session.editor._editor_core import _word_boundary_right

            cursor_col = _word_boundary_right(input_lines[cursor_row], cursor_col)
        elif key == "word_backspace":
            from scrum_agent.ui.session.editor._editor_core import _word_boundary_left

            word_start = _word_boundary_left(input_lines[cursor_row], cursor_col)
            line = input_lines[cursor_row]
            input_lines[cursor_row] = line[:word_start] + line[cursor_col:]
            cursor_col = word_start
        elif isinstance(key, str) and key.startswith("paste:"):
            pasted = key[6:]
            paste_lines = pasted.split("\n")
            if paste_lines:
                # Insert first chunk at cursor
                line = input_lines[cursor_row]
                input_lines[cursor_row] = line[:cursor_col] + paste_lines[0]
                cursor_col += len(paste_lines[0])
                # Insert remaining lines
                for pl in paste_lines[1:]:
                    cursor_row += 1
                    input_lines.insert(cursor_row, pl)
                    cursor_col = len(pl)
                # Append remaining text from original line
                if len(paste_lines) > 1:
                    input_lines[cursor_row] += line[cursor_col - len(paste_lines[-1]) :]
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            line = input_lines[cursor_row]
            input_lines[cursor_row] = line[:cursor_col] + key + line[cursor_col:]
            cursor_col += 1
        elif key == "":
            pass  # timeout, no input
        else:
            continue

        w, h = console.size
        live.update(_build_description_screen(input_lines, cursor_row, cursor_col, width=w, height=h))


# ---------------------------------------------------------------------------
# Phase B: Intake Questions
# ---------------------------------------------------------------------------


def _phase_intake_questions(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    export_only: bool,
) -> dict | None:
    """Loop through intake questions in TUI until questionnaire completes or enters review.

    Returns updated graph_state, or None if user cancelled.
    """
    logger.info("_phase_intake_questions started")
    while True:
        qs = graph_state.get("questionnaire")
        if isinstance(qs, QuestionnaireState):
            # Don't exit the intake loop while PTO sub-loop is active —
            # _awaiting_leave_input means we're still collecting leave entries
            # within the confirmation gate, and the user needs to answer PTO
            # questions before seeing the review screen.
            if (qs.completed or qs.awaiting_confirmation) and not qs._awaiting_leave_input:
                logger.info("Intake questions complete: completed=%s", qs.completed)
                return graph_state

        # Check what the next node will be — if not intake, hand off
        next_node = _predict_next_node(graph_state)
        if next_node != "project_intake":
            return graph_state

        # Determine current question context
        question_text = ""
        preamble_lines: list[str] = []
        choices: list[tuple[str, bool]] | None = None
        suggestion: str | None = None
        progress = ""
        phase_label = ""

        ai_msgs = graph_state.get("messages", [])
        if ai_msgs and isinstance(ai_msgs[-1], AIMessage):
            content = ai_msgs[-1].content
            preamble_parts, q_text = _split_intake_preamble(content)
            preamble_lines = preamble_parts
            question_text = q_text

        if isinstance(qs, QuestionnaireState) and not qs.completed:
            cur_q = qs.current_question
            phase = qs.current_phase
            phase_label = PHASE_LABELS.get(phase, "")
            if qs.intake_mode == "standard":
                progress = f"Q{cur_q} of {TOTAL_QUESTIONS}"
            suggestion = _get_active_suggestion(graph_state)

            # Choice options for single-choice questions
            if is_choice_question(cur_q) and cur_q not in qs.probed_questions:
                meta = QUESTION_METADATA.get(cur_q)
                if meta:
                    choices = [(opt, i == meta.default_index) for i, opt in enumerate(meta.options)]

            # Dynamic choices — follow-up probes or node-generated options (e.g. Q27 sprint selection)
            follow_up_choices = qs._follow_up_choices.get(cur_q)
            if follow_up_choices:
                choices = [(opt, False) for opt in follow_up_choices]

        # If no question text from AI, use a generic prompt
        if not question_text:
            question_text = "Tell me about your project \u2014 what are you building and why?"

        # Show question screen and get user input
        answer = _question_input_loop(
            live,
            console,
            _key,
            question_text=question_text,
            choices=choices,
            suggestion=suggestion,
            progress=progress,
            phase_label=phase_label,
            preamble_lines=preamble_lines,
            export_only=export_only,
            graph_state=graph_state,
            questionnaire=qs if isinstance(qs, QuestionnaireState) else None,
        )

        if answer is None:
            return None  # Esc

        # Resolve choice/suggestion input
        if isinstance(qs, QuestionnaireState) and not qs.completed:
            cur_q = qs.current_question
            # Handle suggestion confirmation
            if not answer or answer.lower() in _SUGGEST_CONFIRM:
                sugg = _get_active_suggestion(graph_state)
                if sugg:
                    answer = sugg

            # Resolve numeric choice
            if qs.editing_question is not None:
                answer = _resolve_choice_input(answer, qs.editing_question)
            elif not qs.awaiting_confirmation:
                dynamic_choices = qs._follow_up_choices.get(cur_q)
                if dynamic_choices:
                    answer = _resolve_dynamic_choice(answer, dynamic_choices)
                else:
                    answer = _resolve_choice_input(answer, cur_q)

        if not answer:
            # Empty enter on Q2 repo URL follow-up → treat as skip
            if isinstance(qs, QuestionnaireState) and qs.current_question in qs.probed_questions:
                answer = "skip"
            else:
                continue

        # Invoke graph with the answer
        user_msg = HumanMessage(content=answer)
        invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}

        # Animate the input box border with green/white cycling while the LLM processes.
        # When we have a QuestionnaireState, pass it so _invoke_with_animation uses
        # the accordion screen for the loading animation.
        screen_kwargs: dict = {
            "question_text": question_text,
            "input_value": answer,
            "choices": choices,
            "suggestion": suggestion,
            "progress": progress,
            "phase_label": phase_label,
            "selected_choice": 0,
        }
        if isinstance(qs, QuestionnaireState):
            screen_kwargs["questionnaire"] = qs
        else:
            screen_kwargs["preamble_lines"] = preamble_lines

        logger.debug("Intake graph invoke: Q%s", cur_q if isinstance(qs, QuestionnaireState) else "?")
        result = _invoke_with_animation(
            live,
            console,
            graph,
            invoke_state,
            "Processing your answer",
            "",
            question_screen_kwargs=screen_kwargs,
        )
        if result is None:
            return None

        graph_state = result


def _question_input_loop(
    live: Live,
    console: Console,
    _key,
    *,
    question_text: str,
    choices: list[tuple[str, bool]] | None,
    suggestion: str | None,
    progress: str,
    phase_label: str,
    preamble_lines: list[str] | None,
    export_only: bool,
    graph_state: dict,
    questionnaire: QuestionnaireState | None = None,
) -> str | None:
    """Show a question screen and collect user input.

    Returns the answer string, or None if Esc pressed.
    For export_only mode, returns a synthetic answer.

    When questionnaire is provided, uses the accordion-style screen showing
    all 26 questions at once. Otherwise falls back to the single-question screen.
    """
    if export_only:
        # Auto-answer: use suggestion or "continue"
        # Late import so tests can patch scrum_agent.ui.session._get_active_suggestion
        from scrum_agent.ui import session as _session_mod

        sugg = _session_mod._get_active_suggestion(graph_state)
        return sugg or "continue"

    # Pre-fill input with existing answer when editing a previously answered question
    input_value = ""
    if questionnaire is not None and questionnaire.editing_question is not None:
        existing = questionnaire.answers.get(questionnaire.editing_question, "")
        if existing:
            input_value = existing
    cursor_pos = len(input_value)  # cursor starts at end of pre-filled text
    selected_choice = 0
    scroll_offset = 0
    use_accordion = questionnaire is not None

    def _render():
        w, h = console.size
        if use_accordion:
            return _build_accordion_question_screen(
                question_text,
                input_value,
                questionnaire,
                choices=choices,
                suggestion=suggestion,
                progress=progress,
                phase_label=phase_label,
                selected_choice=selected_choice,
                scroll_offset=scroll_offset,
                width=w,
                height=h,
                cursor_pos=cursor_pos,
                edit_hint="Enter/Ctrl+S submit \u00b7 Esc cancel",
            )
        return _build_question_screen(
            question_text,
            input_value,
            choices=choices,
            suggestion=suggestion,
            progress=progress,
            phase_label=phase_label,
            preamble_lines=preamble_lines,
            selected_choice=selected_choice,
            width=w,
            height=h,
        )

    live.update(_render())

    while True:
        key = _key()

        if key == "esc":
            return None
        elif key in ("enter", "ctrl+s"):
            # Enter or Ctrl+S submits the answer
            if choices:
                return choices[selected_choice][0]
            return input_value
        elif key in ("up", "scroll_up") and choices:
            selected_choice = (selected_choice - 1) % len(choices)
        elif key in ("down", "scroll_down") and choices:
            selected_choice = (selected_choice + 1) % len(choices)
        elif choices:
            # Choice questions are arrow-key only — no typing
            continue
        elif key == "left":
            cursor_pos = max(0, cursor_pos - 1)
        elif key == "right":
            cursor_pos = min(len(input_value), cursor_pos + 1)
        elif key == "backspace":
            if cursor_pos > 0:
                input_value = input_value[: cursor_pos - 1] + input_value[cursor_pos:]
                cursor_pos -= 1
        elif key == "clear":
            input_value = ""
            cursor_pos = 0
        elif key == "shift+left":
            from scrum_agent.ui.session.editor._editor_core import _word_boundary_left

            cursor_pos = _word_boundary_left(input_value, cursor_pos)
        elif key == "shift+right":
            from scrum_agent.ui.session.editor._editor_core import _word_boundary_right

            cursor_pos = _word_boundary_right(input_value, cursor_pos)
        elif key == "word_backspace":
            from scrum_agent.ui.session.editor._editor_core import _word_boundary_left

            word_start = _word_boundary_left(input_value, cursor_pos)
            input_value = input_value[:word_start] + input_value[cursor_pos:]
            cursor_pos = word_start
        elif isinstance(key, str) and key.startswith("paste:"):
            pasted = key[6:]
            input_value = input_value[:cursor_pos] + pasted + input_value[cursor_pos:]
            cursor_pos += len(pasted)
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            input_value = input_value[:cursor_pos] + key + input_value[cursor_pos:]
            cursor_pos += 1
        elif key == "":
            pass
        else:
            continue

        live.update(_render())
