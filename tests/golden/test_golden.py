"""Golden dataset regression tests — structural evaluators for pipeline output.

Runs each curated project through the full pipeline (analyzer → features → stories
→ tasks → sprints) with mocked LLM responses that return structurally valid but
deterministic JSON. Validates structural properties of the output:

- Feature count within bounds
- Story-to-feature relationships valid
- Story points are Fibonacci values
- Acceptance criteria in Given/When/Then format
- Task-to-story references valid
- Sprint allocation covers all stories without exceeding velocity

These tests catch regressions in parsing, validation, and fallback logic.
They do NOT test LLM quality — for that, use LangSmith evaluators with real
API calls.

Run with: ``make eval`` or ``pytest tests/golden/ -v``
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from scrum_agent.agent.nodes import (
    feature_generator,
    project_analyzer,
    sprint_planner,
    story_writer,
    task_decomposer,
)
from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Feature,
    Priority,
    ProjectAnalysis,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)

from .datasets import ALL_DATASETS, build_questionnaire

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DATASET_IDS = list(ALL_DATASETS.keys())
VALID_FIBONACCI = {v.value for v in StoryPointValue}
VALID_PRIORITIES = {p.value for p in Priority}


@pytest.fixture(params=DATASET_IDS)
def dataset(request):
    """Parametrize over all golden datasets."""
    return ALL_DATASETS[request.param]


@pytest.fixture
def pipeline_state(dataset, monkeypatch):
    """Run the full pipeline for a dataset and return the final state.

    Uses deterministic fallback (garbage LLM) so the test exercises the
    fallback code paths and validates that even worst-case outputs meet
    structural requirements.
    """
    _patch_external_lookups(monkeypatch)

    # Use garbage LLM — all nodes will fall back to deterministic defaults.
    # This tests that fallback logic produces structurally valid output.
    garbage_llm = _make_simple_llm("Not valid JSON at all!")
    monkeypatch.setattr("scrum_agent.agent.nodes.get_llm", lambda **kw: garbage_llm)

    qs = build_questionnaire(dataset)
    state: dict = {
        "messages": [HumanMessage(content="continue")],
        "questionnaire": qs,
    }

    # Run pipeline stages sequentially
    result = project_analyzer(state)
    state.update(result)

    result = feature_generator(state)
    state.update(result)

    result = story_writer(state)
    state.update(result)

    result = task_decomposer(state)
    state.update(result)

    state["team_size"] = dataset["expected"]["team_size"]
    state["velocity_per_sprint"] = dataset["expected"]["team_size"] * 5
    state["target_sprints"] = dataset["expected"]["target_sprints"]

    result = sprint_planner(state)
    state.update(result)

    return state, dataset["expected"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_simple_llm(response_text: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.content = response_text
    mock = MagicMock()
    mock.invoke.return_value = mock_resp
    return mock


def _patch_external_lookups(monkeypatch):
    monkeypatch.setattr("scrum_agent.agent.nodes._scan_repo_context", lambda *a, **kw: (None, {}))
    monkeypatch.setattr("scrum_agent.agent.nodes._fetch_confluence_context", lambda *a, **kw: (None, {}))
    monkeypatch.setattr("scrum_agent.agent.nodes._load_user_context", lambda *a, **kw: (None, {}))


# ---------------------------------------------------------------------------
# Structural Evaluators
# ---------------------------------------------------------------------------


class TestAnalysisEvaluator:
    """Validate project analysis output."""

    def test_analysis_is_project_analysis(self, pipeline_state):
        state, _expected = pipeline_state
        assert isinstance(state["project_analysis"], ProjectAnalysis)

    def test_analysis_has_project_name(self, pipeline_state):
        state, _expected = pipeline_state
        assert state["project_analysis"].project_name.strip()

    def test_analysis_has_tech_stack(self, pipeline_state):
        state, _expected = pipeline_state
        assert len(state["project_analysis"].tech_stack) >= 1

    def test_analysis_has_goals(self, pipeline_state):
        state, _expected = pipeline_state
        assert len(state["project_analysis"].goals) >= 1

    def test_analysis_sprint_length_positive(self, pipeline_state):
        state, _expected = pipeline_state
        assert state["project_analysis"].sprint_length_weeks > 0

    def test_analysis_target_sprints_positive(self, pipeline_state):
        state, _expected = pipeline_state
        assert state["project_analysis"].target_sprints > 0


class TestFeatureEvaluator:
    """Validate feature generation — count, structure, uniqueness."""

    def test_feature_count_in_range(self, pipeline_state):
        state, expected = pipeline_state
        features = state["features"]
        assert expected["min_features"] <= len(features) <= expected["max_features"], (
            f"Expected {expected['min_features']}–{expected['max_features']} features, got {len(features)}"
        )

    def test_all_features_are_feature_type(self, pipeline_state):
        state, _expected = pipeline_state
        assert all(isinstance(e, Feature) for e in state["features"])

    def test_features_have_titles(self, pipeline_state):
        state, _expected = pipeline_state
        for feature in state["features"]:
            assert feature.title.strip(), f"Feature {feature.id} has empty title"

    def test_features_have_descriptions(self, pipeline_state):
        state, _expected = pipeline_state
        for feature in state["features"]:
            assert feature.description.strip(), f"Feature {feature.id} has empty description"

    def test_features_have_valid_priorities(self, pipeline_state):
        state, _expected = pipeline_state
        for feature in state["features"]:
            assert feature.priority.value in VALID_PRIORITIES, (
                f"Feature {feature.id} has invalid priority: {feature.priority}"
            )

    def test_feature_ids_unique(self, pipeline_state):
        state, _expected = pipeline_state
        ids = [e.id for e in state["features"]]
        assert len(ids) == len(set(ids)), f"Duplicate feature IDs: {ids}"


class TestStoryEvaluator:
    """Validate story generation — references, points, ACs."""

    def test_all_stories_are_user_story_type(self, pipeline_state):
        state, _expected = pipeline_state
        assert all(isinstance(s, UserStory) for s in state["stories"])

    def test_story_feature_ids_reference_real_features(self, pipeline_state):
        state, _expected = pipeline_state
        feature_ids = {e.id for e in state["features"]}
        for story in state["stories"]:
            assert story.feature_id in feature_ids, (
                f"Story {story.id} references non-existent feature {story.feature_id}"
            )

    def test_story_points_are_fibonacci(self, pipeline_state):
        state, _expected = pipeline_state
        for story in state["stories"]:
            assert story.story_points.value in VALID_FIBONACCI, (
                f"Story {story.id} has non-Fibonacci points: {story.story_points}"
            )

    def test_stories_have_personas(self, pipeline_state):
        state, _expected = pipeline_state
        for story in state["stories"]:
            assert story.persona.strip(), f"Story {story.id} has empty persona"

    def test_stories_have_goals(self, pipeline_state):
        state, _expected = pipeline_state
        for story in state["stories"]:
            assert story.goal.strip(), f"Story {story.id} has empty goal"

    def test_stories_have_benefits(self, pipeline_state):
        state, _expected = pipeline_state
        for story in state["stories"]:
            assert story.benefit.strip(), f"Story {story.id} has empty benefit"

    def test_acceptance_criteria_format(self, pipeline_state):
        """Each AC should have non-empty given, when, then fields."""
        state, _expected = pipeline_state
        for story in state["stories"]:
            for ac in story.acceptance_criteria:
                assert isinstance(ac, AcceptanceCriterion)
                assert ac.given.strip(), f"Story {story.id} has AC with empty 'given'"
                assert ac.when.strip(), f"Story {story.id} has AC with empty 'when'"
                assert ac.then.strip(), f"Story {story.id} has AC with empty 'then'"

    def test_stories_have_at_least_one_ac(self, pipeline_state):
        state, _expected = pipeline_state
        for story in state["stories"]:
            assert len(story.acceptance_criteria) >= 1, f"Story {story.id} has no acceptance criteria"

    def test_story_ids_unique(self, pipeline_state):
        state, _expected = pipeline_state
        ids = [s.id for s in state["stories"]]
        assert len(ids) == len(set(ids)), f"Duplicate story IDs: {ids}"

    def test_stories_have_valid_priorities(self, pipeline_state):
        state, _expected = pipeline_state
        for story in state["stories"]:
            assert story.priority.value in VALID_PRIORITIES


class TestTaskEvaluator:
    """Validate task generation — references, structure."""

    def test_all_tasks_are_task_type(self, pipeline_state):
        state, _expected = pipeline_state
        assert all(isinstance(t, Task) for t in state["tasks"])

    def test_task_story_ids_reference_real_stories(self, pipeline_state):
        state, _expected = pipeline_state
        story_ids = {s.id for s in state["stories"]}
        for task in state["tasks"]:
            assert task.story_id in story_ids, f"Task {task.id} references non-existent story {task.story_id}"

    def test_tasks_have_titles(self, pipeline_state):
        state, _expected = pipeline_state
        for task in state["tasks"]:
            assert task.title.strip(), f"Task {task.id} has empty title"

    def test_tasks_have_descriptions(self, pipeline_state):
        state, _expected = pipeline_state
        for task in state["tasks"]:
            assert task.description.strip(), f"Task {task.id} has empty description"

    def test_task_ids_unique(self, pipeline_state):
        state, _expected = pipeline_state
        ids = [t.id for t in state["tasks"]]
        assert len(ids) == len(set(ids)), f"Duplicate task IDs: {ids}"

    def test_every_story_has_at_least_one_task(self, pipeline_state):
        state, _expected = pipeline_state
        stories_with_tasks = {t.story_id for t in state["tasks"]}
        for story in state["stories"]:
            assert story.id in stories_with_tasks, f"Story {story.id} has no tasks"


class TestSprintEvaluator:
    """Validate sprint planning — allocation, capacity, orphans."""

    def test_all_sprints_are_sprint_type(self, pipeline_state):
        state, _expected = pipeline_state
        assert all(isinstance(s, Sprint) for s in state["sprints"])

    def test_sprint_count_in_range(self, pipeline_state):
        state, expected = pipeline_state
        assert expected["min_sprints"] <= len(state["sprints"]) <= expected["max_sprints"], (
            f"Expected {expected['min_sprints']}–{expected['max_sprints']} sprints, got {len(state['sprints'])}"
        )

    def test_all_stories_allocated_to_sprints(self, pipeline_state):
        """No orphan stories — every story appears in at least one sprint."""
        state, _expected = pipeline_state
        allocated = set()
        for sprint in state["sprints"]:
            allocated.update(sprint.story_ids)
        story_ids = {s.id for s in state["stories"]}
        orphans = story_ids - allocated
        assert not orphans, f"Orphan stories not allocated to any sprint: {orphans}"

    def test_no_duplicate_story_allocation(self, pipeline_state):
        """A story should appear in exactly one sprint."""
        state, _expected = pipeline_state
        seen: set[str] = set()
        duplicates: set[str] = set()
        for sprint in state["sprints"]:
            for sid in sprint.story_ids:
                if sid in seen:
                    duplicates.add(sid)
                seen.add(sid)
        assert not duplicates, f"Stories allocated to multiple sprints: {duplicates}"

    def test_sprint_story_ids_reference_real_stories(self, pipeline_state):
        state, _expected = pipeline_state
        story_ids = {s.id for s in state["stories"]}
        for sprint in state["sprints"]:
            for sid in sprint.story_ids:
                assert sid in story_ids, f"Sprint {sprint.id} references non-existent story {sid}"

    def test_sprints_have_goals(self, pipeline_state):
        state, _expected = pipeline_state
        for sprint in state["sprints"]:
            assert sprint.goal.strip(), f"Sprint {sprint.id} has empty goal"

    def test_sprint_capacity_non_negative(self, pipeline_state):
        state, _expected = pipeline_state
        for sprint in state["sprints"]:
            assert sprint.capacity_points >= 0, f"Sprint {sprint.id} has negative capacity: {sprint.capacity_points}"

    def test_sprint_ids_unique(self, pipeline_state):
        state, _expected = pipeline_state
        ids = [s.id for s in state["sprints"]]
        assert len(ids) == len(set(ids)), f"Duplicate sprint IDs: {ids}"


# ---------------------------------------------------------------------------
# Cross-artifact integrity
# ---------------------------------------------------------------------------


class TestCrossArtifactIntegrity:
    """Validate relationships across all artifact types."""

    def test_total_story_points_reasonable(self, pipeline_state):
        """Total story points should be achievable within the planned sprints."""
        state, expected = pipeline_state
        total_points = sum(s.story_points.value for s in state["stories"])
        velocity = expected["team_size"] * 5
        max_capacity = velocity * expected["max_sprints"]
        assert total_points <= max_capacity, (
            f"Total story points ({total_points}) exceeds max capacity "
            f"({max_capacity} = {velocity}/sprint × {expected['max_sprints']} sprints)"
        )

    def test_pipeline_produces_all_artifact_types(self, pipeline_state):
        """Every pipeline stage should produce output."""
        state, _expected = pipeline_state
        assert state.get("project_analysis") is not None
        assert len(state.get("features", [])) >= 1
        assert len(state.get("stories", [])) >= 1
        assert len(state.get("tasks", [])) >= 1
        assert len(state.get("sprints", [])) >= 1
