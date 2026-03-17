"""Adversarial LLM-output coverage for the parser and validation stack.

# See README: "Architecture" — four layers, three design principles
# See README: "Scrum Standards" — story points, priority, epic decomposition

Extension of the per-node parser tests focused on scenarios not already covered
in test_analyzer.py / test_epic_generator.py / test_story_writer.py /
test_task_decomposer.py.

What this adds over the individual node parser tests
-----------------------------------------------------
* **Vague / empty descriptions** — preamble+fence combination that prevents
  clean stripping, parametrized sweep of 8 bad-input forms, partial JSON.
* **Contradictory field values** — case-insensitive priority normalisation for
  all four values; extreme story points (0 → 1, 99 → 8); wrong type for a
  numeric field; 100% invalid epic_ids producing a fallback; non-dict scalars
  in the array.
* **Absurdly large scope** — 50 epic response, 30 stories/epic, 50 stories
  across multiple epics, 30-story-id sprint, ``_validate_stories`` per-epic
  count warning.

Tests already in the individual node files are NOT repeated here:
  ``test_bad_json_returns_fallback``, ``test_empty_response_returns_fallback``,
  ``test_invalid_priority_defaults_to_medium``, ``test_code_fence_stripping``,
  ``test_invalid_epic_id_skipped``, ``test_skips_items_with_unknown_story_id``,
  ``test_rounds_to_nearest_fibonacci``.
"""

from __future__ import annotations

import json

