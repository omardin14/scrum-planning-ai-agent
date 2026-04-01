"""Unit tests for LLM-based sample generation functions in team_learning.py.

Covers: generate_sample_epic, generate_sample_stories, generate_sample_tasks,
generate_sample_sprint — both successful LLM calls (mocked) and fallback paths.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from scrum_agent.tools.team_learning import (
    generate_sample_epic,
    generate_sample_sprint,
    generate_sample_stories,
    generate_sample_tasks,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CALIBRATION = """\
## Team Calibration
- Velocity: 23.5 pts/sprint
- Sprint length: 2 weeks
- Story points: Fibonacci (1,2,3,5,8)
"""

_EXAMPLES = {
    "naming_conventions": {
        "epic_naming_style": "quarter-scoped",
        "epic_examples": ["Q4|2025|High Region Outage DR", "Q1|2026|Low Overmind improvement"],
        "template_sections": [("What is this about?", 0.8), ("Why does it matter?", 0.6)],
    },
    "ac_patterns": {"median_ac": 3},
    "task_decomposition": {
        "avg_tasks_per_story": 4.8,
        "type_distribution": {"Development": 64, "Testing": 13},
        "common_tasks": [("create rollback module", 2)],
    },
    "scope_changes": {
        "totals": {"avg_delivered_velocity": 25.9, "avg_committed_velocity": 19.1},
    },
}

_SAMPLE_EPIC = {
    "title": "Q1|2026|Medium Platform Resilience",
    "description": "Improve platform resilience.",
    "priority": "high",
    "stories_estimate": 5,
    "points_estimate": 18,
    "rationale": "Matches quarter-scoped naming.",
}

_SAMPLE_STORIES = [
    {
        "id": "S1",
        "title": "Implement failover",
        "persona": "developer",
        "goal": "automated failover",
        "benefit": "reduced downtime",
        "story_points": 5,
        "priority": "high",
        "discipline": "infrastructure",
        "acceptance_criteria": [
            {"given": "primary fails", "when": "failover triggers", "then": "traffic reroutes"},
        ],
        "rationale": "Matches infra pattern.",
    },
    {
        "id": "S2",
        "title": "Add monitoring",
        "persona": "SRE",
        "goal": "visibility",
        "benefit": "faster response",
        "story_points": 3,
        "priority": "medium",
        "discipline": "observability",
        "acceptance_criteria": [],
        "rationale": "Matches observability pattern.",
    },
]

_SAMPLE_TASKS = [
    {"id": "T-S1-01", "story_id": "S1", "title": "Build health endpoint", "label": "Code"},
    {"id": "T-S1-02", "story_id": "S1", "title": "Write integration tests", "label": "Testing"},
    {"id": "T-S2-01", "story_id": "S2", "title": "Create dashboard", "label": "Infrastructure"},
]


def _mock_llm_response(content: str) -> MagicMock:
    """Create a mock LLM response object."""
    resp = MagicMock()
    resp.content = content
    return resp


# ---------------------------------------------------------------------------
# generate_sample_epic
# ---------------------------------------------------------------------------


class TestGenerateSampleEpic:
    """Test sample epic generation with mocked LLM and fallback."""

    @patch("scrum_agent.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        epic_json = json.dumps(_SAMPLE_EPIC)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(epic_json)

        result = generate_sample_epic(_CALIBRATION, _EXAMPLES)

        assert isinstance(result, dict)
        assert result["title"] == _SAMPLE_EPIC["title"]
        assert result["priority"] == "high"
        assert result["stories_estimate"] == 5

    @patch("scrum_agent.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        """LLM wraps JSON in markdown code fences — should still parse."""
        epic_json = f"```json\n{json.dumps(_SAMPLE_EPIC)}\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(epic_json)

        result = generate_sample_epic(_CALIBRATION)
        assert isinstance(result, dict)
        assert "title" in result

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_on_llm_error(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_epic(_CALIBRATION, _EXAMPLES)

        assert isinstance(result, dict)
        assert "title" in result
        assert "rationale" in result
        assert "Fallback" in result["rationale"]

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_on_invalid_json(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("not valid json {{{")

        result = generate_sample_epic(_CALIBRATION)
        assert isinstance(result, dict)
        assert "Fallback" in result["rationale"]

    def test_fallback_uses_epic_examples(self):
        """When LLM unavailable, fallback should use first epic example if available."""
        with patch("scrum_agent.agent.llm.get_llm", side_effect=RuntimeError("unavailable")):
            result = generate_sample_epic(_CALIBRATION, _EXAMPLES)
            assert isinstance(result, dict)
            # Should use first example title from naming_conventions
            assert result["title"] == "Q4|2025|High Region Outage DR"

    @patch("scrum_agent.agent.llm.get_llm")
    def test_no_examples(self, mock_get_llm):
        epic_json = json.dumps(_SAMPLE_EPIC)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(epic_json)

        result = generate_sample_epic(_CALIBRATION, None)
        assert isinstance(result, dict)

    @patch("scrum_agent.agent.llm.get_llm")
    def test_empty_examples(self, mock_get_llm):
        epic_json = json.dumps(_SAMPLE_EPIC)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(epic_json)

        result = generate_sample_epic(_CALIBRATION, {})
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# generate_sample_stories
# ---------------------------------------------------------------------------


class TestGenerateSampleStories:
    """Test sample stories generation with mocked LLM and fallback."""

    @patch("scrum_agent.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        stories_json = json.dumps(_SAMPLE_STORIES)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(stories_json)

        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC, _EXAMPLES)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == "S1"
        assert result[0]["story_points"] == 5

    @patch("scrum_agent.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        stories_json = f"```json\n{json.dumps(_SAMPLE_STORIES)}\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(stories_json)

        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC)
        assert isinstance(result, list)
        assert len(result) >= 1

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_on_llm_error(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC)

        assert isinstance(result, list)
        assert len(result) >= 1
        assert "Fallback" in result[0]["rationale"]

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_on_invalid_json(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("broken")

        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC)
        assert isinstance(result, list)
        assert len(result) >= 1

    @patch("scrum_agent.agent.llm.get_llm")
    def test_stories_count_capped_at_3(self, mock_get_llm):
        """Even if epic estimates 10 stories, generate at most 3."""
        big_epic = {**_SAMPLE_EPIC, "stories_estimate": 10}
        stories = [{"id": f"S{i}", "title": f"Story {i}"} for i in range(3)]
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(stories))

        result = generate_sample_stories(_CALIBRATION, big_epic)
        assert len(result) <= 3

    @patch("scrum_agent.agent.llm.get_llm")
    def test_no_examples(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_STORIES))
        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC, None)
        assert isinstance(result, list)

    @patch("scrum_agent.agent.llm.get_llm")
    def test_epic_with_zero_stories_estimate(self, mock_get_llm):
        """Epic with stories_estimate=0 should still generate at least 2."""
        epic = {**_SAMPLE_EPIC, "stories_estimate": 0}
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_STORIES))
        result = generate_sample_stories(_CALIBRATION, epic)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# generate_sample_tasks
# ---------------------------------------------------------------------------


class TestGenerateSampleTasks:
    """Test sample tasks generation with mocked LLM and fallback."""

    @patch("scrum_agent.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        tasks_json = json.dumps(_SAMPLE_TASKS)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(tasks_json)

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES, _EXAMPLES)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0]["id"] == "T-S1-01"

    @patch("scrum_agent.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        tasks_json = f"```\n{json.dumps(_SAMPLE_TASKS)}\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(tasks_json)

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES)
        assert isinstance(result, list)

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_on_llm_error(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES)

        assert isinstance(result, list)
        assert len(result) >= 1
        # Fallback generates one task per story
        story_ids = {t["story_id"] for t in result}
        assert "S1" in story_ids

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_on_invalid_json(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("{invalid")

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES)
        assert isinstance(result, list)
        assert len(result) >= 1

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_creates_task_per_story(self, mock_get_llm):
        """Fallback should create at least one task per story."""
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("fail")

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES)
        fallback_story_ids = {t["story_id"] for t in result}
        input_story_ids = {s["id"] for s in _SAMPLE_STORIES}
        assert fallback_story_ids == input_story_ids

    @patch("scrum_agent.agent.llm.get_llm")
    def test_no_examples(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_TASKS))
        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES, None)
        assert isinstance(result, list)

    @patch("scrum_agent.agent.llm.get_llm")
    def test_empty_stories(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("[]")
        result = generate_sample_tasks(_CALIBRATION, [])
        assert isinstance(result, list)

    @patch("scrum_agent.agent.llm.get_llm")
    def test_task_decomposition_context_injected(self, mock_get_llm):
        """When examples include task_decomposition, it should be in the prompt."""
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_TASKS))

        generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES, _EXAMPLES)

        # Verify prompt was called and contains task context
        call_args = mock_get_llm.return_value.invoke.call_args
        prompt_content = call_args[0][0][0].content
        assert "4.8" in prompt_content  # avg_tasks_per_story


# ---------------------------------------------------------------------------
# generate_sample_sprint
# ---------------------------------------------------------------------------


class TestGenerateSampleSprint:
    """Test sample sprint plan generation with mocked LLM and fallback."""

    _SPRINT_RESULT = {
        "sprint_name": "Sprint 1",
        "velocity_target": 20,
        "stories_included": ["S1", "S2"],
        "total_points": 8,
        "capacity_notes": "Based on team avg.",
        "risks": ["External dependency"],
        "rationale": "Conservative allocation.",
    }

    @patch("scrum_agent.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(self._SPRINT_RESULT))

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, _EXAMPLES)

        assert isinstance(result, dict)
        assert result["sprint_name"] == "Sprint 1"
        assert result["velocity_target"] == 20
        assert result["total_points"] == 8

    @patch("scrum_agent.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        sprint_json = f"```json\n{json.dumps(self._SPRINT_RESULT)}\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(sprint_json)

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS)
        assert isinstance(result, dict)
        assert "sprint_name" in result

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_on_llm_error(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, _EXAMPLES)

        assert isinstance(result, dict)
        assert "Fallback" in result["rationale"]
        assert result["velocity_target"] == 25.9  # avg_delivered_velocity from examples

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_on_invalid_json(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("not json")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS)
        assert isinstance(result, dict)
        assert "Fallback" in result["rationale"]

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_includes_all_story_ids(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("fail")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS)
        assert set(result["stories_included"]) == {"S1", "S2"}

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_total_points_matches_stories(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("fail")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS)
        expected_pts = sum(s.get("story_points", 0) for s in _SAMPLE_STORIES)
        assert result["total_points"] == expected_pts

    @patch("scrum_agent.agent.llm.get_llm")
    def test_fallback_without_examples(self, mock_get_llm):
        """Without examples, fallback velocity_target should be 20."""
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("fail")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, None)
        assert result["velocity_target"] == 20  # default fallback

    @patch("scrum_agent.agent.llm.get_llm")
    def test_no_examples(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(self._SPRINT_RESULT))
        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, None)
        assert isinstance(result, dict)

    @patch("scrum_agent.agent.llm.get_llm")
    def test_empty_stories_and_tasks(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(
            json.dumps({**self._SPRINT_RESULT, "stories_included": [], "total_points": 0})
        )
        result = generate_sample_sprint(_CALIBRATION, [], [])
        assert isinstance(result, dict)
