"""LangGraph node functions for the Scrum Agent.

# See README: "Agentic Blueprint Reference" — two core nodes (call_model + tool_node)
# See README: "The ReAct Loop" — Thought → Action → Observation pattern

Node functions are the building blocks of a LangGraph graph. Each node is a
plain Python function that takes the current state and returns a partial state
update. LangGraph merges the returned dict into the existing state using the
reducers defined on the state schema (e.g. add_messages for the messages list).

This file is kept separate from graph wiring (Step 6) so that node functions
remain unit-testable — they are pure functions of state, with no dependency
on the graph object itself.
"""

import dataclasses
import json
import logging
import math
import re
from collections.abc import Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END

from scrum_agent.agent.llm import get_llm
from scrum_agent.agent.state import (
    DOD_ITEMS,
    PHASE_QUESTION_RANGES,
    TOTAL_QUESTIONS,
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    ProjectAnalysis,
    PromptQualityRating,
    QuestionnairePhase,
    QuestionnaireState,
    ReviewDecision,
    ScrumState,
    Sprint,
    StoryPointValue,
    Task,
    TaskLabel,
    UserStory,
)
from scrum_agent.prompts.analyzer import get_analyzer_prompt
from scrum_agent.prompts.feature_generator import get_feature_generator_prompt
from scrum_agent.prompts.intake import (
    ADAPTIVE_QUESTION_TEMPLATES,
    CONDITIONAL_ESSENTIALS,
    ESSENTIAL_QUESTIONS,
    FOLLOW_UP_TEMPLATES,
    INTAKE_QUESTIONS,
    PHASE_INTROS,
    PHASE_LABELS,
    Q2_CONSTRAINT_HINTS,
    Q2_INFERENCE_KEYWORDS,
    Q2_TO_Q15_MAP,
    Q12_SERVICE_KEYWORDS,
    Q13_INFRA_KEYWORDS,
    QUESTION_DEFAULTS,
    QUESTION_IMPROVEMENT_HINTS,
    QUESTION_METADATA,
    QUESTION_SHORT_LABELS,
    QUICK_ESSENTIALS,
    QUICK_FALLBACK_DEFAULTS,
    SCRUM_MD_HINT,
    SMART_ESSENTIALS,
    AnswerSource,
    ValidationWarning,
    is_choice_question,
)
from scrum_agent.prompts.sprint_planner import get_sprint_planner_prompt
from scrum_agent.prompts.story_writer import MAX_STORIES_PER_FEATURE, MIN_STORIES_PER_FEATURE, get_story_writer_prompt
from scrum_agent.prompts.system import get_system_prompt  # noqa: E402 — direct submodule imports avoid circular import
from scrum_agent.prompts.task_decomposer import get_task_decomposer_prompt
from scrum_agent.tools import detect_platform

logger = logging.getLogger(__name__)


def _is_llm_auth_or_billing_error(exc: Exception) -> bool:
    """Check whether an exception is an LLM authentication or billing error.

    These errors should NOT be silently swallowed — the user needs to know
    their API key is invalid or their balance is too low, otherwise every
    LLM feature silently degrades and the app appears broken.

    Covers Anthropic, OpenAI, and Google provider error classes.
    """
    # Anthropic: AuthenticationError, PermissionDeniedError, BadRequestError (billing)
    try:
        import anthropic

        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return True
        if isinstance(exc, anthropic.BadRequestError) and "credit balance" in str(exc).lower():
            return True
    except ImportError:
        pass

    # OpenAI: AuthenticationError (401), PermissionDeniedError (403)
    try:
        import openai

        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return True
    except ImportError:
        pass

    # Google: catch by message pattern (no typed billing error class)
    msg = str(exc).lower()
    if "api key" in msg and ("invalid" in msg or "not valid" in msg):
        return True
    if "quota" in msg and "exceeded" in msg:
        return True

    return False


# ---------------------------------------------------------------------------
# High-risk tool constants
# ---------------------------------------------------------------------------

# These are write operations that modify external systems (Jira, Confluence).
# They require explicit user confirmation before execution — the agent must ask
# and receive a "yes" before the graph routes to the ToolNode.
# See README: "Guardrails" — human-in-the-loop pattern (Tool layer)
_HIGH_RISK_TOOLS: frozenset[str] = frozenset(
    {
        "jira_create_epic",
        "jira_create_story",
        "jira_create_sprint",
        "confluence_create_page",
        "confluence_update_page",
    }
)


def _user_confirmed(text: str) -> bool:
    """Return True if the text looks like an affirmative confirmation.

    Used by should_continue to detect whether the user approved a high-risk
    tool call in the preceding HumanMessage.
    """
    lowered = text.strip().lower()
    affirm_exact = {"yes", "y", "ok", "sure", "confirm", "go", "proceed", "yep", "yup"}
    if lowered in affirm_exact:
        return True
    affirm_prefixes = ("yes ", "y ", "ok ", "sure", "go ahead", "proceed", "confirm", "please go")
    return any(lowered.startswith(p) for p in affirm_prefixes)


def make_call_model(tools: list[BaseTool]) -> Callable[[ScrumState], dict[str, list[BaseMessage]]]:
    """Return a call_model node function with the given tools bound to the LLM.

    # See README: "Agentic Blueprint Reference" — bind_tools wires tools into the LLM
    # See README: "Tools" — tool types, @tool decorator
    #
    # bind_tools() is a LangChain method on ChatModels. It takes a list of
    # tool definitions (functions decorated with @tool) and returns a new Runnable
    # that includes those tool schemas in every API call to Claude.
    #
    # Why does this matter?
    # Without bind_tools(), the LLM has NO knowledge of available tools. It can
    # never produce tool_calls in its response — so should_continue always routes
    # to END and the ReAct loop never invokes any tools.
    #
    # The binding is LAZY — it happens on the first invocation of the returned
    # closure, not at factory / graph-creation time. This is important for
    # testability: create_graph() can compile the graph structure in CI without
    # an ANTHROPIC_API_KEY; the key is only required when the node is actually
    # called (i.e. during a real agent run or in tests that mock get_llm).
    #
    # Why a factory function and not a class?
    # A simple closure is the idiomatic LangGraph pattern for parameterised nodes.
    # The graph wires it as: graph.add_node("agent", make_call_model(tools))
    """
    _bound_llm = None  # initialised lazily on first call

    def call_model_with_tools(state: ScrumState) -> dict[str, list[BaseMessage]]:
        """LangGraph node: invoke the LLM with bound tools."""
        nonlocal _bound_llm
        if _bound_llm is None:
            # bind_tools() returns a new Runnable (RunnableBinding) that wraps
            # the LLM and injects the tool schemas into every API request.
            # Claude reads these schemas to know what tools are available and
            # generates tool_calls when it wants to use one.
            _bound_llm = get_llm().bind_tools(tools)
        system_message = SystemMessage(content=get_system_prompt())
        all_messages = [system_message, *state["messages"]]
        response = _bound_llm.invoke(all_messages)
        return {"messages": [response]}

    return call_model_with_tools


def call_model(state: ScrumState) -> dict[str, list[BaseMessage]]:
    """LangGraph node: invoke the LLM with the current conversation.

    # See README: "Agentic Blueprint Reference" — this is the "agent" node
    # See README: "The ReAct Loop" — this node is the "Thought" step
    #
    # How this works:
    # 1. Build a SystemMessage from the Scrum Master prompt — this sets the
    #    LLM's persona and constraints for every call.
    # 2. Prepend it to the existing conversation messages from state.
    # 3. Call the LLM with the full message list.
    # 4. Return {"messages": [response]} — LangGraph's add_messages reducer
    #    will APPEND this to the existing state["messages"], not replace it.
    #
    # Why the SystemMessage is injected here (not stored in state):
    # - System messages are infrastructure, not conversation history —
    #   they shouldn't pollute the user/assistant message list.
    # - Different nodes can inject different system context later
    #   (e.g. a story_writer node could add DoD rules to its system prompt).
    # - Keeps state clean: messages only contains user/assistant turns.
    #
    # Why invoke() and not stream():
    # invoke() returns the complete response in one call. Streaming is handled
    # at the REPL layer (repl.py) by iterating over graph.stream(), not here.
    # The node's job is to produce the response; the UI decides how to display it.

    Args:
        state: The current LangGraph state containing the conversation messages.

    Returns:
        A dict with a single "messages" key containing the LLM's response
        in a list. The add_messages reducer on ScrumState will append this
        to the existing conversation history.
    """
    system_message = SystemMessage(content=get_system_prompt())

    # Prepend system prompt to conversation history for each call.
    # The system message is NOT stored in state — it's injected fresh
    # each invocation so different nodes can use different system context.
    all_messages = [system_message, *state["messages"]]

    response = get_llm().invoke(all_messages)

    # Return single-item list — the add_messages reducer on ScrumState["messages"]
    # will append this response to the existing conversation history.
    # See README: "Agentic Blueprint Reference" — node return format
    return {"messages": [response]}


def should_continue(state: ScrumState) -> str:
    """Route after call_model: continue to tools, request human review, or end.

    # See README: "Agentic Blueprint Reference" — conditional edges
    # See README: "The ReAct Loop" — this is the decision point
    # See README: "Guardrails" — human-in-the-loop pattern
    #
    # Three-way routing:
    #   no tool_calls        → END             (LLM is done; return response)
    #   low/medium-risk      → "tools"         (auto-execute via ToolNode)
    #   high-risk            → "human_review"  (pause for user confirmation)
    #
    # High-risk tools are Jira/Confluence write operations that create or modify
    # external records and cannot be easily undone. _HIGH_RISK_TOOLS lists them.
    #
    # Confirmation detection: if the immediately preceding messages show that
    # the user already confirmed (human_review asked → user said "yes"), route
    # directly to "tools" on the second trip through this function.
    # Pattern: [..., AIMessage(confirmation request), HumanMessage("yes"), AIMessage(tool_calls)]
    """
    last_message = state["messages"][-1]

    if not last_message.tool_calls:
        return END

    # Check if any requested tool is high-risk.
    tool_names = {tc["name"] for tc in last_message.tool_calls}
    if not (tool_names & _HIGH_RISK_TOOLS):
        # All tools are low/medium risk — auto-execute.
        return "tools"

    # High-risk tool detected. Check for a prior confirmation in message history.
    # After human_review runs, the conversation has:
    #   messages[-3] = AIMessage(confirmation request, no tool_calls)
    #   messages[-2] = HumanMessage("yes")
    #   messages[-1] = AIMessage(new tool_calls)  ← current
    messages = state["messages"]
    if len(messages) >= 3:
        prev_ai = messages[-3]
        prev_human = messages[-2]
        if (
            isinstance(prev_ai, AIMessage)
            and not getattr(prev_ai, "tool_calls", None)  # Was a confirmation request
            and isinstance(prev_human, HumanMessage)
            and _user_confirmed(prev_human.content)
        ):
            return "tools"

    return "human_review"


def human_review(state: ScrumState) -> dict[str, list[BaseMessage]]:
    """LangGraph node: pause before high-risk write operations for user confirmation.

    # See README: "Guardrails" — human-in-the-loop pattern
    #
    # This node is reached when should_continue detects a high-risk tool call
    # (Jira/Confluence write). It replaces the tool_calls AIMessage with a
    # plain-text confirmation request so the graph routes to END and the REPL
    # shows the user what the agent wants to do.
    #
    # The ID trick: add_messages (the LangGraph reducer) replaces messages that
    # share the same ID rather than appending a new one. By returning a new
    # AIMessage with the same ID as the tool_calls message, we replace it in
    # state — this prevents two consecutive AIMessages, which would violate
    # Anthropic's alternating-message rule. In the rare case where the original
    # message has no ID (some test scenarios), langchain-anthropic merges
    # consecutive same-role messages before the API call, so it's still safe.
    #
    # Flow after this node:
    #   → END → REPL shows confirmation text
    #   → user types "yes" → call_model re-generates the tool call
    #   → should_continue detects prior confirmation → "tools" (auto-executes)
    """
    last_message = state["messages"][-1]
    tool_calls = last_message.tool_calls

    lines = ["I'd like to perform the following write operation(s):\n"]
    for tc in tool_calls:
        lines.append(f"  \u2022 **{tc['name']}**")
        for k, v in tc["args"].items():
            lines.append(f"    - {k}: {v!r}")
    lines.append("\nPlease confirm \u2014 type **yes** to proceed or **no** to cancel.")

    # Replace the tool_calls AIMessage with a plain confirmation request.
    # Same ID → add_messages replaces; None ID → add_messages appends (safe fallback).
    # See README: "Guardrails" — human-in-the-loop pattern
    replacement = AIMessage(id=last_message.id, content="\n".join(lines))
    return {"messages": [replacement]}


# ── Intake questionnaire ─────────────────────────────────────────────

# ── Adaptive skip helpers ────────────────────────────────────────────
# See README: "Project Intake Questionnaire" — adaptive skip logic
#
# When the user's initial description (typed at scrum> before Q1) already
# answers some intake questions, these helpers extract those answers and
# skip the corresponding questions. This avoids asking the user to repeat
# information they already provided.
#
# Why LLM-powered analysis (not keyword matching)?
# Natural language like "3 engineers working on React" answers Q6 (team size)
# and Q11 (tech stack), but no keyword pattern would reliably catch Q6. The
# LLM understands context and maps descriptions to specific questions. The
# LLM call only happens once (on the first project_intake call) so the
# latency cost is minimal.


def _extract_answers_from_description(description: str) -> dict[int, str]:
    """Use the LLM to extract answers to intake questions from a project description.

    Sends the description + all 30 questions to the LLM with a structured prompt
    asking for a JSON object mapping question numbers to extracted answers.

    Args:
        description: The user's initial project description.

    Returns:
        A dict mapping question numbers (1–30) to extracted answer strings.
        Returns {} on empty description, bad JSON, or any exception.
    """
    if not description or not description.strip():
        return {}

    # Build the question list for the prompt
    question_list = "\n".join(f"Q{num}: {text}" for num, text in INTAKE_QUESTIONS.items())

    prompt = (
        "You are analyzing a project description to extract answers to intake questions.\n\n"
        f'Project description:\n"{description}"\n\n'
        f"Questions:\n{question_list}\n\n"
        "For each question that the description EXPLICITLY and CLEARLY answers, return a JSON object "
        "mapping the question number (as a string key) to the extracted answer.\n\n"
        "STRICT RULES:\n"
        "- Only include questions where the user DIRECTLY stated the answer.\n"
        "- Q2 (project type): INFER from strong signals like 'refactor', 'migrate', 'legacy' → Existing codebase; "
        "'from scratch', 'new project', 'greenfield' → Greenfield.\n"
        "- Q12 (integrations): EXTRACT named services (e.g. 'Stripe', 'Auth0', 'Firebase').\n"
        "- Q13 (constraints): EXTRACT named infra (e.g. 'Kubernetes', 'AWS', 'microservices').\n"
        "- Do NOT infer Q11 (tech stack) from product/service names (e.g. 'Teleport', 'Kubernetes').\n"
        "- Do NOT infer Q4 (end-state) from vague goals like 'improve security'.\n"
        "- Do NOT extract Q3 (problem/users) unless the description names specific users or problems.\n"
        "- When in doubt, leave the question OUT — the user will be asked directly.\n\n"
        "Return ONLY the JSON object, no other text.\n\n"
        'Example: {"1": "A todo app for tracking tasks", "6": "3 engineers"}'
    )

    try:
        # Single LLM call with low temperature for deterministic extraction.
        # See README: "Agentic Blueprint Reference" — using the LLM outside the main graph
        response = get_llm(temperature=0.0).invoke([HumanMessage(content=prompt)])
        raw = response.content

        # Strip markdown code fences that LLMs sometimes wrap JSON in
        # (e.g. ```json\n{...}\n```)
        raw = raw.strip()
        if raw.startswith("```"):
            # Remove opening fence (```json or ```)
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

        parsed = json.loads(raw)

        # Validate: must be a dict, keys must be valid question numbers, values non-empty strings
        if not isinstance(parsed, dict):
            return {}

        result: dict[int, str] = {}
        for key, value in parsed.items():
            try:
                q_num = int(key)
            except (ValueError, TypeError):
                continue
            if 1 <= q_num <= TOTAL_QUESTIONS and isinstance(value, str) and value.strip():
                result[q_num] = value.strip()

        return result

    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            raise
        # Graceful fallback: if anything goes wrong (bad JSON, LLM timeout,
        # network error), return empty dict. The questionnaire will ask all
        # 26 questions as usual — the feature never breaks the existing flow.
        logger.debug("Failed to extract answers from description, falling back to full questionnaire", exc_info=True)
        return {}


def _keyword_extract_fallback(description: str, extracted: dict[int, str]) -> dict[int, str]:
    """Deterministic keyword-based extraction fallback after LLM extraction.

    # See README: "Project Intake Questionnaire" — smart intake
    #
    # Scans the description for strong keyword signals for Q2 (project type),
    # Q12 (integrations), and Q13 (architectural constraints). Only fills
    # questions not already extracted by the LLM — LLM-extracted values
    # always take priority.

    Args:
        description: The user's project description text.
        extracted: The LLM-extracted answers dict (mutated in place with additions).

    Returns:
        The same extracted dict with any new keyword-based additions.
    """
    desc_lower = description.lower()

    # Q2: project type from keywords
    if 2 not in extracted:
        for keyword, value in Q2_INFERENCE_KEYWORDS.items():
            if keyword in desc_lower:
                extracted[2] = value
                break

    # Q12: service/integration names
    if 12 not in extracted:
        found_services = [kw for kw in Q12_SERVICE_KEYWORDS if kw in desc_lower]
        if found_services:
            # Capitalize service names for readability
            extracted[12] = ", ".join(s.capitalize() for s in sorted(found_services))

    # Q13: infrastructure/architecture keywords
    if 13 not in extracted:
        found_infra = [kw for kw in Q13_INFRA_KEYWORDS if kw in desc_lower]
        if found_infra:
            extracted[13] = ", ".join(sorted(found_infra))

    return extracted


def _next_unskipped_question(current: int, skipped: set[int]) -> int | None:
    """Find the next question number that hasn't been skipped.

    Scans forward from `current` through TOTAL_QUESTIONS.

    Args:
        current: The question number to start scanning from (inclusive).
        skipped: Set of question numbers to skip over.

    Returns:
        The first question number >= current not in skipped, or None if all
        remaining questions are skipped.
    """
    for q_num in range(current, TOTAL_QUESTIONS + 1):
        if q_num not in skipped:
            return q_num
    return None


def _build_extraction_summary(extracted: dict[int, str]) -> str:
    """Format what was extracted from the initial description for user review.

    Shows each extracted question + answer so the user can see what was
    auto-detected and verify correctness before continuing.

    Args:
        extracted: Dict mapping question numbers to extracted answers.

    Returns:
        A formatted string summarizing the extracted answers.
    """
    lines = ["I extracted the following from your description:\n"]
    for q_num in sorted(extracted):
        question = INTAKE_QUESTIONS[q_num]
        answer = extracted[q_num]
        lines.append(f"  **Q{q_num}.** {question}")
        lines.append(f"  > {answer}\n")
    return "\n".join(lines)


# ── Skip intent detection ────────────────────────────────────────────
# See README: "Project Intake Questionnaire" — adaptive behavior
#
# Deterministic keyword matching for "skip" / "I don't know" responses.
# No LLM call needed — these are unambiguous user signals. Exact matches
# use a frozenset for O(1) lookup; substring matches use a tuple for
# ordered iteration via `in`.

_SKIP_EXACT: frozenset[str] = frozenset({"skip", "pass", "next", "n/a", "na", "idk", "-", "none"})

_SKIP_SUBSTRINGS: tuple[str, ...] = (
    "i don't know",
    "i dont know",
    "not sure",
    "unsure",
    "no idea",
    "don't know",
    "dont know",
    "skip this",
    "pass on this",
    "move on",
    "no answer",
)


def _is_skip_intent(message: str) -> bool:
    """Detect whether a user message is a skip/don't-know signal.

    Uses deterministic keyword matching — no LLM call. Normalized to
    lowercase + stripped before checking.

    Args:
        message: The raw user message text.

    Returns:
        True if the message indicates the user wants to skip the question.
    """
    normalized = message.strip().lower()
    if normalized in _SKIP_EXACT:
        return True
    return any(phrase in normalized for phrase in _SKIP_SUBSTRINGS)


# ── Defaults intent detection ────────────────────────────────────────
# See README: "Project Intake Questionnaire" — batch defaults
#
# When the user types "defaults" during the questionnaire, all remaining
# questions in the current phase are answered with their defaults (from
# QUESTION_METADATA for choice Qs, QUESTION_DEFAULTS for free-text).
# Essential questions with no default are skipped over. This lets the
# user fast-forward through a phase without answering each question.

_DEFAULTS_EXACT: frozenset[str] = frozenset({"defaults", "default", "use defaults"})


def _is_defaults_intent(message: str) -> bool:
    """Detect whether a user message is a 'use defaults' signal.

    Uses deterministic keyword matching — no LLM call.

    Args:
        message: The raw user message text.

    Returns:
        True if the message indicates the user wants to apply defaults.
    """
    return message.strip().lower() in _DEFAULTS_EXACT


def _batch_defaults_for_phase(questionnaire: QuestionnaireState) -> tuple[list[str], int]:
    """Apply defaults to all remaining questions in the current phase.

    # See README: "Project Intake Questionnaire" — batch defaults
    #
    # Iterates from current_question through the end of the current phase.
    # For each question:
    #   - Choice Q with default_index → use option at default_index
    #   - Free-text Q with QUESTION_DEFAULTS entry → use that default
    #   - Essential Q with no default → skip (flagged in summary)
    #
    # Returns a list of summary lines and the count of questions defaulted.

    Args:
        questionnaire: The mutable QuestionnaireState to update.

    Returns:
        A tuple of (summary_lines, count_defaulted).
    """
    from scrum_agent.agent.state import PHASE_QUESTION_RANGES

    phase = questionnaire.current_phase
    _start, end = PHASE_QUESTION_RANGES[phase]
    summary_lines: list[str] = []
    count = 0

    for q_num in range(questionnaire.current_question, end + 1):
        if q_num in questionnaire.answers or q_num in questionnaire.skipped_questions:
            continue  # already answered or skipped

        meta = QUESTION_METADATA.get(q_num)
        if meta and meta.default_index is not None:
            # Choice question with a default — use the option
            default_val = meta.options[meta.default_index]
            questionnaire.answers[q_num] = default_val
            questionnaire.defaulted_questions.add(q_num)
            questionnaire.answer_sources[q_num] = AnswerSource.DEFAULTED
            summary_lines.append(f"  Q{q_num}: {default_val}")
            count += 1
        elif q_num in QUESTION_DEFAULTS:
            # Free-text question with a default
            default_val = QUESTION_DEFAULTS[q_num]
            questionnaire.answers[q_num] = default_val
            questionnaire.defaulted_questions.add(q_num)
            questionnaire.answer_sources[q_num] = AnswerSource.DEFAULTED
            summary_lines.append(f"  Q{q_num}: {default_val}")
            count += 1
        else:
            # Essential question — no default, flag as skipped
            questionnaire.skipped_questions.add(q_num)

    return summary_lines, count


# ── Confirm intent detection ─────────────────────────────────────────
# See README: "Project Intake Questionnaire" — confirmation gate
#
# After the last question is answered, the intake node shows a summary and
# asks the user to confirm before proceeding to the main agent. This helper
# detects confirmation signals — deterministic keyword matching, same
# pattern as _is_skip_intent().

_CONFIRM_KEYWORDS: frozenset[str] = frozenset(
    {
        "confirm",
        "confirmed",
        "accept",
        "accepted",
        "yes",
        "y",
        "looks good",
        "lgtm",
        "proceed",
        "go ahead",
        "ok",
        "okay",
    }
)


def _is_confirm_intent(text: str) -> bool:
    """Detect whether a user message is a confirmation signal.

    Uses deterministic keyword matching — no LLM call. Normalized to
    lowercase + stripped before checking.

    Args:
        text: The raw user message text.

    Returns:
        True if the message indicates the user wants to confirm and proceed.
    """
    return text.strip().lower() in _CONFIRM_KEYWORDS


# ── Edit intent detection ─────────────────────────────────────────
# See README: "Project Intake Questionnaire" — edit flow
#
# During the confirmation gate the user can reference a question by
# number to revise their answer. Two modes:
#   - Inline: "Q6: 5 engineers" → update immediately, re-show summary
#   - Re-ask: "Q6" or "edit Q6" → show question, collect new answer
#
# Deterministic regex matching (same pattern as _is_skip_intent and
# _is_confirm_intent) — no LLM call needed.

_EDIT_PATTERN = re.compile(
    r"^(?:edit|change|revise|update)?\s*"
    r"(?:q(?:uestion)?\s*)?"  # Q prefix is optional — bare numbers like "25" also match
    r"(\d{1,2})"
    r"(?:\s*[:=]\s*(.+))?$",
    re.IGNORECASE,
)


def _parse_edit_intent(text: str) -> tuple[int, str | None] | None:
    """Detect whether a user message is an edit request for a specific question.

    Supports formats like:
    - "Q6" or "q6" → re-ask Q6
    - "6" or "25" → bare number, re-ask that question (Q prefix optional)
    - "edit Q6" / "change Q6" / "revise Q6" / "update Q6" → re-ask Q6
    - "edit 6" / "change 25" → re-ask without Q prefix
    - "Q6: 5 engineers" or "Q6 = new answer" → inline edit Q6
    - "question 6: new answer" → inline edit Q6

    Args:
        text: The raw user message text.

    Returns:
        A tuple of (question_number, inline_answer_or_None) if an edit intent
        is detected, or None if the message is not an edit request.
    """
    match = _EDIT_PATTERN.match(text.strip())
    if not match:
        return None

    q_num = int(match.group(1))
    if not (1 <= q_num <= TOTAL_QUESTIONS):
        return None

    inline_answer = match.group(2)
    if inline_answer is not None:
        inline_answer = inline_answer.strip()
        if not inline_answer:
            inline_answer = None

    return (q_num, inline_answer)


_EDIT_HELP = (
    "To edit an answer, use one of these formats:\n"
    "- **Q6: new answer** — update Q6 immediately\n"
    "- **edit Q6** or just **Q6** — re-answer Q6 interactively\n\n"
)


# ── Velocity extraction helpers ──────────────────────────────────────
# See README: "Scrum Standards" — velocity and capacity planning
#
# After the user confirms the intake summary, we parse team size (Q6)
# and velocity (Q9) into typed ScrumState fields. Downstream nodes
# (sprint planner, Phase 5) need these numeric values to allocate
# stories to sprints without exceeding capacity.
#
# Deterministic parsing (not LLM) — same philosophy as _is_skip_intent().
# A simple regex extracts the first integer from natural-language answers
# like "3 engineers" or "20 points per sprint". No LLM call needed for
# unambiguous numeric extraction.

_VELOCITY_PER_ENGINEER = 5


def _parse_first_int(text: str) -> int | None:
    """Extract the first integer from a natural-language string.

    Uses a simple regex to find the first sequence of digits. This is
    sufficient for answers like "3 engineers", "About 5 people", or
    "velocity is 20 points" where the number is unambiguous.

    Args:
        text: The raw string to extract an integer from.

    Returns:
        The first integer found, or None if no digits are present.
    """
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def _parse_jira_team_size(questionnaire: QuestionnaireState) -> int | None:
    """Extract the Jira org team size if available.

    Primary source: ``_jira_org_team_size`` transient field — set during
    smart intake even when Jira velocity is zero (team size is derived from
    sub-task assignees independently of story point completion).

    Fallback: parse Q9 answer text for "from Jira: … N team member(s)".

    The Jira team size is the total headcount on the board — used to cap
    the "increase team" recommendation so we never suggest more engineers
    than actually exist.
    """
    # Primary: transient field (always set when Jira is reachable)
    if getattr(questionnaire, "_jira_org_team_size", None):
        return questionnaire._jira_org_team_size
    # Fallback: parse Q9 text (for sessions started before this field existed)
    q9 = questionnaire.answers.get(9, "")
    if "from Jira" not in q9 and "from jira" not in q9:
        return None
    m = re.search(r"(\d+)\s+team member", q9)
    return int(m.group(1)) if m else None


def _extract_team_and_velocity(questionnaire: QuestionnaireState) -> dict:
    """Parse team size and velocity from intake answers, calculating defaults.

    # See README: "Scrum Standards" — velocity and capacity planning
    #
    # The system prompt documents the rule: "When team velocity is unknown,
    # use 5 story points per engineer per sprint as the baseline estimate."
    # This function implements that calculation deterministically.
    #
    # Why at confirmation time (not inside the sprint planner)?
    # The confirmation gate is the natural boundary — answers are finalized,
    # and the state update can include the extracted numeric fields alongside
    # the questionnaire. This keeps the sprint planner focused on allocation
    # rather than parsing.

    Args:
        questionnaire: The completed questionnaire with all answers.

    Returns:
        A dict with team_size, velocity_per_sprint, and _velocity_was_calculated
        (transient flag for the confirmation message). Returns {} if Q6 has
        no parseable team size — confirmation still works, just without
        velocity info.
    """
    # Parse Q6 → team_size
    q6_answer = questionnaire.answers.get(6)
    if not q6_answer:
        return {}
    team_size = _parse_first_int(q6_answer)
    if not team_size:  # None or 0
        return {}

    # When Jira per-dev velocity is available, recompute the feature velocity
    # from the stored per-dev rate × current Q6 team size. This handles the
    # case where the user edits Q6 at the confirmation gate — the velocity
    # automatically adjusts to the new team size.
    # See README: "Scrum Standards" — capacity planning
    if questionnaire._jira_per_dev_velocity is not None:
        velocity = round(questionnaire._jira_per_dev_velocity * team_size)
        if velocity <= 0:
            velocity = team_size * _VELOCITY_PER_ENGINEER
        return {
            "team_size": team_size,
            "velocity_per_sprint": velocity,
            "_velocity_was_calculated": False,
        }

    # Parse Q9 → velocity. Skip if Q9 was defaulted (the default text says
    # "No historical velocity — will use default of 5 points per engineer
    # per sprint"), missing, or has no parseable number.
    velocity = None
    q9_answer = questionnaire.answers.get(9)
    if q9_answer and 9 not in questionnaire.defaulted_questions:
        velocity = _parse_first_int(q9_answer)
        if velocity is not None and velocity <= 0:
            velocity = None

    velocity_was_calculated = velocity is None
    if velocity is None:
        velocity = team_size * _VELOCITY_PER_ENGINEER

    return {
        "team_size": team_size,
        "velocity_per_sprint": velocity,
        "_velocity_was_calculated": velocity_was_calculated,
    }


def _is_jira_configured() -> bool:
    """Check whether all 4 Jira environment variables are set.

    # See README: "Tools" — tool types, Jira integration
    #
    # Returns True only when JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN,
    # and JIRA_PROJECT_KEY are all non-empty. Used by the intake node
    # to decide whether to ask Q27 (sprint selection) interactively.
    """
    from scrum_agent.config import get_jira_base_url, get_jira_email, get_jira_project_key, get_jira_token

    return bool(get_jira_base_url() and get_jira_email() and get_jira_token() and get_jira_project_key())


