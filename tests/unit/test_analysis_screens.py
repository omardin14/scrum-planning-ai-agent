"""Unit tests for analysis mode screen builders and preview flow.

Covers:
- _build_analysis_review_screen (shared template: progress dots, scrollbar, viewport)
- _build_instructions_review_screen
- _build_sample_epic_screen
- _build_sample_stories_screen
- _build_sample_tasks_screen
- _build_sample_sprint_screen
- _build_analysis_progress_screen
- _build_team_analysis_screen (scrollbar + viewport wrapping)

Mirrors the test patterns used for planning mode screens in tests/test_session.py
(TestBuildDescriptionScreen, TestBuildQuestionScreen, etc.).
"""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from scrum_agent.ui.mode_select.screens._screens_secondary import (
    _build_analysis_progress_screen,
    _build_analysis_review_screen,
    _build_instructions_review_screen,
    _build_sample_epic_screen,
    _build_sample_sprint_screen,
    _build_sample_stories_screen,
    _build_sample_tasks_screen,
    _build_team_analysis_screen,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(panel: Panel, width: int = 100) -> str:
    """Render a Rich Panel to plain text for content assertions."""
    buf = StringIO()
    console = Console(file=buf, width=width, force_terminal=False, highlight=False)
    console.print(panel)
    return buf.getvalue()


def _make_body_lines(n: int = 10, prefix: str = "Line") -> list:
    """Create a list of Text objects for use as body_lines."""
    return [Text(f"    {prefix} {i}", justify="left") for i in range(n)]


# ---------------------------------------------------------------------------
# Sample fixture data
# ---------------------------------------------------------------------------

_SAMPLE_INSTRUCTIONS = """\
## Velocity & Capacity
- Team velocity — 23.5 pts/sprint average
- Sprint length — 2 weeks
- Team size — 4 developers

## Story Conventions
- Story points — use Fibonacci scale (1, 2, 3, 5, 8)
- Acceptance criteria — Given/When/Then format, median 3 per story
→ Match this team's style exactly.

## Naming Conventions
- Label convention: quarterly goal (42%), released to dev (6%)
- Epic naming: quarter-scoped (e.g. "Q4|2025|High Region Outage DR")
→ Generated tickets MUST match these naming conventions.

Estimation note: Use THESE team-specific patterns, not generic Fibonacci rules.
"""

_SAMPLE_EPIC = {
    "title": "Q1|2026|Medium Platform Resilience Upgrade",
    "description": "Improve platform resilience with automated failover and monitoring.",
    "priority": "high",
    "stories_estimate": 5,
    "points_estimate": 18,
    "rationale": "Matches team's quarter-scoped naming convention.",
}

_SAMPLE_STORIES = [
    {
        "id": "S1",
        "title": "Implement automated failover",
        "persona": "developer",
        "goal": "automated failover between regions",
        "benefit": "reduced downtime",
        "story_points": 5,
        "priority": "high",
        "discipline": "infrastructure",
        "acceptance_criteria": [
            {"given": "primary region fails", "when": "failover triggered", "then": "traffic routes to secondary"},
            {"given": "secondary region active", "when": "primary recovers", "then": "failback completes"},
        ],
        "rationale": "Matches team's infrastructure story patterns.",
    },
    {
        "id": "S2",
        "title": "Add monitoring dashboards",
        "persona": "SRE",
        "goal": "visibility into failover health",
        "benefit": "faster incident response",
        "story_points": 3,
        "priority": "medium",
        "discipline": "observability",
        "acceptance_criteria": [
            {"given": "failover runs", "when": "dashboard queried", "then": "shows status within 30s"},
        ],
        "rationale": "Matches observability story pattern.",
    },
]

_SAMPLE_TASKS = [
    {
        "id": "T-S1-01",
        "story_id": "S1",
        "title": "Implement health check endpoint",
        "description": "Add /health endpoint to secondary region.",
        "label": "Code",
        "test_plan": "Unit test: verify endpoint returns 200.",
    },
    {
        "id": "T-S1-02",
        "story_id": "S1",
        "title": "Write failover integration tests",
        "description": "End-to-end test for failover trigger.",
        "label": "Testing",
        "test_plan": "Integration test: simulate region failure.",
    },
    {
        "id": "T-S2-01",
        "story_id": "S2",
        "title": "Create Grafana dashboard",
        "description": "Build dashboard with failover metrics.",
        "label": "Infrastructure",
        "test_plan": "Manual: verify dashboard loads in staging.",
    },
]

_SAMPLE_SPRINT = {
    "sprint_name": "Sprint 1",
    "velocity_target": 20,
    "stories_included": ["S1", "S2"],
    "total_points": 8,
    "capacity_notes": "Based on team avg of 23.5 pts/sprint, 8 pts leaves buffer.",
    "risks": ["S1 depends on cloud provider API", "S2 blocked until S1 failover endpoint is live"],
    "rationale": "Conservative allocation matching team's 88% completion rate.",
}

_SAMPLE_EXAMPLES = {
    "naming_conventions": {
        "epic_naming_style": "quarter-scoped",
        "epic_examples": ["Q4|2025|High Region Outage DR", "Q1|2026|Low Overmind improvement"],
        "template_sections": [("What is this about?", 0.8), ("Why does it matter?", 0.6)],
    },
    "ac_patterns": {"median_ac": 3},
    "task_decomposition": {
        "avg_tasks_per_story": 4.8,
        "type_distribution": {"Development": 64, "Testing": 13, "Deploy": 12},
        "common_tasks": [("create aurora rollback module", 2), ("update engine version", 2)],
    },
    "scope_changes": {
        "totals": {"avg_delivered_velocity": 25.9, "avg_committed_velocity": 19.1},
    },
}


# ---------------------------------------------------------------------------
# Shared builder: _build_analysis_review_screen
# ---------------------------------------------------------------------------


class TestBuildAnalysisReviewScreen:
    """Test the shared analysis review screen template."""

    def test_returns_panel(self):
        result = _build_analysis_review_screen(_make_body_lines(), stage_index=0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_body(self):
        result = _build_analysis_review_screen([], stage_index=0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_all_stage_indices(self):
        """Each stage index (0-4) should render a valid panel."""
        lines = _make_body_lines(5)
        for idx in range(5):
            result = _build_analysis_review_screen(lines, stage_index=idx, width=80, height=24)
            assert isinstance(result, Panel)

    def test_custom_actions(self):
        result = _build_analysis_review_screen(
            _make_body_lines(),
            actions=["Done", "Export"],
            action_sel=1,
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_subtitle_rendered(self):
        result = _build_analysis_review_screen(
            _make_body_lines(),
            subtitle="Review planning instructions",
            width=80,
            height=24,
        )
        output = _render(result)
        assert "Review planning instructions" in output

    def test_progress_dots_current_stage(self):
        """Progress dots should show the current stage name in bold."""
        result = _build_analysis_review_screen(_make_body_lines(), stage_index=2, width=80, height=24)
        output = _render(result)
        assert "Stories" in output

    def test_scrollbar_appears_when_content_overflows(self):
        """Scrollbar should render when content exceeds viewport."""
        long_body = _make_body_lines(100)
        result = _build_analysis_review_screen(long_body, stage_index=0, width=80, height=24)
        output = _render(result)
        # Scrollbar uses thin/thick vertical bars
        assert "\u2502" in output or "\u2503" in output

    def test_no_scrollbar_short_content(self):
        """No scrollbar for content that fits within viewport."""
        short_body = _make_body_lines(3)
        result = _build_analysis_review_screen(short_body, stage_index=0, width=80, height=30)
        assert isinstance(result, Panel)

    def test_scroll_offset_clamps(self):
        """Scroll offset beyond max should clamp without error."""
        lines = _make_body_lines(10)
        result = _build_analysis_review_screen(lines, scroll_offset=9999, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection_highlights(self):
        """Each action index should produce a valid panel."""
        lines = _make_body_lines(5)
        for sel in range(4):
            result = _build_analysis_review_screen(lines, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_wrapping_lines_dont_overflow(self):
        """Long lines that wrap should not push buttons off-screen."""
        long_lines = [Text("    " + "x" * 200, justify="left") for _ in range(20)]
        result = _build_analysis_review_screen(long_lines, scroll_offset=15, width=80, height=24)
        output = _render(result, width=80)
        # Buttons should always be present in the rendered output
        assert "\u256d" in output  # top border of button box
        assert "\u256f" in output  # bottom border of button box

    def test_narrow_width(self):
        result = _build_analysis_review_screen(_make_body_lines(), width=40, height=24)
        assert isinstance(result, Panel)

    def test_tall_height(self):
        result = _build_analysis_review_screen(_make_body_lines(5), width=80, height=60)
        assert isinstance(result, Panel)

    def test_minimum_height(self):
        """Very short terminal should still render."""
        result = _build_analysis_review_screen(_make_body_lines(3), width=80, height=12)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Instructions review screen
# ---------------------------------------------------------------------------


class TestBuildInstructionsReviewScreen:
    """Test the planning instructions review page (stage 1 of preview flow)."""

    def test_returns_panel(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_instructions(self):
        result = _build_instructions_review_screen("", width=80, height=24)
        assert isinstance(result, Panel)

    def test_section_headers_rendered(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=100, height=40)
        output = _render(result, width=100)
        assert "Velocity & Capacity" in output
        assert "Story Conventions" in output
        assert "Naming Conventions" in output

    def test_numbered_items(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=100, height=40)
        output = _render(result, width=100)
        # Items should be numbered
        assert "1" in output
        assert "Team velocity" in output

    def test_arrow_directives_rendered(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=120, height=40)
        output = _render(result, width=120)
        assert "Match this team" in output or "naming conventions" in output

    def test_scrollable(self):
        result1 = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, scroll_offset=0, width=80, height=24)
        result2 = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, scroll_offset=5, width=80, height=24)
        assert isinstance(result1, Panel)
        assert isinstance(result2, Panel)

    def test_action_buttons(self):
        """Instructions page has Accept/Edit/Export buttons."""
        # Use tall height to ensure buttons are visible (not clipped by viewport)
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=100, height=60)
        output = _render(result, width=100)
        assert "Accept" in output
        assert "Edit" in output
        assert "Export" in output

    def test_action_selection(self):
        for sel in range(3):
            result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_instructions(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=100, height=24)
        output = _render(result, width=100)
        assert "Instructions" in output

    def test_long_instructions_scrollbar(self):
        """Long instructions should show scrollbar."""
        long_text = "\n".join(f"- Item {i} — description of item {i}" for i in range(50))
        result = _build_instructions_review_screen(long_text, width=80, height=24)
        output = _render(result, width=80)
        assert "\u2502" in output or "\u2503" in output


# ---------------------------------------------------------------------------
# Sample epic screen
# ---------------------------------------------------------------------------


class TestBuildSampleEpicScreen:
    """Test the sample epic review page (stage 2 of preview flow)."""

    def test_returns_panel(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_epic(self):
        result = _build_sample_epic_screen({}, width=80, height=24)
        assert isinstance(result, Panel)

    def test_epic_title_rendered(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=120, height=40)
        output = _render(result, width=120)
        assert "Platform Resilience" in output

    def test_epic_priority_rendered(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=100, height=40)
        output = _render(result, width=100)
        assert "high" in output.lower()

    def test_epic_rationale_rendered(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=120, height=40)
        output = _render(result, width=120)
        assert "quarter-scoped" in output or "rationale" in output.lower()

    def test_with_examples(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, examples=_SAMPLE_EXAMPLES, width=80, height=24)
        assert isinstance(result, Panel)

    def test_examples_pattern_info(self):
        """When examples provided, should show naming style info."""
        result = _build_sample_epic_screen(_SAMPLE_EPIC, examples=_SAMPLE_EXAMPLES, width=120, height=40)
        output = _render(result, width=120)
        assert "quarter-scoped" in output or "naming" in output.lower()

    def test_scrollable(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, scroll_offset=3, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection(self):
        for sel in range(4):
            result = _build_sample_epic_screen(_SAMPLE_EPIC, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_epic(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=100, height=24)
        output = _render(result, width=100)
        assert "Epic" in output


# ---------------------------------------------------------------------------
# Sample stories screen
# ---------------------------------------------------------------------------


class TestBuildSampleStoriesScreen:
    """Test the sample stories review page (stage 3 of preview flow)."""

    def test_returns_panel(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_stories(self):
        result = _build_sample_stories_screen([], width=80, height=24)
        assert isinstance(result, Panel)

    def test_single_story(self):
        result = _build_sample_stories_screen([_SAMPLE_STORIES[0]], width=80, height=24)
        assert isinstance(result, Panel)

    def test_story_ids_rendered(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "S1" in output
        assert "S2" in output

    def test_story_points_rendered(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "5" in output  # S1 points
        assert "3" in output  # S2 points

    def test_acceptance_criteria_rendered(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=120, height=50)
        output = _render(result, width=120)
        # Given/When/Then format
        assert "Given" in output or "given" in output.lower()

    def test_persona_goal_rendered(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=120, height=40)
        output = _render(result, width=120)
        assert "developer" in output or "SRE" in output

    def test_with_epic_title(self):
        result = _build_sample_stories_screen(
            _SAMPLE_STORIES,
            epic_title="Q1|2026|Medium Platform Resilience",
            width=100,
            height=24,
        )
        output = _render(result, width=100)
        assert "Platform Resilience" in output or "Stories" in output

    def test_scrollable(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, scroll_offset=5, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection(self):
        for sel in range(4):
            result = _build_sample_stories_screen(_SAMPLE_STORIES, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_stories(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=100, height=24)
        output = _render(result, width=100)
        assert "Stories" in output

    def test_story_without_acceptance_criteria(self):
        """Stories with empty AC list should still render."""
        story = {**_SAMPLE_STORIES[0], "acceptance_criteria": []}
        result = _build_sample_stories_screen([story], width=80, height=24)
        assert isinstance(result, Panel)

    def test_story_with_missing_fields(self):
        """Minimal story dict should not crash."""
        story = {"id": "S1", "title": "Minimal story"}
        result = _build_sample_stories_screen([story], width=80, height=24)
        assert isinstance(result, Panel)

    def test_definition_of_done_rendered(self):
        """Stories with definition_of_done should render DoD items."""
        story = {
            **_SAMPLE_STORIES[0],
            "definition_of_done": ["Code reviewed", "Tests passing", "Deployed to staging"],
        }
        result = _build_sample_stories_screen([story], width=120, height=60)
        output = _render(result, width=120)
        assert "Definition of Done" in output
        assert "Code reviewed" in output

    def test_story_without_dod(self):
        """Stories without definition_of_done should still render."""
        story = {k: v for k, v in _SAMPLE_STORIES[0].items() if k != "definition_of_done"}
        result = _build_sample_stories_screen([story], width=80, height=24)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Sample tasks screen
# ---------------------------------------------------------------------------


class TestBuildSampleTasksScreen:
    """Test the sample tasks review page (stage 4 of preview flow)."""

    def test_returns_panel(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_tasks(self):
        result = _build_sample_tasks_screen([], width=80, height=24)
        assert isinstance(result, Panel)

    def test_task_ids_rendered(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=100, height=40)
        output = _render(result, width=100)
        assert "T-S1-01" in output
        assert "T-S2-01" in output

    def test_task_labels_rendered(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=100, height=40)
        output = _render(result, width=100)
        assert "Code" in output
        assert "Testing" in output

    def test_task_grouping_by_story(self):
        """Tasks should be grouped under their story ID."""
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=100, height=40)
        output = _render(result, width=100)
        # Both story IDs should appear as group headers
        assert "S1" in output
        assert "S2" in output

    def test_test_plan_rendered(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=120, height=40)
        output = _render(result, width=120)
        assert "Unit test" in output or "test" in output.lower()

    def test_scrollable(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, scroll_offset=3, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection(self):
        for sel in range(4):
            result = _build_sample_tasks_screen(_SAMPLE_TASKS, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_tasks(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=100, height=24)
        output = _render(result, width=100)
        assert "Tasks" in output

    def test_single_task(self):
        result = _build_sample_tasks_screen([_SAMPLE_TASKS[0]], width=80, height=24)
        assert isinstance(result, Panel)

    def test_task_with_missing_fields(self):
        """Minimal task dict should not crash."""
        task = {"id": "T-1", "title": "Do something"}
        result = _build_sample_tasks_screen([task], width=80, height=24)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Sample sprint screen
# ---------------------------------------------------------------------------


class TestBuildSampleSprintScreen:
    """Test the sample sprint plan review page (stage 5 of preview flow)."""

    def test_returns_panel(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_sprint(self):
        result = _build_sample_sprint_screen({}, [], width=80, height=24)
        assert isinstance(result, Panel)

    def test_sprint_name_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "Sprint 1" in output

    def test_velocity_target_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "20" in output

    def test_total_points_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "8" in output

    def test_stories_listed(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "S1" in output
        assert "S2" in output

    def test_risks_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=120, height=40)
        output = _render(result, width=120)
        assert "cloud provider" in output or "risk" in output.lower()

    def test_capacity_notes_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=120, height=40)
        output = _render(result, width=120)
        assert "23.5" in output or "buffer" in output

    def test_done_button(self):
        """Sprint page should have Done button (not Accept)."""
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=60)
        output = _render(result, width=100)
        assert "Done" in output

    def test_scrollable(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, scroll_offset=3, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection(self):
        for sel in range(3):  # Done, Regenerate, Export
            result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_sprint(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=24)
        output = _render(result, width=100)
        assert "Sprint" in output

    def test_sprint_without_risks(self):
        sprint = {**_SAMPLE_SPRINT, "risks": []}
        result = _build_sample_sprint_screen(sprint, _SAMPLE_STORIES, width=80, height=24)
        assert isinstance(result, Panel)

    def test_sprint_without_stories(self):
        """Sprint with story IDs but no matching story objects."""
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, [], width=80, height=24)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Analysis progress screen
# ---------------------------------------------------------------------------


class TestBuildAnalysisProgressScreen:
    """Test the loading/progress screen shown during analysis."""

    def test_returns_panel(self):
        result = _build_analysis_progress_screen([], width=80, height=24)
        assert isinstance(result, Panel)

    def test_with_progress_steps(self):
        steps = ["Fetching sprints...", "Analysing velocity...", "Building profile..."]
        result = _build_analysis_progress_screen(steps, width=80, height=24)
        output = _render(result)
        assert "Fetching sprints" in output

    def test_elapsed_time(self):
        result = _build_analysis_progress_screen(["Working..."], elapsed=12.5, width=80, height=24)
        output = _render(result)
        assert "12" in output  # elapsed seconds shown

    def test_animation_tick(self):
        """Different animation ticks should produce valid panels."""
        for tick in [0.0, 0.25, 0.5, 0.75, 1.0]:
            result = _build_analysis_progress_screen(["Working..."], anim_tick=tick, width=80, height=24)
            assert isinstance(result, Panel)

    def test_analysis_mode_renders(self):
        """Analysis mode should render without error and use analysis title."""
        result = _build_analysis_progress_screen(["Working..."], mode="analysis", width=100, height=24)
        assert isinstance(result, Panel)

    def test_planning_mode_renders(self):
        """Planning mode should render without error and use planning title."""
        result = _build_analysis_progress_screen(["Working..."], mode="planning", width=100, height=24)
        assert isinstance(result, Panel)

    def test_source_label(self):
        result = _build_analysis_progress_screen(
            ["Fetching..."], source="azdevops", mode="analysis", width=100, height=24
        )
        assert isinstance(result, Panel)

    def test_empty_progress(self):
        result = _build_analysis_progress_screen([], elapsed=0.0, width=80, height=24)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Team analysis screen (initial report page with custom viewport)
# ---------------------------------------------------------------------------


class TestBuildTeamAnalysisScreenExtended:
    """Extended tests for the initial analysis report screen.

    Supplements the 2 existing tests in test_team_profile.py with coverage
    for scrollbar, viewport wrapping, export selection, and edge cases.
    """

    @pytest.fixture()
    def profile(self):
        from scrum_agent.team_profile import (
            DoDSignal,
            EpicPattern,
            SpilloverStats,
            StoryPointCalibration,
            StoryShapePattern,
            TeamProfile,
            WritingPatterns,
        )

        return TeamProfile(
            team_id="azdevops-PROJ",
            source="azdevops",
            project_key="PROJ",
            sample_sprints=8,
            sample_stories=64,
            velocity_avg=23.5,
            velocity_stddev=3.2,
            point_calibrations=(
                StoryPointCalibration(point_value=1, avg_cycle_time_days=0.5, sample_count=10),
                StoryPointCalibration(point_value=3, avg_cycle_time_days=2.1, sample_count=20, overshoot_pct=15.0),
                StoryPointCalibration(point_value=5, avg_cycle_time_days=4.2, sample_count=15, overshoot_pct=20.0),
            ),
            story_shapes=(
                StoryShapePattern(
                    discipline="backend", avg_points=3.2, avg_ac_count=3.0, avg_task_count=2.8, sample_count=20
                ),
                StoryShapePattern(
                    discipline="frontend", avg_points=2.5, avg_ac_count=2.5, avg_task_count=2.0, sample_count=12
                ),
            ),
            epic_pattern=EpicPattern(
                avg_stories_per_epic=6.0, avg_points_per_epic=18.0, typical_story_count_range=(4, 9)
            ),
            estimation_accuracy_pct=78.0,
            sprint_completion_rate=88.0,
            spillover=SpilloverStats(
                carried_over_pct=12.5,
                avg_spillover_pts=3.2,
                most_common_spillover_reason="backend stories",
            ),
            dod_signal=DoDSignal(
                common_checklist_items=("tests passing", "PR merged", "code reviewed"),
                stories_with_comments_pct=85.0,
                stories_with_pr_link_pct=82.0,
                stories_with_review_mention_pct=76.0,
                stories_with_testing_mention_pct=61.0,
                stories_with_deploy_mention_pct=44.0,
            ),
            writing_patterns=WritingPatterns(
                median_ac_count=3.0,
                median_task_count_per_story=2.5,
                subtask_label_distribution=(("Code", 0.58), ("Testing", 0.28)),
                common_subtask_patterns=("Write unit tests", "Deploy to staging"),
                subtasks_use_consistent_naming=True,
                common_personas=("developer", "admin"),
                uses_given_when_then=True,
                stories_with_subtasks_pct=72.0,
            ),
            sprints_fully_completed=6,
            sprints_partially_completed=2,
            sprints_analysed=8,
        )

    def test_returns_panel(self, profile):
        result = _build_team_analysis_screen(profile, width=80, height=30)
        assert isinstance(result, Panel)

    def test_export_button_selection(self, profile):
        """Each export_sel value should highlight a different button."""
        for sel in range(2):  # Export, Continue
            result = _build_team_analysis_screen(profile, export_sel=sel, width=80, height=30)
            assert isinstance(result, Panel)

    def test_scrollbar_on_tall_content(self, profile):
        """Profile with many sections should show scrollbar."""
        result = _build_team_analysis_screen(profile, scroll_offset=0, width=80, height=24)
        output = _render(result, width=80)
        assert "\u2502" in output or "\u2503" in output

    def test_scroll_to_bottom(self, profile):
        """Scrolling to a large offset should clamp and still show buttons."""
        result = _build_team_analysis_screen(profile, scroll_offset=9999, width=80, height=24)
        output = _render(result, width=80)
        # Buttons should still be visible
        assert "Export" in output or "Continue" in output

    def test_with_examples(self, profile):
        result = _build_team_analysis_screen(profile, examples=_SAMPLE_EXAMPLES, width=80, height=30)
        assert isinstance(result, Panel)

    def test_with_sprint_names(self, profile):
        names = ["Sprint 101", "Sprint 102", "Sprint 103"]
        result = _build_team_analysis_screen(profile, sprint_names=names, width=80, height=30)
        assert isinstance(result, Panel)

    def test_with_team_name(self, profile):
        result = _build_team_analysis_screen(profile, team_name="Platform Team", width=100, height=30)
        output = _render(result, width=100)
        assert "Platform Team" in output

    def test_narrow_terminal(self, profile):
        """Should render without crash on narrow terminals."""
        result = _build_team_analysis_screen(profile, width=40, height=24)
        assert isinstance(result, Panel)

    def test_short_terminal(self, profile):
        """Should render on short terminals without crash."""
        result = _build_team_analysis_screen(profile, width=80, height=14)
        assert isinstance(result, Panel)

    def test_velocity_section_rendered(self, profile):
        result = _build_team_analysis_screen(profile, width=100, height=50)
        output = _render(result, width=100)
        assert "23.5" in output  # velocity_avg

    def test_spillover_rendered(self, profile):
        result = _build_team_analysis_screen(profile, width=100, height=100)
        output = _render(result, width=100)
        assert "12.5" in output or "spillover" in output.lower()

    def test_zero_scroll(self, profile):
        result = _build_team_analysis_screen(profile, scroll_offset=0, width=80, height=30)
        assert isinstance(result, Panel)

    def test_mid_scroll(self, profile):
        result = _build_team_analysis_screen(profile, scroll_offset=10, width=80, height=30)
        assert isinstance(result, Panel)
