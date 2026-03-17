"""Tests for the accordion-style intake question screen."""

from rich.panel import Panel

from scrum_agent.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from scrum_agent.ui.session.screens._accordion import (
    _build_accordion_question_screen,
    _compute_accordion_viewport,
    _compute_item_heights,
    _render_active_item,
    _render_completed_item,
    _render_future_item,
    _render_skipped_item,
)


def _make_qs(**overrides) -> QuestionnaireState:
    """Create a QuestionnaireState with sensible defaults for testing."""
    defaults = {
        "current_question": 5,
        "answers": {1: "E-commerce app", 2: "Greenfield", 3: "Sell stuff", 4: "MVP launch"},
        "skipped_questions": set(),
        "extracted_questions": set(),
        "suggested_answers": {},
        "probed_questions": set(),
        "defaulted_questions": set(),
        "completed": False,
        "awaiting_confirmation": False,
        "intake_mode": "standard",
    }
    defaults.update(overrides)
    return QuestionnaireState(**defaults)


# ---------------------------------------------------------------------------
# Item renderers
# ---------------------------------------------------------------------------


class TestCompletedItem:
    def test_green_tick(self):
        lines = _render_completed_item(1)
        assert len(lines) == 1
        text = lines[0].plain
        assert "\u2713" in text  # tick mark
        assert "1." in text
        assert "Project description" in text

    def test_different_question(self):
        lines = _render_completed_item(8)
        text = lines[0].plain
        assert "8." in text
        assert "Sprint length" in text


class TestSkippedItem:
    def test_dash(self):
        lines = _render_skipped_item(5)
        assert len(lines) == 1
        text = lines[0].plain
        assert "\u2013" in text  # en-dash
        assert "5." in text
        assert "Deadlines" in text


class TestFutureItem:
    def test_dim_label(self):
        lines = _render_future_item(10)
        assert len(lines) == 1
        text = lines[0].plain
        assert "10." in text
        assert "Target sprints" in text


class TestActiveItem:
    def test_text_input(self):
        lines = _render_active_item(
            5,
            "Are there any hard deadlines?",
            "",
            box_w=60,
        )
        # Should have title, description, blank line, input box
        assert len(lines) >= 4
        # First line should have question number and triangle marker
        first = lines[0].plain if hasattr(lines[0], "plain") else str(lines[0])
        assert "5." in first
        assert "deadlines" in first.lower()

    def test_with_suggestion(self):
        lines = _render_active_item(
            5,
            "Are there any hard deadlines?",
            "",
            suggestion="No hard deadlines",
            box_w=60,
        )
        # Should have title, description, blank, and input box
        assert len(lines) >= 4

    def test_with_choices(self):
        choices = [("Greenfield", False), ("Existing codebase", True)]
        lines = _render_active_item(
            2,
            "Is this a greenfield project?",
            "",
            choices=choices,
            selected_choice=0,
            box_w=60,
        )
        text = "\n".join(getattr(item, "plain", str(item)) for item in lines)
        assert "Greenfield" in text
        assert "Existing codebase" in text
        assert "(default)" in text

    def test_with_input_value(self):
        lines = _render_active_item(
            6,
            "How many engineers?",
            "4 developers",
            box_w=60,
        )
        # The input box is wrapped by _pad_left (returns a Padding), so check
        # that at least one item is not a plain Text (i.e. the input box exists).
        from rich.text import Text

        assert any(not isinstance(item, Text) for item in lines)


# ---------------------------------------------------------------------------
# Viewport calculation
# ---------------------------------------------------------------------------


class TestComputeItemHeights:
    def test_collapsed_heights(self):
        qs = _make_qs(current_question=5)
        heights = _compute_item_heights(qs, 5, "Test question?", None, None, 60)
        # Completed questions are 1 line
        assert heights[1] == 1
        assert heights[2] == 1
        # Future questions are 1 line
        assert heights[10] == 1
        assert heights[26] == 1
        # Active question is taller
        assert heights[5] > 1

    def test_choice_question_height(self):
        qs = _make_qs(current_question=2, answers={1: "E-commerce"})
        choices = [("Greenfield", False), ("Existing", True), ("Hybrid", False)]
        heights = _compute_item_heights(qs, 2, "Project type?", choices, None, 60)
        # Active choice question: title(1) + desc(1) + blank(1) + 3 choices = 6
        assert heights[2] >= 6

    def test_all_26_questions_present(self):
        qs = _make_qs()
        heights = _compute_item_heights(qs, 5, "Question?", None, None, 60)
        assert len(heights) == TOTAL_QUESTIONS
        # Hidden questions (e.g. Q15) have height 0; all others >= 1
        assert all(h >= 0 for h in heights.values())
        assert heights[15] == 0