def _fetch_jira_velocity() -> dict | None:
    """Fetch avg velocity AND team size from last 3 closed sprints in Jira.

    # See README: "Scrum Standards" — capacity planning
    #
    # Thin wrapper around the jira_fetch_velocity @tool in tools/jira.py.
    # The tool handles all Jira connection logic and returns a JSON string;
    # this wrapper parses it back to a dict for the intake node.
    # Same pattern as _prepare_bank_holiday_choices calling detect_bank_holidays.invoke().

    Returns:
        Dict with keys {team_velocity, jira_team_size, per_dev_velocity},
        or None if Jira is unavailable or has no data.
        When velocity is zero but team size is available, the dict includes
        a ``velocity_error`` key — callers should skip velocity extraction
        but still use ``jira_team_size``.
    """
    try:
        from scrum_agent.tools.jira import jira_fetch_velocity

        result = jira_fetch_velocity.invoke({})
        if result.startswith("Error"):
            logger.debug("jira_fetch_velocity returned: %s", result)
            return None
        data = json.loads(result)
        if "velocity_error" in data:
            logger.debug("jira_fetch_velocity: zero velocity but team_size=%s", data.get("jira_team_size"))
        return data
    except Exception:
        logger.debug("Failed to fetch Jira velocity", exc_info=True)
    return None


def _fetch_active_sprint_number() -> tuple[int | None, str | None, str]:
    """Connect to Jira and return the current active sprint number and start date.

    # See README: "Scrum Standards" — sprint planning
    #
    # Thin wrapper around the jira_fetch_active_sprint @tool in tools/jira.py.
    # The tool handles all Jira connection logic and returns a JSON string;
    # this wrapper parses it back to a tuple for the intake node.
    # Same pattern as _prepare_bank_holiday_choices calling detect_bank_holidays.invoke().

    Returns:
        Tuple of (sprint_number, start_date, status_message).
        sprint_number is None on failure. start_date is ISO string or None.
        status_message explains what happened — shown to the user so they know
        why sprint selection fell back to "Fresh start".
    """
    try:
        from scrum_agent.tools.jira import jira_fetch_active_sprint

        result = jira_fetch_active_sprint.invoke({})
        if result.startswith("Error"):
            # Strip "Error: " prefix for the status message
            return None, None, result.removeprefix("Error: ")
        data = json.loads(result)
        return data["sprint_number"], data.get("start_date"), f"Active sprint: {data['sprint_name']}"
    except Exception as exc:
        logger.debug("Failed to fetch Jira sprints for sprint selection", exc_info=True)
        return None, None, f"Jira connection failed: {exc}"


def _extract_capacity_deductions(questionnaire: QuestionnaireState) -> dict:
    """Parse all capacity questions (Q27-Q30) from intake answers.

    # See README: "Scrum Standards" — capacity planning
    #
    # All capacity questions are now collected during intake (Phase 6).
    # Q27 (sprint selection) determines which sprint to plan for.
    # Q28 (bank holidays) is auto-detected from locale and confirmed by user.
    # Q29 (unplanned %), Q30 (onboarding) are defaulted in smart mode.

    Args:
        questionnaire: The completed questionnaire with all answers.

    Returns:
        A dict with all capacity fields for ScrumState.
    """
    # Q28 — bank holidays (auto-detected, confirmed by user as a choice)
    # The _detected_bank_holiday_days transient field is set during Q28 processing.
    # Fall back to parsing the Q28 answer text for a count.
    bank_holidays = questionnaire._detected_bank_holiday_days
    if bank_holidays == 0:
        q28 = questionnaire.answers.get(28, "No bank holidays detected")
        parsed = _parse_first_int(q28)
        if parsed is not None:
            bank_holidays = parsed

    # Q29 — unplanned leave % (choice question)
    q29 = questionnaire.answers.get(29, "10%")
    unplanned_pct = _parse_first_int(q29)
    if unplanned_pct is None:
        unplanned_pct = 10  # default 10%

    # Q30 — onboarding engineer-sprints
    q30 = questionnaire.answers.get(30, "No engineers onboarding")
    onboarding = _parse_first_int(q30) or 0

    # Planned leave — sum of working days from per-person leave entries
    # collected in the PTO sub-loop after Q28. Defaults to 0 if no entries.
    # See README: "Scrum Standards" — capacity planning
    planned_leave = sum(e.get("working_days", 0) for e in questionnaire._planned_leave_entries)

    return {
        "capacity_bank_holiday_days": bank_holidays,
        "capacity_planned_leave_days": planned_leave,
        "capacity_unplanned_leave_pct": unplanned_pct,
        "capacity_onboarding_engineer_sprints": onboarding,
        "capacity_ktlo_engineers": 0,
        "capacity_discovery_pct": 5,
    }


def _compute_net_velocity(
    team_size: int,
    velocity_per_sprint: int,
    sprint_length_weeks: int,
    target_sprints: int,
    bank_holiday_days: int,
    planned_leave_days: int,
    unplanned_leave_pct: int,
    onboarding_engineer_sprints: int,
    ktlo_engineers: int = 0,
    discovery_pct: int = 5,
) -> int:
    """Compute net velocity per sprint after capacity deductions.

    # See README: "Scrum Standards" — capacity planning
    #
    # Formula:
    #   gross_days = team_size × sprint_length_days × num_sprints
    #   ktlo_days = ktlo_engineers × sprint_length_days × num_sprints
    #   available_days = gross_days - ktlo_days
    #   deductions = bank_holidays + planned_leave + unplanned + onboarding_days
    #   discovery_days = (available_days - deductions) × discovery_pct / 100
    #   net_days = max(available_days - deductions - discovery_days, 0)
    #   net_velocity = round(net_days / gross_days × velocity_per_sprint)

    Args:
        ktlo_engineers: Engineers dedicated to KTLO/BAU work (default 0).
        discovery_pct: Discovery/design tax as a percentage (default 5).

    Returns:
        The adjusted velocity (always >= 1).
    """
    sprint_length_days = sprint_length_weeks * 5  # working days per sprint
    gross_days = team_size * sprint_length_days * target_sprints
    ktlo_days = ktlo_engineers * sprint_length_days * target_sprints
    available_days = gross_days - ktlo_days
    unplanned_days = gross_days * unplanned_leave_pct / 100
    onboarding_days = onboarding_engineer_sprints * sprint_length_days
    total_deductions = bank_holiday_days + planned_leave_days + unplanned_days + onboarding_days
    after_deductions = max(available_days - total_deductions, 0)
    discovery_days = after_deductions * discovery_pct / 100
    net_days = max(after_deductions - discovery_days, 0)

    if gross_days > 0:
        net_ratio = net_days / gross_days
        return max(1, round(net_ratio * velocity_per_sprint))
    return velocity_per_sprint


def _assign_holidays_to_sprints(
    holidays: list[dict],
    sprint_start_date: str,
    sprint_length_weeks: int,
    target_sprints: int,
) -> dict[int, list[dict]]:
    """Map each bank holiday to its 0-based sprint index.

    # See README: "Scrum Standards" — capacity planning
    #
    # Each holiday dict has {"date": date, "name": str, "weekday": str}
    # from get_bank_holidays_structured(). We compute which sprint window
    # the holiday falls in so per-sprint velocity can be calculated.

    Returns:
        Dict mapping sprint_index → list of holiday dicts in that sprint.
    """
    from datetime import date

    if not holidays or target_sprints <= 0:
        return {}

    try:
        start = date.fromisoformat(sprint_start_date) if sprint_start_date else date.today()
    except (ValueError, TypeError):
        start = date.today()

    sprint_days = sprint_length_weeks * 7
    result: dict[int, list[dict]] = {}

    for holiday in holidays:
        h_date = holiday.get("date")
        if h_date is None:
            continue
        # Convert string dates to date objects
        if isinstance(h_date, str):
            try:
                h_date = date.fromisoformat(h_date)
            except ValueError:
                continue

        delta = (h_date - start).days
        if delta < 0:
            continue
        sprint_idx = delta // sprint_days
        if sprint_idx < target_sprints:
            result.setdefault(sprint_idx, []).append(holiday)

    return result


def _parse_date_dmy(text: str):
    """Parse a user-entered date in DD/MM/YYYY, DD/MM/YY, DD-MM-YYYY, or DD-MM-YY format.

    # See README: "Scrum Standards" — capacity planning
    #
    # Users enter leave dates in day-first format (common in UK/EU).
    # Two-digit years assume 2000s (e.g. 26 → 2026).

    Returns:
        A datetime.date or None if the input is unrecognised.
    """
    import re as _re
    from datetime import date

    m = _re.match(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})$", text.strip())
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    # Reject dates obviously in the past (> 6 months ago) — catches 2-digit year
    # typos like "12/12/12" → 2012 which is clearly not a future leave date.
    if (date.today() - parsed).days > 180:
        return None
    return parsed


def _count_working_days(start_date, end_date) -> int:
    """Count weekdays (Mon–Fri) between two dates, inclusive.

    # See README: "Scrum Standards" — capacity planning
    #
    # Used to compute working days lost per leave entry. Only counts
    # Mon(0)–Fri(4); Sat(5) and Sun(6) are excluded.

    Returns:
        Number of working days (>= 0).
    """
    from datetime import timedelta

    if end_date < start_date:
        return 0
    count = 0
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # Mon=0 .. Fri=4
            count += 1
        current += timedelta(days=1)
    return count


def _assign_leave_to_sprints(
    leave_entries: list[dict],
    sprint_start_date: str,
    sprint_length_weeks: int,
    target_sprints: int,
) -> dict[int, list[dict]]:
    """Map each PTO leave entry to sprint windows, handling multi-sprint spans.

    # See README: "Scrum Standards" — capacity planning
    #
    # Similar to _assign_holidays_to_sprints but:
    # - Each entry can span multiple sprints (clipped to sprint boundaries)
    # - No × team_size multiplier (PTO is per-person, not team-wide)
    # - Returns working days per sprint, not holiday count

    Returns:
        Dict mapping sprint_index → list of {"person": str, "days": int}.
    """
    from datetime import date, timedelta

    if not leave_entries or target_sprints <= 0:
        return {}

    try:
        start = date.fromisoformat(sprint_start_date) if sprint_start_date else date.today()
    except (ValueError, TypeError):
        start = date.today()

    sprint_days = sprint_length_weeks * 7  # calendar days per sprint
    result: dict[int, list[dict]] = {}

    for entry in leave_entries:
        person = entry.get("person", "")
        try:
            leave_start = date.fromisoformat(entry["start_date"])
            leave_end = date.fromisoformat(entry["end_date"])
        except (ValueError, TypeError, KeyError):
            continue

        # Check each sprint window for overlap
        for i in range(target_sprints):
            sprint_begin = start + timedelta(days=i * sprint_days)
            sprint_end = start + timedelta(days=(i + 1) * sprint_days - 1)

            # Clip leave to sprint boundaries
            overlap_start = max(leave_start, sprint_begin)
            overlap_end = min(leave_end, sprint_end)

            if overlap_start <= overlap_end:
                working = _count_working_days(overlap_start, overlap_end)
                if working > 0:
                    result.setdefault(i, []).append({"person": person, "days": working})

    return result


def _compute_per_sprint_velocities(
    team_size: int,
    velocity_per_sprint: int,
    sprint_length_weeks: int,
    target_sprints: int,
    holidays_by_sprint: dict[int, list[dict]],
    planned_leave_days: int,
    unplanned_leave_pct: int,
    onboarding_engineer_sprints: int,
    ktlo_engineers: int = 0,
    discovery_pct: int = 5,
    leave_by_sprint: dict[int, list[dict]] | None = None,
) -> list[dict]:
    """Compute per-sprint velocities with sprint-specific bank holiday and PTO deductions.

    # See README: "Scrum Standards" — capacity planning
    #
    # Unlike _compute_net_velocity (which spreads bank holidays evenly),
    # this function only reduces velocity for sprints that actually contain
    # bank holidays or PTO. Other deductions (unplanned leave, discovery, KTLO)
    # are still applied uniformly since they're probabilistic averages.
    #
    # leave_by_sprint maps sprint_index → list of {"person": str, "days": int}.
    # PTO is per-person (1 × days), not team-wide like bank holidays (team_size × days).
    # The None default preserves backward compatibility.

    Returns:
        List of dicts: [{"sprint_index": 0, "bank_holiday_days": 2,
                         "bank_holiday_names": ["Good Friday", ...],
                         "pto_days": 5, "pto_entries": [...],
                         "net_velocity": 3}, ...]
    """
    sprint_length_days = sprint_length_weeks * 5  # working days per sprint
    gross_days_per_sprint = team_size * sprint_length_days

    if gross_days_per_sprint == 0 or target_sprints == 0:
        return [
            {
                "sprint_index": i,
                "bank_holiday_days": 0,
                "bank_holiday_names": [],
                "pto_days": 0,
                "pto_entries": [],
                "net_velocity": velocity_per_sprint,
            }
            for i in range(target_sprints)
        ]

    # Flat deductions per sprint (uniform across all sprints)
    ktlo_days_per_sprint = ktlo_engineers * sprint_length_days
    unplanned_days_per_sprint = gross_days_per_sprint * unplanned_leave_pct / 100
    # When leave_by_sprint is provided, planned leave is already tracked per-sprint —
    # don't also spread the aggregate total, or PTO gets double-counted.
    planned_leave_per_sprint = 0 if leave_by_sprint else (planned_leave_days / target_sprints if target_sprints else 0)
    onboarding_days_total = onboarding_engineer_sprints * sprint_length_days
    onboarding_per_sprint = onboarding_days_total / target_sprints if target_sprints else 0

    result = []
    for i in range(target_sprints):
        # Sprint-specific bank holidays — each holiday costs team_size person-days
        sprint_holidays = holidays_by_sprint.get(i, [])
        bank_days = len(sprint_holidays) * team_size

        # Sprint-specific PTO — per-person, no team_size multiplier
        pto_entries = leave_by_sprint.get(i, []) if leave_by_sprint else []
        pto_days = sum(e["days"] for e in pto_entries)

        available = gross_days_per_sprint - ktlo_days_per_sprint
        total_deductions = (
            bank_days + pto_days + planned_leave_per_sprint + unplanned_days_per_sprint + onboarding_per_sprint
        )
        after_deductions = max(available - total_deductions, 0)
        discovery_days = after_deductions * discovery_pct / 100
        net_days = max(after_deductions - discovery_days, 0)

        net_ratio = net_days / gross_days_per_sprint
        net_vel = max(1, round(net_ratio * velocity_per_sprint))

        result.append(
            {
                "sprint_index": i,
                "bank_holiday_days": len(sprint_holidays),
                "bank_holiday_names": [h.get("name", "") for h in sprint_holidays],
                "pto_days": pto_days,
                "pto_entries": pto_entries,
                "net_velocity": net_vel,
            }
        )

    return result


def _build_velocity_breakdown(
    velocity_per_sprint: int,
    velocity_source: str,
    team_size: int,
    sprint_length_weeks: int,
    target_sprints: int,
    bank_holiday_days: int,
    planned_leave_days: int,
    unplanned_leave_pct: int,
    onboarding_engineer_sprints: int,
    ktlo_engineers: int = 0,
    discovery_pct: int = 5,
    planned_leave_entries: list[dict] | None = None,
) -> tuple[int, str]:
    """Compute net velocity and build a transparent markdown breakdown.

    # See README: "Scrum Standards" — capacity planning
    #
    # Shows the user exactly how the recommended velocity was calculated,
    # converting each deduction into points-per-sprint so the impact is
    # immediately visible. Displayed at the confirmation gate before the
    # user accepts or overrides the velocity.

    Returns:
        Tuple of (net_velocity, breakdown_text).
    """
    net_vel = _compute_net_velocity(
        team_size=team_size,
        velocity_per_sprint=velocity_per_sprint,
        sprint_length_weeks=sprint_length_weeks,
        target_sprints=target_sprints,
        bank_holiday_days=bank_holiday_days,
        planned_leave_days=planned_leave_days,
        unplanned_leave_pct=unplanned_leave_pct,
        onboarding_engineer_sprints=onboarding_engineer_sprints,
        ktlo_engineers=ktlo_engineers,
        discovery_pct=discovery_pct,
    )

    # Convert each deduction to points-per-sprint for the breakdown.
    # The ratio is: deduction_days / gross_days × velocity_per_sprint.
    sprint_length_days = sprint_length_weeks * 5
    gross_days = team_size * sprint_length_days * target_sprints
    if gross_days == 0:
        return net_vel, f"**Net velocity: {net_vel} pts/sprint**"

    def _to_pts(days: float) -> float:
        return days / gross_days * velocity_per_sprint

    ktlo_days = ktlo_engineers * sprint_length_days * target_sprints
    unplanned_days = gross_days * unplanned_leave_pct / 100
    onboarding_days = onboarding_engineer_sprints * sprint_length_days
    available_days = gross_days - ktlo_days
    total_leave = bank_holiday_days + planned_leave_days + unplanned_days + onboarding_days
    after_deductions = max(available_days - total_leave, 0)
    discovery_days = after_deductions * discovery_pct / 100

    source_map = {
        "jira": "from Jira: avg of last 3 sprints",
        "manual": "user-provided",
        "estimated": "estimated",
    }
    source_label = source_map.get(velocity_source, velocity_source)

    lines = ["**Recommended Velocity**\n"]
    lines.append(f"Gross velocity:       {velocity_per_sprint} pts/sprint ({source_label})")

    deduction_lines: list[str] = []
    if bank_holiday_days > 0:
        pts = _to_pts(bank_holiday_days)
        detail = f"{bank_holiday_days} day(s) across {target_sprints} sprints"
        deduction_lines.append(f"  Bank holidays:      −{pts:.1f} pts  ({detail})")
    if planned_leave_days > 0:
        pts = _to_pts(planned_leave_days)
        # Show person names when leave entries are available
        if planned_leave_entries:
            names = ", ".join(f"{e['person']} {e['working_days']}d" for e in planned_leave_entries)
            deduction_lines.append(f"  Planned leave:      −{pts:.1f} pts  ({names})")
        else:
            deduction_lines.append(f"  Planned leave:      −{pts:.1f} pts  ({planned_leave_days} day(s))")
    if unplanned_leave_pct > 0:
        pts = _to_pts(unplanned_days)
        deduction_lines.append(f"  Unplanned absence:  −{pts:.1f} pts  ({unplanned_leave_pct}%)")
    if onboarding_engineer_sprints > 0:
        pts = _to_pts(onboarding_days)
        deduction_lines.append(
            f"  Onboarding:         −{pts:.1f} pts  ({onboarding_engineer_sprints} engineer × sprint(s))"
        )
    if ktlo_engineers > 0:
        pts = _to_pts(ktlo_days)
        deduction_lines.append(f"  KTLO/BAU:           −{pts:.1f} pts  ({ktlo_engineers} dedicated engineer(s))")
    if discovery_pct > 0:
        pts = _to_pts(discovery_days)
        deduction_lines.append(f"  Discovery/design:   −{pts:.1f} pts  ({discovery_pct}%)")

    if deduction_lines:
        lines.extend(deduction_lines)
        lines.append("                      ─────────")

    lines.append(f"**Net velocity:       {net_vel} pts/sprint**")

    return net_vel, "\n".join(lines)


def _parse_velocity_override(text: str) -> int | None:
    """Parse a velocity override from user input.

    Accepts bare numbers ("14"), numbers with units ("12 pts", "15 points"),
    or sentences like "use 18 points per sprint".

    Returns:
        The parsed velocity as int, or None if the input can't be parsed.
    """
    normalized = text.strip().lower()
    # Match patterns like "12", "12 pts", "12 points", "12 pts/sprint"
    match = re.search(r"(\d+)\s*(?:pts|points|pts/sprint)?", normalized)
    if match:
        val = int(match.group(1))
        if val > 0:
            return val
    return None


def _build_suggestion_line(questionnaire: QuestionnaireState, q_num: int) -> str:
    """Return a markdown suggestion line if the question has a suggested answer.

    Args:
        questionnaire: The current questionnaire state.
        q_num: The question number to check for suggestions.

    Returns:
        A formatted suggestion line, or empty string if no suggestion.
    """
    suggestion = questionnaire.suggested_answers.get(q_num)
    if suggestion:
        return f"\n\n> Extracted: **{suggestion}** *(Enter to accept, or type your own)*"
    return ""


# ── Smart / quick intake helpers ──────────────────────────────────────
# See README: "Project Intake Questionnaire" — smart intake
#
# These helpers implement the three-mode intake system. Smart mode auto-
# applies LLM-extracted answers and defaults, only asking unfilled essential
# gaps. Quick mode is even more aggressive — only team size and tech stack.
# Standard mode is the original 26-question flow (unchanged).


def _load_user_context(path: str | None = None, docs_dir: str | None = None) -> tuple[str | None, dict]:
    """Read SCRUM.md and scrum-docs/ from the working directory and return combined content + status.

    # See README: "Tools" — read-only tool pattern
    #
    # Thin wrapper around the load_project_context @tool in tools/codebase.py.
    # The tool handles all filesystem I/O and returns a JSON string; this wrapper
    # parses it back to the (context, status) tuple expected by the analyzer node.
    # Same pattern as _prepare_bank_holiday_choices calling detect_bank_holidays.invoke().

    Args:
        path: Override the SCRUM.md file path (used in tests). Defaults to SCRUM.md in CWD.
        docs_dir: Override the docs directory path (used in tests). Defaults to scrum-docs/ in CWD.

    Returns:
        Tuple of (context string or None, status dict with name/status/detail).
    """
    try:
        from scrum_agent.tools.codebase import load_project_context

        result = load_project_context.invoke({"path": path or "", "docs_dir": docs_dir or ""})
        data = json.loads(result)
        return data["context"], data["status"]
    except Exception as e:
        logger.debug("User context load failed (non-fatal)", exc_info=True)
        return None, {"name": "User context", "status": "error", "detail": str(e)[:80]}


def _extract_confluence_page_ids(text: str) -> list[str]:
    """Extract Confluence page IDs from URLs found in free-form text (e.g. SCRUM.md).

    # See README: "Tools" — read-only tool pattern
    #
    # Confluence Cloud URLs embed the numeric page ID in the path, e.g.:
    #   https://example.atlassian.net/wiki/spaces/SPACE/pages/1234567890/Page+Title
    # This regex extracts those IDs so we can call confluence_read_page directly
    # instead of relying on keyword search which often misses specific pages.
    """
    # Match /wiki/spaces/<key>/pages/<page_id> — the page_id is always numeric.
    return re.findall(r"/wiki/spaces/[^/]+/pages/(\d+)", text)


def _fetch_confluence_context(
    questionnaire: QuestionnaireState,
    user_context: str | None = None,
) -> tuple[str | None, dict]:
    """Search Confluence for docs related to the project and return combined context + status.

    # See README: "Tools" — read-only tool pattern
    #
    # Two strategies are used to find relevant Confluence pages:
    # 1. Keyword search using the project name (Q1) — broad discovery.
    # 2. Direct page fetch for any Confluence URLs found in SCRUM.md — precise
    #    targeting of pages the user has explicitly linked (e.g. RunBook URLs).
    #
    # Both are combined into a single context string. Strategy 2 is the fix for
    # the issue where RunBook URLs in SCRUM.md's "Key Links" section were ignored
    # because the keyword search used the full project description as a CQL query,
    # which rarely matched specific page titles.

    Uses the project name (Q1) as the search query, and also extracts Confluence
    page IDs from user_context (SCRUM.md) URLs to fetch those pages directly.
    Falls back to (None, status) when Confluence is not configured.
    The caller proceeds gracefully with reduced context when None is returned.

    Args:
        questionnaire: The completed QuestionnaireState with Q1 (project name).
        user_context: Raw SCRUM.md content — scanned for Confluence URLs to fetch directly.

    Returns:
        Tuple of (context string or None, status dict with name/status/detail).
    """
    try:
        from scrum_agent.config import get_jira_base_url, get_jira_email, get_jira_token

        # Only proceed if Confluence/Jira credentials are configured.
        if not all([get_jira_base_url(), get_jira_email(), get_jira_token()]):
            return None, {"name": "Confluence", "status": "skipped", "detail": "not configured"}

        from scrum_agent.tools.confluence import confluence_read_page, confluence_search_docs

        parts: list[str] = []

        # Strategy 1: Keyword search using project name (Q1).
        project_name = questionnaire.answers.get(1, "").strip()
        if project_name and project_name != QUESTION_DEFAULTS.get(1):
            result = confluence_search_docs.invoke({"query": project_name, "limit": 5})
            if result and not result.startswith("Error") and not result.startswith("No Confluence"):
                parts.append(result)
                logger.debug("CONFLUENCE: keyword search returned results for %r", project_name)
            else:
                logger.debug("CONFLUENCE: keyword search returned no results for %r", project_name)

        # Strategy 2: Fetch pages directly from Confluence URLs in SCRUM.md.
        # This covers explicit links the user added (e.g. RunBook URLs) that
        # keyword search would miss.
        if user_context:
            page_ids = _extract_confluence_page_ids(user_context)
            logger.debug("CONFLUENCE: extracted %d page ID(s) from user_context: %s", len(page_ids), page_ids[:10])
            seen = set()
            for pid in page_ids:
                if pid in seen:
                    continue
                seen.add(pid)
                try:
                    logger.debug("CONFLUENCE: fetching page ID %s", pid)
                    page_content = confluence_read_page.invoke({"page_id": pid})
                    if page_content and not page_content.startswith("Error"):
                        parts.append(page_content)
                        logger.debug("CONFLUENCE: fetched page ID %s (%d chars)", pid, len(page_content))
                    else:
                        logger.debug(
                            "CONFLUENCE: page ID %s returned error: %s",
                            pid,
                            page_content[:100] if page_content else "empty",
                        )
                except Exception:
                    logger.debug("CONFLUENCE: failed to fetch page %s (non-fatal)", pid, exc_info=True)
        else:
            logger.debug("CONFLUENCE: no user_context provided, skipping URL extraction")

        if not parts:
            return None, {"name": "Confluence", "status": "error", "detail": "no docs found"}

        combined = "\n\n---\n\n".join(parts)
        detail = f"search + {len(parts) - 1} linked page(s)" if len(parts) > 1 else f"docs for '{project_name}'"
        return combined, {"name": "Confluence", "status": "success", "detail": detail}
    except Exception as e:
        logger.debug("Confluence context fetch failed (non-fatal)", exc_info=True)
        return None, {"name": "Confluence", "status": "error", "detail": str(e)[:80]}


def _scan_repo_context(questionnaire: QuestionnaireState) -> tuple[str | None, dict]:
    """Scan the repo referenced in Q17 and return a combined context string + status.

    Calls GitHub or AzDO read tools directly as Python functions — no LLM
    ReAct loop needed, they are plain functions that return strings.
    Returns (None, status) if no URL was provided, the platform is unsupported
    (GitLab, Bitbucket — tools not yet implemented), or all tool calls fail.
    The caller proceeds gracefully with reduced context when None is returned.

    Returns:
        Tuple of (context string or None, status dict with name/status/detail).

    # See README: "Tools" — read-only tool pattern
    """
    url = questionnaire.answers.get(17, "")
    if not url or url == QUESTION_DEFAULTS.get(17):
        return None, {"name": "Repository", "status": "skipped", "detail": "no URL provided"}

    platform = questionnaire.answers.get(16, "GitHub")
    sections: list[str] = []

    try:
        if platform == "GitHub":
            from scrum_agent.tools.github import github_read_readme, github_read_repo

            for fn, kwargs in [
                (github_read_repo, {"repo_url": url}),
                (github_read_readme, {"repo_url": url}),
            ]:
                result = fn.invoke(kwargs)
                if result and not result.startswith("Error:") and not result.startswith("GitHub rate limit"):
                    sections.append(result)

        elif platform == "Azure DevOps":
            from scrum_agent.tools.azure_devops import azdevops_read_repo

            result = azdevops_read_repo.invoke({"repo_url": url})
            if result and not result.startswith("Error:"):
                sections.append(result)

        elif not url.startswith(("http://", "https://")):
            # Treat as a local filesystem path (user selected "local only" in Q16
            # or typed an absolute/relative path instead of a remote URL).
            from scrum_agent.tools.codebase import read_codebase

            result = read_codebase.invoke({"path": url})
            if result and not result.startswith("Error:"):
                sections.append(result)

        else:
            # GitLab and Bitbucket: no tools implemented yet
            return None, {"name": "Repository", "status": "skipped", "detail": f"{platform} not yet supported"}

    except Exception as e:
        return None, {"name": "Repository", "status": "error", "detail": str(e)[:80]}

    if sections:
        context = "\n\n---\n\n".join(sections)
        return context, {"name": "Repository", "status": "success", "detail": f"{platform} — scanned"}

    return None, {"name": "Repository", "status": "error", "detail": f"{platform} scan returned no data"}


def _sync_platform_from_url(questionnaire: QuestionnaireState) -> None:
    """Auto-update Q16 (platform) when Q17 (repo URL) is stored.

    If the URL implies a known platform and Q16 differs, Q16 is silently
    corrected so the LLM sees the right platform in the intake summary.
    """
    url = questionnaire.answers.get(17, "")
    if not url or url == QUESTION_DEFAULTS.get(17):
        return  # "No repo URL provided" or empty — skip
    platform = detect_platform(url)
    if platform and questionnaire.answers.get(16) != platform:
        questionnaire.answers[16] = platform
        questionnaire.defaulted_questions.discard(16)


def _auto_apply_extractions(questionnaire: QuestionnaireState, extracted: dict[int, str]) -> None:
    """Move extracted answers directly into questionnaire.answers (not suggestions).

    # In smart/quick mode, extracted answers are auto-accepted (not suggested
    # for confirmation). This is the key UX difference from standard mode.

    Args:
        questionnaire: The mutable QuestionnaireState to update.
        extracted: Dict mapping question numbers to extracted answer strings.
    """
    for q_num, answer in extracted.items():
        questionnaire.answers[q_num] = answer
        questionnaire.extracted_questions.add(q_num)
        questionnaire.answer_sources[q_num] = AnswerSource.EXTRACTED


def _auto_default_remaining(
    questionnaire: QuestionnaireState,
    essential_set: frozenset[int],
    fallbacks: dict[int, str] | None = None,
) -> None:
    """Apply defaults to all non-essential, non-answered questions.

    # See README: "Project Intake Questionnaire" — smart intake
    #
    # In smart/quick mode, optional questions are auto-defaulted so only
    # essential gaps remain. This is the mechanism that reduces 26 questions
    # to 2-4.
    #
    # For each unanswered question:
    #   - Choice Q with default_index → use that option
    #   - Free-text Q with QUESTION_DEFAULTS entry → use that default
    #   - Fallback provided → use the fallback (quick mode uses these)
    #   - Otherwise → skip it (essential Qs stay as gaps)

    Args:
        questionnaire: The mutable QuestionnaireState to update.
        essential_set: The set of essential question numbers (not defaulted).
        fallbacks: Optional fallback defaults for questions that have none
            in QUESTION_DEFAULTS (used by quick mode).
    """
    for q_num in range(1, TOTAL_QUESTIONS + 1):
        if q_num in questionnaire.answers or q_num in questionnaire.skipped_questions:
            continue  # already answered or skipped

        if q_num in essential_set:
            continue  # essential gap — will be asked interactively

        # Try standard defaults first
        meta = QUESTION_METADATA.get(q_num)
        if meta and meta.default_index is not None:
            questionnaire.answers[q_num] = meta.options[meta.default_index]
            questionnaire.defaulted_questions.add(q_num)
            questionnaire.answer_sources[q_num] = AnswerSource.DEFAULTED
        elif q_num in QUESTION_DEFAULTS:
            questionnaire.answers[q_num] = QUESTION_DEFAULTS[q_num]
            questionnaire.defaulted_questions.add(q_num)
            questionnaire.answer_sources[q_num] = AnswerSource.DEFAULTED
        elif fallbacks and q_num in fallbacks:
            questionnaire.answers[q_num] = fallbacks[q_num]
            questionnaire.defaulted_questions.add(q_num)
            questionnaire.answer_sources[q_num] = AnswerSource.DEFAULTED