from scrum_agent.agent.nodes import (
    _parse_analysis_response,
    _parse_epics_response,
    _parse_sprints_response,
    _parse_stories_response,
    _validate_stories,
)
from scrum_agent.agent.state import Priority, StoryPointValue
from scrum_agent.prompts.epic_generator import MAX_EPICS
from scrum_agent.prompts.story_writer import MAX_STORIES_PER_EPIC
from tests._node_helpers import (
    make_completed_questionnaire,
    make_dummy_analysis,
    make_sample_epics,
    make_sample_stories,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _qs():
    qs = make_completed_questionnaire()
    qs.answers[1] = "Task management SaaS for engineering teams"
    qs.answers[2] = "greenfield"
    return qs


# ---------------------------------------------------------------------------
# Vague / empty project descriptions
# ---------------------------------------------------------------------------


class TestVagueProjectDescriptions:
    """Unique fallback paths not covered by test_analyzer.py.

    # See README: "Architecture" — project_analyzer node, fallback path
    """

    def test_preamble_plus_fence_parses_gracefully(self):
        """LLM preamble before the JSON fence prevents clean stripping → fallback.

        Some providers output: 'Sure! Here's the analysis:\n```json\n{...}\n```'
        The fence stripper only handles fences at the start of the string, so
        preamble text causes a parse failure. This documents that the fallback
        fires (no crash) — not a bug report.
        """
        preamble = "Sure, here's the analysis you requested:\n"
        json_body = (
            '{"project_name": "TodoApp", "project_type": "greenfield", "sprint_length_weeks": 2, "target_sprints": 3}'
        )
        with_fence = f"{preamble}```json\n{json_body}\n```"
        result = _parse_analysis_response(with_fence, _qs(), 5, 20)
        assert result is not None  # no crash; fallback used

    def test_minimal_json_uses_defaults_for_missing_fields(self):
        """A one-field JSON dict returns ProjectAnalysis with sane defaults."""
        from scrum_agent.agent.state import ProjectAnalysis

        result = _parse_analysis_response('{"project_name": "MinimalApp"}', _qs(), 5, 20)
        assert isinstance(result, ProjectAnalysis)
        assert result.project_name == "MinimalApp"
        assert result.goals == ()
        assert result.sprint_length_weeks == 2

    def test_fallback_always_returns_project_analysis(self):
        """Every known malformed-input form returns a ProjectAnalysis, never None."""
        from scrum_agent.agent.state import ProjectAnalysis

        bad_inputs = [
            "",
            "not json at all",
            "null",
            "{}",
            "[]",
            '{"bad": "structure"}',
            "true",
            "42",
        ]
        for raw in bad_inputs:
            result = _parse_analysis_response(raw, _qs(), 5, 20)
            assert isinstance(result, ProjectAnalysis), (
                f"Expected ProjectAnalysis for {raw!r}, got {type(result).__name__}"
            )


# ---------------------------------------------------------------------------
# Contradictory / invalid field values
# ---------------------------------------------------------------------------


class TestContradictoryFieldValues:
    """Impossible/invalid field values are corrected, not crashed on.

    # See README: "Scrum Standards" — priority levels, Fibonacci story points
    """

    def test_all_four_valid_priorities_preserved_case_insensitively(self):
        """CRITICAL / High / medium / low are all normalised to the Priority enum."""
        raw = json.dumps(
            [
                {"id": "E1", "title": "E1", "description": "d", "priority": "CRITICAL"},
                {"id": "E2", "title": "E2", "description": "d", "priority": "High"},
                {"id": "E3", "title": "E3", "description": "d", "priority": "medium"},
                {"id": "E4", "title": "E4", "description": "d", "priority": "low"},
            ]
        )
        epics = _parse_epics_response(raw, make_dummy_analysis())
        assert [e.priority for e in epics] == [Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM, Priority.LOW]

    def test_extreme_story_points_clamped_to_fibonacci(self):
        """story_points=99 clamps to 8; story_points=0 clamps to 1."""
        epics = make_sample_epics()
        stories_raw = json.dumps(
            [
                {
                    "id": "US-E1-001",
                    "epic_id": "E1",
                    "persona": "u",
                    "goal": "g",
                    "benefit": "b",
                    "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                    "story_points": 99,
                    "priority": "high",
                },
                {
                    "id": "US-E1-002",
                    "epic_id": "E1",
                    "persona": "u",
                    "goal": "g",
                    "benefit": "b",
                    "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                    "story_points": 0,
                    "priority": "medium",
                },
            ]
        )
        stories = _parse_stories_response(stories_raw, epics, make_dummy_analysis())
        assert stories[0].story_points == StoryPointValue.EIGHT
        assert stories[1].story_points == StoryPointValue.ONE

    def test_sprint_length_weeks_wrong_type_defaults_to_two(self):
        """``sprint_length_weeks: "two"`` (string instead of int) defaults to 2."""
        raw = '{"project_name": "X", "sprint_length_weeks": "two", "target_sprints": "3"}'
        result = _parse_analysis_response(raw, _qs(), 5, 20)
        assert result.sprint_length_weeks == 2

    def test_all_stories_with_invalid_epic_id_trigger_fallback(self):
        """If every story references a non-existent epic, fallback stories are generated."""
        epics = make_sample_epics()  # E1, E2, E3
        stories_raw = json.dumps(
            [
                {
                    "id": "US-X-001",
                    "epic_id": "DOES_NOT_EXIST",
                    "persona": "u",
                    "goal": "g",
                    "benefit": "b",
                    "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                    "story_points": 3,
                    "priority": "high",
                },
            ]
        )
        # All skipped → empty list → _build_fallback_stories runs
        stories = _parse_stories_response(stories_raw, epics, make_dummy_analysis())
        assert len(stories) >= len(epics)

    def test_non_dict_items_in_epics_array_skipped(self):
        """Hallucinated scalars (strings, numbers, null) in the epics array are skipped."""
        raw = json.dumps(
            [
                "this is a string, not an object",
                42,
                None,
                {"id": "E1", "title": "Valid", "description": "desc", "priority": "high"},
            ]
        )
        epics = _parse_epics_response(raw, make_dummy_analysis())
        assert len(epics) == 1
        assert epics[0].title == "Valid"


# ---------------------------------------------------------------------------
# Absurdly large scope
# ---------------------------------------------------------------------------


class TestAbsurdlyLargeScope:
    """Oversized LLM outputs (50+ epics, 30+ stories/epic) do not crash.

    # See README: "Scrum Standards" — epic decomposition (3-6 epics rule)
    #
    # The epic_generator prompt instructs the LLM to return 3-6 epics.
    # The parser does NOT enforce this cap — it returns whatever the LLM sent.
    # Enforcement of the 3-6 rule lives in the prompt (MAX_EPICS=6 constant).
    # ``_validate_stories`` DOES warn when per-epic story count exceeds
    # MAX_STORIES_PER_EPIC (5).
    """

    def test_fifty_epics_no_crash(self):
        """50-epic response is parsed without error.

        Documents that the parser is permissive — the prompt budget is the
        enforcement mechanism, not the parser.
        """
        raw = json.dumps(
            [
                {"id": f"E{i}", "title": f"Epic {i}", "description": f"Scope {i}", "priority": "medium"}
                for i in range(1, 51)
            ]
        )
        epics = _parse_epics_response(raw, make_dummy_analysis())
        assert len(epics) == 50

    def test_parser_does_not_enforce_max_epics_cap(self):
        """Parser accepts MAX_EPICS+4 epics — confirms enforcement is in the prompt."""
        raw = json.dumps(
            [
                {"id": f"E{i}", "title": f"Epic {i}", "description": "d", "priority": "high"}
                for i in range(1, MAX_EPICS + 5)
            ]
        )
        epics = _parse_epics_response(raw, make_dummy_analysis())
        assert len(epics) == MAX_EPICS + 4

    def test_thirty_stories_per_epic_no_crash(self):
        """30 stories for a single epic are all parsed (no truncation)."""
        epics = [e for e in make_sample_epics() if e.id == "E1"]
        stories_data = [
            {
                "id": f"US-E1-{i:03d}",
                "epic_id": "E1",
                "persona": "user",
                "goal": f"feature {i}",
                "benefit": "value",
                "acceptance_criteria": [
                    {"given": "g", "when": "w", "then": "t"},
                    {"given": "g", "when": "w", "then": "t"},
                    {"given": "g", "when": "w", "then": "t"},
                ],
                "story_points": 3,
                "priority": "medium",
            }
            for i in range(1, 31)
        ]
        stories = _parse_stories_response(json.dumps(stories_data), epics, make_dummy_analysis())
        assert len(stories) == 30

    def test_validate_stories_warns_for_overcrowded_epic(self):
        """``_validate_stories`` issues a warning when a single epic exceeds MAX_STORIES_PER_EPIC.

        # See README: "Scrum Standards" — story count constraints
        """
        epics = [e for e in make_sample_epics() if e.id == "E1"]
        stories_data = [
            {
                "id": f"US-E1-{i:03d}",
                "epic_id": "E1",
                "persona": "user",
                "goal": f"feature {i}",
                "benefit": "value",
                "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                "story_points": 2,
                "priority": "medium",
            }
            for i in range(1, MAX_STORIES_PER_EPIC + 3)  # 3 over the recommended limit
        ]
        stories = _parse_stories_response(json.dumps(stories_data), epics, make_dummy_analysis())
        _, warnings = _validate_stories(stories, epics)
        over_limit = [w for w in warnings if "maximum" in w.lower() or "stories" in w.lower()]
        assert len(over_limit) > 0, f"Expected overcapacity warning, got: {warnings}"

    def test_fifty_stories_across_three_epics_no_crash(self):
        """50 stories spread across 3 epics are all parsed — no truncation."""
        epics = make_sample_epics()
        stories_data = [
            {
                "id": f"US-E{(i % 3) + 1}-{i:03d}",
                "epic_id": f"E{(i % 3) + 1}",
                "persona": "user",
                "goal": f"feature {i}",
                "benefit": "value",
                "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                "story_points": 3,
                "priority": "medium",
            }
            for i in range(1, 51)
        ]
        stories = _parse_stories_response(json.dumps(stories_data), epics, make_dummy_analysis())
        assert len(stories) == 50

    def test_sprint_with_duplicate_story_ids_no_crash(self):
        """Sprint plan containing duplicate/overcapacity story_ids is handled by the validator."""
        stories = make_sample_stories()
        story_ids = [s.id for s in stories]
        sprints_data = json.dumps(
            [
                {
                    "id": "SP-1",
                    "name": "Mega Sprint",
                    "goal": "Do everything",
                    "capacity_points": 300,
                    "story_ids": story_ids * 10,  # duplicates
                }
            ]
        )
        result = _parse_sprints_response(sprints_data, stories, velocity=10)
        assert result is not None
        assert isinstance(result, list)

    def test_empty_epics_triggers_fallback(self):
        """Empty JSON array for epics → _build_fallback_epics generates 3 generic epics."""
        epics = _parse_epics_response("[]", make_dummy_analysis())
        assert len(epics) == 3

    def test_empty_stories_triggers_fallback(self):
        """Empty JSON array for stories → _build_fallback_stories generates at least one per epic."""
        epics = make_sample_epics()
        stories = _parse_stories_response("[]", epics, make_dummy_analysis())
        assert len(stories) >= len(epics)
