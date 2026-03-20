"""Tests for JSON plan exporter."""

import json

from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)
from scrum_agent.json_exporter import export_plan_json


class TestExportPlanJson:
    """Tests for export_plan_json()."""

    def _make_analysis(self) -> ProjectAnalysis:
        return ProjectAnalysis(
            project_name="TodoApp",
            project_description="A simple todo list application",
            project_type="greenfield",
            goals=("MVP launch",),
            end_users=("developers",),
            target_state="deployed",
            tech_stack=("Python", "FastAPI"),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=4,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )

    def test_full_state(self):
        """Full graph state with all artifacts produces valid JSON."""
        qs = QuestionnaireState(completed=True, answers={1: "TodoApp", 6: "5"})
        analysis = self._make_analysis()
        feature = Feature(id="f-1", title="Task Management", description="CRUD tasks", priority=Priority.HIGH)
        story = UserStory(
            id="s-1",
            feature_id="f-1",
            persona="developer",
            goal="create tasks",
            benefit="track work",
            acceptance_criteria=(AcceptanceCriterion(given="logged in", when="create task", then="task saved"),),
            story_points=StoryPointValue.THREE,
            priority=Priority.HIGH,
            title="Create Task",
            discipline=Discipline.BACKEND,
        )
        task = Task(id="t-1", story_id="s-1", title="Implement POST /tasks", description="Create endpoint")
        sprint = Sprint(id="sp-1", name="Sprint 1", goal="Core CRUD", capacity_points=20, story_ids=("s-1",))

        state = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": analysis,
            "features": [feature],
            "stories": [story],
            "tasks": [task],
            "sprints": [sprint],
        }

        result = export_plan_json(state)
        parsed = json.loads(result)

        assert parsed["version"] == "1.0.0"
        assert parsed["project"]["name"] == "TodoApp"
        assert parsed["project"]["team_size"] == "5"
        assert parsed["project"]["tech_stack"] == ["Python", "FastAPI"]
        assert len(parsed["features"]) == 1
        assert len(parsed["stories"]) == 1
        assert len(parsed["tasks"]) == 1
        assert len(parsed["sprints"]) == 1
        assert parsed["stories"][0]["story_points"] == 3
        assert parsed["sprints"][0]["story_ids"] == ["s-1"]

    def test_partial_state_only_analysis(self):
        """State with only analysis (no features/stories) omits empty sections."""
        state = {
            "messages": [],
            "project_analysis": self._make_analysis(),
        }
        result = export_plan_json(state)
        parsed = json.loads(result)

        assert parsed["version"] == "1.0.0"
        assert "project" in parsed
        assert "features" not in parsed
        assert "stories" not in parsed
        assert "tasks" not in parsed
        assert "sprints" not in parsed

    def test_empty_state(self):
        """Empty graph state produces minimal JSON with just version."""
        state = {"messages": []}
        result = export_plan_json(state)
        parsed = json.loads(result)

        assert parsed["version"] == "1.0.0"
        assert "project" not in parsed
        assert "features" not in parsed

    def test_output_is_valid_json(self):
        """Output is valid, parseable JSON."""
        state = {"messages": []}
        result = export_plan_json(state)
        # Should not raise
        json.loads(result)

    def test_no_internal_fields_leaked(self):
        """Internal state fields (messages, pending_review, etc.) are not in output."""
        state = {
            "messages": [{"role": "user", "content": "hello"}],
            "pending_review": "features",
            "_intake_mode": "quick",
            "project_analysis": self._make_analysis(),
        }
        result = export_plan_json(state)
        parsed = json.loads(result)

        assert "messages" not in parsed
        assert "pending_review" not in parsed
        assert "_intake_mode" not in parsed

    def test_multiple_features(self):
        """Multiple features are all serialized."""
        features = [
            Feature(id=f"f-{i}", title=f"Feature {i}", description=f"Desc {i}", priority=Priority.MEDIUM)
            for i in range(1, 4)
        ]
        state = {"messages": [], "features": features}
        result = export_plan_json(state)
        parsed = json.loads(result)
        assert len(parsed["features"]) == 3