# Prompt shown immediately after Q2 is answered as "Existing codebase" or "Hybrid",
# before advancing to the next question. Stored in Q17 when answered.
_Q2_REPO_URL_PROMPT = (
    "Since you're building on an existing codebase, could you share the repository URL? "
    "The agent can scan it for tech stack context.\n\n"
    "Paste a URL, or hit enter to skip"
)

# Q2 answers that trigger the repo URL follow-up.
_EXISTING_CODEBASE_ANSWERS = frozenset({"Existing codebase", "Hybrid"})


def _needs_repo_url_prompt(questionnaire: QuestionnaireState) -> bool:
    """Return True if Q2 was answered 'Existing codebase'/'Hybrid' and Q17 needs asking.

    Q17 is considered unanswered if it is absent OR was only auto-defaulted by
    _auto_default_remaining (i.e. in defaulted_questions). A defaulted Q17 means
    the LLM never saw a real URL — we still need to ask the user explicitly.
    """
    if questionnaire.answers.get(2) not in _EXISTING_CODEBASE_ANSWERS:
        return False
    # Ask if Q17 is missing entirely, or was only filled by auto-default (not by user/extraction)
    return 17 not in questionnaire.answers or 17 in questionnaire.defaulted_questions


def _derive_q15_from_q2(questionnaire: QuestionnaireState) -> None:
    """Deterministically derive Q15 (existing codebase?) from Q2 (project type).

    # Q15 asks "Does the project have an existing codebase, or is this a new build?"
    # Q2 asks "Is this a greenfield project or are you building on an existing codebase?"
    # These are redundant — Q15 can be derived from Q2 without an LLM call.
    #
    # Only applies if Q2 is answered and Q15 is not already answered.

    Args:
        questionnaire: The mutable QuestionnaireState to update.
    """
    if 15 in questionnaire.answers:
        return  # already answered

    q2_answer = questionnaire.answers.get(2, "")
    derived = Q2_TO_Q15_MAP.get(q2_answer)
    if derived:
        questionnaire.answers[15] = derived
        questionnaire.defaulted_questions.add(15)
        questionnaire.answer_sources[15] = AnswerSource.DEFAULTED


def _detect_bank_holidays_for_window(
    sprint_start_date: str | None,
    sprint_length_weeks: int,
    target_sprints: int,
) -> tuple[int, str]:
    """Auto-detect bank holidays from system locale for a sprint planning window.

    # See README: "Scrum Standards" — capacity planning
    #
    # Uses get_bank_holidays_structured with locale auto-detection to count
    # weekday bank holidays in the planning window.

    Args:
        sprint_start_date: ISO date string for sprint start, or None for today.
        sprint_length_weeks: Length of each sprint in weeks.
        target_sprints: Number of sprints to plan for.

    Returns:
        Tuple of (count, summary_text) where summary_text is human-readable.
    """
    from scrum_agent.tools.calendar_tools import _detect_country_from_locale, get_bank_holidays_structured

    country = _detect_country_from_locale()
    if not country:
        return 0, "No bank holidays detected (locale not detected)"

    from datetime import date

    start = sprint_start_date or date.today().isoformat()
    holidays = get_bank_holidays_structured(
        country_code=country,
        sprint_length_weeks=sprint_length_weeks,
        num_sprints=target_sprints,
        start_date=start,
    )

    count = len(holidays)
    if count == 0:
        return 0, "No bank holidays detected in planning window"

    # Build summary with holiday names and sprint mapping
    try:
        start_dt = date.fromisoformat(start)
    except ValueError:
        start_dt = date.today()

    sprint_days = sprint_length_weeks * 7
    names = []
    for h in holidays:
        h_date = h["date"]
        offset_days = (h_date - start_dt).days
        sprint_num = offset_days // sprint_days + 1
        day_name = h["weekday"][:3]
        names.append(f"{h['name']} (Sprint {sprint_num}, {day_name} {h_date.strftime('%-d %b')})")

    summary = f"{count} bank holiday(s): " + ", ".join(names)
    return count, summary


def _derive_q27_from_locale(questionnaire: QuestionnaireState) -> None:
    """Auto-default Q27 (sprint selection) when Jira is unavailable.

    # See README: "Scrum Standards" — capacity planning
    #
    # In smart/quick mode without Jira (or when Jira fetch fails), Q27 is
    # auto-defaulted to "Fresh start (today)". Bank holiday detection is
    # handled separately via Q28.

    Args:
        questionnaire: The mutable QuestionnaireState to update.
    """
    if 27 in questionnaire.answers and 27 not in questionnaire.defaulted_questions:
        return  # already answered explicitly

    questionnaire.answers[27] = "Fresh start (today)"
    questionnaire.extracted_questions.add(27)
    questionnaire.defaulted_questions.discard(27)
    questionnaire.answer_sources[27] = AnswerSource.EXTRACTED


def _resolve_sprint_start_date(questionnaire: QuestionnaireState) -> str:
    """Derive the sprint start date from Q27's answer.

    # See README: "Scrum Standards" — capacity planning
    #
    # When Jira is configured and the user selects a future sprint (e.g. Sprint 107
    # when active is Sprint 104), compute the start date by adding the sprint offset:
    #   start = today + (selected - active) × sprint_weeks
    # This ensures bank holiday detection uses the correct planning window.
    # When Q27 is "Fresh start (today)" or no Jira, we use today's date.

    Returns:
        ISO date string (YYYY-MM-DD) for the planning window start.
    """
    from datetime import date, timedelta

    q27 = questionnaire.answers.get(27, "")
    active = questionnaire._active_sprint_number
    if active is not None:
        # User selected a specific sprint via Jira — compute start date
        # using the active sprint's actual Jira start date + offset.
        selected_match = re.search(r"Sprint\s+(\d+)", q27)
        if selected_match:
            selected = int(selected_match.group(1))
            offset_sprints = selected - active
            sprint_weeks = _parse_first_int(questionnaire.answers.get(8, "2 weeks")) or 2
            # Use Jira's actual start date as anchor when available
            anchor = questionnaire._active_sprint_start_date
            if anchor:
                anchor_date = date.fromisoformat(anchor)
            else:
                anchor_date = date.today()
            return (anchor_date + timedelta(weeks=offset_sprints * sprint_weeks)).isoformat()

    # Default to today — covers "Fresh start (today)" and fallbacks
    return date.today().isoformat()


def _get_planning_window(questionnaire: QuestionnaireState):
    """Compute the planning window (start, end) from Q8/Q10/Q27.

    Returns (start_date, end_date) as datetime.date objects, or (None, None)
    if the window can't be determined (missing answers).
    """
    from datetime import date, timedelta

    q8 = questionnaire.answers.get(8, "")
    sprint_weeks = _parse_first_int(q8)
    if not sprint_weeks:
        return None, None

    q10 = questionnaire.answers.get(10, "")
    q10_nums = re.findall(r"\d+", q10)
    num_sprints = int(q10_nums[-1]) if q10_nums else None
    if not num_sprints:
        return None, None

    start_str = _resolve_sprint_start_date(questionnaire)
    start = date.fromisoformat(start_str)
    end = start + timedelta(weeks=sprint_weeks * num_sprints)
    return start, end


def _prepare_bank_holiday_choices(questionnaire: QuestionnaireState) -> None:
    """Detect bank holidays using the detect_bank_holidays @tool.

    # See README: "Scrum Standards" — capacity planning
    # See README: "Tools" — tool types, @tool decorator
    #
    # Called after Q27 (sprint selection) is resolved — regardless of whether
    # Jira is configured. Uses the actual sprint window dates:
    #   - Start date: from Jira active sprint or today for "Fresh start"
    #   - Duration: sprint length (Q8) × target sprints (Q10)
    # The tool auto-detects the user's region from system locale.
    #
    # In smart/quick mode, Q28 is NOT asked interactively — the result is
    # auto-filled and shown in the velocity breakdown at confirmation.
    # In standard mode, choice menu options are populated for Q28.

    Args:
        questionnaire: The mutable QuestionnaireState to update.
    """
    from scrum_agent.tools.calendar_tools import detect_bank_holidays

    q8 = questionnaire.answers.get(8, "2 weeks")
    sprint_weeks = _parse_first_int(q8) or 2
    # Q10 uses ranges like "1–2 sprints" — use the upper bound (last number)
    # to match the project_analyzer logic. _parse_first_int would grab "1"
    # from "1–2 sprints", missing holidays that fall in the second sprint.
    q10 = questionnaire.answers.get(10, "")
    q10_nums = re.findall(r"\d+", q10)
    num_sprints = int(q10_nums[-1]) if q10_nums else 6
    start_date = _resolve_sprint_start_date(questionnaire)

    logger.debug(
        "BANK_HOLIDAY: _prepare_bank_holiday_choices called — weeks=%d sprints=%d start=%s",
        sprint_weeks,
        num_sprints,
        start_date,
    )

    # Call the @tool's underlying function directly (bypassing LangChain
    # .invoke() machinery) for reliability in all runtime contexts.
    try:
        tool_output = detect_bank_holidays.func(
            country_code="",
            sprint_length_weeks=sprint_weeks,
            num_sprints=num_sprints,
            start_date=start_date,
        )
        logger.debug("BANK_HOLIDAY: tool returned %d chars: %.200s", len(tool_output), tool_output)
    except Exception:
        logger.warning("BANK_HOLIDAY: detection failed", exc_info=True)
        tool_output = ""

    # Also fetch structured holiday data so per-sprint velocity can be computed.
    # The structured data includes individual dates we can map to sprint windows.
    from scrum_agent.tools.calendar_tools import get_bank_holidays_structured

    try:
        structured = get_bank_holidays_structured(
            country_code="",
            sprint_length_weeks=sprint_weeks,
            num_sprints=num_sprints,
            start_date=start_date,
        )
        # Convert date objects to ISO strings for serialization safety
        questionnaire._detected_bank_holidays = [
            {
                "date": h["date"].isoformat() if hasattr(h["date"], "isoformat") else str(h["date"]),
                "name": h["name"],
                "weekday": h["weekday"],
            }
            for h in structured
        ]
        logger.debug("BANK_HOLIDAY: structured data — %d holidays stored", len(structured))
    except Exception:
        logger.warning("BANK_HOLIDAY: structured detection failed", exc_info=True)
        questionnaire._detected_bank_holidays = []

    # Parse the tool output to extract the count and summary.
    # The tool outputs: "Total working days lost to bank holidays: **N**"
    count = 0
    count_match = re.search(r"Total working days lost.*?\*\*(\d+)\*\*", tool_output)
    if count_match:
        count = int(count_match.group(1))

    # Build a concise summary from the tool output
    summary = ""
    if count > 0:
        holiday_lines = re.findall(r"- \d{4}-\d{2}-\d{2} \(\w+\): (.+)", tool_output)
        if holiday_lines:
            summary = f"{count} bank holiday(s): " + ", ".join(holiday_lines)
        else:
            summary = f"{count} bank holiday(s) in planning window"

    questionnaire._detected_bank_holiday_days = count
    logger.debug("BANK_HOLIDAY: count=%d summary=%r", count, summary)

    # Auto-fill Q28 answer so _extract_capacity_deductions reads it correctly.
    # In smart/quick mode this is the final answer (shown in velocity breakdown).
    # In standard mode the user can still override via the choice menu.
    if count > 0:
        questionnaire.answers[28] = summary
        questionnaire.extracted_questions.add(28)
        questionnaire.defaulted_questions.discard(28)
        questionnaire.answer_sources[28] = AnswerSource.EXTRACTED
        logger.debug("BANK_HOLIDAY: Q28 set to %r (extracted)", summary)
    else:
        questionnaire.answers[28] = "No bank holidays detected"
        questionnaire.extracted_questions.add(28)
        questionnaire.defaulted_questions.discard(28)
        questionnaire.answer_sources[28] = AnswerSource.EXTRACTED
        logger.debug("BANK_HOLIDAY: Q28 set to 'No bank holidays detected'")

    # Populate choice menu for standard mode (Q28 is still interactive there)
    if count > 0:
        questionnaire._follow_up_choices[28] = (
            f"Accept: {summary}",
            "No bank holidays",
            "Enter manually",
        )
    else:
        questionnaire._follow_up_choices[28] = (
            "No bank holidays detected — accept",
            "Enter manually",
        )


def _find_essential_gaps(questionnaire: QuestionnaireState, essential_set: frozenset[int]) -> list[int]:
    """Return sorted list of essential question numbers that are still unanswered.

    # See README: "Project Intake Questionnaire" — conditional essentials
    #
    # In addition to the static essential set, CONDITIONAL_ESSENTIALS maps
    # questions to their prerequisites. A conditional question becomes a gap
    # when its prerequisite has a real (non-defaulted) answer — e.g., Q7
    # (team roles) is only asked when Q6 (team size) was actually answered.

    Args:
        questionnaire: The current questionnaire state.
        essential_set: The set of essential question numbers to check.

    Returns:
        Sorted list of question numbers that have no answer recorded.
    """
    gaps = set(q for q in essential_set if q not in questionnaire.answers)

    # Conditionals: promote to essential when prerequisite is answered (not defaulted).
    # A conditional question becomes a gap when:
    #   - it has no answer at all, OR it was auto-defaulted (worth re-asking)
    #   - its prerequisite has a real (non-defaulted) answer
    for q, prereq in CONDITIONAL_ESSENTIALS.items():
        if (
            (q not in questionnaire.answers or q in questionnaire.defaulted_questions)
            and prereq in questionnaire.answers
            and prereq not in questionnaire.defaulted_questions
        ):
            gaps.add(q)

    return sorted(gaps)


def _resolve_adaptive_text(q_num: int, questionnaire: QuestionnaireState) -> str:
    """Return personalized question text if a template exists and dependencies are met.

    # See README: "Project Intake Questionnaire" — adaptive question text
    #
    # Checks ADAPTIVE_QUESTION_TEMPLATES for q_num. If all referenced prior
    # answers are available (and not defaulted), formats the template with
    # those answers. Otherwise falls back to INTAKE_QUESTIONS[q_num].

    Args:
        q_num: The question number to resolve text for.
        questionnaire: The current questionnaire state with answers.

    Returns:
        The personalized question text, or the original INTAKE_QUESTIONS text.
    """
    template = ADAPTIVE_QUESTION_TEMPLATES.get(q_num)
    if not template:
        return INTAKE_QUESTIONS[q_num]

    try:
        # Build format kwargs from prior answers
        kwargs: dict[str, str] = {}
        if "{q6}" in template:
            q6 = questionnaire.answers.get(6)
            if not q6 or 6 in questionnaire.defaulted_questions:
                return INTAKE_QUESTIONS[q_num]
            kwargs["q6"] = q6
        if "{q11}" in template:
            q11 = questionnaire.answers.get(11)
            if not q11 or 11 in questionnaire.defaulted_questions:
                return INTAKE_QUESTIONS[q_num]
            kwargs["q11"] = q11
        if "{q2}" in template:
            q2 = questionnaire.answers.get(2)
            if not q2 or 2 in questionnaire.defaulted_questions:
                return INTAKE_QUESTIONS[q_num]
            kwargs["q2"] = q2
        if "{hint}" in template:
            q2 = questionnaire.answers.get(2, "")
            hint = Q2_CONSTRAINT_HINTS.get(q2, "microservices vs monolith, cloud provider")
            kwargs["hint"] = hint
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return INTAKE_QUESTIONS[q_num]


def _build_gap_prompt(gaps: list[int], questionnaire: QuestionnaireState) -> tuple[str, list[int]]:
    """Build the prompt text for the next essential gap to ask.

    Asks one question at a time so each essential question gets a focused answer.
    Appends a suggestion line when the question has an extracted value in
    suggested_answers — lets the user confirm with Enter or override.

    Args:
        gaps: Sorted list of remaining essential gap question numbers.
        questionnaire: The current questionnaire state.

    Returns:
        A tuple of (prompt_text, list_of_q_nums_being_asked).
    """
    if not gaps:
        return ("", [])

    q_num = gaps[0]

    prompt = _resolve_adaptive_text(q_num, questionnaire)
    suggest = _build_suggestion_line(questionnaire, q_num)
    return (f"{prompt}{suggest}", [q_num])


def _build_skip_acknowledgment(question_num: int, *, during_probe: bool, default: str | None) -> str:
    """Build a user-facing acknowledgment message for a skipped question.

    Three cases:
    - Skip during follow-up probe → keep the original answer.
    - Default available → tell the user what assumption is being made.
    - No default → flag that we'll work without this information.

    Args:
        question_num: The question number being skipped.
        during_probe: True if the skip happened on a follow-up probe.
        default: The default value, or None if no default exists.

    Returns:
        A short acknowledgment string (no phase label or progress info).
    """
    if during_probe:
        return f"Got it, I'll keep your earlier answer for Q{question_num}."
    if default is not None:
        return f'Noted — I\'ll assume: **"{default}"** (you can change this in the summary).'
    return f"Skipped Q{question_num} — I'll work without this information."


def _check_vague_answer(question: str, answer: str, q_num: int = 0) -> tuple[str, tuple[str, ...]] | None:
    """Use the LLM to judge whether an intake answer is too vague.

    # See README: "Project Intake Questionnaire" — follow-up probing
    #
    # Why LLM detection (not heuristics)?
    # "React" is specific for Q11 (tech stack) but vague for Q1 (project
    # description). Vagueness depends on question context — only the LLM
    # can judge this reliably across all 26 questions.
    #
    # Performance short-circuit: answers longer than 100 characters are
    # assumed to be detailed enough — skip the LLM call entirely.
    #
    # Dynamic choices: when the answer is vague, the LLM also generates 2-4
    # contextual options for the follow-up so the user can pick a number
    # instead of composing a free-text answer from scratch.
    #
    # Custom follow-up templates (Step 4): when q_num has a FOLLOW_UP_TEMPLATES
    # entry, the template is injected into the prompt as a hint. This produces
    # more targeted follow-ups for key questions.

    Args:
        question: The intake question that was asked.
        answer: The user's response to the question.
        q_num: The question number (used for custom follow-up templates).

    Returns:
        A (follow_up, choices) tuple if the answer is vague, where choices
        is a tuple of 2-4 option strings (may be empty if the LLM didn't
        provide valid choices — degrades to open-ended follow-up). Returns
        None if the answer is specific enough or on any error (graceful
        fallback — the feature never breaks the existing flow).
    """
    # Short-circuit: long answers are assumed to be detailed enough.
    if len(answer.strip()) > 100:
        return None

    # Short-circuit: numeric answers (e.g. "7" for team size, "3" for
    # sprint count) are precise by definition — never vague.
    try:
        float(answer.strip())
        return None
    except ValueError:
        pass

    # Build custom follow-up hint if available for this question
    custom_hint = ""
    if q_num and q_num in FOLLOW_UP_TEMPLATES:
        custom_hint = f"\nIf vague, use this follow-up: '{FOLLOW_UP_TEMPLATES[q_num]}'\n"

    prompt = (
        "You are evaluating whether a user's answer to a project intake question "
        "is specific enough to be useful for Scrum planning.\n\n"
        f'Question: "{question}"\n'
        f'Answer: "{answer}"\n\n'
        "IMPORTANT rules for judging vagueness:\n"
        "- Named technologies, tools, or frameworks (e.g. 'Angular', 'Python', 'PostgreSQL') "
        "are SPECIFIC answers to tech stack questions — NOT vague.\n"
        "- Short answers can still be specific. 'React' is specific, 'something modern' is vague.\n"
        "- Only flag an answer as vague if it truly lacks actionable detail "
        "(e.g. 'stuff', 'not sure', 'the usual', 'some things').\n\n"
        f"{custom_hint}"
        "If the answer is too vague or generic to be actionable, respond with JSON:\n"
        '{"vague": true, "follow_up": "A specific follow-up question to get more detail", '
        '"choices": ["Option A", "Option B", "Option C"]}\n\n'
        "The follow_up MUST ask for more detail about the SAME topic as the original question. "
        "Do NOT change the subject or ask about a different topic.\n"
        "The choices array should contain 2-4 concrete, contextually relevant options "
        "that the user can pick from to clarify their answer. Each choice should be a "
        "short phrase (not a full sentence).\n\n"
        "If the answer is specific enough, respond with JSON:\n"
        '{"vague": false}\n\n'
        "Return ONLY the JSON object, no other text."
    )

    try:
        response = get_llm(temperature=0.0).invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()

        # Strip markdown code fences that LLMs sometimes wrap JSON in
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

        parsed = json.loads(raw)

        if not isinstance(parsed, dict):
            return None

        if parsed.get("vague") is True:
            follow_up = parsed.get("follow_up", "")
            if isinstance(follow_up, str) and follow_up.strip():
                # Parse and validate choices — graceful fallback to empty tuple
                choices = _parse_follow_up_choices(parsed.get("choices"))
                return (follow_up.strip(), choices)

        return None

    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            raise
        # Graceful fallback: if anything goes wrong (bad JSON, LLM timeout,
        # network error), accept the answer as-is. The feature never breaks
        # the existing flow.
        logger.debug("Vague-answer check failed, accepting answer as-is", exc_info=True)
        return None


def _parse_follow_up_choices(raw_choices: object) -> tuple[str, ...]:
    """Validate and clamp LLM-generated follow-up choices to 2-4 non-empty strings.

    Graceful degradation: if the input is missing, not a list, or contains
    non-string / empty items, returns an empty tuple (the follow-up degrades
    to the current open-ended behavior — no menu shown).

    Args:
        raw_choices: The raw "choices" value from the LLM JSON response.

    Returns:
        A tuple of 2-4 non-empty strings, or an empty tuple if invalid.
    """
    if not isinstance(raw_choices, list):
        return ()

    # Filter to non-empty strings only
    cleaned = [c.strip() for c in raw_choices if isinstance(c, str) and c.strip()]

    # Enforce 2-4 range — too few is useless, clamp excess
    if len(cleaned) < 2:
        return ()
    return tuple(cleaned[:4])


def _validate_cross_questions(answers: dict[int, str]) -> list[ValidationWarning]:
    """Run deterministic cross-question validation rules on completed answers.

    # See README: "Project Intake Questionnaire" — cross-question validation
    #
    # Three rules (no LLM call):
    # 1. Greenfield + repo URL → contradiction
    # 2. Timeline > 6 months → advisory
    # 3. Velocity / team_size outside 2-15 range → sanity check

    Args:
        answers: The questionnaire answers dict (q_num → answer string).

    Returns:
        A list of ValidationWarning instances (may be empty).
    """
    warnings: list[ValidationWarning] = []

    # Rule 1: Greenfield + repo URL
    q2 = answers.get(2, "")
    q17 = answers.get(17, "")
    if q2.lower().strip() == "greenfield" and q17 and "http" in q17.lower():
        warnings.append(
            ValidationWarning(
                question_nums=(2, 17),
                message="Q2 says Greenfield but Q17 has a repo URL — did you mean Existing codebase?",
            )
        )

    # Rule 2: Timeline > 6 months
    q8 = answers.get(8, "")
    q10 = answers.get(10, "")
    sprint_weeks = _parse_first_int(q8) or 2
    q10_nums = re.findall(r"\d+", q10)
    if q10_nums:
        target_sprints = int(q10_nums[-1])
        total_weeks = sprint_weeks * target_sprints
        if total_weeks > 26:
            months = round(total_weeks / 4.3, 1)
            warnings.append(
                ValidationWarning(
                    question_nums=(8, 10),
                    message=f"Plan spans ~{months} months. Consider breaking into phases.",
                    severity="info",
                )
            )

    # Rule 3: Velocity sanity check
    q6 = answers.get(6, "")
    q9 = answers.get(9, "")
    team_size = _parse_first_int(q6)
    velocity = _parse_first_int(q9)
    if team_size and team_size > 0 and velocity and velocity > 0:
        ratio = velocity / team_size
        if ratio < 2 or ratio > 15:
            warnings.append(
                ValidationWarning(
                    question_nums=(6, 9),
                    message=(
                        f"Velocity of {velocity} with {team_size} engineers = "
                        f"{ratio:.1f} pts/engineer. Typical range: 3-10."
                    ),
                )
            )

    return warnings


def _build_intake_summary(questionnaire: QuestionnaireState) -> str:
    """Build a formatted markdown summary of all intake answers grouped by phase.

    Called once when the questionnaire is complete. The summary is sent as an
    AIMessage so it appears in the conversation history for the main agent to
    reference when generating Scrum artifacts.

    Four-tier answer display:
    - Extracted: `> {answer}  *(from your description)*`
    - Normal answer: `> {answer}`
    - Defaulted: `> {answer}  *(assumed default)*`
    - Skipped with no default: `> _skipped (no default available)_`
    """
    sections: list[str] = []
    for phase, (start, end) in PHASE_QUESTION_RANGES.items():
        label = PHASE_LABELS[phase]
        lines = [f"## {label}\n"]
        for q_num in range(start, end + 1):
            question = INTAKE_QUESTIONS[q_num]
            answer = questionnaire.answers.get(q_num)
            lines.append(f"**Q{q_num}.** {question}")
            if answer is None:
                # No answer recorded and not defaulted — truly skipped
                lines.append("> _skipped (no default available)_\n")
            elif q_num in questionnaire.extracted_questions:
                source = "from SCRUM.md" if q_num in questionnaire._scrum_md_questions else "from your description"
                lines.append(f"> {answer}  *({source})*\n")
            elif q_num in questionnaire.defaulted_questions:
                lines.append(f"> {answer}  *(assumed default)*\n")
            else:
                lines.append(f"> {answer}\n")
        sections.append("\n".join(lines))

    # Stats header — shows answer provenance at a glance
    # Use answer_sources for the confidence breakdown when available,
    # fall back to the legacy tracking sets for backward compatibility.
    sources = questionnaire.answer_sources
    if sources:
        num_direct = sum(1 for v in sources.values() if v == AnswerSource.DIRECT)
        num_extracted = sum(1 for v in sources.values() if v == AnswerSource.EXTRACTED)
        num_defaulted = sum(1 for v in sources.values() if v == AnswerSource.DEFAULTED)
        num_probed = sum(1 for v in sources.values() if v == AnswerSource.PROBED)
    else:
        num_direct = len(
            questionnaire.answers.keys() - questionnaire.extracted_questions - questionnaire.defaulted_questions
        )
        num_extracted = len(questionnaire.extracted_questions)
        num_defaulted = len(questionnaire.defaulted_questions)
        num_probed = len(questionnaire.probed_questions)
    stats_parts: list[str] = []
    if num_direct > 0:
        stats_parts.append(f"{num_direct} direct")
    if num_extracted > 0:
        stats_parts.append(f"{num_extracted} extracted")
    if num_defaulted > 0:
        stats_parts.append(f"{num_defaulted} defaulted")
    if num_probed > 0:
        stats_parts.append(f"{num_probed} probed")
    stats_line = f"**{' | '.join(stats_parts)}**\n\n" if stats_parts else ""

    # Cross-question validation — advisory warnings for contradictions/unrealistic combos
    validation_warnings = _validate_cross_questions(questionnaire.answers)
    warnings_block = ""
    if validation_warnings:
        warning_lines = ["**Heads up** — a few things worth double-checking:\n"]
        for w in validation_warnings:
            q_refs = ", ".join(f"Q{q}" for q in w.question_nums)
            warning_lines.append(f"- {w.message} (edit {q_refs})")
        warnings_block = "\n".join(warning_lines) + "\n\n"

    header = (
        f"# Project Intake Summary\n\n{stats_line}{warnings_block}"
        "Here's a summary of your answers. I'll use this to generate your Scrum plan.\n"
    )
    body = "\n---\n\n".join(sections)

    # Add a footer when defaults were used so the user knows to review assumptions
    if num_defaulted > 0:
        footer = (
            f"\n\n**Note:** {num_defaulted} answer(s) above are assumed defaults (marked with *assumed default*)."
            " You can revise these before I generate the Scrum plan."
        )
        result = header + body + footer
    else:
        result = header + body

    # Append velocity breakdown section if team size is available.
    # See README: "Scrum Standards" — capacity planning
    extracted = _extract_team_and_velocity(questionnaire)
    if extracted:
        ts = extracted["team_size"]
        vel = extracted["velocity_per_sprint"]
        velocity_was_calculated = extracted.get("_velocity_was_calculated", False)
        velocity_source = "estimated" if velocity_was_calculated else "manual"
        # Check if velocity came from Jira (Q9 has "from Jira" marker)
        q9 = questionnaire.answers.get(9, "")
        if "from Jira" in q9 or "from jira" in q9:
            velocity_source = "jira"

        sprint_weeks = _parse_first_int(questionnaire.answers.get(8, "2 weeks")) or 2
        q10_nums = re.findall(r"\d+", questionnaire.answers.get(10, ""))
        target = int(q10_nums[-1]) if q10_nums else 6
        capacity = _extract_capacity_deductions(questionnaire)

        # Use override if set
        override = questionnaire._velocity_override
        net_vel, breakdown = _build_velocity_breakdown(
            velocity_per_sprint=vel,
            velocity_source=velocity_source,
            team_size=ts,
            sprint_length_weeks=sprint_weeks,
            target_sprints=target,
            bank_holiday_days=capacity["capacity_bank_holiday_days"],
            planned_leave_days=capacity["capacity_planned_leave_days"],
            unplanned_leave_pct=capacity["capacity_unplanned_leave_pct"],
            onboarding_engineer_sprints=capacity["capacity_onboarding_engineer_sprints"],
            ktlo_engineers=capacity["capacity_ktlo_engineers"],
            discovery_pct=capacity["capacity_discovery_pct"],
            planned_leave_entries=list(questionnaire._planned_leave_entries),
        )

        if override is not None:
            display_vel = override
            breakdown += f"\n\n**User override: {override} pts/sprint**"
        else:
            display_vel = net_vel

        result += f"\n\n---\n\n{breakdown}"

        # Per-sprint breakdown — when bank holidays or PTO hit specific sprints,
        # show which sprints are impacted so the user sees the uneven capacity.
        from datetime import date

        holidays_by_sprint = _assign_holidays_to_sprints(
            questionnaire._detected_bank_holidays,
            date.today().isoformat(),
            sprint_weeks,
            target,
        )
        leave_by_sprint = _assign_leave_to_sprints(
            questionnaire._planned_leave_entries,
            date.today().isoformat(),
            sprint_weeks,
            target,
        )
        if holidays_by_sprint or leave_by_sprint:
            sprint_caps = _compute_per_sprint_velocities(
                team_size=ts,
                velocity_per_sprint=vel,
                sprint_length_weeks=sprint_weeks,
                target_sprints=target,
                holidays_by_sprint=holidays_by_sprint,
                planned_leave_days=capacity["capacity_planned_leave_days"],
                unplanned_leave_pct=capacity["capacity_unplanned_leave_pct"],
                onboarding_engineer_sprints=capacity["capacity_onboarding_engineer_sprints"],
                ktlo_engineers=capacity["capacity_ktlo_engineers"],
                discovery_pct=capacity["capacity_discovery_pct"],
                leave_by_sprint=leave_by_sprint,
            )
            has_uneven = any(sc["bank_holiday_days"] > 0 or sc.get("pto_days", 0) > 0 for sc in sprint_caps)
            if has_uneven:
                per_sprint_lines = ["\n**Per-sprint capacity:**"]
                total_pts = 0
                for sc in sprint_caps:
                    nv = sc["net_velocity"]
                    total_pts += nv
                    label = f"Sprint {sc['sprint_index'] + 1}"
                    annotations = []
                    if sc["bank_holiday_names"]:
                        names = ", ".join(sc["bank_holiday_names"])
                        annotations.append(f"−{sc['bank_holiday_days']}d: {names}")
                    if sc.get("pto_days", 0) > 0:
                        pto_names = ", ".join(f"{e['person']} {e['days']}d" for e in sc.get("pto_entries", []))
                        annotations.append(f"PTO: {pto_names}")
                    if annotations:
                        per_sprint_lines.append(f"  {label}: **{nv} pts** ({'; '.join(annotations)})")
                    else:
                        per_sprint_lines.append(f"  {label}: **{nv} pts**")
                per_sprint_lines.append(f"  Total: **{total_pts} pts** across {len(sprint_caps)} sprints")
                result += "\n".join(per_sprint_lines)

        result += f"\n\n[1] Accept {display_vel} pts/sprint"
        result += "\n[2] Override — enter a custom velocity"

    return result


