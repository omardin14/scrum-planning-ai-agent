"""UI helpers — spinner messages, toolbar, streaming, phase header."""

import time
from collections.abc import Iterator

from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from scrum_agent.agent.state import QuestionnaireState

# See README: "Architecture" — each node maps to a user-facing status message.
_SPINNER_MESSAGES: dict[str, str] = {
    "project_intake": "Processing your answer",
    "project_analyzer": "Analysing project",
    "feature_skip": "Generating features",
    "feature_generator": "Generating features",
    "story_writer": "Writing user stories",
    "task_decomposer": "Breaking down tasks",
    "sprint_planner": "Planning sprints",
    "agent": "Thinking",
}

# Pipeline step order — used for [1/5] progress display.
# Only nodes AFTER the questionnaire are counted as pipeline steps.
_PIPELINE_STEPS: tuple[str, ...] = (
    "project_analyzer",
    "epic_review",
    "feature_generator",
    "story_writer",
    "task_decomposer",
    "sprint_planner",
)


def _predict_next_node(state: dict) -> str:
    """Predict which graph node will run next, mirroring route_entry() logic.

    Used to pick the right spinner message before graph.invoke().
    We duplicate the routing checks here (rather than importing route_entry)
    because route_entry expects a ScrumState TypedDict while the REPL works
    with a plain dict. The logic is intentionally kept in sync.
    """
    qs = state.get("questionnaire")
    if qs is None or not qs.completed:
        return "project_intake"
    if state.get("project_analysis") is None:
        return "project_analyzer"
    if not state.get("features"):
        analysis = state.get("project_analysis")
        if analysis and getattr(analysis, "skip_features", False):
            return "feature_skip"
        return "feature_generator"
    if not state.get("stories"):
        return "story_writer"
    if not state.get("tasks"):
        return "task_decomposer"
    if not state.get("sprints"):
        return "sprint_planner"
    return "agent"


def _build_spinner_message(node_name: str) -> str:
    """Build the status message with optional [N/5] prefix for pipeline steps."""
    base = _SPINNER_MESSAGES.get(node_name, "Working")
    # feature_skip occupies the same pipeline slot as feature_generator (step 2/5).
    step_node = "feature_generator" if node_name == "feature_skip" else node_name
    if step_node in _PIPELINE_STEPS:
        step = _PIPELINE_STEPS.index(step_node) + 1
        total = len(_PIPELINE_STEPS)
        return f"[{step}/{total}] {base}"
    return base


def _build_toolbar(graph_state: dict) -> HTML:
    """Build an HTML status bar for prompt_toolkit's bottom_toolbar.

    # See README: "Architecture" — REPL-side UI layer
    #
    # prompt_toolkit's bottom_toolbar uses HTML() objects, not Rich markup.
    # The closure over graph_state re-reads the mutable dict on each prompt
    # repaint, so reassignment at the end of the loop is seen by the lambda.
    # Only visible during the prompt — disappears during spinner/output.

    Args:
        graph_state: The current mutable graph state dict.

    Returns:
        An HTML object for the prompt_toolkit bottom toolbar.
    """
    parts: list[str] = []

    # Project name (if analysis produced one)
    analysis = graph_state.get("project_analysis")
    if analysis and hasattr(analysis, "project_name"):
        parts.append(f"<b>{analysis.project_name}</b>")

    # Phase / progress
    qs = graph_state.get("questionnaire")
    if isinstance(qs, QuestionnaireState) and not qs.completed:
        if qs.awaiting_confirmation:
            parts.append("Intake: awaiting confirmation")
        else:
            pct = int(qs.progress * 100)
            parts.append(f"Intake: {pct}%")
    elif isinstance(qs, QuestionnaireState) and qs.completed:
        # Show pipeline step
        node = _predict_next_node(graph_state)
        toolbar_node = "feature_generator" if node == "feature_skip" else node
        if toolbar_node in _PIPELINE_STEPS:
            step = _PIPELINE_STEPS.index(toolbar_node) + 1
            total = len(_PIPELINE_STEPS)
            label = _SPINNER_MESSAGES.get(node, node)
            parts.append(f"Pipeline [{step}/{total}] {label}")
        elif node == "agent":
            parts.append("Pipeline complete")

    if not parts:
        parts.append("Scrum AI Agent")

    return HTML(" │ ".join(parts))


def print_phase_header(console: Console, title: str, style: str = "blue") -> None:
    """Print a styled phase header / section divider.

    Args:
        console: Rich Console instance for output.
        title: The section title (e.g. "Phase 1: Project Context").
        style: Rich colour/style for the rule. Defaults to "blue".
    """
    console.print()
    console.rule(f"[bold]{title}[/bold]", style=style)
    console.print()


def stream_response(console: Console, tokens: Iterator[str]) -> str:
    """Stream tokens to the console with live markdown rendering.

    Args:
        console: Rich Console instance for output.
        tokens: Iterator yielding string chunks.

    Returns:
        The complete accumulated response text.
    """
    accumulated = ""
    with Live(console=console, vertical_overflow="visible", refresh_per_second=15) as live:
        for token in tokens:
            accumulated += token
            live.update(Markdown(accumulated))
    return accumulated


def _simulate_stream(text: str) -> Iterator[str]:
    """Simulate token streaming by yielding words with a small delay.

    Preserves newlines so that markdown paragraph breaks (\\n\\n) survive
    through to the Rich Markdown renderer. Without this, multi-paragraph
    messages collapse into a single wall of text.
    """
    import re

    # Split into tokens that are either a word or a newline character.
    # re.findall keeps the order and captures both kinds of token.
    tokens = re.findall(r"\S+|\n", text)
    prev_was_newline = True  # suppress leading space on first token
    for token in tokens:
        if token == "\n":
            yield "\n"
            prev_was_newline = True
        else:
            prefix = "" if prev_was_newline else " "
            yield prefix + token
            prev_was_newline = False
            time.sleep(0.03)