class TestComputeViewport:
    def test_all_fit(self):
        """When total height fits, show everything."""
        heights = {q: 1 for q in range(1, TOTAL_QUESTIONS + 1)}
        heights[5] = 7  # active question
        # TOTAL_QUESTIONS + 6 extra lines total, viewport = 50
        first, last = _compute_accordion_viewport(5, heights, 50)
        assert first == 1
        assert last == TOTAL_QUESTIONS

    def test_scrolls_to_center_active(self):
        """Active question should be roughly centered."""
        heights = {q: 1 for q in range(1, TOTAL_QUESTIONS + 1)}
        heights[13] = 7  # active = Q13, middle of the list
        first, last = _compute_accordion_viewport(13, heights, 15)
        # Q13 should be visible
        assert first <= 13 <= last
        # Should not show everything (15 lines viewport, 36 total lines)
        assert first > 1 or last < TOTAL_QUESTIONS

    def test_first_question_active(self):
        heights = {q: 1 for q in range(1, TOTAL_QUESTIONS + 1)}
        heights[1] = 7
        first, last = _compute_accordion_viewport(1, heights, 15)
        assert first == 1
        assert 1 <= last <= TOTAL_QUESTIONS

    def test_last_question_active(self):
        heights = {q: 1 for q in range(1, TOTAL_QUESTIONS + 1)}
        heights[TOTAL_QUESTIONS] = 7
        first, last = _compute_accordion_viewport(TOTAL_QUESTIONS, heights, 15)
        assert last == TOTAL_QUESTIONS
        assert 1 <= first <= TOTAL_QUESTIONS

    def test_tiny_viewport(self):
        """Even with very small viewport, active question is included."""
        heights = {q: 1 for q in range(1, TOTAL_QUESTIONS + 1)}
        heights[10] = 7
        first, last = _compute_accordion_viewport(10, heights, 7)
        assert first <= 10 <= last


# ---------------------------------------------------------------------------
# Full screen builder
# ---------------------------------------------------------------------------


class TestBuildAccordionScreen:
    def test_returns_panel(self):
        qs = _make_qs()
        result = _build_accordion_question_screen(
            "Are there any hard deadlines?",
            "",
            qs,
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_with_choices(self):
        qs = _make_qs(current_question=2, answers={1: "E-commerce"})
        choices = [("Greenfield", False), ("Existing codebase", True)]
        result = _build_accordion_question_screen(
            "Is this a greenfield project?",
            "",
            qs,
            choices=choices,
            selected_choice=0,
            width=80,
            height=30,
        )
        assert isinstance(result, Panel)

    def test_smart_mode_with_extracted(self):
        """Extracted questions should appear as completed (green tick)."""
        qs = _make_qs(
            current_question=6,
            answers={1: "App", 2: "Greenfield", 3: "Users", 4: "MVP", 5: "Q3"},
            extracted_questions={1, 3, 4},
            intake_mode="smart",
        )
        result = _build_accordion_question_screen(
            "How many engineers?",
            "",
            qs,
            width=80,
            height=30,
        )
        assert isinstance(result, Panel)

    def test_with_skipped_questions(self):
        qs = _make_qs(
            current_question=8,
            answers={1: "App", 2: "Greenfield", 3: "Users", 4: "MVP", 6: "4"},
            skipped_questions={5, 7},
        )
        result = _build_accordion_question_screen(
            "How long are your sprints?",
            "",
            qs,
            width=80,
            height=30,
        )
        assert isinstance(result, Panel)

    def test_progress_and_phase_in_subtitle(self):
        qs = _make_qs()
        result = _build_accordion_question_screen(
            "Test question?",
            "",
            qs,
            progress="Q5 of 26",
            phase_label="Phase 2: Team & Capacity",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_border_override(self):
        qs = _make_qs()
        result = _build_accordion_question_screen(
            "Test?",
            "answer",
            qs,
            border_override="rgb(80,220,120)",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_small_terminal(self):
        """Should not crash on a very small terminal."""
        qs = _make_qs()
        result = _build_accordion_question_screen(
            "Test?",
            "",
            qs,
            width=40,
            height=12,
        )
        assert isinstance(result, Panel)