_CONFIRM_PROMPT = (
    "\n\nPlease review the summary above. Select a velocity option above, or type **edit Q6** to change an answer."
)


# ── Review intent detection ─────────────────────────────────────────
# See README: "Guardrails" — human-in-the-loop pattern
#
# After each generation node (feature_generator, story_writer, task_decomposer,
# sprint_planner), the user reviews the output and chooses Accept / Edit / Reject.
# This is deterministic keyword matching — same pattern as _is_skip_intent() and
# _is_confirm_intent(). No LLM call needed.
#
# Design: unrecognized text defaults to REJECT with the full text as feedback.
# This is the most natural UX — typing "add a security feature" without a keyword
# prefix is clearly rejection feedback.

_ACCEPT_KEYWORDS: frozenset[str] = frozenset(
    {"accept", "approve", "ok", "yes", "y", "looks good", "lgtm", "proceed", "continue", "good", "fine"}
)

_REJECT_KEYWORDS: frozenset[str] = frozenset({"reject", "redo", "regenerate", "again", "try again", "no", "n"})

_EDIT_BARE_KEYWORDS: frozenset[str] = frozenset({"edit", "change", "modify", "update", "adjust", "tweak", "revise"})

_EDIT_PREFIXES: tuple[str, ...] = (
    "edit:",
    "change:",
    "modify:",
    "update:",
    "adjust:",
    "tweak:",
    "revise:",
    "edit ",
    "change ",
    "modify ",
    "update ",
    "adjust ",
    "tweak ",
    "revise ",
)


def _parse_review_intent(text: str) -> tuple[ReviewDecision, str]:
    """Detect the user's review intent from their input text.

    # See README: "Guardrails" — human-in-the-loop pattern
    #
    # Three possible outcomes:
    # - ACCEPT: user approves the output, pipeline continues
    # - EDIT: user wants specific modifications (feedback extracted)
    # - REJECT: user wants a full regeneration (feedback extracted)
    #
    # Fallback: unrecognized text → REJECT with full text as feedback.
    # This is the most natural UX — "add a security feature" without a prefix
    # is clearly rejection feedback.

    Args:
        text: The raw user input text.

    Returns:
        A tuple of (ReviewDecision, feedback_string). Feedback is "" for
        accept, and the user's feedback text for edit/reject.
    """
    normalized = text.strip().lower()

    # Check accept keywords first — exact match only
    if normalized in _ACCEPT_KEYWORDS:
        return (ReviewDecision.ACCEPT, "")

    # Check edit prefixes — extract feedback after the prefix
    for prefix in _EDIT_PREFIXES:
        if normalized.startswith(prefix):
            feedback = text.strip()[len(prefix) :].strip()
            return (ReviewDecision.EDIT, feedback)

    # Check bare edit keywords — exact match returns EDIT with no feedback
    # (the REPL will prompt for feedback). This handles "2" → "edit" from
    # the numbered menu, as well as typing "edit" directly.
    if normalized in _EDIT_BARE_KEYWORDS:
        return (ReviewDecision.EDIT, "")

    # Check reject keywords — may have inline feedback after ":"
    for keyword in _REJECT_KEYWORDS:
        if normalized == keyword:
            return (ReviewDecision.REJECT, "")
        if normalized.startswith(keyword + ":"):
            feedback = text.strip()[len(keyword) + 1 :].strip()
            return (ReviewDecision.REJECT, feedback)

    # Fallback: unrecognized text → REJECT with the full text as feedback
    return (ReviewDecision.REJECT, text.strip())


def route_entry(state: ScrumState) -> str:
    """Route from START: seven-way branch based on pipeline progress.

    # See README: "Agentic Blueprint Reference" — conditional edges
    #
    # This is a conditional edge function that runs at the START of every
    # graph invocation. It checks questionnaire, analysis, feature, story,
    # task, and sprint state for seven-way routing:
    #   - Questionnaire not completed → "project_intake" node
    #   - Questionnaire completed, no project_analysis → "project_analyzer"
    #   - Analysis done, no features → "feature_generator"
    #   - Features done, no stories → "story_writer"
    #   - Stories done, no tasks → "task_decomposer"
    #   - Tasks done, no sprints → "sprint_planner"
    #   - Sprints populated → "agent" node (main ReAct loop)
    #
    # Why check `not state.get("sprints")`?
    # Same pattern as tasks — empty list is falsy, None is falsy. Once the
    # sprint_planner populates the list, it becomes truthy and we route to the agent.
    """
    questionnaire = state.get("questionnaire")
    if questionnaire is None or not questionnaire.completed:
        return "project_intake"
    if state.get("project_analysis") is None:
        return "project_analyzer"
    if not state.get("features"):
        # When the analyzer determined the project is too small for features,
        # skip feature generation and use a sentinel feature instead.
        # See README: "Scrum Standards" — feature generation
        analysis = state.get("project_analysis")
        if analysis and analysis.skip_features:
            return "feature_skip"
        return "feature_generator"
    if not state.get("stories"):
        return "story_writer"
    if not state.get("tasks"):
        return "task_decomposer"
    if not state.get("sprints"):
        return "sprint_planner"
    return "agent"


def _show_summary_or_pto(questionnaire: QuestionnaireState, prefix: str = "") -> dict:
    """Show the confirmation summary OR trigger the PTO sub-loop first.

    # See README: "Scrum Standards" — capacity planning
    #
    # In smart and standard modes, PTO is always asked interactively before the
    # confirmation summary because it's per-person knowledge the system can't
    # auto-detect. In quick mode, auto-default to "no planned leave".
    #
    # After PTO is resolved (or skipped), the confirmation summary is shown
    # with the velocity choice menu as usual.
    """
    # Check if PTO should be asked before the summary
    if (
        questionnaire.intake_mode != "quick"
        and not questionnaire._leave_input_stage
        and not questionnaire._awaiting_leave_input
        and not questionnaire._planned_leave_entries
    ):
        questionnaire._awaiting_leave_input = True
        questionnaire._leave_input_stage = "ask"
        # Set awaiting_confirmation so the PTO handler runs in the confirmation gate
        questionnaire.awaiting_confirmation = True
        # Show PTO under Q28 (Holidays & leave) in the accordion — semantically
        # PTO belongs with the leave/holidays section, not onboarding (Q30).
        questionnaire.current_question = 28
        return {
            "questionnaire": questionnaire,
            "messages": [
                AIMessage(
                    content=f"{prefix}Does anyone have planned leave (PTO/vacation) during the planning window?\n\n"
                    "[1] Yes\n[2] No"
                )
            ],
        }

    # PTO already handled (or quick mode) — show the confirmation summary
    questionnaire.current_question = TOTAL_QUESTIONS + 1
    questionnaire.awaiting_confirmation = True
    summary = _build_intake_summary(questionnaire)
    return {
        "questionnaire": questionnaire,
        "messages": [AIMessage(content=f"{prefix}{summary}{_CONFIRM_PROMPT}")],
        "pending_review": "project_intake",
    }


def project_intake(state: ScrumState) -> dict:
    """LangGraph node: ask one intake question per graph invocation.

    # See README: "Scrum Standards" — questionnaire phases
    # See README: "Agentic Blueprint Reference" — node return format
    #
    # How this works:
    # 1. First call (no questionnaire in state) → initialize QuestionnaireState,
    #    ask Q1 with a phase header.
    # 2. Subsequent calls → record the user's answer from the last HumanMessage,
    #    advance to the next question, and ask it.
    # 3. After Q26 is answered → mark completed, return a formatted summary
    #    of all answers grouped by phase.
    #
    # Why one question per invocation (not all at once)?
    # Each graph.invoke() call processes one user turn. The REPL collects the
    # user's answer, then calls graph.invoke() again with the updated state.
    # This gives the user a conversational, one-question-at-a-time experience
    # rather than a wall of 26 questions.
    #
    # Why deterministic questions (not LLM-generated)?
    # Using a predefined question list ensures all 26 questions are asked
    # consistently, in order, and enables progress tracking. Follow-up probing
    # (a separate TODO) will add LLM-powered refinement later.
    """
    questionnaire = state.get("questionnaire")

    if questionnaire is None:
        # First call — initialize questionnaire and attempt adaptive skip.
        # See README: "Project Intake Questionnaire" — adaptive skip logic
        #
        # If the user typed a project description at the scrum> prompt before
        # Q1, we send it to the LLM to extract any answers it can find. This
        # pre-populates the questionnaire so the user only answers remaining
        # questions they haven't already covered.
        qs = QuestionnaireState()

        # Read intake_mode from the REPL-injected state key.
        # Default to "standard" for backward compatibility with tests that
        # don't set _intake_mode.
        intake_mode = state.get("_intake_mode", "standard")
        qs.intake_mode = intake_mode

        # Extract initial description from the first message (if present).
        # The REPL sends the user's first input as a HumanMessage before
        # the first graph invocation reaches this node.
        description = ""
        if state.get("messages"):
            first_msg = state["messages"][0]
            if isinstance(first_msg, HumanMessage):
                description = first_msg.content

        extracted = _extract_answers_from_description(description)
        # Deterministic keyword fallback — catches strong signals the LLM
        # may have been too conservative to infer (Q2, Q12, Q13).
        _keyword_extract_fallback(description, extracted)

        # ── SCRUM.md auto-population ─────────────────────────────────
        # Load SCRUM.md early so its content can pre-fill intake answers,
        # avoiding duplicate data entry when the user has already documented
        # project context in the file.
        # See README: "Tools" — read-only tool pattern
        scrum_md_context, _scrum_status = _load_user_context()
        scrum_extracted: dict[int, str] = {}
        _scrum_md_contributed: set[int] = set()
        if scrum_md_context:
            scrum_extracted = _extract_answers_from_description(scrum_md_context)
            # Merge: user's typed description wins over SCRUM.md.
            # SCRUM.md fills gaps the description didn't cover.
            for q_num, answer in scrum_extracted.items():
                if q_num not in extracted:
                    extracted[q_num] = answer
                    _scrum_md_contributed.add(q_num)
            if scrum_extracted:
                logger.debug(
                    "SCRUM.md extracted %d answers (questions: %s), contributed %d new",
                    len(scrum_extracted),
                    sorted(scrum_extracted.keys()),
                    len(_scrum_md_contributed),
                )

        # ── Smart / quick mode first-invocation ──────────────────────
        # See README: "Project Intake Questionnaire" — smart intake
        #
        # In smart/quick mode, extracted answers are auto-accepted (not
        # shown for confirmation). Defaults are auto-applied to all
        # non-essential questions. Only essential gaps are asked.
        if intake_mode in ("smart", "quick"):
            logger.debug("BANK_HOLIDAY: entering %s intake mode", intake_mode)
            # Store SCRUM.md provenance so the preamble can report it
            qs._scrum_md_questions = _scrum_md_contributed
            # Q1 is always the user's description (first message)
            if description.strip():
                qs.answers[1] = description.strip()
                qs.extracted_questions.add(1)
                qs.answer_sources[1] = AnswerSource.EXTRACTED

            # Pick essential set based on mode (needed before extraction split)
            essential_set = QUICK_ESSENTIALS if intake_mode == "quick" else SMART_ESSENTIALS

            # Split extractions: essential questions go to suggested_answers
            # (user confirms or overrides), non-essential go to answers (auto-accepted).
            # This ensures essential questions are always asked interactively,
            # with the extracted value shown as a pre-filled suggestion.
            if extracted:
                essential_extractions = {q: a for q, a in extracted.items() if q in essential_set}
                non_essential_extractions = {q: a for q, a in extracted.items() if q not in essential_set}
                if non_essential_extractions:
                    _auto_apply_extractions(qs, non_essential_extractions)
                if essential_extractions:
                    qs.suggested_answers.update(essential_extractions)
                if 17 in extracted:
                    _sync_platform_from_url(qs)
            fallbacks = QUICK_FALLBACK_DEFAULTS if intake_mode == "quick" else None

            # Auto-default all non-essential, non-answered questions
            _auto_default_remaining(qs, essential_set, fallbacks)

            # Derive Q15 from Q2 if available
            _derive_q15_from_q2(qs)

            # Q27 sprint selection: no Jira → auto-default to "Fresh start (today)".
            # Q27 is in SMART_ESSENTIALS, but auto-deriving fills the answer so it
            # won't appear as a gap when Jira is absent.
            # Q28 (bank holidays) choices are prepared so the user sees a confirmation.
            # See README: "Scrum Standards" — capacity planning
            if _is_jira_configured():
                # Fire both Jira calls concurrently — they are independent HTTP
                # requests and running them in parallel halves the wait time.
                # See README: "Scrum Standards" — capacity planning
                from concurrent.futures import ThreadPoolExecutor

                need_velocity = 9 not in qs.answers or 9 in qs.defaulted_questions
                with ThreadPoolExecutor(max_workers=2) as pool:
                    vel_future = pool.submit(_fetch_jira_velocity) if need_velocity else None
                    sprint_future = pool.submit(_fetch_active_sprint_number)
                    jira_data = vel_future.result() if vel_future else None
                    active_result = sprint_future.result()

                # Apply velocity data from Jira if Q9 hasn't been answered yet.
                # Uses per-dev velocity × feature team size (Q6) so the velocity
                # reflects the subset of the Jira team working on this feature.
                if need_velocity and jira_data is not None:
                    jira_team_size = jira_data["jira_team_size"]
                    # Always store the Jira org team size — used to cap the
                    # "increase team" recommendation even when velocity is zero.
                    qs._jira_org_team_size = jira_team_size

                    if "velocity_error" not in jira_data:
                        per_dev = jira_data["per_dev_velocity"]
                        team_vel = jira_data["team_velocity"]
                        qs._jira_per_dev_velocity = per_dev

                        # Scale by Q6 (feature team size) if available
                        q6 = _parse_first_int(qs.answers.get(6, ""))
                        if q6 and q6 > 0:
                            feature_vel = round(per_dev * q6)
                            qs.answers[9] = (
                                f"{feature_vel} pts/sprint "
                                f"({per_dev:.0f} pts/dev × {q6} dev(s) — "
                                f"from Jira: {team_vel} pts team avg, "
                                f"{jira_team_size} team member(s))"
                            )
                        else:
                            # Q6 not yet answered — store per-dev rate,
                            # will be recomputed at confirmation
                            qs.answers[9] = (
                                f"{per_dev:.0f} pts/dev/sprint "
                                f"(from Jira: {team_vel} pts team avg, "
                                f"{jira_team_size} team member(s))"
                            )
                        qs.extracted_questions.add(9)
                        qs.defaulted_questions.discard(9)
                        qs.answer_sources[9] = AnswerSource.EXTRACTED
                    else:
                        logger.debug(
                            "Jira velocity zero but org team size=%d stored",
                            jira_team_size,
                        )

                # Unpack active sprint result (fetched concurrently above)
                active_num, active_start, jira_status = active_result
            else:
                _derive_q27_from_locale(qs)
                active_num, active_start, jira_status = None, None, ""
            # Find remaining essential gaps (bank holidays are detected later,
            # once Q10 has its final answer — not here where Q10 may still be
            # the default "6 sprints").
            gaps = _find_essential_gaps(qs, essential_set)

            # Build extraction summary for the preamble
            num_from_desc = len(qs.extracted_questions - qs._scrum_md_questions)
            num_from_scrum = len(qs.extracted_questions & qs._scrum_md_questions)
            num_defaulted = len(qs.defaulted_questions)
            preamble_parts: list[str] = []
            if num_from_desc > 0:
                preamble_parts.append(f"**{num_from_desc}** extracted from your description")
            if num_from_scrum > 0:
                preamble_parts.append(f"**{num_from_scrum}** from SCRUM.md")
            if num_defaulted > 0:
                preamble_parts.append(f"**{num_defaulted}** filled with defaults")
            preamble = ""
            if preamble_parts:
                preamble = "I " + " and ".join(preamble_parts) + ".\n\n"

            if not gaps:
                # All essentials filled — detect bank holidays now that Q10 is
                # finalized, then ask PTO or jump straight to summary.
                _prepare_bank_holiday_choices(qs)
                return _show_summary_or_pto(qs, prefix=preamble)

            # Ask the first gap (or merged Q3+Q4)
            prompt_text, q_nums = _build_gap_prompt(gaps, qs)
            qs._pending_merged_questions = q_nums
            # Set current_question to the first gap being asked
            qs.current_question = q_nums[0]

            # Q27 with Jira: use the active sprint number (fetched concurrently above)
            # to populate dynamic choices for the sprint selection menu
            if q_nums[0] == 27 and active_num is not None:
                qs._active_sprint_number = active_num
                qs._active_sprint_start_date = active_start
                qs.answers[27] = f"_active:{active_num}"
                qs._follow_up_choices[27] = (
                    f"Sprint {active_num + 1} (next)",
                    f"Sprint {active_num + 2}",
                    f"Sprint {active_num + 3}",
                )
                prompt_text = (
                    f"Detected active sprint in Jira: **Sprint {active_num}**.\n\nWhich sprint are you planning for?"
                )
            elif q_nums[0] == 27 and _is_jira_configured() and active_num is None:
                # Couldn't fetch sprint — tell the user why, then fall back
                logger.warning("Jira sprint fetch failed: %s", jira_status)
                _derive_q27_from_locale(qs)
                gaps = _find_essential_gaps(qs, essential_set)
                if not gaps:
                    # All essentials filled — detect bank holidays with
                    # finalized Q10, then ask PTO or show summary.
                    _prepare_bank_holiday_choices(qs)
                    return _show_summary_or_pto(qs, prefix=preamble)
                prompt_text, q_nums = _build_gap_prompt(gaps, qs)
                qs._pending_merged_questions = q_nums
                qs.current_question = q_nums[0]

            remaining_text = f"A few more questions ({len(gaps)} remaining):" if len(gaps) > 1 else "One more question:"
            return {
                "questionnaire": qs,
                "messages": [AIMessage(content=f"{preamble}{remaining_text}\n\n{prompt_text}")],
            }

        # ── Standard mode first-invocation (original 26-Q flow) ─────
        if extracted:
            # Store extracted answers as confirmable suggestions — the user
            # will see each one inline and can press Enter to confirm or
            # type a different answer. Questions are NOT skipped.
            qs.suggested_answers.update(extracted)

        # Always start at Q1 — even with suggestions, we walk through
        # every question so the user can confirm or override.
        question = INTAKE_QUESTIONS[1]
        phase_label = PHASE_LABELS[qs.current_phase]
        phase_intro = PHASE_INTROS.get(qs.current_phase, "")
        intro_line = f"*{phase_intro}*\n\n" if phase_intro else ""

        # If Q1 has a suggestion from the description, show it inline
        suggestion = qs.suggested_answers.get(1)
        suggest_line = f"\n\n> Suggested: **{suggestion}**" if suggestion else ""

        preamble = ""
        if extracted:
            preamble = (
                f"I picked up **{len(extracted)}** detail(s) from your description "
                "— I'll show each one for you to confirm or change.\n\n"
            )

        return {
            "questionnaire": qs,
            "messages": [AIMessage(content=f"{preamble}**{phase_label}**\n\n{intro_line}{question}{suggest_line}")],
        }

    # Record the user's answer to the current question.
    # The last message in state is always the HumanMessage with the user's reply.
    last_msg = state["messages"][-1]
    current_q = questionnaire.current_question

    # ── Edit re-ask handler ──────────────────────────────────────────
    # See README: "Project Intake Questionnaire" — edit flow
    #
    # When editing_question is set, the user is answering a re-asked
    # question from the confirmation summary. Record their new answer
    # (or keep the current one on skip), clear editing state, and
    # re-show the updated summary.
    if questionnaire.editing_question is not None:
        eq = questionnaire.editing_question
        if _is_skip_intent(last_msg.content):
            # Skip during re-ask → keep the current answer unchanged
            pass
        else:
            # Record new answer, clear any defaulted/skipped flags for this question
            questionnaire.answers[eq] = last_msg.content
            questionnaire.defaulted_questions.discard(eq)
            questionnaire.skipped_questions.discard(eq)

        questionnaire.editing_question = None
        summary = _build_intake_summary(questionnaire)
        return {
            "questionnaire": questionnaire,
            "messages": [AIMessage(content=f"{summary}{_CONFIRM_PROMPT}")],
            "pending_review": "project_intake",
        }

    # ── Smart / quick mode gap-filling ──────────────────────────────
    # See README: "Project Intake Questionnaire" — smart intake
    #
    # In smart/quick mode, after the first invocation, the user is
    # answering essential gap questions one at a time. Record the
    # answer, derive Q15 if Q2 was just answered, then find the next
    # gap or show the summary.
    if (
        questionnaire.intake_mode in ("smart", "quick")
        and not questionnaire.awaiting_confirmation
        and questionnaire.editing_question is None
    ):
        # Confirmation prefix for repo URL detection — set after _sync_platform_from_url
        # and prepended to the next AIMessage so the user sees immediate feedback.
        repo_confirm = ""

        # ── Follow-up probe response ──────────────────────────────
        # If this question was already probed for vagueness, the user
        # is responding to the follow-up. Combine original + follow-up
        # answers (same logic as standard mode) then advance.
        if current_q in questionnaire.probed_questions:
            if current_q == 2 and questionnaire.answers.get(2) in _EXISTING_CODEBASE_ANSWERS:
                # This is the repo URL follow-up — store in Q17, not combined into Q2.
                # Q17 is "Can you share the repo URL(s)?" — storing here lets the
                # repo scan and platform detection use it downstream.
                questionnaire.answers[17] = last_msg.content
                questionnaire.defaulted_questions.discard(17)
                _sync_platform_from_url(questionnaire)
                platform = questionnaire.answers.get(16, "")
                if platform:
                    repo_confirm = f"*✓ {platform} repo detected — will be scanned during analysis.*\n\n"
            else:
                original = questionnaire.answers.get(current_q, "")
                combined = f"{original}\n\n(Follow-up detail: {last_msg.content})"
                questionnaire.answers[current_q] = combined
            questionnaire._follow_up_choices.pop(current_q, None)
        else:
            # Record answer for the primary question first. For merged Q3+Q4,
            # only store the primary question's answer here — secondary questions
            # are stored AFTER the vagueness check passes, so they don't show
            # as completed in the accordion while a follow-up probe is active.
            pending = questionnaire._pending_merged_questions
            if pending:
                for q_num in pending:
                    questionnaire.answers[q_num] = last_msg.content
                    questionnaire.defaulted_questions.discard(q_num)
                    questionnaire.skipped_questions.discard(q_num)
                    questionnaire.answer_sources[q_num] = AnswerSource.DIRECT
                questionnaire._pending_merged_questions = []
            else:
                # Single gap question
                questionnaire.answers[current_q] = last_msg.content
                questionnaire.defaulted_questions.discard(current_q)
                questionnaire.skipped_questions.discard(current_q)
                questionnaire.answer_sources[current_q] = AnswerSource.DIRECT
                if current_q == 17:
                    _sync_platform_from_url(questionnaire)
                    platform = questionnaire.answers.get(16, "")
                    if platform:
                        repo_confirm = f"*✓ {platform} repo detected — will be scanned during analysis.*\n\n"

            # Q2 repo URL follow-up: when Q2 = "Existing codebase"/"Hybrid",
            # prompt for the repo URL inline as part of Q2. The answer is stored
            # in Q17 so downstream repo scan and platform detection can use it.
            # Q2 stays active in the accordion during this follow-up.
            # Check defaulted_questions because _auto_default_remaining may have
            # already filled Q17 with a placeholder default.
            if questionnaire.answers.get(2) in _EXISTING_CODEBASE_ANSWERS and (
                17 not in questionnaire.answers or 17 in questionnaire.defaulted_questions
            ):
                questionnaire.probed_questions.add(2)
                return {
                    "questionnaire": questionnaire,
                    "messages": [AIMessage(content=_Q2_REPO_URL_PROMPT)],
                }

            # ── Vague answer probing ──────────────────────────────
            # See README: "Project Intake Questionnaire" — follow-up probing
            #
            # Same vagueness check as standard mode — even in smart mode,
            # a vague answer to an essential question defeats the purpose.
            # Skip for choice questions (selections are never vague).
            if not is_choice_question(current_q):
                check_text = INTAKE_QUESTIONS[current_q]
                vague_result = _check_vague_answer(check_text, last_msg.content, current_q)
                if vague_result:
                    follow_up, choices = vague_result
                    questionnaire.probed_questions.add(current_q)
                    questionnaire.answer_sources[current_q] = AnswerSource.PROBED
                    if choices:
                        questionnaire._follow_up_choices[current_q] = choices
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content=f"**Follow-up on Q{current_q}:**\n\n{follow_up}")],
                    }

            # Clear pending merged list after answer is stored.
            if pending:
                questionnaire._pending_merged_questions = []

        # Derive Q15 from Q2 if Q2 was just answered
        _derive_q15_from_q2(questionnaire)

        # Q27 sprint selection: resolve the selected sprint.
        # The resolved choice text is "Sprint 105 (next)" — extract the sprint number.
        # Bank holidays are now a separate question (Q28).
        if current_q == 27 and _is_jira_configured():
            q27_answer = questionnaire.answers.get(27, "")
            sprint_num_match = re.search(r"Sprint\s+(\d+)", q27_answer)
            if sprint_num_match:
                questionnaire.answers[27] = f"Sprint {sprint_num_match.group(1)}"
            questionnaire._follow_up_choices.pop(27, None)
            # Prepare bank holiday detection choices for Q28
            _prepare_bank_holiday_choices(questionnaire)

        # Q28 bank holiday: parse the user's answer (same logic as standard mode)
        if current_q == 28:
            q28_answer = questionnaire.answers.get(28, "")
            if "accept" in q28_answer.lower():
                count = questionnaire._detected_bank_holiday_days
                questionnaire.answers[28] = f"{count} bank holiday(s)" if count > 0 else "No bank holidays"
            elif "no bank holidays" in q28_answer.lower():
                questionnaire._detected_bank_holiday_days = 0
                questionnaire.answers[28] = "No bank holidays"
            elif "enter manually" in q28_answer.lower():
                questionnaire._follow_up_choices.pop(28, None)
                questionnaire.answers.pop(28, None)
                questionnaire.probed_questions.add(28)
                return {
                    "questionnaire": questionnaire,
                    "messages": [AIMessage(content="How many bank/public holiday days fall in your planning window?")],
                }
            else:
                parsed = _parse_first_int(q28_answer)
                if parsed is not None:
                    questionnaire._detected_bank_holiday_days = parsed
                    questionnaire.answers[28] = f"{parsed} bank holiday(s)"
            questionnaire._follow_up_choices.pop(28, None)

        # Find remaining essential gaps
        essential_set = QUICK_ESSENTIALS if questionnaire.intake_mode == "quick" else SMART_ESSENTIALS
        gaps = _find_essential_gaps(questionnaire, essential_set)

        if not gaps:
            # All essentials filled — detect bank holidays with finalized
            # Q10 answer, then ask PTO or show summary.
            _prepare_bank_holiday_choices(questionnaire)
            return _show_summary_or_pto(questionnaire, prefix=repo_confirm)

        # Ask the next gap
        prompt_text, q_nums = _build_gap_prompt(gaps, questionnaire)
        questionnaire._pending_merged_questions = q_nums
        questionnaire.current_question = q_nums[0]

        # Q27 with Jira: populate dynamic choices for the sprint selection menu
        if q_nums[0] == 27 and _is_jira_configured():
            active_num, active_start, jira_status = _fetch_active_sprint_number()
            if active_num is not None:
                questionnaire._active_sprint_number = active_num
                questionnaire._active_sprint_start_date = active_start
                questionnaire.answers[27] = f"_active:{active_num}"
                questionnaire._follow_up_choices[27] = (
                    f"Sprint {active_num + 1} (next)",
                    f"Sprint {active_num + 2}",
                    f"Sprint {active_num + 3}",
                )
                prompt_text = (
                    f"Detected active sprint in Jira: **Sprint {active_num}**.\n\nWhich sprint are you planning for?"
                )
            else:
                # Couldn't fetch sprint — tell the user why, then fall back
                logger.warning("Jira sprint fetch failed: %s", jira_status)
                _derive_q27_from_locale(questionnaire)
                gaps = _find_essential_gaps(questionnaire, essential_set)
                if not gaps:
                    _prepare_bank_holiday_choices(questionnaire)
                    return _show_summary_or_pto(questionnaire, prefix=repo_confirm)
                prompt_text, q_nums = _build_gap_prompt(gaps, questionnaire)
                questionnaire._pending_merged_questions = q_nums
                questionnaire.current_question = q_nums[0]

        return {
            "questionnaire": questionnaire,
            "messages": [AIMessage(content=f"{repo_confirm}{prompt_text}")],
        }

    # ── Confirmation gate ────────────────────────────────────────────
    # See README: "Project Intake Questionnaire" — confirmation gate
    #
    # After the last question is answered the summary is shown and
    # awaiting_confirmation is set. On the NEXT invocation, check here:
    #   - User confirms → set completed=True, route to main agent
    #   - User edits a question → inline update or re-ask
    #   - User says anything else → show edit format help + re-show summary
    if questionnaire.awaiting_confirmation:
        # ── Velocity override input handler ──────────────────────────
        # When user picked "Override" from the velocity choice menu,
        # we're waiting for them to type a number.
        if questionnaire._awaiting_velocity_input:
            override = _parse_velocity_override(last_msg.content)
            if override is not None:
                questionnaire._velocity_override = override
                questionnaire._awaiting_velocity_input = False
                # Re-show summary with overridden velocity
                summary = _build_intake_summary(questionnaire)
                return {
                    "questionnaire": questionnaire,
                    "messages": [AIMessage(content=f"{summary}{_CONFIRM_PROMPT}")],
                    "pending_review": "project_intake",
                }
            else:
                return {
                    "questionnaire": questionnaire,
                    "messages": [AIMessage(content="Please enter a number (e.g. **14** or **14 pts/sprint**):")],
                }

        # ── PTO leave sub-loop handler ────────────────────────────────
        # See README: "Scrum Standards" — capacity planning
        #
        # After bank holidays (Q28) are resolved, the PTO sub-loop collects
        # per-person leave entries. This mirrors the _awaiting_velocity_input
        # pattern: a transient state machine driven by flags on QuestionnaireState.
        # In quick mode, PTO is auto-defaulted to "no planned leave".
        if questionnaire._awaiting_leave_input:
            stage = questionnaire._leave_input_stage
            user_text = last_msg.content.strip()

            if stage == "ask":
                # User answering "Does anyone have planned leave?"
                choice = user_text.lower()
                if choice in ("1", "yes", "y"):
                    questionnaire._leave_input_stage = "person"
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content="Who is taking leave? (name or initials):")],
                    }
                elif choice in ("2", "no", "n"):
                    # No planned leave — exit sub-loop
                    questionnaire._awaiting_leave_input = False
                    questionnaire._leave_input_stage = ""
                    summary = _build_intake_summary(questionnaire)
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content=f"{summary}{_CONFIRM_PROMPT}")],
                        "pending_review": "project_intake",
                    }
                else:
                    # Invalid input — re-prompt
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content="Please choose [1] Yes or [2] No:")],
                    }

            elif stage == "person":
                questionnaire._leave_input_buffer = {"person": user_text}
                questionnaire._leave_input_stage = "start"
                return {
                    "questionnaire": questionnaire,
                    "messages": [AIMessage(content="Start date (DD/MM/YYYY):")],
                }

            elif stage == "start":
                parsed = _parse_date_dmy(user_text)
                if parsed is None:
                    return {
                        "questionnaire": questionnaire,
                        "messages": [
                            AIMessage(
                                content="Invalid date format. Please enter "
                                "the start date as **DD/MM/YYYY** (e.g. 06/04/2026):"
                            )
                        ],
                    }
                # Validate date falls within a reasonable window around the planning period
                window_start, window_end = _get_planning_window(questionnaire)
                if window_start and parsed < window_start:
                    return {
                        "questionnaire": questionnaire,
                        "messages": [
                            AIMessage(
                                content=f"Date is before the planning window starts "
                                f"({window_start.strftime('%d/%m/%Y')}). Try again:"
                            )
                        ],
                    }
                if window_end and parsed > window_end:
                    return {
                        "questionnaire": questionnaire,
                        "messages": [
                            AIMessage(
                                content=f"Date is after the planning window ends "
                                f"({window_end.strftime('%d/%m/%Y')}). Try again:"
                            )
                        ],
                    }
                questionnaire._leave_input_buffer["start_date"] = parsed.isoformat()
                questionnaire._leave_input_stage = "end"
                return {
                    "questionnaire": questionnaire,
                    "messages": [AIMessage(content="End date (DD/MM/YYYY):")],
                }

            elif stage == "end":
                parsed = _parse_date_dmy(user_text)
                if parsed is None:
                    return {
                        "questionnaire": questionnaire,
                        "messages": [
                            AIMessage(
                                content="Invalid date format. Please enter "
                                "the end date as **DD/MM/YYYY** (e.g. 10/04/2026):"
                            )
                        ],
                    }
                from datetime import date as _date

                start = _date.fromisoformat(questionnaire._leave_input_buffer["start_date"])
                # Validate end date within planning window
                _, window_end = _get_planning_window(questionnaire)
                if window_end and parsed > window_end:
                    return {
                        "questionnaire": questionnaire,
                        "messages": [
                            AIMessage(
                                content=f"Date is after the planning window ends "
                                f"({window_end.strftime('%d/%m/%Y')}). Try again:"
                            )
                        ],
                    }
                if parsed < start:
                    return {
                        "questionnaire": questionnaire,
                        "messages": [
                            AIMessage(
                                content=f"End date must be on or after start date "
                                f"({start.strftime('%d/%m/%Y')}). Try again:"
                            )
                        ],
                    }
                working = _count_working_days(start, parsed)
                entry = {
                    "person": questionnaire._leave_input_buffer["person"],
                    "start_date": start.isoformat(),
                    "end_date": parsed.isoformat(),
                    "working_days": working,
                }
                questionnaire._planned_leave_entries.append(entry)
                questionnaire._leave_input_buffer = {}
                questionnaire._leave_input_stage = "more?"

                person = entry["person"]
                start_fmt = start.strftime("%d/%m")
                end_fmt = parsed.strftime("%d/%m")
                summary_line = f"**{person}:** {start_fmt} – {end_fmt} ({working} working day(s))"
                return {
                    "questionnaire": questionnaire,
                    "messages": [AIMessage(content=f"{summary_line}\n\n[1] Add another\n[2] Done")],
                }

            elif stage == "more?":
                if user_text.lower() in ("1", "add", "another", "add another"):
                    questionnaire._leave_input_stage = "person"
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content="Who is taking leave? (name or initials):")],
                    }
                elif user_text.lower() in ("2", "done", "d"):
                    # Done — exit sub-loop, re-show summary with PTO factored in
                    questionnaire._awaiting_leave_input = False
                    questionnaire._leave_input_stage = ""
                    summary = _build_intake_summary(questionnaire)
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content=f"{summary}{_CONFIRM_PROMPT}")],
                        "pending_review": "project_intake",
                    }
                else:
                    # Invalid input — re-prompt
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content="Please choose [1] Add another or [2] Done:")],
                    }

        # ── Velocity choice menu handler ─────────────────────────────
        # "1" or "accept" → accept computed/overridden velocity
        # "2" or "override" → prompt for custom number
        choice_text = last_msg.content.strip().lower()
        if choice_text == "2" or choice_text == "override":
            questionnaire._awaiting_velocity_input = True
            return {
                "questionnaire": questionnaire,
                "messages": [AIMessage(content="Enter your velocity (pts/sprint):")],
            }

        # Choice "1" maps to confirm intent (accept velocity + proceed)
        if choice_text == "1":
            # Treat as confirm — fall through to the confirm block below
            pass
        elif not _is_confirm_intent(last_msg.content):
            # Check for edit intent — inline edit or re-ask
            edit = _parse_edit_intent(last_msg.content)
            if edit is not None:
                q_num, inline_answer = edit
                if inline_answer is not None:
                    # Inline edit: update answer immediately, re-show summary
                    questionnaire.answers[q_num] = inline_answer
                    questionnaire.defaulted_questions.discard(q_num)
                    questionnaire.skipped_questions.discard(q_num)
                    # Clear velocity override when answers change — will be recomputed
                    questionnaire._velocity_override = None
                    summary = _build_intake_summary(questionnaire)
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content=f"Updated Q{q_num}.\n\n{summary}{_CONFIRM_PROMPT}")],
                        "pending_review": "project_intake",
                    }
                else:
                    # Re-ask: show the question text + current answer, collect new answer
                    questionnaire.editing_question = q_num
                    question_text = INTAKE_QUESTIONS[q_num]
                    current_answer = questionnaire.answers.get(q_num, "_no answer_")
                    return {
                        "questionnaire": questionnaire,
                        "messages": [
                            AIMessage(
                                content=(
                                    f"**Q{q_num}.** {question_text}\n\n"
                                    f"Current answer: {current_answer}\n\n"
                                    "Enter your new answer:"
                                )
                            )
                        ],
                    }

            # Not a confirmation, choice, or edit — show edit format help + re-show summary
            summary = _build_intake_summary(questionnaire)
            return {
                "questionnaire": questionnaire,
                "messages": [AIMessage(content=f"{_EDIT_HELP}{summary}{_CONFIRM_PROMPT}")],
                "pending_review": "project_intake",
            }

        # ── Confirm: lock in answers + velocity ──────────────────────
        questionnaire.awaiting_confirmation = False
        questionnaire.completed = True

        # Extract team size and velocity from answers (Q6/Q9).
        extracted = _extract_team_and_velocity(questionnaire)
        velocity_was_calculated = extracted.pop("_velocity_was_calculated", False)

        # Extract all capacity deductions (Q27-Q30).
        # See README: "Scrum Standards" — capacity planning
        capacity = _extract_capacity_deductions(questionnaire)

        # Determine velocity source
        q9 = questionnaire.answers.get(9, "")
        if "from Jira" in q9 or "from jira" in q9:
            velocity_source = "jira"
        elif velocity_was_calculated:
            velocity_source = "estimated"
        else:
            velocity_source = "manual"

        # Compute per-sprint velocities — only sprints with bank holidays
        # get reduced capacity. Other deductions are applied uniformly.
        ts = extracted.get("team_size", 1)
        vel = extracted.get("velocity_per_sprint", ts * _VELOCITY_PER_ENGINEER)
        sprint_weeks = _parse_first_int(questionnaire.answers.get(8, "2 weeks")) or 2
        q10_nums = re.findall(r"\d+", questionnaire.answers.get(10, ""))
        target = int(q10_nums[-1]) if q10_nums else 6

        # Determine sprint start date — use _resolve_sprint_start_date which
        # computes the offset for future sprints (e.g. Sprint 107 when active is 104).
        sprint_start = _resolve_sprint_start_date(questionnaire)
        starting_sprint = -1  # default: no Jira
        q27_answer = questionnaire.answers.get(27, "")
        sprint_num_match = re.search(r"Sprint\s+(\d+)", q27_answer)
        if sprint_num_match:
            starting_sprint = int(sprint_num_match.group(1))

        # Map detected holidays and PTO to sprint windows for per-sprint velocity
        holidays_by_sprint = _assign_holidays_to_sprints(
            questionnaire._detected_bank_holidays,
            sprint_start,
            sprint_weeks,
            target,
        )
        leave_by_sprint = _assign_leave_to_sprints(
            questionnaire._planned_leave_entries,
            sprint_start,
            sprint_weeks,
            target,
        )
        sprint_caps = _compute_per_sprint_velocities(
            team_size=ts,
            velocity_per_sprint=vel,
            sprint_length_weeks=sprint_weeks,
            target_sprints=target,
            holidays_by_sprint=holidays_by_sprint,
            planned_leave_days=capacity["capacity_planned_leave_days"],
            unplanned_leave_pct=capacity["capacity_unplanned_leave_pct"],
            onboarding_engineer_sprints=capacity["capacity_onboarding_engineer_sprints"],
            ktlo_engineers=capacity["capacity_ktlo_engineers"],
            discovery_pct=capacity["capacity_discovery_pct"],
            leave_by_sprint=leave_by_sprint,
        )

        # Use minimum per-sprint velocity as the conservative net velocity
        # (for backward compat and capacity overflow checks)
        if sprint_caps:
            net_vel = min(sc["net_velocity"] for sc in sprint_caps)
        else:
            net_vel = _compute_net_velocity(
                team_size=ts,
                velocity_per_sprint=vel,
                sprint_length_weeks=sprint_weeks,
                target_sprints=target,
                bank_holiday_days=capacity["capacity_bank_holiday_days"],
                planned_leave_days=capacity["capacity_planned_leave_days"],
                unplanned_leave_pct=capacity["capacity_unplanned_leave_pct"],
                onboarding_engineer_sprints=capacity["capacity_onboarding_engineer_sprints"],
                ktlo_engineers=capacity["capacity_ktlo_engineers"],
                discovery_pct=capacity["capacity_discovery_pct"],
            )

        # Apply user override if set
        if questionnaire._velocity_override is not None:
            net_vel = questionnaire._velocity_override
            # Override replaces per-sprint velocities too
            sprint_caps = [{**sc, "net_velocity": questionnaire._velocity_override} for sc in sprint_caps]

        # Build confirmation message with velocity info
        msg = "Great, your answers are locked in!"
        if extracted:
            velocity_note = f" (calculated as {ts} × {_VELOCITY_PER_ENGINEER})" if velocity_was_calculated else ""
            msg += f"\n\nPlanning with: **{ts} engineer(s), {vel} pts/sprint**{velocity_note}"
        if sprint_caps and any(sc["bank_holiday_days"] > 0 or sc.get("pto_days", 0) > 0 for sc in sprint_caps):
            # Show per-sprint breakdown when bank holidays or PTO affect specific sprints
            sprint_label_start = starting_sprint if starting_sprint > 0 else 1
            sprint_lines = []
            for sc in sprint_caps:
                label = f"Sprint {sprint_label_start + sc['sprint_index']}"
                annotations = []
                if sc["bank_holiday_names"]:
                    annotations.append(", ".join(sc["bank_holiday_names"]))
                if sc.get("pto_days", 0) > 0:
                    pto_names = ", ".join(f"{e['person']} {e['days']}d" for e in sc.get("pto_entries", []))
                    annotations.append(f"PTO: {pto_names}")
                if annotations:
                    sprint_lines.append(f"  {label}: **{sc['net_velocity']} pts** ({'; '.join(annotations)})")
                else:
                    sprint_lines.append(f"  {label}: **{sc['net_velocity']} pts**")
            msg += "\n**Per-sprint velocity** (after capacity deductions):\n" + "\n".join(sprint_lines)
        elif net_vel != vel:
            msg += f"\n**Net velocity: {net_vel} pts/sprint** (after capacity deductions)"
        if questionnaire._velocity_override is not None:
            msg += " *(user override)*"
        msg += "\n\n---\nI'll analyze your project next."

        return {
            "questionnaire": questionnaire,
            **extracted,
            **capacity,
            "net_velocity_per_sprint": net_vel,
            "sprint_capacities": sprint_caps,
            "velocity_source": velocity_source,
            "sprint_start_date": sprint_start,
            "starting_sprint_number": starting_sprint,
            "planned_leave_entries": list(questionnaire._planned_leave_entries),
            "messages": [AIMessage(content=msg)],
        }

    # ── Defaults command handling ─────────────────────────────────────
    # See README: "Project Intake Questionnaire" — batch defaults
    #
    # When the user types "defaults", apply defaults to all remaining
    # questions in the current phase and advance past it. This lets users
    # fast-forward through phases they don't have strong opinions on.
    if _is_defaults_intent(last_msg.content):
        prev_phase = questionnaire.current_phase
        summary_lines, count = _batch_defaults_for_phase(questionnaire)

        # Advance to the next unskipped question after the current phase
        from scrum_agent.agent.state import PHASE_QUESTION_RANGES

        _start, end = PHASE_QUESTION_RANGES[prev_phase]
        next_q = _next_unskipped_question(end + 1, questionnaire.skipped_questions)

        if count > 0:
            ack = f"Applied **{count}** default(s) for {PHASE_LABELS[prev_phase]}:\n" + "\n".join(summary_lines)
        else:
            ack = f"No remaining questions in {PHASE_LABELS[prev_phase]} needed defaults."

        if next_q is None:
            # All done — ask PTO or show summary
            return _show_summary_or_pto(questionnaire, prefix=f"{ack}\n\n")

        questionnaire.current_question = next_q
        question = _resolve_adaptive_text(next_q, questionnaire)
        new_phase = questionnaire.current_phase
        phase_label = PHASE_LABELS[new_phase]
        phase_intro = PHASE_INTROS.get(new_phase, "")
        intro_line = f"*{phase_intro}*\n\n" if phase_intro else ""
        suggest_line = _build_suggestion_line(questionnaire, next_q)
        return {
            "questionnaire": questionnaire,
            "messages": [
                AIMessage(
                    content=(
                        f"{ack}\n\n**{phase_label}** (Q{next_q}/{TOTAL_QUESTIONS})"
                        f"\n\n{intro_line}{question}{suggest_line}"
                    )
                )
            ],
        }

    # ── Skip / "I don't know" handling ───────────────────────────────
    # See README: "Project Intake Questionnaire" — adaptive behavior
    #
    # Check for skip intent BEFORE the probed_questions check. This ensures
    # "skip" during a follow-up probe is handled correctly (keeps original
    # answer, advances) rather than combining "skip" as follow-up detail.
    if _is_skip_intent(last_msg.content):
        if current_q in questionnaire.probed_questions:
            # Skip during a follow-up probe — keep the original answer as-is
            ack = _build_skip_acknowledgment(current_q, during_probe=True, default=None)
        elif current_q in QUESTION_DEFAULTS:
            # Default available — store it and mark as defaulted
            default = QUESTION_DEFAULTS[current_q]
            questionnaire.answers[current_q] = default
            questionnaire.defaulted_questions.add(current_q)
            questionnaire.answer_sources[current_q] = AnswerSource.DEFAULTED
            ack = _build_skip_acknowledgment(current_q, during_probe=False, default=default)
        else:
            # Essential question with no default — flag the gap
            questionnaire.skipped_questions.add(current_q)
            ack = _build_skip_acknowledgment(current_q, during_probe=False, default=None)

        # Advance to next question (same logic as normal flow)
        next_q = _next_unskipped_question(current_q + 1, questionnaire.skipped_questions)
        if next_q is None:
            return _show_summary_or_pto(questionnaire, prefix=f"{ack}\n\n")

        prev_skip_phase = questionnaire.current_phase
        questionnaire.current_question = next_q
        question = _resolve_adaptive_text(next_q, questionnaire)
        new_skip_phase = questionnaire.current_phase
        phase_label = PHASE_LABELS[new_skip_phase]
        # Show phase intro when entering a new phase after a skip
        phase_intro = PHASE_INTROS.get(new_skip_phase, "") if new_skip_phase != prev_skip_phase else ""
        intro_line = f"*{phase_intro}*\n\n" if phase_intro else ""
        suggest_line = _build_suggestion_line(questionnaire, next_q)
        return {
            "questionnaire": questionnaire,
            "messages": [
                AIMessage(
                    content=(
                        f"{ack}\n\n**{phase_label}** (Q{next_q}/{TOTAL_QUESTIONS})"
                        f"\n\n{intro_line}{question}{suggest_line}"
                    )
                )
            ],
        }

    # ── Follow-up probing logic ──────────────────────────────────────
    # See README: "Project Intake Questionnaire" — follow-up probing
    #
    # After each answer, the LLM judges whether it's specific enough.
    # If vague, one targeted follow-up is asked before moving on.
    # Max 1 follow-up per question — if still vague, accept and advance.
    #
    # Why inline probing (not batch at end)? Probing right after the
    # answer keeps context fresh. A batch pass would require the user
    # to context-switch back to earlier questions.
    #
    # Flow:
    #   IF current_question already probed → combine original + follow-up, advance
    #   ELSE → record answer, check vagueness:
    #     vague → mark probed, DON'T advance, return follow-up
    #     not vague → advance normally

    # Confirmation prefix for repo URL detection — set after _sync_platform_from_url
    # and prepended to the next AIMessage so the user sees immediate feedback.
    repo_confirm = ""

    if current_q in questionnaire.probed_questions:
        # This is a follow-up response.
        if current_q == 2 and questionnaire.answers.get(2) in _EXISTING_CODEBASE_ANSWERS:
            # The follow-up was our repo URL prompt — store answer in Q17, not Q2.
            # Q17 is "Can you share the repo URL(s)?", storing here lets the repo
            # scan and platform detection use it downstream.
            questionnaire.answers[17] = last_msg.content
            questionnaire.defaulted_questions.discard(17)
            _sync_platform_from_url(questionnaire)
            platform = questionnaire.answers.get(16, "")
            if platform:
                repo_confirm = f"*✓ {platform} repo detected — will be scanned during analysis.*\n\n"
        else:
            # Normal vagueness follow-up — combine original + follow-up detail.
            # Format: "{original}\n\n(Follow-up detail: {follow_up_answer})"
            # so downstream nodes get full context even if the follow-up is
            # incremental (e.g. "React and Node" expanding "A web app").
            original = questionnaire.answers.get(current_q, "")
            combined = f"{original}\n\n(Follow-up detail: {last_msg.content})"
            questionnaire.answers[current_q] = combined
        # Clear dynamic choices — they were consumed when the user answered.
        questionnaire._follow_up_choices.pop(current_q, None)
    else:
        # First answer to this question — record it and check vagueness.
        questionnaire.answers[current_q] = last_msg.content
        questionnaire.answer_sources[current_q] = AnswerSource.DIRECT
        if current_q == 17:
            _sync_platform_from_url(questionnaire)
            platform = questionnaire.answers.get(16, "")
            if platform:
                repo_confirm = f"*✓ {platform} repo detected — will be scanned during analysis.*\n\n"

        # Q2 repo URL follow-up: when Q2 is answered "Existing codebase" or
        # "Hybrid" and Q17 is not yet set, ask for the repo URL immediately
        # before advancing to Q3. The answer is stored in Q17 (not combined
        # into Q2) so downstream repo scan and platform detection can use it.
        # Reuses probed_questions so the existing skip path handles it gracefully.
        if _needs_repo_url_prompt(questionnaire):
            questionnaire.probed_questions.add(2)
            return {
                "questionnaire": questionnaire,
                "messages": [AIMessage(content=_Q2_REPO_URL_PROMPT)],
            }

        # Skip vagueness check for choice questions — a selection from a
        # predefined list is never vague. Only probe free-text answers.
        if is_choice_question(current_q):
            vague_result = None
        else:
            vague_result = _check_vague_answer(INTAKE_QUESTIONS[current_q], last_msg.content, current_q)
        if vague_result:
            # Unpack follow-up question and dynamic choices from the LLM.
            # choices may be empty — the REPL gracefully degrades to open-ended.
            follow_up, choices = vague_result
            # Answer is vague — ask a follow-up. Don't advance current_question.
            # The follow-up message has NO phase label or progress indicator
            # — it feels like a conversational clarification, not a new question.
            questionnaire.probed_questions.add(current_q)
            questionnaire.answer_sources[current_q] = AnswerSource.PROBED
            # Store dynamic choices so the REPL can render a numbered menu.
            # Same lifecycle as probed_questions — cleared when follow-up is answered.
            if choices:
                questionnaire._follow_up_choices[current_q] = choices
            return {
                "questionnaire": questionnaire,
                "messages": [AIMessage(content=f"**Follow-up on Q{current_q}:**\n\n{follow_up}")],
            }

    # ── Q27 sprint selection handling (standard mode) ────────────────
    # See README: "Scrum Standards" — capacity planning
    #
    # When Q27 is the current question and the user just answered it:
    # If Jira configured, the answer is a sprint selection (1/2/3/custom).
    # Parse it using resolve_sprint_selection. If not Jira, Q27 was auto-filled
    # so this path isn't reached.
    if current_q == 27 and current_q not in questionnaire.probed_questions:
        # Check if Q27 answer is a sprint selection response
        q27_answer = questionnaire.answers.get(27, "")
        if _is_jira_configured() and not q27_answer.startswith("Fresh start"):
            # Try to parse as sprint selection — the active sprint number was
            # stored temporarily in the answer as "_active:N" by the Q27 prompt.
            active_match = re.search(r"_active:(\d+)", q27_answer)
            if active_match:
                active_num = int(active_match.group(1))
                user_answer = last_msg.content

                # The TUI/REPL resolves dynamic choices to the full option text
                # (e.g. "Sprint 105 (next)"), so try extracting the sprint number
                # from the resolved text first, then fall back to resolve_sprint_selection
                # for raw numeric input (e.g. "1", "105").
                sprint_num_match = re.search(r"Sprint\s+(\d+)", user_answer)
                if sprint_num_match:
                    resolved = int(sprint_num_match.group(1))
                else:
                    resolved = resolve_sprint_selection(user_answer, active_num)

                if resolved is not None and resolved > 0:
                    questionnaire.answers[27] = f"Sprint {resolved}"
                    # Clear dynamic choices — they were consumed
                    questionnaire._follow_up_choices.pop(27, None)
                    # Prepare Q28 bank holiday choices for the next question
                    _prepare_bank_holiday_choices(questionnaire)
                else:
                    # Invalid selection — re-ask
                    questionnaire.answers.pop(27, None)
                    return {
                        "questionnaire": questionnaire,
                        "messages": [AIMessage(content="Please pick 1–3, or type a sprint number.")],
                    }

    # ── Q28 bank holiday answer processing ────────────────────────────
    # When Q28 is answered, parse the user's choice to set the bank holiday count.
    if current_q == 28 and current_q not in questionnaire.probed_questions:
        q28_answer = questionnaire.answers.get(28, "")
        if "accept" in q28_answer.lower():
            # User accepted the detected count — keep _detected_bank_holiday_days as-is
            count = questionnaire._detected_bank_holiday_days
            questionnaire.answers[28] = f"{count} bank holiday(s)" if count > 0 else "No bank holidays"
        elif "no bank holidays" in q28_answer.lower():
            questionnaire._detected_bank_holiday_days = 0
            questionnaire.answers[28] = "No bank holidays"
        elif "enter manually" in q28_answer.lower():
            # User wants to enter manually — clear choices and re-ask as free text
            questionnaire._follow_up_choices.pop(28, None)
            questionnaire.answers.pop(28, None)
            questionnaire.probed_questions.add(28)
            return {
                "questionnaire": questionnaire,
                "messages": [AIMessage(content="How many bank/public holiday days fall in your planning window?")],
            }
        else:
            # Free-text answer (manual entry or follow-up) — parse the number
            parsed = _parse_first_int(q28_answer)
            if parsed is not None:
                questionnaire._detected_bank_holiday_days = parsed
                questionnaire.answers[28] = f"{parsed} bank holiday(s)"
        questionnaire._follow_up_choices.pop(28, None)

    # ── Advance to next question ─────────────────────────────────────
    # _next_unskipped_question scans forward, skipping any questions that
    # were already answered from the initial description.
    next_q = _next_unskipped_question(current_q + 1, questionnaire.skipped_questions)

    if next_q is None:
        # All remaining questions have been answered or skipped —
        # ask PTO or show summary and wait for confirmation.
        return _show_summary_or_pto(questionnaire, prefix=repo_confirm)

    prev_advance_phase = questionnaire.current_phase
    questionnaire.current_question = next_q

    # ── Q27 sprint selection auto-handling when advancing ─────────────
    # See README: "Scrum Standards" — capacity planning
    #
    # When advancing to Q27 in standard mode:
    # - No Jira: auto-fill with bank holiday detection and skip to Q28
    # - Jira: fetch active sprint and present options
    if next_q == 27:
        if not _is_jira_configured():
            # No Jira — auto-fill Q27 as "Fresh start (today)" and advance to Q28
            _derive_q27_from_locale(questionnaire)
            # Prepare bank holiday choices for Q28
            _prepare_bank_holiday_choices(questionnaire)
            # Advance past Q27 to Q28 (bank holidays)
            next_q = _next_unskipped_question(28, questionnaire.skipped_questions)
            if next_q is None:
                return _show_summary_or_pto(questionnaire, prefix=repo_confirm)
            questionnaire.current_question = next_q
            prev_advance_phase = QuestionnairePhase.CAPACITY_PLANNING
        else:
            # Jira configured — fetch active sprint and show selection as a choice menu
            active_num, active_start, jira_status = _fetch_active_sprint_number()
            if active_num is not None:
                # Store active sprint number and start date in transient fields
                questionnaire._active_sprint_number = active_num
                questionnaire._active_sprint_start_date = active_start
                questionnaire.answers[27] = f"_active:{active_num}"
                # Populate dynamic choices so the TUI accordion / REPL renders a proper
                # numbered menu instead of a free-text input box.
                questionnaire._follow_up_choices[27] = (
                    f"Sprint {active_num + 1} (next)",
                    f"Sprint {active_num + 2}",
                    f"Sprint {active_num + 3}",
                )
                phase_label = PHASE_LABELS[questionnaire.current_phase]
                phase_intro = PHASE_INTROS.get(questionnaire.current_phase, "")
                intro_line = f"*{phase_intro}*\n\n" if phase_intro else ""
                prompt = (
                    f"Detected active sprint in Jira: **Sprint {active_num}**.\n\nWhich sprint are you planning for?"
                )
                return {
                    "questionnaire": questionnaire,
                    "messages": [
                        AIMessage(
                            content=(
                                f"{repo_confirm}**{phase_label}** (Q{next_q}/{TOTAL_QUESTIONS})\n\n{intro_line}{prompt}"
                            )
                        )
                    ],
                }
            else:
                # Jira configured but couldn't fetch sprint — fall back with feedback
                logger.warning("Jira sprint fetch failed: %s", jira_status)
                _derive_q27_from_locale(questionnaire)
                _prepare_bank_holiday_choices(questionnaire)
                next_q = _next_unskipped_question(28, questionnaire.skipped_questions)
                if next_q is None:
                    return _show_summary_or_pto(questionnaire, prefix=repo_confirm)
                questionnaire.current_question = next_q
                prev_advance_phase = QuestionnairePhase.CAPACITY_PLANNING

    # Ask the next question, with phase label and progress indicator.
    # The phase label changes when transitioning between phases (e.g. Q5→Q6).
    question = INTAKE_QUESTIONS[questionnaire.current_question]
    new_advance_phase = questionnaire.current_phase
    phase_label = PHASE_LABELS[new_advance_phase]
    # Show phase intro when entering a new phase
    phase_intro = PHASE_INTROS.get(new_advance_phase, "") if new_advance_phase != prev_advance_phase else ""
    intro_line = f"*{phase_intro}*\n\n" if phase_intro else ""
    suggest_line = _build_suggestion_line(questionnaire, questionnaire.current_question)
    return {
        "questionnaire": questionnaire,
        "messages": [
            AIMessage(
                content=(
                    f"{repo_confirm}"
                    f"**{phase_label}** (Q{questionnaire.current_question}/{TOTAL_QUESTIONS})"
                    f"\n\n{intro_line}{question}{suggest_line}"
                )
            )
        ],
    }


# ── Project analyzer ────────────────────────────────────────────────
# See README: "Architecture" — project_analyzer sits between intake and agent
# See README: "Scrum Standards" — project analysis
#
# After the user confirms the 26-question intake questionnaire, this node
# synthesizes all answers into a structured ProjectAnalysis dataclass.
# Downstream nodes (feature_generator, story_writer, sprint_planner) read
# ProjectAnalysis instead of re-parsing raw conversation history.
#
# Why LLM-powered extraction (not deterministic)?
# The 26 answers are natural language — extracting structured fields like
# "goals" from free-text like "We want to improve onboarding and reduce churn"
# requires semantic understanding. A single LLM call with a JSON-schema prompt
# handles this, with a deterministic fallback on parse failure.


def _build_answers_block(questionnaire: QuestionnaireState) -> str:
    """Format all 26 Q&A pairs for the analyzer prompt.

    Marks defaulted answers with *(assumed default)* and skipped questions
    with *(skipped)* so the LLM can flag them as assumptions.

    Args:
        questionnaire: The completed questionnaire with all answers.

    Returns:
        A formatted string with one Q/A pair per block.
    """
    lines: list[str] = []
    for q_num in range(1, TOTAL_QUESTIONS + 1):
        question = INTAKE_QUESTIONS[q_num]
        answer = questionnaire.answers.get(q_num)
        marker = ""
        if q_num in questionnaire.extracted_questions:
            source = "SCRUM.md" if q_num in questionnaire._scrum_md_questions else "description"
            marker = f" *(extracted from {source})*"
        elif q_num in questionnaire.defaulted_questions:
            marker = " *(assumed default)*"
        elif answer is None:
            marker = " *(skipped)*"
        display_answer = answer if answer is not None else "(no answer)"
        lines.append(f"Q{q_num}. {question}\nA: {display_answer}{marker}\n")
    return "\n".join(lines)


def _parse_analysis_response(
    raw: str,
    questionnaire: QuestionnaireState,
    team_size: int,
    velocity: int,
) -> ProjectAnalysis:
    """Parse the LLM's JSON response into a ProjectAnalysis dataclass.

    Strips markdown code fences, parses JSON, and converts list fields to
    tuples (frozen dataclass requires immutable sequences). Falls back to
    _build_fallback_analysis on any parse error.

    Args:
        raw: The raw LLM response string (expected to be JSON).
        questionnaire: The completed questionnaire (for fallback).
        team_size: Team size (for fallback).
        velocity: Velocity per sprint (for fallback).

    Returns:
        A ProjectAnalysis instance.
    """
    try:
        # Strip markdown code fences that LLMs sometimes wrap JSON in
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return _build_fallback_analysis(questionnaire, team_size, velocity)

        # Helper to safely convert list-like values to tuple[str, ...]
        def to_str_tuple(val: object) -> tuple[str, ...]:
            if isinstance(val, list):
                return tuple(str(item) for item in val if item)
            if isinstance(val, str) and val.strip():
                return (val,)
            return ()

        # Parse sprint_length_weeks with default of 2
        sprint_weeks_raw = parsed.get("sprint_length_weeks", 2)
        try:
            sprint_weeks = int(sprint_weeks_raw)
        except (ValueError, TypeError):
            sprint_weeks = 2

        # Parse target_sprints with default of 0
        target_raw = parsed.get("target_sprints", 0)
        try:
            target_sprints = int(target_raw)
        except (ValueError, TypeError):
            target_sprints = 0

        return ProjectAnalysis(
            project_name=str(parsed.get("project_name", "Untitled Project")),
            project_description=str(parsed.get("project_description", "")),
            project_type=str(parsed.get("project_type", "unknown")),
            goals=to_str_tuple(parsed.get("goals")),
            end_users=to_str_tuple(parsed.get("end_users")),
            target_state=str(parsed.get("target_state", "")),
            tech_stack=to_str_tuple(parsed.get("tech_stack")),
            integrations=to_str_tuple(parsed.get("integrations")),
            constraints=to_str_tuple(parsed.get("constraints")),
            sprint_length_weeks=sprint_weeks,
            target_sprints=target_sprints,
            risks=to_str_tuple(parsed.get("risks")),
            out_of_scope=to_str_tuple(parsed.get("out_of_scope")),
            assumptions=to_str_tuple(parsed.get("assumptions")),
            # Deterministic guardrail: only allow skip_features when the project is
            # genuinely small (target_sprints ≤ 2 AND goals ≤ 3). The LLM may
            # over-eagerly set skip_features=true for larger projects.
            skip_features=bool(parsed.get("skip_features", False))
            and target_sprints <= 2
            and len(to_str_tuple(parsed.get("goals"))) <= 3,
            scrum_md_contributions=to_str_tuple(parsed.get("scrum_md_contributions")),
        )

    except Exception:
        logger.debug("Failed to parse analysis JSON, falling back to deterministic extraction", exc_info=True)
        return _build_fallback_analysis(questionnaire, team_size, velocity)


def _build_fallback_analysis(
    questionnaire: QuestionnaireState,
    team_size: int,
    velocity: int,
) -> ProjectAnalysis:
    """Build a best-effort ProjectAnalysis from raw answers when LLM fails.

    Deterministic extraction — pulls answers directly by question number.
    No LLM call, so this always succeeds. The result may be less polished
    than the LLM version but captures all the raw data.

    Args:
        questionnaire: The completed questionnaire with all answers.
        team_size: Team size from state.
        velocity: Velocity per sprint from state.

    Returns:
        A ProjectAnalysis with fields populated from raw answers.
    """
    answers = questionnaire.answers

    # Parse sprint length from Q8 answer
    sprint_weeks = 2
    q8 = answers.get(8, "")
    sprint_int = _parse_first_int(q8) if q8 else None
    if sprint_int and 1 <= sprint_int <= 4:
        sprint_weeks = sprint_int

    # Parse target sprints from Q10 answer.
    # Q10 uses ranges like "3–5 sprints" — extract the upper bound so the
    # planner has room to spread work. "No preference" has no digits → 0.
    target_sprints = 0
    q10 = answers.get(10, "")
    if q10:
        q10_nums = re.findall(r"\d+", q10)
        if q10_nums:
            target_sprints = int(q10_nums[-1])  # upper bound of range

    # Collect assumptions from defaulted/skipped questions
    assumptions: list[str] = []
    for q_num in sorted(questionnaire.defaulted_questions):
        assumptions.append(f"Q{q_num}: used default — {answers.get(q_num, 'N/A')}")
    for q_num in sorted(questionnaire.skipped_questions - set(answers.keys())):
        assumptions.append(f"Q{q_num}: skipped with no answer")

    return ProjectAnalysis(
        project_name=answers.get(1, "Untitled Project")[:50],
        project_description=answers.get(1, ""),
        project_type=answers.get(2, "unknown").lower().strip(),
        goals=(answers.get(3, ""),) if answers.get(3) else (),
        end_users=(answers.get(3, ""),) if answers.get(3) else (),
        target_state=answers.get(4, ""),
        tech_stack=(answers.get(11, ""),) if answers.get(11) else (),
        integrations=(answers.get(12, ""),) if answers.get(12) else (),
        constraints=(answers.get(13, ""),) if answers.get(13) else (),
        sprint_length_weeks=sprint_weeks,
        target_sprints=target_sprints,
        risks=(answers.get(21, ""),) if answers.get(21) else (),
        out_of_scope=(answers.get(23, ""),) if answers.get(23) else (),
        assumptions=tuple(assumptions),
        scrum_md_contributions=(),
    )


def _format_analysis(
    analysis: ProjectAnalysis,
    *,
    sprint_capacities: list[dict] | None = None,
    net_velocity: int | None = None,
    velocity_per_sprint: int | None = None,
    team_size: int | None = None,
    velocity_source: str | None = None,
) -> str:
    """Format a ProjectAnalysis as a markdown display for the user.

    Matches the project's intake summary style — sections with bullet points.
    The REPL renders this as a Rich panel and waits for user
    input before routing to the main agent.

    Args:
        analysis: The completed ProjectAnalysis to display.
        sprint_capacities: Per-sprint velocity breakdown (from capacity analysis).
        net_velocity: Net velocity after deductions.
        velocity_per_sprint: Gross velocity before deductions.
        team_size: Number of engineers.
        velocity_source: How velocity was determined ("jira", "estimated", "manual").

    Returns:
        A formatted markdown string.
    """

    def _bullet_list(items: tuple[str, ...]) -> str:
        if not items:
            return "  - _(none)_"
        return "\n".join(f"  - {item}" for item in items)

    sections = [
        f"# Project Analysis: {analysis.project_name}\n",
        f"**Description:** {analysis.project_description}\n",
        f"**Type:** {analysis.project_type}\n",
        f"## Goals\n{_bullet_list(analysis.goals)}\n",
        f"## End Users\n{_bullet_list(analysis.end_users)}\n",
        f"## Target State\n{analysis.target_state or '_(not specified)_'}\n",
        f"## Tech Stack\n{_bullet_list(analysis.tech_stack)}\n",
        f"## Integrations\n{_bullet_list(analysis.integrations)}\n",
        f"## Constraints\n{_bullet_list(analysis.constraints)}\n",
        f"## Sprint Planning\n"
        f"  - Sprint length: **{analysis.sprint_length_weeks} week(s)**\n"
        f"  - Target sprints: **{analysis.target_sprints or 'scope-based'}**\n",
        f"## Risks\n{_bullet_list(analysis.risks)}\n",
        f"## Out of Scope\n{_bullet_list(analysis.out_of_scope)}\n",
    ]

    # ── Capacity Analysis section ─────────────────────────────────────
    # Shows the velocity calculation and per-sprint breakdown when bank
    # holidays affect specific sprints. This gives the user visibility into
    # exactly how planning capacity was derived before the agent proceeds.
    if net_velocity is not None and velocity_per_sprint is not None:
        ts = team_size or 1
        gross = velocity_per_sprint
        source_label = {"jira": "from Jira", "estimated": "estimated", "manual": "manual"}.get(
            velocity_source or "", ""
        )
        cap_lines = [
            "## Capacity",
            f"  - Team: **{ts} engineer(s)** · Gross velocity: **{gross} pts/sprint**"
            + (f" ({source_label})" if source_label else ""),
        ]

        has_per_sprint = sprint_capacities and any(sc.get("bank_holiday_days", 0) > 0 for sc in sprint_capacities)
        if has_per_sprint:
            cap_lines.append("  - Per-sprint breakdown:")
            total_pts = 0
            for sc in sprint_capacities:
                idx = sc["sprint_index"] + 1
                nv = sc["net_velocity"]
                total_pts += nv
                if sc["bank_holiday_names"]:
                    names = ", ".join(sc["bank_holiday_names"])
                    cap_lines.append(f"    - Sprint {idx}: **{nv} pts** (−{sc['bank_holiday_days']}d: {names})")
                else:
                    cap_lines.append(f"    - Sprint {idx}: **{nv} pts**")
            cap_lines.append(f"  - Total capacity: **{total_pts} pts** across {len(sprint_capacities)} sprints")
        cap_lines.append(f"  - Net velocity: **{net_velocity} pts/sprint**")
        sections.append("\n".join(cap_lines) + "\n")

    if analysis.assumptions:
        sections.append(f"## Assumptions\n{_bullet_list(analysis.assumptions)}\n")

    if analysis.scrum_md_contributions:
        fields = ", ".join(analysis.scrum_md_contributions)
        sections.append(f"## User Docs Enriched\n  - {fields}\n")

    sections.append("")  # Trailing newline for clean formatting

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Prompt quality scoring — deterministic rating from QuestionnaireState
# ---------------------------------------------------------------------------
# See README: "Scrum Standards" — prompt quality rating
#
# Pure function: computes a quality score from the questionnaire tracking sets
# (answered, extracted, defaulted, skipped, probed). No LLM call. Used by
# project_analyzer to attach a PromptQualityRating to the ProjectAnalysis.

# Essential questions worth 5 pts each; all others worth 2 pts each.
_ESSENTIAL_QUESTIONS: frozenset[int] = frozenset({1, 2, 3, 4, 6, 11, 15})
_ESSENTIAL_WEIGHT = 5
_OTHER_WEIGHT = 2
_PROBING_BONUS = 1
_DEFAULTED_FACTOR = 0.4  # defaulted answers get 40% of points
_MAX_SUGGESTIONS = 4
# High-value non-essential questions — worth suggesting when defaulted/skipped.
# These provide context that significantly improves analysis quality (repo URLs,
# existing docs) but aren't strictly required to generate a plan.
_HIGH_VALUE_QUESTIONS: frozenset[int] = frozenset({14, 17})


def compute_prompt_quality(qs: QuestionnaireState, *, has_user_context: bool = False) -> PromptQualityRating:
    """Compute a deterministic prompt quality rating from questionnaire tracking sets.

    Scoring formula:
    - 7 essential questions (Q1-Q4, Q6, Q11, Q15): 5 pts each = 35 pts max
    - 19 other questions: 2 pts each = 38 pts max
    - Probing bonus: 1 pt per probed question
    - Total ~78 pts max, normalized to percentage

    Deductions:
    - User-answered or extracted from description → full points
    - Defaulted → 40% of points
    - Skipped (no answer at all) → 0 points

    Grade: A (≥85%), B (≥70%), C (≥50%), D (<50%)

    Args:
        qs: The completed QuestionnaireState with tracking sets populated.
        has_user_context: Whether a SCRUM.md file was loaded. When False,
            a suggestion to add one is included.

    Returns:
        A PromptQualityRating with score, grade, counts, and suggestions.
    """
    total_possible = 0.0
    total_earned = 0.0
    answered_count = 0
    extracted_count = 0
    defaulted_count = 0
    skipped_count = 0

    for q_num in range(1, TOTAL_QUESTIONS + 1):
        weight = _ESSENTIAL_WEIGHT if q_num in _ESSENTIAL_QUESTIONS else _OTHER_WEIGHT
        total_possible += weight

        if q_num in qs.extracted_questions:
            # Extracted from description — full points
            total_earned += weight
            extracted_count += 1
        elif q_num in qs.defaulted_questions:
            # Defaulted — partial credit
            total_earned += weight * _DEFAULTED_FACTOR
            defaulted_count += 1
        elif q_num in qs.answers:
            # User answered directly — full points
            total_earned += weight
            answered_count += 1
        else:
            # Skipped with no answer — 0 points
            skipped_count += 1

    # Probing bonus — 1 pt per probed question (shows engagement)
    probed_count = len(qs.probed_questions)
    total_earned += probed_count * _PROBING_BONUS
    total_possible += probed_count * _PROBING_BONUS  # keep ratio fair

    # Normalize to percentage
    score_pct = round((total_earned / total_possible) * 100) if total_possible > 0 else 0

    # Grade thresholds
    if score_pct >= 85:
        grade = "A"
    elif score_pct >= 70:
        grade = "B"
    elif score_pct >= 50:
        grade = "C"
    else:
        grade = "D"

    # Generate suggestions — essential questions first, then high-value, then SCRUM.md
    suggestions: list[str] = []
    # Essential questions that were defaulted or skipped
    for q_num in sorted(_ESSENTIAL_QUESTIONS):
        if len(suggestions) >= _MAX_SUGGESTIONS:
            break
        if q_num in qs.defaulted_questions or (q_num not in qs.answers and q_num not in qs.extracted_questions):
            hint = QUESTION_IMPROVEMENT_HINTS.get(q_num)
            if hint:
                suggestions.append(f"{hint} (Q{q_num})")
    # High-value non-essential questions (repo URL, docs) that were defaulted or skipped
    for q_num in sorted(_HIGH_VALUE_QUESTIONS):
        if len(suggestions) >= _MAX_SUGGESTIONS:
            break
        if q_num in qs.defaulted_questions or (q_num not in qs.answers and q_num not in qs.extracted_questions):
            hint = QUESTION_IMPROVEMENT_HINTS.get(q_num)
            if hint:
                suggestions.append(f"{hint} (Q{q_num})")
    # SCRUM.md suggestion — shown when no user context file was loaded
    if not has_user_context and len(suggestions) < _MAX_SUGGESTIONS:
        suggestions.append(SCRUM_MD_HINT)

    # Low-confidence areas — essential questions that were defaulted.
    # These are flagged as assumptions in ProjectAnalysis for downstream
    # spike recommendations. Uses answer_sources when available.
    low_confidence: list[str] = []
    for q_num in sorted(ESSENTIAL_QUESTIONS):
        is_defaulted = (
            qs.answer_sources.get(q_num) == AnswerSource.DEFAULTED
            if qs.answer_sources
            else q_num in qs.defaulted_questions
        )
        if is_defaulted:
            label = QUESTION_SHORT_LABELS.get(q_num, f"Q{q_num}")
            low_confidence.append(label)

    return PromptQualityRating(
        score_pct=score_pct,
        grade=grade,
        answered_count=answered_count,
        extracted_count=extracted_count,
        defaulted_count=defaulted_count,
        skipped_count=skipped_count,
        probed_count=probed_count,
        suggestions=tuple(suggestions),
        low_confidence_areas=tuple(low_confidence),
    )


def project_analyzer(state: ScrumState) -> dict:
    """LangGraph node: synthesize intake answers into a structured ProjectAnalysis.

    # See README: "Agentic Blueprint Reference" — node return format
    # See README: "Architecture" — project_analyzer node
    #
    # How this works:
    # 1. Read the confirmed questionnaire answers + team_size + velocity from state.
    # 2. Build a formatted answers block for the LLM prompt.
    # 3. Call the LLM with the analyzer prompt (temperature=0.0 for deterministic JSON).
    # 4. Parse the JSON response into a ProjectAnalysis dataclass.
    # 5. Return the analysis + populate ScrumState metadata fields.
    #
    # Why this returns to END (not to agent)?
    # Same pattern as feature_generator — the node produces output, the REPL
    # displays it, and the user reviews with [Accept / Edit / Reject]. On
    # accept, route_entry sees project_analysis populated and routes to
    # the next pipeline node.

    Args:
        state: The current LangGraph state with completed questionnaire.

    Returns:
        A dict updating project_analysis, project_name, project_description,
        sprint_length_weeks, target_sprints, and messages.
    """
    questionnaire = state["questionnaire"]
    team_size = state.get("team_size", 1)
    velocity = state.get("velocity_per_sprint", team_size * _VELOCITY_PER_ENGINEER)

    # See README: "Guardrails" — human-in-the-loop pattern
    # Read review state from previous edit decision. When present, the
    # REPL has cleared the old analysis and set last_review_decision/feedback
    # so this node regenerates with user feedback injected into the prompt.
    review_decision = state.get("last_review_decision")
    review_feedback = state.get("last_review_feedback", "")
    review_mode = review_decision.value if review_decision else None

    # For edit mode, extract previous output from the feedback string.
    # The REPL packs it as: "{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}"
    previous_output = None
    if review_mode == "edit" and "---PREVIOUS OUTPUT---" in review_feedback:
        parts = review_feedback.split("---PREVIOUS OUTPUT---", 1)
        review_feedback = parts[0].strip()
        previous_output = parts[1].strip()

    # Scan repo if Q17 has a URL — grounds analysis in real codebase data.
    # Each function returns (context_str | None, status_dict) so we can track
    # what sources were used, skipped, or failed for user transparency.
    # See README: "Tools" — read-only tool pattern
    repo_context, repo_status = _scan_repo_context(questionnaire)

    # Load SCRUM.md first — the user's own project context file. Loaded before
    # Confluence so that any Confluence URLs in SCRUM.md can be fetched directly.
    # Similar to CLAUDE.md for Claude Code: a free-form markdown file containing URLs,
    # design notes, tech decisions, and anything else the user wants the agent to know.
    user_context, user_status = _load_user_context()

    # Search Confluence for docs related to the project name AND fetch any pages
    # linked directly in SCRUM.md (e.g. RunBook URLs). Passing user_context allows
    # _fetch_confluence_context to extract page IDs from Confluence URLs.
    # Returns (None, status) gracefully when Confluence is not configured or no docs found.
    # See README: "Tools" — read-only tool pattern
    logger.debug(
        "CONFLUENCE: passing user_context=%s to _fetch_confluence_context", "present" if user_context else "None"
    )
    confluence_context, confluence_status = _fetch_confluence_context(questionnaire, user_context=user_context)
    logger.debug(
        "CONFLUENCE: result status=%s detail=%s", confluence_status.get("status"), confluence_status.get("detail")
    )

    # Build the formatted answers block for the prompt
    answers_block = _build_answers_block(questionnaire)
    prompt = get_analyzer_prompt(
        answers_block,
        team_size,
        velocity,
        repo_context=repo_context,
        confluence_context=confluence_context,
        user_context=user_context,
        review_feedback=review_feedback if review_mode else None,
        review_mode=review_mode,
        previous_output=previous_output,
    )

    try:
        # Single LLM call with low temperature for deterministic JSON extraction.
        # See README: "Agentic Blueprint Reference" — using the LLM outside the main graph
        response = get_llm(temperature=0.0).invoke([HumanMessage(content=prompt)])
        analysis = _parse_analysis_response(response.content, questionnaire, team_size, velocity)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            raise
        # LLM call failed entirely — use deterministic fallback.
        logger.warning("LLM call failed in project_analyzer, using fallback", exc_info=True)
        analysis = _build_fallback_analysis(questionnaire, team_size, velocity)

    # Compute prompt quality rating from questionnaire tracking sets (no LLM call).
    # Attach to the analysis via dataclasses.replace since ProjectAnalysis is frozen.
    quality = compute_prompt_quality(questionnaire, has_user_context=user_context is not None)
    analysis = dataclasses.replace(analysis, prompt_quality=quality)

    # Format the analysis for display — include capacity data so the user
    # sees velocity breakdown and per-sprint bank holiday impact on this screen.
    display = _format_analysis(
        analysis,
        sprint_capacities=state.get("sprint_capacities"),
        net_velocity=state.get("net_velocity_per_sprint"),
        velocity_per_sprint=state.get("velocity_per_sprint"),
        team_size=state.get("team_size"),
        velocity_source=state.get("velocity_source"),
    )

    # Set pending_review so the REPL intercepts the next user input for
    # the [Accept / Edit / Reject] review flow — same pattern as features/stories.
    return_dict: dict = {
        "project_analysis": analysis,
        "project_name": analysis.project_name,
        "project_description": analysis.project_description,
        "sprint_length_weeks": analysis.sprint_length_weeks,
        "target_sprints": analysis.target_sprints,
        "pending_review": "project_analyzer",
        "messages": [AIMessage(content=display)],
        # Context source diagnostics — tells the REPL which external sources
        # were used, skipped, or failed so the user gets transparency.
        "context_sources": [repo_status, confluence_status, user_status],
    }
    # Only write repo_context / confluence_context / user_context when present —
    # avoids overwriting a prior value on re-runs (e.g. edit review) where
    # the scan might silently fail (network down, token expired, file deleted).
    if repo_context is not None:
        return_dict["repo_context"] = repo_context
    if confluence_context is not None:
        return_dict["confluence_context"] = confluence_context
    if user_context is not None:
        return_dict["user_context"] = user_context
    return return_dict


# ---------------------------------------------------------------------------
# Feature generator node — decomposes ProjectAnalysis into 3-6 Feature dataclasses
# ---------------------------------------------------------------------------
# See README: "Architecture" — feature_generator sits between analyzer and agent
# See README: "Scrum Standards" — feature decomposition
#
# Same pattern as project_analyzer: extract from state → build prompt →
# LLM call → parse JSON → fallback → format display → return state update.
# The node returns to END so the REPL can display the features and wait for user
# input. On the next invocation, route_entry sees features populated and routes
# to the agent node.


def _format_epic_list(items: tuple[str, ...]) -> str:
    """Format a tuple of strings as a markdown bullet list for the prompt.

    Args:
        items: Tuple of string items from ProjectAnalysis.

    Returns:
        A bullet list string, or "_(none)_" if empty.
    """
    if not items:
        return "_(none)_"
    return "\n".join(f"- {item}" for item in items)


def _parse_features_response(raw: str, analysis: ProjectAnalysis) -> list[Feature]:
    """Parse the LLM's JSON response into a list of Feature dataclasses.

    # See README: "Scrum Standards" — feature format
    #
    # Strips markdown code fences, parses JSON array, validates priority
    # against the Priority enum, and falls back to _build_fallback_features
    # on any parse error. Same defensive pattern as _parse_analysis_response.

    Args:
        raw: The raw LLM response string (expected to be a JSON array).
        analysis: The ProjectAnalysis (for fallback).

    Returns:
        A list of Feature instances.
    """
    try:
        # Strip markdown code fences that LLMs sometimes wrap JSON in
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return _build_fallback_features(analysis)

        # Validate and convert each feature dict into a Feature dataclass
        # Priority validation: default to MEDIUM if the LLM returns an invalid value
        valid_priorities = {p.value for p in Priority}
        features: list[Feature] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            raw_priority = str(item.get("priority", "medium")).lower().strip()
            priority = Priority(raw_priority) if raw_priority in valid_priorities else Priority.MEDIUM
            features.append(
                Feature(
                    id=str(item.get("id", f"F{len(features) + 1}")),
                    title=str(item.get("title", "Untitled Feature")),
                    description=str(item.get("description", "")),
                    priority=priority,
                )
            )

        if not features:
            return _build_fallback_features(analysis)

        return features

    except Exception:
        logger.debug("Failed to parse features JSON, falling back to deterministic extraction", exc_info=True)
        return _build_fallback_features(analysis)


def _build_fallback_features(analysis: ProjectAnalysis) -> list[Feature]:
    """Build 3 deterministic fallback features from ProjectAnalysis fields.

    # See README: "Scrum Standards" — feature decomposition
    #
    # When the LLM fails to produce valid JSON, this function creates 3
    # generic features that give downstream nodes something to work with:
    # 1. Core functionality — derived from the first goal
    # 2. Infrastructure & setup — always needed
    # 3. Integrations & extensions — derived from integrations/tech stack

    Args:
        analysis: The ProjectAnalysis to derive features from.

    Returns:
        A list of 3 Feature instances.
    """
    # Derive core feature title from the first goal, or use a generic label
    core_title = "Core Functionality"
    core_desc = "Implement the primary features of the project."
    if analysis.goals:
        core_title = analysis.goals[0][:60]
        core_desc = f"Implement: {analysis.goals[0]}"

    return [
        Feature(
            id="F1",
            title=core_title,
            description=core_desc,
            priority=Priority.HIGH,
        ),
        Feature(
            id="F2",
            title="Infrastructure & Setup",
            description=f"Project scaffolding, CI/CD, and deployment for {analysis.project_name}.",
            priority=Priority.HIGH,
        ),
        Feature(
            id="F3",
            title="Integrations & Extensions",
            description="Third-party integrations, APIs, and extensibility features.",
            priority=Priority.MEDIUM,
        ),
    ]


def _format_features(features: list[Feature], project_name: str) -> str:
    """Format a list of Features as a markdown display for the user.

    Matches the project's analysis summary style — sections with structured
    content. The REPL renders this as a Rich table and waits for user
    review before routing to the next pipeline step.

    Args:
        features: List of Feature dataclasses to display.
        project_name: Project name for the header.

    Returns:
        A formatted markdown string.
    """
    sections = [
        f"# Feature Decomposition: {project_name}\n",
        f"**{len(features)} feature(s) identified**\n",
    ]

    for feature in features:
        sections.append(
            f"## {feature.id}: {feature.title}\n**Priority:** {feature.priority.value}\n{feature.description}\n"
        )

    sections.append("\n---\n**[Accept / Edit / Reject]** — Review the features above.")

    return "\n".join(sections)


def feature_skip(state: ScrumState) -> dict:
    """LangGraph node: create a single feature for small projects.

    # See README: "Scrum Standards" — feature generation
    #
    # When the analyzer sets skip_features=True, this node runs instead of
    # feature_generator. It creates a single feature (id=F1) named after the project
    # so the user sees 1 feature instead of the usual 3-6. This keeps downstream
    # code (story_writer, task_decomposer, sprint_planner, renderers) working
    # unchanged — they still see a feature_id on every story.
    #
    # Sets pending_review to "feature_generator" so the review checkpoint fires
    # and the user sees the feature before moving to stories. This keeps
    # the flow consistent: analyze → features (review) → stories → tasks → sprints.

    Args:
        state: The current LangGraph state with project_analysis.

    Returns:
        A dict updating features, messages, and pending_review.
    """
    analysis: ProjectAnalysis = state["project_analysis"]
    sentinel = Feature(
        id="F1",
        title=analysis.project_name,
        description=analysis.project_description,
        priority=Priority.HIGH,
    )

    display = (
        f"# {analysis.project_name}\n\n"
        f"Project scope is small — 1 feature covers all planned work.\n\n"
        f"**{analysis.project_name}:** {analysis.project_description}\n"
    )

    return {
        "features": [sentinel],
        "messages": [AIMessage(content=display)],
        "pending_review": "feature_generator",
    }


def feature_generator(state: ScrumState) -> dict:
    """LangGraph node: decompose ProjectAnalysis into 3-6 features.

    # See README: "Agentic Blueprint Reference" — node return format
    # See README: "Architecture" — feature_generator node
    # See README: "Scrum Standards" — feature decomposition
    #
    # How this works:
    # 1. Read the ProjectAnalysis from state.
    # 2. Format analysis fields into strings for the prompt.
    # 3. Call the LLM with the feature generator prompt (temperature=0.0).
    # 4. Parse the JSON array response into Feature dataclasses.
    # 5. Return {"features": [...], "messages": [AIMessage]}.
    #
    # Why this returns to END (not to agent)?
    # Same pattern as project_analyzer — the node produces output, the REPL
    # displays it, and the user types anything to continue. On the next
    # invocation, route_entry sees features populated and routes to the agent.

    Args:
        state: The current LangGraph state with project_analysis.

    Returns:
        A dict updating features and messages.
    """
    analysis: ProjectAnalysis = state["project_analysis"]
    # Read repo context scanned during project_analyzer — grounds feature scope
    # in real directory structure and README goals vs. user descriptions only.
    repo_context: str | None = state.get("repo_context")

    # See README: "Guardrails" — human-in-the-loop pattern
    # Read review state from previous reject/edit decision. When present, the
    # REPL has cleared the old artifacts and set last_review_decision/feedback
    # so this node regenerates with user feedback injected into the prompt.
    review_decision = state.get("last_review_decision")
    review_feedback = state.get("last_review_feedback", "")
    review_mode = review_decision.value if review_decision else None

    # For edit mode, extract previous output from the feedback string.
    # The REPL packs it as: "{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}"
    previous_output = None
    if review_mode == "edit" and "---PREVIOUS OUTPUT---" in review_feedback:
        parts = review_feedback.split("---PREVIOUS OUTPUT---", 1)
        review_feedback = parts[0].strip()
        previous_output = parts[1].strip()

    # Format ProjectAnalysis fields into strings for the prompt.
    # This avoids passing the dataclass directly, keeping the prompt module
    # free of imports from agent.state (avoiding circular imports).
    target_sprints_str = str(analysis.target_sprints) if analysis.target_sprints else "scope-based"

    prompt = get_feature_generator_prompt(
        project_name=analysis.project_name,
        project_description=analysis.project_description,
        project_type=analysis.project_type,
        goals=_format_epic_list(analysis.goals),
        end_users=_format_epic_list(analysis.end_users),
        target_state=analysis.target_state or "_(not specified)_",
        tech_stack=_format_epic_list(analysis.tech_stack),
        constraints=_format_epic_list(analysis.constraints),
        risks=_format_epic_list(analysis.risks),
        target_sprints=target_sprints_str,
        out_of_scope=_format_epic_list(analysis.out_of_scope),
        repo_context=repo_context,
        review_feedback=review_feedback if review_mode else None,
        review_mode=review_mode,
        previous_output=previous_output,
    )

    try:
        # Single LLM call with low temperature for deterministic JSON output.
        # See README: "Agentic Blueprint Reference" — using the LLM outside the main graph
        response = get_llm(temperature=0.0).invoke([HumanMessage(content=prompt)])
        features = _parse_features_response(response.content, analysis)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            raise
        # LLM call failed entirely — use deterministic fallback.
        logger.warning("LLM call failed in feature_generator, using fallback", exc_info=True)
        features = _build_fallback_features(analysis)

    # Format the features for display
    display = _format_features(features, analysis.project_name)

    # Set pending_review so the REPL intercepts the next user input for
    # the [Accept / Edit / Reject] flow instead of invoking the graph.
    return {
        "features": features,
        "messages": [AIMessage(content=display)],
        "pending_review": "feature_generator",
    }


# ---------------------------------------------------------------------------
# Story writer node — decomposes Features into UserStory dataclasses
# ---------------------------------------------------------------------------
# See README: "Architecture" — story_writer sits between feature_generator and agent
# See README: "Scrum Standards" — story format, acceptance criteria, story points
#
# Same pattern as feature_generator: extract from state → build prompt →
# LLM call → parse JSON → fallback → format display → return state update.
# The node returns to END so the REPL can display the stories and wait for
# user input. On the next invocation, route_entry sees stories populated
# and routes to the agent node.


def _format_features_for_prompt(features: list[Feature]) -> str:
    """Format a list of features as a readable text block for the story writer prompt.

    Each feature is shown with its ID, title, description, and priority so the LLM
    can reference them when generating stories.

    Args:
        features: List of Feature dataclasses from the feature_generator node.

    Returns:
        A formatted multi-line string describing all features.
    """
    lines: list[str] = []
    for feature in features:
        lines.append(f"**{feature.id}: {feature.title}** (Priority: {feature.priority.value})")
        lines.append(f"  {feature.description}")
        lines.append("")
    return "\n".join(lines)


def _snap_to_fibonacci(value: int) -> StoryPointValue:
    """Clamp an integer to [1, 8] and snap to the nearest Fibonacci story point.

    # See README: "Scrum Standards" — story points on Fibonacci scale
    #
    # The LLM may return non-Fibonacci values (e.g. 4, 6, 10). This helper
    # ensures all story points are valid StoryPointValue members:
    # 1. Clamp to [1, 8] range
    # 2. Find the closest Fibonacci value

    Args:
        value: The raw story point value from the LLM.

    Returns:
        The nearest valid StoryPointValue (1, 2, 3, 5, or 8).
    """
    clamped = max(1, min(8, value))
    # Find the Fibonacci value with the smallest distance
    fibonacci_values = list(StoryPointValue)
    closest = min(fibonacci_values, key=lambda fib: abs(fib.value - clamped))
    return closest


# ── Discipline inference ──────────────────────────────────────────────
# See README: "Scrum Standards" — discipline tagging
#
# Keyword-based discipline inference. The LLM prompt asks for a discipline
# field in the JSON, but if the LLM omits it or gives a bad value, this
# function guesses based on keywords in the story text. Best-effort —
# the TODO says "where possible". If both frontend and backend keywords
# match, defaults to FULLSTACK.

_FRONTEND_KEYWORDS = frozenset(
    {
        "ui",
        "component",
        "css",
        "frontend",
        "page",
        "form",
        "button",
        "layout",
        "responsive",
        "style",
    }
)
_BACKEND_KEYWORDS = frozenset(
    {
        "api",
        "endpoint",
        "database",
        "server",
        "backend",
        "query",
        "migration",
        "schema",
    }
)
_INFRASTRUCTURE_KEYWORDS = frozenset(
    {
        "ci",
        "cd",
        "deploy",
        "pipeline",
        "docker",
        "infrastructure",
        "monitoring",
        "logging",
    }
)
_DESIGN_KEYWORDS = frozenset({"design", "wireframe", "mockup", "prototype", "ux"})
_TESTING_KEYWORDS = frozenset({"test", "qa", "automation", "coverage"})


def _infer_discipline(story: UserStory) -> Discipline:
    """Infer a discipline tag from story text using keyword matching.

    Combines the story's persona, goal, benefit, and acceptance criteria text
    into a single searchable string and checks for discipline keywords.
    If both frontend and backend keywords match, returns FULLSTACK.

    Args:
        story: A UserStory to classify.

    Returns:
        The inferred Discipline value.
    """
    # Combine all textual fields into a single lowercase string for matching
    parts = [story.persona, story.goal, story.benefit]
    for ac in story.acceptance_criteria:
        parts.extend([ac.given, ac.when, ac.then])
    text = " ".join(parts).lower()

    words = set(text.split())

    has_frontend = bool(words & _FRONTEND_KEYWORDS)
    has_backend = bool(words & _BACKEND_KEYWORDS)

    # If both frontend and backend match, it's fullstack
    if has_frontend and has_backend:
        return Discipline.FULLSTACK
    if has_frontend:
        return Discipline.FRONTEND
    if has_backend:
        return Discipline.BACKEND
    if words & _INFRASTRUCTURE_KEYWORDS:
        return Discipline.INFRASTRUCTURE
    if words & _DESIGN_KEYWORDS:
        return Discipline.DESIGN
    if words & _TESTING_KEYWORDS:
        return Discipline.TESTING
    return Discipline.FULLSTACK


# ── Story validation ──────────────────────────────────────────────────
# See README: "Scrum Standards" — Story Checklist
#
# Deterministic post-processing after LLM parsing. Checks acceptance criteria
# count (>= 3), non-empty required fields, and per-feature story counts. Auto-fixes
# what it can and collects warnings for the user. No LLM call needed — all rules
# are deterministic.

_GENERIC_ACS = (
    AcceptanceCriterion(
        given="the feature is available",
        when="the user performs the happy-path workflow",
        then="the expected outcome is achieved",
    ),
    AcceptanceCriterion(
        given="invalid input is provided",
        when="the user attempts the action",
        then="an appropriate error message is shown",
    ),
    AcceptanceCriterion(
        given="an edge case scenario occurs",
        when="the user encounters unusual conditions",
        then="the system handles it gracefully",
    ),
)


def _validate_stories(stories: list[UserStory], features: list[Feature]) -> tuple[list[UserStory], list[str]]:
    """Validate stories against the Story Checklist and auto-fix where possible.

    # See README: "Scrum Standards" — Story Checklist
    #
    # Checks:
    # 1. AC count >= 3 — pads with generic ACs if fewer.
    # 2. Non-empty persona, goal, benefit — sets defaults if empty.
    # 3. Per-feature story count in [MIN_STORIES_PER_FEATURE, MAX_STORIES_PER_FEATURE] — warns if out of range.
    #
    # Returns new UserStory instances (frozen, so must rebuild) with fixes applied.

    Args:
        stories: The parsed stories to validate.
        features: The feature list for per-feature count validation.

    Returns:
        A tuple of (validated_stories, warnings).
    """
    warnings: list[str] = []
    validated: list[UserStory] = []

    for story in stories:
        needs_rebuild = False
        new_persona = story.persona
        new_goal = story.goal
        new_benefit = story.benefit
        new_title = story.title
        new_acs = list(story.acceptance_criteria)

        # Check title — generate from goal if missing
        if not story.title.strip():
            # Capitalise the goal and truncate to ~7 words for a concise heading
            goal_words = story.goal.strip().split()
            new_title = " ".join(goal_words[:7]).rstrip(",;:.")
            # Capitalise first letter of each word for a title-case heading
            new_title = new_title.title()
            needs_rebuild = True

        # Check non-empty fields
        if not story.persona.strip():
            new_persona = "user"
            needs_rebuild = True
            warnings.append(f"Story {story.id} had empty persona — defaulted to 'user'.")
        if not story.goal.strip():
            new_goal = "perform the required action"
            needs_rebuild = True
            warnings.append(f"Story {story.id} had empty goal — set default.")
        if not story.benefit.strip():
            new_benefit = "the expected value is delivered"
            needs_rebuild = True
            warnings.append(f"Story {story.id} had empty benefit — set default.")

        # Check AC count >= 3
        if len(new_acs) < 3:
            added = 0
            for generic_ac in _GENERIC_ACS:
                if len(new_acs) >= 3:
                    break
                # Only add generic ACs that aren't already present
                if generic_ac not in new_acs:
                    new_acs.append(generic_ac)
                    added += 1
            if added > 0:
                needs_rebuild = True
                warnings.append(
                    f"Story {story.id} had only {len(story.acceptance_criteria)} AC(s) "
                    f"— added {added} generic AC(s) to reach 3."
                )

        if needs_rebuild:
            validated.append(
                dataclasses.replace(
                    story,
                    persona=new_persona,
                    goal=new_goal,
                    benefit=new_benefit,
                    acceptance_criteria=tuple(new_acs),
                    title=new_title,
                )
            )
        else:
            validated.append(story)

    # Per-feature story count warnings.

    feature_counts: dict[str, int] = {}
    for story in validated:
        feature_counts[story.feature_id] = feature_counts.get(story.feature_id, 0) + 1

    for feature in features:
        count = feature_counts.get(feature.id, 0)
        if count < MIN_STORIES_PER_FEATURE:
            warnings.append(
                f"Feature {feature.id} ({feature.title}) has only {count} story(ies)"
                f" — minimum recommended is {MIN_STORIES_PER_FEATURE}."
            )
        elif count > MAX_STORIES_PER_FEATURE:
            warnings.append(
                f"Feature {feature.id} ({feature.title}) has {count} stories "
                f"— maximum recommended is {MAX_STORIES_PER_FEATURE}."
            )

    return validated, warnings


def _parse_stories_response(raw: str, features: list[Feature], analysis: ProjectAnalysis) -> list[UserStory]:
    """Parse the LLM's JSON response into a list of UserStory dataclasses.

    # See README: "Scrum Standards" — story format, acceptance criteria
    #
    # Strips markdown code fences, parses JSON array, validates priorities
    # and story points, parses nested acceptance criteria, generates IDs
    # when missing, and falls back to _build_fallback_stories on error.
    # Same defensive pattern as _parse_features_response.

    Args:
        raw: The raw LLM response string (expected to be a JSON array).
        features: The list of features (for fallback and ID validation).
        analysis: The ProjectAnalysis (for fallback).

    Returns:
        A list of UserStory instances.
    """
    try:
        # Strip markdown code fences that LLMs sometimes wrap JSON in
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return _build_fallback_stories(features, analysis)

        # Build a set of valid feature IDs for validation
        valid_feature_ids = {e.id for e in features}

        # Track per-feature story counts for auto-ID generation
        feature_story_counts: dict[str, int] = {}

        valid_priorities = {p.value for p in Priority}
        stories: list[UserStory] = []

        for item in parsed:
            if not isinstance(item, dict):
                continue

            # Validate feature_id — skip stories with unknown feature IDs
            feature_id = str(item.get("feature_id", ""))
            if feature_id not in valid_feature_ids:
                continue

            # Auto-generate ID if missing
            feature_story_counts.setdefault(feature_id, 0)
            feature_story_counts[feature_id] += 1
            story_id = str(item.get("id", ""))
            if not story_id:
                story_id = f"US-{feature_id}-{feature_story_counts[feature_id]:03d}"

            # Validate and snap story points to Fibonacci
            raw_points = item.get("story_points", 3)
            try:
                points = _snap_to_fibonacci(int(raw_points))
            except (ValueError, TypeError):
                points = StoryPointValue.THREE

            # Validate priority — default to MEDIUM if invalid
            raw_priority = str(item.get("priority", "medium")).lower().strip()
            priority = Priority(raw_priority) if raw_priority in valid_priorities else Priority.MEDIUM

            # Parse discipline — fall back to inference if missing or invalid.
            # See README: "Scrum Standards" — discipline tagging
            valid_disciplines = {d.value for d in Discipline}
            raw_discipline = str(item.get("discipline", "")).lower().strip()
            discipline = Discipline(raw_discipline) if raw_discipline in valid_disciplines else None

            # Parse nested acceptance criteria
            raw_acs = item.get("acceptance_criteria", [])
            acs: list[AcceptanceCriterion] = []
            if isinstance(raw_acs, list):
                for ac_item in raw_acs:
                    if isinstance(ac_item, dict):
                        given = str(ac_item.get("given", "")).strip()
                        when = str(ac_item.get("when", "")).strip()
                        then = str(ac_item.get("then", "")).strip()
                        if given and when and then:
                            acs.append(AcceptanceCriterion(given=given, when=when, then=then))

            # Fallback: if no valid ACs parsed, add a generic happy-path AC
            if not acs:
                persona = str(item.get("persona", "user"))
                goal = str(item.get("goal", "perform the action"))
                acs.append(
                    AcceptanceCriterion(
                        given=f"the {persona} is authenticated",
                        when=f"they {goal}",
                        then="the operation completes successfully",
                    )
                )

            # Parse Definition of Done applicability flags.
            # LLM returns a 7-element boolean array matching DOD_ITEMS order.
            # Fall back to all-True (fully applicable) if the field is missing or malformed.
            raw_dod = item.get("dod_applicable")
            if isinstance(raw_dod, list) and len(raw_dod) == len(DOD_ITEMS):
                dod_applicable: tuple[bool, ...] = tuple(bool(f) for f in raw_dod)
            else:
                dod_applicable = (True,) * len(DOD_ITEMS)

            points_rationale = str(item.get("points_rationale", ""))

            story = UserStory(
                id=story_id,
                feature_id=feature_id,
                persona=str(item.get("persona", "user")),
                goal=str(item.get("goal", "")),
                benefit=str(item.get("benefit", "")),
                acceptance_criteria=tuple(acs),
                story_points=points,
                priority=priority,
                title=str(item.get("title", "")),
                discipline=discipline if discipline is not None else Discipline.FULLSTACK,
                dod_applicable=dod_applicable,
                points_rationale=points_rationale,
            )
            # Infer discipline from text when the LLM didn't provide a valid one
            if discipline is None:
                story = dataclasses.replace(story, discipline=_infer_discipline(story))
            stories.append(story)

        if not stories:
            return _build_fallback_stories(features, analysis)

        return stories

    except Exception:
        logger.debug("Failed to parse stories JSON, falling back to deterministic extraction", exc_info=True)
        return _build_fallback_stories(features, analysis)


def _build_fallback_stories(features: list[Feature], analysis: ProjectAnalysis) -> list[UserStory]:
    """Build deterministic fallback stories per feature.

    # See README: "Scrum Standards" — story format
    #
    # When the LLM fails to produce valid JSON, this function creates generic
    # stories that give downstream nodes something to work with.
    # Generates 2 stories per feature (core functionality + setup/testing).

    Args:
        features: The list of features to generate stories for.
        analysis: The ProjectAnalysis for context.

    Returns:
        A list of UserStory instances.
    """
    stories: list[UserStory] = []
    end_user = analysis.end_users[0] if analysis.end_users else "user"

    for feature in features:
        # Story 1: core functionality
        stories.append(
            UserStory(
                id=f"US-{feature.id}-001",
                feature_id=feature.id,
                persona=end_user,
                goal=f"use the core features of {feature.title}",
                benefit=f"I can accomplish the primary objectives of {feature.title}",
                acceptance_criteria=(
                    AcceptanceCriterion(
                        given=f"the {end_user} has access to {feature.title}",
                        when="they perform the main workflow",
                        then="the expected outcome is achieved",
                    ),
                ),
                story_points=StoryPointValue.FIVE,
                priority=feature.priority,
                title=f"Core {feature.title}",
                discipline=Discipline.FULLSTACK,
            )
        )

        # Story 2: setup and testing
        stories.append(
            UserStory(
                id=f"US-{feature.id}-002",
                feature_id=feature.id,
                persona="developer",
                goal=f"set up and validate {feature.title}",
                benefit="the feature is reliable and properly tested",
                acceptance_criteria=(
                    AcceptanceCriterion(
                        given="the development environment is configured",
                        when=f"the {feature.title} feature is deployed",
                        then="all tests pass and the feature works as expected",
                    ),
                ),
                story_points=StoryPointValue.THREE,
                priority=feature.priority,
                title=f"Validate {feature.title}",
                discipline=Discipline.TESTING,
            )
        )

    return stories


def _format_stories(
    stories: list[UserStory],
    features: list[Feature],
    project_name: str,
    warnings: list[str] | None = None,
) -> str:
    """Format a list of UserStories as a markdown display for the user.

    Groups stories by their parent feature for readability. Shows the full
    story text, acceptance criteria, story points, priority, and discipline.
    Includes optional validation warnings. The REPL renders this as Rich
    tables and waits for user review.

    Args:
        stories: List of UserStory dataclasses to display.
        features: List of Feature dataclasses (for grouping headers).
        project_name: Project name for the header.
        warnings: Optional list of validation warning strings to display.

    Returns:
        A formatted markdown string.
    """
    sections = [
        f"# User Stories: {project_name}\n",
        f"**{len(stories)} user story(ies) across {len(features)} feature(s)**\n",
    ]

    # Build a feature lookup for grouping
    feature_map = {e.id: e for e in features}

    # Group stories by feature_id, preserving order
    stories_by_feature: dict[str, list[UserStory]] = {}
    for story in stories:
        stories_by_feature.setdefault(story.feature_id, []).append(story)

    for feature_id, feature_stories in stories_by_feature.items():
        feature = feature_map.get(feature_id)
        feature_title = feature.title if feature else feature_id
        sections.append(f"## {feature_id}: {feature_title}\n")

        for story in feature_stories:
            sections.append(f"### {story.id}")
            sections.append(f"**As a** {story.persona}, **I want to** {story.goal}, **so that** {story.benefit}.")
            sections.append(
                f"**Priority:** {story.priority.value} | **Points:** {story.story_points.value} "
                f"| **Discipline:** {story.discipline.value}\n"
            )

            sections.append("**Acceptance Criteria:**")
            for i, ac in enumerate(story.acceptance_criteria, 1):
                sections.append(f"  {i}. **Given** {ac.given}")
                sections.append(f"     **When** {ac.when}")
                sections.append(f"     **Then** {ac.then}")
            sections.append("")

    # Show validation warnings if any were collected
    if warnings:
        sections.append("## Validation Notes\n")
        for warning in warnings:
            sections.append(f"- {warning}")
        sections.append("")

    sections.append("\n---\n**[Accept / Edit / Reject]** — Review the stories above.")

    return "\n".join(sections)


def story_writer(state: ScrumState) -> dict:
    """LangGraph node: decompose features into user stories with ACs and points.

    # See README: "Agentic Blueprint Reference" — node return format
    # See README: "Architecture" — story_writer sits between feature_generator and agent
    # See README: "Scrum Standards" — story format, acceptance criteria, story points
    #
    # How this works:
    # 1. Read the ProjectAnalysis and features from state.
    # 2. Format analysis fields and features into strings for the prompt.
    # 3. Call the LLM with the story writer prompt (temperature=0.0).
    # 4. Parse the JSON array response into UserStory dataclasses.
    # 5. Return {"stories": [...], "messages": [AIMessage]}.
    #
    # Why a single LLM call for all features (not per-feature)?
    # Projects have 3-6 features producing 6-30 stories. A single call keeps
    # the pattern consistent with feature_generator, avoids per-feature prompt
    # complexity, and lets the LLM see all features to avoid cross-feature
    # duplication. ~6000 output tokens is well within Claude's limits.
    #
    # Why this returns to END (not to agent)?
    # Same pattern as feature_generator — the node produces output, the REPL
    # displays it, and the user types anything to continue. On the next
    # invocation, route_entry sees stories populated and routes to the agent.

    Args:
        state: The current LangGraph state with project_analysis and features.

    Returns:
        A dict updating stories and messages.
    """
    analysis: ProjectAnalysis = state["project_analysis"]
    features: list[Feature] = state["features"]

    # Read review state (same pattern as feature_generator)
    review_decision = state.get("last_review_decision")
    review_feedback = state.get("last_review_feedback", "")
    review_mode = review_decision.value if review_decision else None

    previous_output = None
    if review_mode == "edit" and "---PREVIOUS OUTPUT---" in review_feedback:
        parts = review_feedback.split("---PREVIOUS OUTPUT---", 1)
        review_feedback = parts[0].strip()
        previous_output = parts[1].strip()

    # Format ProjectAnalysis fields and features into strings for the prompt.
    # This avoids passing dataclasses directly, keeping the prompt module
    # free of imports from agent.state (avoiding circular imports).
    # See README: "Prompt Construction" — pre-formatted strings pattern
    features_block = _format_features_for_prompt(features)

    prompt = get_story_writer_prompt(
        project_name=analysis.project_name,
        project_description=analysis.project_description,
        project_type=analysis.project_type,
        goals=_format_epic_list(analysis.goals),
        end_users=_format_epic_list(analysis.end_users),
        tech_stack=_format_epic_list(analysis.tech_stack),
        constraints=_format_epic_list(analysis.constraints),
        features_block=features_block,
        out_of_scope=_format_epic_list(analysis.out_of_scope),
        review_feedback=review_feedback if review_mode else None,
        review_mode=review_mode,
        previous_output=previous_output,
    )

    try:
        # Single LLM call with low temperature for deterministic JSON output.
        # See README: "Agentic Blueprint Reference" — using the LLM outside the main graph
        response = get_llm(temperature=0.0).invoke([HumanMessage(content=prompt)])
        stories = _parse_stories_response(response.content, features, analysis)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            raise
        # LLM call failed entirely — use deterministic fallback.
        logger.warning("LLM call failed in story_writer, using fallback", exc_info=True)
        stories = _build_fallback_stories(features, analysis)

    # Validate stories against the Story Checklist — auto-fix where possible
    # and collect warnings for the user. Deterministic post-processing, no LLM.
    # See README: "Scrum Standards" — Story Checklist
    stories, warnings = _validate_stories(stories, features)

    # Format the stories for display (with warnings if any)
    display = _format_stories(stories, features, analysis.project_name, warnings=warnings)

    return {
        "stories": stories,
        "messages": [AIMessage(content=display)],
        "pending_review": "story_writer",
    }


# ---------------------------------------------------------------------------
# Task decomposer node — breaks UserStories into Task dataclasses
# ---------------------------------------------------------------------------
# See README: "Architecture" — task_decomposer sits between story_writer and agent
# See README: "Scrum Standards" — task decomposition
#
# Same pattern as story_writer: extract from state → build prompt →
# LLM call → parse JSON → fallback → format display → return state update.
# The node returns to END so the REPL can display the tasks and wait for user
# input. On the next invocation, route_entry sees tasks populated and routes
# to the agent node.


def _build_doc_context(state: ScrumState) -> str | None:
    """Gather documentation references from intake answers and external sources.

    Collects documentation context from three places:
    1. Q14 answer — "Is there any existing documentation, PRDs, or design docs?"
    2. confluence_context — text scraped from Confluence during intake.
    3. user_context — free-form markdown from SCRUM.md (may contain doc URLs).

    Returns a formatted string for injection into the task decomposer prompt,
    or None if no documentation context is available.

    # See README: "Tools" — read-only tool pattern
    # See README: "Prompt Construction" — context injection
    """
    parts: list[str] = []

    # Q14: existing documentation, PRDs, design docs
    questionnaire = state.get("questionnaire")
    if questionnaire and questionnaire.answers.get(14):
        q14 = questionnaire.answers[14]
        # Skip the default "No existing documentation to reference"
        if "no existing documentation" not in q14.lower():
            parts.append(f"- **Existing docs (from intake):** {q14}")

    # Confluence context — scraped page content from confluence_search_docs / confluence_read_page
    confluence = state.get("confluence_context", "")
    if confluence and confluence.strip():
        parts.append(f"- **Confluence:** {confluence.strip()[:500]}")

    # User context from SCRUM.md — may contain wiki URLs, design doc links, etc.
    user_ctx = state.get("user_context", "")
    if user_ctx and user_ctx.strip():
        parts.append(f"- **Project docs (SCRUM.md):** {user_ctx.strip()[:500]}")

    if not parts:
        return None

    return "\n".join(parts)


def _format_stories_for_prompt(stories: list[UserStory], features: list[Feature]) -> str:
    """Format stories grouped by feature for the task decomposer prompt.

    For each story shows: ID, story text, story points, discipline,
    a summary of acceptance criteria, and a `[Documentation in DoD]`
    annotation when the story's Definition of Done includes documentation
    (dod_applicable[1] == True). The task decomposer prompt uses this
    annotation to decide whether to generate a dedicated documentation sub-task.

    Args:
        stories: List of UserStory dataclasses from the story_writer node.
        features: List of Feature dataclasses for grouping headers.

    Returns:
        A formatted multi-line string describing all stories grouped by feature.
    """
    feature_map = {e.id: e for e in features}

    # Group stories by feature_id, preserving order
    stories_by_feature: dict[str, list[UserStory]] = {}
    for story in stories:
        stories_by_feature.setdefault(story.feature_id, []).append(story)

    lines: list[str] = []
    for feature_id, feature_stories in stories_by_feature.items():
        feature = feature_map.get(feature_id)
        feature_title = feature.title if feature else feature_id
        lines.append(f"### {feature_id}: {feature_title}\n")

        for story in feature_stories:
            # Check if Documentation (index 1 in DOD_ITEMS) is applicable for this story.
            # When True, the prompt instructs the LLM to generate a dedicated documentation
            # sub-task that consolidates all doc work for the story.
            # See README: "Scrum Standards" — Definition of Done
            doc_in_dod = len(story.dod_applicable) > 1 and story.dod_applicable[1]
            dod_tag = " [Documentation in DoD]" if doc_in_dod else ""

            lines.append(f"**{story.id}** ({story.story_points.value} pts, {story.discipline.value}){dod_tag}")
            lines.append(f"  As a {story.persona}, I want to {story.goal}, so that {story.benefit}.")
            if story.acceptance_criteria:
                lines.append("  ACs:")
                for ac in story.acceptance_criteria:
                    lines.append(f"    - Given {ac.given}, When {ac.when}, Then {ac.then}")
            lines.append("")

    return "\n".join(lines)


def _parse_tasks_response(raw: str, stories: list[UserStory]) -> list[Task]:
    """Parse the LLM's JSON response into a list of Task dataclasses.

    # See README: "Scrum Standards" — task decomposition
    #
    # Strips markdown code fences, parses JSON array, validates story_id
    # against known story IDs, auto-generates IDs when missing, and falls
    # back to _build_fallback_tasks on error. Same defensive pattern as
    # _parse_stories_response.

    Args:
        raw: The raw LLM response string (expected to be a JSON array).
        stories: The list of stories (for story_id validation and fallback).

    Returns:
        A list of Task instances.
    """
    try:
        # Strip markdown code fences that LLMs sometimes wrap JSON in
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return _build_fallback_tasks(stories)

        # Build a set of valid story IDs for validation
        valid_story_ids = {s.id for s in stories}

        # Track per-story task counts for auto-ID generation
        story_task_counts: dict[str, int] = {}

        tasks: list[Task] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            # Validate story_id — skip tasks with unknown story IDs
            story_id = str(item.get("story_id", ""))
            if story_id not in valid_story_ids:
                continue

            # Auto-generate ID if missing
            story_task_counts.setdefault(story_id, 0)
            story_task_counts[story_id] += 1
            task_id = str(item.get("id", ""))
            if not task_id:
                task_id = f"T-{story_id}-{story_task_counts[story_id]:02d}"

            # Validate non-empty title and description
            title = str(item.get("title", "")).strip()
            if not title:
                title = f"Implement task for {story_id}"

            description = str(item.get("description", "")).strip()
            if not description:
                description = f"Implementation task for story {story_id}"

            # Parse label — default to CODE if missing or invalid.
            # The LLM is prompted to return one of: Code, Documentation, Infrastructure, Testing.
            # See README: "Scrum Standards" — task decomposition, task labels
            raw_label = str(item.get("label", "")).strip()
            try:
                label = TaskLabel(raw_label)
            except ValueError:
                label = TaskLabel.CODE

            # Parse test_plan — only expected for Code/Infrastructure tasks.
            # Empty string for Documentation/Testing tasks or if missing.
            test_plan = str(item.get("test_plan", "")).strip()

            # Parse ai_prompt — self-contained ARC-structured instruction for AI coding assistants.
            ai_prompt = str(item.get("ai_prompt", "")).strip()

            tasks.append(
                Task(
                    id=task_id,
                    story_id=story_id,
                    title=title,
                    description=description,
                    label=label,
                    test_plan=test_plan,
                    ai_prompt=ai_prompt,
                )
            )

        if not tasks:
            return _build_fallback_tasks(stories)

        return tasks

    except Exception:
        logger.debug("Failed to parse tasks JSON, falling back to deterministic extraction", exc_info=True)
        return _build_fallback_tasks(stories)


def _build_fallback_tasks(stories: list[UserStory]) -> list[Task]:
    """Build 2 deterministic fallback tasks per story.

    # See README: "Scrum Standards" — task decomposition
    #
    # When the LLM fails to produce valid JSON, this function creates 2
    # generic tasks per story that give downstream nodes something to work with:
    # 1. Core implementation — implement the story's goal
    # 2. Testing — write tests for the story's goal

    Args:
        stories: The list of stories to generate tasks for.

    Returns:
        A list of Task instances (2 per story).
    """
    tasks: list[Task] = []

    for story in stories:
        # Task 1: core implementation
        tasks.append(
            Task(
                id=f"T-{story.id}-01",
                story_id=story.id,
                title=f"Implement {story.goal}",
                description=f"Core implementation for story {story.id}: {story.goal}",
            )
        )

        # Task 2: testing
        tasks.append(
            Task(
                id=f"T-{story.id}-02",
                story_id=story.id,
                title=f"Write tests for {story.goal}",
                description=f"Write unit and integration tests for story {story.id}: {story.goal}",
            )
        )

    return tasks


def _format_tasks(
    tasks: list[Task],
    stories: list[UserStory],
    features: list[Feature],
    project_name: str,
) -> str:
    """Format a list of Tasks as a markdown display for the user.

    Groups tasks by feature → story for readability. Shows the task ID, title,
    and description. The REPL renders this as Rich tables and waits for
    user review before routing to the next pipeline step.

    Args:
        tasks: List of Task dataclasses to display.
        stories: List of UserStory dataclasses (for grouping and context).
        features: List of Feature dataclasses (for grouping headers).
        project_name: Project name for the header.

    Returns:
        A formatted markdown string.
    """
    sections = [
        f"# Task Decomposition: {project_name}\n",
        f"**{len(tasks)} task(s) across {len(stories)} story(ies)**\n",
    ]

    # Build lookups for grouping
    feature_map = {e.id: e for e in features}

    # Group tasks by story_id
    tasks_by_story: dict[str, list[Task]] = {}
    for task in tasks:
        tasks_by_story.setdefault(task.story_id, []).append(task)

    # Group stories by feature_id for display
    stories_by_feature: dict[str, list[UserStory]] = {}
    for story in stories:
        stories_by_feature.setdefault(story.feature_id, []).append(story)

    for feature_id, feature_stories in stories_by_feature.items():
        feature = feature_map.get(feature_id)
        feature_title = feature.title if feature else feature_id
        sections.append(f"## {feature_id}: {feature_title}\n")

        for story in feature_stories:
            story_tasks = tasks_by_story.get(story.id, [])
            if not story_tasks:
                continue

            sections.append(f"### {story.id}: {story.goal}")
            sections.append(f"**Points:** {story.story_points.value} | **Priority:** {story.priority.value}\n")

            for task in story_tasks:
                _lbl = task.label.value if hasattr(task.label, "value") else str(task.label)
                sections.append(f"- **{task.id}** [{_lbl}]: {task.title}")
                sections.append(f"  {task.description}")
                if task.test_plan:
                    sections.append(f"  **Test plan:** {task.test_plan}")
                if task.ai_prompt:
                    sections.append(f"  **AI prompt:** {task.ai_prompt}")
            sections.append("")

    sections.append("\n---\n**[Accept / Edit / Reject]** — Review the tasks above.")

    return "\n".join(sections)


def task_decomposer(state: ScrumState) -> dict:
    """LangGraph node: decompose user stories into concrete implementation tasks.

    # See README: "Agentic Blueprint Reference" — node return format
    # See README: "Architecture" — task_decomposer sits between story_writer and agent
    # See README: "Scrum Standards" — task decomposition
    #
    # How this works:
    # 1. Read the ProjectAnalysis, features, and stories from state.
    # 2. Format stories into a text block for the prompt.
    # 3. Call the LLM with the task decomposer prompt (temperature=0.0).
    # 4. Parse the JSON array response into Task dataclasses.
    # 5. Return {"tasks": [...], "messages": [AIMessage]}.
    #
    # Why a single LLM call for all stories (not per-story)?
    # Projects have 6-30 stories producing 12-150 tasks. A single call keeps
    # the pattern consistent with story_writer, avoids per-story prompt
    # complexity, and lets the LLM see all stories to avoid cross-story
    # task duplication.
    #
    # Why this returns to END (not to agent)?
    # Same pattern as story_writer — the node produces output, the REPL
    # displays it, and the user types anything to continue. On the next
    # invocation, route_entry sees tasks populated and routes to the agent.

    Args:
        state: The current LangGraph state with project_analysis, features, and stories.

    Returns:
        A dict updating tasks and messages.
    """
    analysis: ProjectAnalysis = state["project_analysis"]
    features: list[Feature] = state["features"]
    stories: list[UserStory] = state["stories"]

    # Read review state (same pattern as feature_generator)
    review_decision = state.get("last_review_decision")
    review_feedback = state.get("last_review_feedback", "")
    review_mode = review_decision.value if review_decision else None

    previous_output = None
    if review_mode == "edit" and "---PREVIOUS OUTPUT---" in review_feedback:
        parts = review_feedback.split("---PREVIOUS OUTPUT---", 1)
        review_feedback = parts[0].strip()
        previous_output = parts[1].strip()

    # Format stories into a text block for the prompt.
    # This avoids passing dataclasses directly, keeping the prompt module
    # free of imports from agent.state (avoiding circular imports).
    # See README: "Prompt Construction" — pre-formatted strings pattern
    stories_block = _format_stories_for_prompt(stories, features)

    # Build documentation context from intake answers and external sources.
    # The task decomposer prompt uses this to tell the LLM where documentation
    # should live, so the dedicated documentation sub-task can reference specific
    # Confluence pages, README files, or wiki URLs.
    # See README: "Tools" — read-only tool pattern, documentation references
    doc_context = _build_doc_context(state)

    prompt = get_task_decomposer_prompt(
        project_name=analysis.project_name,
        project_type=analysis.project_type,
        tech_stack=_format_epic_list(analysis.tech_stack),
        stories_block=stories_block,
        doc_context=doc_context,
        review_feedback=review_feedback if review_mode else None,
        review_mode=review_mode,
        previous_output=previous_output,
    )

    try:
        # Single LLM call with low temperature for deterministic JSON output.
        # See README: "Agentic Blueprint Reference" — using the LLM outside the main graph
        response = get_llm(temperature=0.0).invoke([HumanMessage(content=prompt)])
        tasks = _parse_tasks_response(response.content, stories)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            raise
        # LLM call failed entirely — use deterministic fallback.
        logger.warning("LLM call failed in task_decomposer, using fallback", exc_info=True)
        tasks = _build_fallback_tasks(stories)

    # Format the tasks for display
    display = _format_tasks(tasks, stories, features, analysis.project_name)

    return {
        "tasks": tasks,
        "messages": [AIMessage(content=display)],
        "pending_review": "task_decomposer",
    }


# ---------------------------------------------------------------------------
# Sprint planner node — allocates stories to sprints based on velocity
# ---------------------------------------------------------------------------
# See README: "Architecture" — sprint_planner sits between task_decomposer and agent
# See README: "Scrum Standards" — sprint planning, capacity allocation
#
# Hybrid approach: LLM allocates stories and writes natural-language sprint goals,
# then a deterministic validator ensures no sprint exceeds velocity, no stories are
# orphaned/duplicated, and capacity_points are correct. Fallback is a greedy
# bin-packing algorithm.
#
# Why not fully deterministic? Sprint goals like "Establish authentication foundation"
# require LLM reasoning. A greedy algorithm would produce generic goals.
#
# Why a single LLM call? Same pattern as task_decomposer — total stories fit in
# one call easily, and the LLM needs to see all stories to write coherent sprint goals.

# Priority sort order for the greedy fallback algorithm.
# Maps Priority enum values to sort keys (lower = higher priority = earlier sprint).
_PRIORITY_SORT_ORDER = {
    Priority.CRITICAL: 0,
    Priority.HIGH: 1,
    Priority.MEDIUM: 2,
    Priority.LOW: 3,
}


def _format_stories_for_sprint_planner(stories: list[UserStory], features: list[Feature]) -> str:
    """Format stories in a compact layout for the sprint planner prompt.

    # See README: "Prompt Construction" — pre-formatted strings pattern
    #
    # Unlike _format_stories_for_prompt (used by task_decomposer), this format
    # omits acceptance criteria — the sprint planner only needs ID, points,
    # priority, discipline, and goal for capacity allocation decisions.

    Args:
        stories: List of UserStory dataclasses to format.
        features: List of Feature dataclasses for grouping headers.

    Returns:
        A formatted multi-line string with stories grouped by feature.
    """
    feature_map = {e.id: e for e in features}

    # Group stories by feature_id, preserving order
    stories_by_feature: dict[str, list[UserStory]] = {}
    for story in stories:
        stories_by_feature.setdefault(story.feature_id, []).append(story)

    lines: list[str] = []
    for feature_id, feature_stories in stories_by_feature.items():
        feature = feature_map.get(feature_id)
        feature_title = feature.title if feature else feature_id
        feature_priority = feature.priority.value if feature else "medium"
        lines.append(f"### {feature_id}: {feature_title} ({feature_priority})")

        for story in feature_stories:
            lines.append(
                f"- **{story.id}** | {story.story_points.value} pts | "
                f"{story.priority.value} | {story.discipline.value} — {story.goal}"
            )
        lines.append("")

    return "\n".join(lines)


def _validate_sprint_capacity(
    sprints: list[Sprint],
    stories: list[UserStory],
    velocity: int,
) -> list[Sprint]:
    """Post-parse validation: fix capacity math, redistribute over-packed sprints, handle orphans.

    # See README: "Scrum Standards" — sprint capacity validation
    #
    # The LLM is good at allocating stories but sometimes gets the math wrong.
    # This function corrects three classes of errors:
    # 1. Wrong capacity_points — recalculate from actual story points
    # 2. Over-packed sprints — move excess stories to the next sprint
    # 3. Orphaned stories — append any missing stories to the last sprint
    #
    # Sprint dataclasses are frozen, so we rebuild them from scratch.

    Args:
        sprints: The parsed Sprint list (may have incorrect capacity/duplicates).
        stories: The full story list (for point lookup and orphan detection).
        velocity: Team velocity cap per sprint.

    Returns:
        A corrected list of Sprint dataclasses.
    """
    story_points_map = {s.id: s.story_points.value for s in stories}
    all_story_ids = {s.id for s in stories}

    # 1. Deduplicate: keep first occurrence of each story across all sprints
    seen: set[str] = set()
    deduped_sprints: list[list[str]] = []
    sprint_meta: list[tuple[str, str, str]] = []  # (id, name, goal) per sprint

    for sp in sprints:
        unique_ids: list[str] = []
        for sid in sp.story_ids:
            if sid not in seen and sid in all_story_ids:
                unique_ids.append(sid)
                seen.add(sid)
        deduped_sprints.append(unique_ids)
        sprint_meta.append((sp.id, sp.name, sp.goal))

    # 2. Redistribute: if any sprint exceeds velocity, move excess to next sprint
    redistributed: list[list[str]] = []
    overflow: list[str] = []
    for i, story_ids in enumerate(deduped_sprints):
        current = list(overflow) + story_ids
        overflow = []

        # Special case: single story exceeding velocity gets its own sprint
        in_sprint: list[str] = []
        total = 0
        for sid in current:
            pts = story_points_map.get(sid, 0)
            if total + pts > velocity and in_sprint:
                # This story would exceed capacity — overflow it
                overflow.append(sid)
            else:
                in_sprint.append(sid)
                total += pts

        redistributed.append(in_sprint)

    # Handle remaining overflow — create new sprints as needed
    while overflow:
        in_sprint: list[str] = []
        total = 0
        remaining: list[str] = []
        for sid in overflow:
            pts = story_points_map.get(sid, 0)
            if total + pts > velocity and in_sprint:
                remaining.append(sid)
            else:
                in_sprint.append(sid)
                total += pts
        redistributed.append(in_sprint)
        overflow = remaining

    # 3. Orphan check: find stories not in any sprint
    assigned = set()
    for story_ids in redistributed:
        assigned.update(story_ids)
    orphans = [sid for sid in all_story_ids if sid not in assigned]

    if orphans:
        # Sort orphans by priority for consistent ordering
        orphan_priority = {s.id: _PRIORITY_SORT_ORDER.get(s.priority, 3) for s in stories}
        orphans.sort(key=lambda sid: orphan_priority.get(sid, 3))

        # Try to fit orphans into the last sprint first
        if redistributed:
            last = redistributed[-1]
            last_total = sum(story_points_map.get(sid, 0) for sid in last)
            still_orphaned: list[str] = []
            for sid in orphans:
                pts = story_points_map.get(sid, 0)
                if last_total + pts <= velocity:
                    last.append(sid)
                    last_total += pts
                else:
                    still_orphaned.append(sid)
            orphans = still_orphaned

        # Create new sprints for remaining orphans
        while orphans:
            in_sprint: list[str] = []
            total = 0
            remaining: list[str] = []
            for sid in orphans:
                pts = story_points_map.get(sid, 0)
                if total + pts > velocity and in_sprint:
                    remaining.append(sid)
                else:
                    in_sprint.append(sid)
                    total += pts
            redistributed.append(in_sprint)
            orphans = remaining

    # 4. Build final Sprint objects with correct capacity_points
    result: list[Sprint] = []
    for i, story_ids in enumerate(redistributed):
        if not story_ids:
            continue  # skip empty sprints

        sprint_num = i + 1
        capacity = sum(story_points_map.get(sid, 0) for sid in story_ids)

        # Reuse original metadata if available, otherwise generate
        if i < len(sprint_meta):
            sp_id, sp_name, sp_goal = sprint_meta[i]
        else:
            sp_id = f"SP-{sprint_num}"
            sp_name = f"Sprint {sprint_num}"
            sp_goal = f"Sprint {sprint_num} stories"

        result.append(
            Sprint(
                id=sp_id,
                name=sp_name,
                goal=sp_goal,
                capacity_points=capacity,
                story_ids=tuple(story_ids),
            )
        )

    return result


def _parse_sprints_response(
    raw: str,
    stories: list[UserStory],
    velocity: int,
    starting_sprint_number: int = 0,
) -> list[Sprint]:
    """Parse the LLM's JSON response into a list of Sprint dataclasses.

    # See README: "Scrum Standards" — sprint format
    #
    # Strips markdown code fences, parses JSON array, validates story_ids
    # against known story IDs, auto-generates IDs when missing, and calls
    # _validate_sprint_capacity for post-parse correction. Falls back to
    # _build_fallback_sprints on any parse error.
    # Same defensive pattern as _parse_tasks_response.

    Args:
        raw: The raw LLM response string (expected to be a JSON array).
        stories: The list of stories (for story_id validation and fallback).
        velocity: Team velocity for capacity validation.

    Returns:
        A list of Sprint instances.
    """
    try:
        # Strip markdown code fences that LLMs sometimes wrap JSON in
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return _build_fallback_sprints(stories, velocity, starting_sprint_number)

        valid_story_ids = {s.id for s in stories}
        # When starting_sprint_number > 0, use real sprint numbers (e.g. 105, 106).
        # Otherwise fall back to generic 1-based numbering.
        base = starting_sprint_number if starting_sprint_number > 0 else 1

        sprints: list[Sprint] = []
        for i, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue

            sprint_num = base + i
            # Always use computed numbering for id/name to ensure consistency.
            # The LLM may return "Sprint 1" even when starting_sprint_number=3.
            sprint_id = f"SP-{sprint_num}"
            sprint_name = f"Sprint {sprint_num}"
            sprint_goal = str(item.get("goal", "")).strip() or f"Sprint {sprint_num} stories"

            # Parse capacity_points (will be recalculated by validator)
            try:
                capacity = int(item.get("capacity_points", 0))
            except (ValueError, TypeError):
                capacity = 0

            # Parse and validate story_ids
            raw_ids = item.get("story_ids", [])
            if not isinstance(raw_ids, list):
                raw_ids = []
            story_ids = tuple(str(sid) for sid in raw_ids if str(sid) in valid_story_ids)

            sprints.append(
                Sprint(
                    id=sprint_id,
                    name=sprint_name,
                    goal=sprint_goal,
                    capacity_points=capacity,
                    story_ids=story_ids,
                )
            )

        if not sprints:
            return _build_fallback_sprints(stories, velocity, starting_sprint_number)

        # Post-parse validation: fix capacity math, redistribute, handle orphans
        return _validate_sprint_capacity(sprints, stories, velocity)

    except Exception:
        logger.debug("Failed to parse sprints JSON, falling back to greedy bin-packing", exc_info=True)
        return _build_fallback_sprints(stories, velocity, starting_sprint_number)


def _build_fallback_sprints(stories: list[UserStory], velocity: int, starting_sprint_number: int = 0) -> list[Sprint]:
    """Greedy bin-packing fallback when the LLM fails to produce valid sprint JSON.

    # See README: "Scrum Standards" — sprint planning fallback
    #
    # Sorts stories by priority (Critical first), then packs them into sprints
    # greedily: fill the current sprint until adding the next story would exceed
    # velocity, then start a new sprint.
    #
    # Edge case: a story whose points exceed velocity gets its own sprint.
    # The alternative — orphaning it — would lose work.

    Args:
        stories: The full list of stories to allocate.
        velocity: Team velocity cap per sprint.

    Returns:
        A list of Sprint instances (one per sprint, all stories allocated).
    """
    if not stories:
        return []

    # Sort by priority (stable sort preserves relative order within same priority)
    sorted_stories = sorted(stories, key=lambda s: _PRIORITY_SORT_ORDER.get(s.priority, 3))

    sprints: list[list[UserStory]] = []
    current: list[UserStory] = []
    current_points = 0

    for story in sorted_stories:
        pts = story.story_points.value

        if current and current_points + pts > velocity:
            # Current sprint is full — start a new one
            sprints.append(current)
            current = [story]
            current_points = pts
        else:
            current.append(story)
            current_points += pts

    # Don't forget the last sprint
    if current:
        sprints.append(current)

    # Build Sprint dataclasses
    # When starting_sprint_number > 0, use real sprint numbers (e.g. 105, 106).
    base = starting_sprint_number if starting_sprint_number > 0 else 1
    result: list[Sprint] = []
    for i, sprint_stories in enumerate(sprints):
        sprint_num = base + i
        capacity = sum(s.story_points.value for s in sprint_stories)
        story_ids = tuple(s.id for s in sprint_stories)

        # Determine dominant priority for the fallback goal
        priority_counts: dict[str, int] = {}
        for s in sprint_stories:
            priority_counts[s.priority.value] = priority_counts.get(s.priority.value, 0) + 1
        dominant = max(priority_counts, key=priority_counts.get) if priority_counts else "medium"

        result.append(
            Sprint(
                id=f"SP-{sprint_num}",
                name=f"Sprint {sprint_num}",
                goal=f"Sprint {sprint_num}: {dominant} priority stories",
                capacity_points=capacity,
                story_ids=story_ids,
            )
        )

    return result


def _merge_sprints_to_target(
    sprints: list[Sprint],
    target: int,
    stories: list[UserStory],
    starting_sprint_number: int = 0,
) -> list[Sprint]:
    """Merge sprints down to a target count when the user enforced a deadline.

    # See README: "Scrum Standards" — sprint planning, capacity allocation
    #
    # When the user rejects the capacity recommendation and keeps their original
    # target, the LLM may still produce more sprints than requested. This function
    # merges them deterministically:
    # 1. Collect all story IDs in priority order from the existing sprints
    # 2. Distribute them round-robin across `target` buckets, keeping priority
    #    ordering so critical stories still land in earlier sprints
    # 3. Rebuild Sprint objects with correct IDs, names, and capacity

    Args:
        sprints: The sprints produced by the LLM or fallback (may exceed target).
        target: The enforced sprint count (user's deadline).
        stories: Full story list for point lookups.
        starting_sprint_number: Starting sprint number (e.g. 105) for naming.

    Returns:
        Exactly `target` Sprint objects with all stories distributed.
    """
    if len(sprints) <= target or target <= 0:
        return sprints

    # Collect all story IDs in their current order (preserves LLM's priority ordering)
    all_story_ids: list[str] = []
    for sp in sprints:
        all_story_ids.extend(sp.story_ids)

    # Build point lookup
    points_map = {s.id: s.story_points.value for s in stories}

    # Distribute stories across target buckets using greedy bin-packing.
    # Each bucket fills until adding the next story would make it the largest;
    # this produces a roughly even distribution.
    buckets: list[list[str]] = [[] for _ in range(target)]
    bucket_points: list[int] = [0] * target

    for sid in all_story_ids:
        pts = points_map.get(sid, 0)
        # Put in the lightest bucket (greedy even distribution)
        lightest = min(range(target), key=lambda i: bucket_points[i])
        buckets[lightest].append(sid)
        bucket_points[lightest] += pts

    # Preserve original sprint goals where possible
    original_goals = [sp.goal for sp in sprints]

    base = starting_sprint_number if starting_sprint_number > 0 else 1
    merged: list[Sprint] = []
    for i in range(target):
        sprint_num = base + i
        goal = original_goals[i] if i < len(original_goals) else f"Sprint {sprint_num} stories"
        merged.append(
            Sprint(
                id=f"SP-{sprint_num}",
                name=f"Sprint {sprint_num}",
                goal=goal,
                capacity_points=bucket_points[i],
                story_ids=tuple(buckets[i]),
            )
        )

    return merged


def _format_sprints(
    sprints: list[Sprint],
    stories: list[UserStory],
    features: list[Feature],
    project_name: str,
    velocity: int,
) -> str:
    """Format a list of Sprints as a rich markdown display for the user.

    Shows a header with sprint count and velocity, then per-sprint details
    including goal, capacity bar, and story list with feature context.
    The REPL renders this as Rich panels and waits for user review.

    Args:
        sprints: List of Sprint dataclasses to display.
        stories: List of UserStory dataclasses (for story details).
        features: List of Feature dataclasses (for feature context).
        project_name: Project name for the header.
        velocity: Team velocity for capacity display.

    Returns:
        A formatted markdown string.
    """
    story_map = {s.id: s for s in stories}
    feature_map = {e.id: e for e in features}

    total_points = sum(sp.capacity_points for sp in sprints)

    sections = [
        f"# Sprint Plan: {project_name}\n",
        f"**{len(sprints)} sprint(s)** | Velocity: **{velocity} pts/sprint** | Total: **{total_points} pts**\n",
    ]

    for sp in sprints:
        sections.append(f"## {sp.name} ({sp.capacity_points}/{velocity} pts)")
        sections.append(f"**Goal:** {sp.goal}\n")

        for sid in sp.story_ids:
            story = story_map.get(sid)
            if story:
                feature = feature_map.get(story.feature_id)
                feature_label = f"[{feature.title}]" if feature else f"[{story.feature_id}]"
                sections.append(
                    f"- **{story.id}** ({story.story_points.value} pts, {story.priority.value}) "
                    f"{feature_label} — {story.goal}"
                )
            else:
                sections.append(f"- **{sid}** (unknown story)")
        sections.append("")

    sections.append("\n---\n**[Accept / Edit / Reject]** — Review the sprint plan above.")

    return "\n".join(sections)


# sprint_selector and capacity_check were removed — sprint selection and
# capacity planning are now handled during intake (Phase 6: Q27-Q30).
# See _is_jira_configured(), _fetch_active_sprint_number(), _derive_q27_from_locale(),
# _extract_capacity_deductions(), and _compute_net_velocity() above.


def resolve_sprint_selection(user_input: str, current_sprint_number: int) -> int | None:
    """Resolve the user's sprint selection input into a starting sprint number.

    # See README: "Scrum Standards" — sprint planning
    #
    # Used by the intake node when processing Q27 (sprint selection) with Jira,
    # and by the REPL for backwards compatibility.

    Args:
        user_input: The user's response (e.g. "1", "2", "3", or a number like "110").
        current_sprint_number: The active Jira sprint number (e.g. 104).

    Returns:
        The starting sprint number (e.g. 105), or None if input is invalid.
    """
    text = user_input.strip()

    # Quick option selection: 1, 2, 3 map to next, +2, +3
    if text == "1":
        return current_sprint_number + 1
    if text == "2":
        return current_sprint_number + 2
    if text == "3":
        return current_sprint_number + 3

    # Try to parse as a direct sprint number
    try:
        num = int(text)
        if num > 0:
            return num
        return None
    except ValueError:
        return None


def sprint_planner(state: ScrumState) -> dict:
    """LangGraph node: allocate stories to sprints based on team velocity.

    # See README: "Agentic Blueprint Reference" — node return format
    # See README: "Architecture" — sprint_planner sits between task_decomposer and agent
    # See README: "Scrum Standards" — sprint planning, capacity allocation
    #
    # How this works:
    # 1. Read the ProjectAnalysis, features, stories, velocity, and target_sprints from state.
    # 2. Format stories in a compact layout for the prompt (no ACs — just points/priority).
    # 3. Call the LLM with the sprint planner prompt (temperature=0.0).
    # 4. Parse the JSON array response into Sprint dataclasses.
    # 5. Validate sprint capacity and fix any LLM math errors.
    # 6. Return {"sprints": [...], "messages": [AIMessage]}.
    #
    # Why a hybrid LLM + deterministic approach?
    # The LLM writes natural-language sprint goals (e.g. "Establish authentication
    # foundation") that a greedy algorithm can't produce. But the LLM sometimes
    # gets the math wrong, so _validate_sprint_capacity corrects capacity_points,
    # redistributes over-packed sprints, and ensures no stories are orphaned.
    #
    # Default velocity: team_size × 5 (same formula as _extract_team_and_velocity).
    # If velocity_per_sprint isn't in state, we calculate it here as a fallback.

    Args:
        state: The current LangGraph state with project_analysis, features, stories, and velocity.

    Returns:
        A dict updating sprints and messages.
    """
    analysis: ProjectAnalysis = state["project_analysis"]
    features: list[Feature] = state["features"]
    stories: list[UserStory] = state["stories"]

    # Read review state (same pattern as feature_generator)
    review_decision = state.get("last_review_decision")
    review_feedback = state.get("last_review_feedback", "")
    review_mode = review_decision.value if review_decision else None

    previous_output = None
    if review_mode == "edit" and "---PREVIOUS OUTPUT---" in review_feedback:
        parts = review_feedback.split("---PREVIOUS OUTPUT---", 1)
        review_feedback = parts[0].strip()
        previous_output = parts[1].strip()

    # Extract velocity — prefer net velocity (after capacity deductions) when available.
    # Falls back to gross velocity, then to default calculation.
    # See README: "Scrum Standards" — capacity planning
    team_size = state.get("team_size", 1)
    original_team_size = team_size  # Save before potential team override
    gross_velocity = state.get("velocity_per_sprint", team_size * _VELOCITY_PER_ENGINEER)
    velocity = state.get("net_velocity_per_sprint") or gross_velocity
    target_sprints = state.get("target_sprints", analysis.target_sprints)

    # Read the starting sprint number set by sprint_selector.
    # Positive values mean the user chose a real sprint number (e.g. 105).
    # Negative or zero means no Jira / no selection — use generic numbering.
    raw_start = state.get("starting_sprint_number", 0)
    starting_sprint_number = raw_start if raw_start > 0 else 0

    # Read the raw Q10 answer (e.g. "3–5 sprints (~1 quarter)") so the prompt
    # can show the user's chosen range rather than just the parsed upper bound.
    # This gives the LLM context like "aim for 3–5 sprints" instead of just "5".
    target_sprints_raw = ""
    qs = state.get("questionnaire")
    if isinstance(qs, QuestionnaireState):
        target_sprints_raw = qs.answers.get(10, "")

    # ── Capacity check ────────────────────────────────────────────────
    # See README: "Guardrails" — human-in-the-loop pattern
    #
    # Before calling the LLM, verify that the user's sprint target can
    # actually hold all the story points. If total points exceed
    # velocity × target, the scope doesn't fit and the user needs to
    # know. We treat the sprint target as a deadline — the source of
    # truth — and warn rather than silently exceeding it.
    #
    # Encoding (same pattern as sprint_selector):
    #   0       → not yet checked
    #   < -1    → warning pending; abs(value) = recommended sprint count
    #   -1      → user rejected recommendation; proceed with original
    #   > 0     → user accepted; override target with this value
    capacity_override = state.get("capacity_override_target", 0)
    sprint_caps = state.get("sprint_capacities", [])

    # enforce_target is set when the user explicitly rejected the capacity
    # recommendation — tells the prompt to treat the target as a hard deadline.
    enforce_target = False

    if capacity_override > 0:
        # User accepted the recommendation — use it as the new target.
        target_sprints = capacity_override
        # Update the raw text so the prompt reflects the accepted target.
        target_sprints_raw = f"{capacity_override} sprints (accepted recommendation)"
    elif capacity_override == -1 and state.get("_capacity_team_override", 0) > 0:
        # User chose to increase team size — recalculate velocity to fit scope
        # in the original sprint count without using enforce_target.
        # See README: "Guardrails" — human-in-the-loop pattern
        new_team = state["_capacity_team_override"]
        velocity_per_engineer = gross_velocity // team_size if team_size > 0 else gross_velocity
        gross_velocity = velocity_per_engineer * new_team
        team_size = new_team
        # Extract holiday/PTO data from existing sprint_caps before recomputing
        old_caps = sprint_caps
        holidays_by_sprint: dict[int, list[dict]] = {}
        leave_by_sprint: dict[int, list[dict]] | None = None
        if old_caps:
            holidays_by_sprint = {
                sc["sprint_index"]: [{"name": n} for n in sc.get("bank_holiday_names", [])] for sc in old_caps
            }
            leave_by_sprint = {sc["sprint_index"]: sc.get("pto_entries", []) for sc in old_caps}
        # Recompute sprint capacities from scratch with the new team size —
        # bank holidays affect ALL engineers (team_size × days), and other
        # deductions also scale with team size, so a simple multiplier won't work.
        sprint_caps = _compute_per_sprint_velocities(
            team_size=new_team,
            velocity_per_sprint=gross_velocity,
            sprint_length_weeks=state.get("sprint_length_weeks", analysis.sprint_length_weeks),
            target_sprints=target_sprints,
            holidays_by_sprint=holidays_by_sprint,
            planned_leave_days=state.get("capacity_planned_leave_days", 0),
            unplanned_leave_pct=state.get("capacity_unplanned_leave_pct", 0),
            onboarding_engineer_sprints=state.get("capacity_onboarding_engineer_sprints", 0),
            ktlo_engineers=state.get("capacity_ktlo_engineers", 0),
            discovery_pct=state.get("capacity_discovery_pct", 5),
            leave_by_sprint=leave_by_sprint,
        )
        # Use the minimum per-sprint velocity as the net velocity
        velocity = min(sc["net_velocity"] for sc in sprint_caps) if sprint_caps else gross_velocity
    elif capacity_override == -1:
        # User rejected — proceed with the original target_sprints but enforce it.
        enforce_target = True
    elif capacity_override == 0 and target_sprints > 0 and velocity > 0 and not review_mode:
        # First time through — check if scope fits in the target.
        # Use per-sprint velocities for a more accurate capacity check.
        total_points = sum(s.story_points for s in stories)
        if sprint_caps:
            total_capacity = sum(sc["net_velocity"] for sc in sprint_caps)
            min_sprints = math.ceil(total_points / velocity) if velocity > 0 else target_sprints
        else:
            total_capacity = velocity * target_sprints
            min_sprints = math.ceil(total_points / velocity)
        if total_points > total_capacity:
            # Scope overflow — warn the user and return early (no LLM call yet).
            # Compute alternative: minimum team size to fit scope in original sprints.
            # Velocity scales linearly with team size (velocity_per_engineer × team_size).
            # See README: "Guardrails" — human-in-the-loop pattern
            velocity_per_engineer = velocity // team_size if team_size > 0 else velocity
            min_team_size = (
                math.ceil(total_points / (velocity_per_engineer * target_sprints))
                if velocity_per_engineer > 0
                else team_size + 1
            )
            # Cap min_team_size to the Jira org team size if available — can't
            # recommend more engineers than actually exist on the Jira board.
            jira_team_size = _parse_jira_team_size(qs) if isinstance(qs, QuestionnaireState) else None
            if jira_team_size and min_team_size > jira_team_size:
                min_team_size = jira_team_size
            target_label = target_sprints_raw or f"{target_sprints} sprints"
            warning = (
                f"Your stories total **{total_points} story points**. "
                f"At **{velocity} points/sprint** velocity, you need at least "
                f"**{min_sprints} sprints** — but your target is **{target_label}**.\n\n"
                f"You can extend the timeline, increase team capacity, or keep as-is."
            )
            return {
                "capacity_override_target": -(min_sprints),
                "_original_target_sprints": target_sprints,
                "_recommended_team_size": min_team_size,
                "messages": [AIMessage(content=warning)],
            }

    # Format stories into a compact text block for the prompt.
    # Unlike the task_decomposer prompt, we omit ACs — the sprint planner
    # only needs points, priority, and discipline for capacity allocation.
    stories_block = _format_stories_for_sprint_planner(stories, features)

    prompt = get_sprint_planner_prompt(
        project_name=analysis.project_name,
        project_description=analysis.project_description,
        velocity=velocity,
        target_sprints=target_sprints,
        stories_block=stories_block,
        target_sprints_raw=target_sprints_raw,
        starting_sprint_number=starting_sprint_number,
        enforce_target=enforce_target,
        sprint_capacities=sprint_caps or None,
        team_override_from=original_team_size if state.get("_capacity_team_override", 0) > 0 else None,
        review_feedback=review_feedback if review_mode else None,
        review_mode=review_mode,
        previous_output=previous_output,
    )

    try:
        # Single LLM call with low temperature for deterministic JSON output.
        # See README: "Agentic Blueprint Reference" — using the LLM outside the main graph
        response = get_llm(temperature=0.0).invoke([HumanMessage(content=prompt)])
        sprints = _parse_sprints_response(response.content, stories, velocity, starting_sprint_number)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            raise
        # LLM call failed entirely — use greedy bin-packing fallback.
        logger.warning("LLM call failed in sprint_planner, using fallback", exc_info=True)
        sprints = _build_fallback_sprints(stories, velocity, starting_sprint_number)

    # When the user enforced a hard target (rejected the capacity recommendation)
    # OR chose to increase team size (which keeps the original sprint count),
    # merge sprints down to the target count. The LLM often ignores the constraint
    # and produces more sprints than requested — this is the programmatic safety net.
    team_override_active = state.get("_capacity_team_override", 0) > 0
    should_merge = (enforce_target or team_override_active) and target_sprints > 0
    if should_merge and len(sprints) > target_sprints:
        sprints = _merge_sprints_to_target(sprints, target_sprints, stories, starting_sprint_number)

    # Format the sprints for display
    display = _format_sprints(sprints, stories, features, analysis.project_name, velocity)

    result: dict = {
        "sprints": sprints,
        "messages": [AIMessage(content=display)],
        "pending_review": "sprint_planner",
    }

    # When team size was overridden, persist the updated velocity and team size
    # back to state so TUI renderers and exports see the correct values.
    if state.get("_capacity_team_override", 0) > 0:
        result["velocity_per_sprint"] = velocity
        result["net_velocity_per_sprint"] = velocity
        result["team_size"] = team_size
        if sprint_caps:
            result["sprint_capacities"] = sprint_caps

    return result
