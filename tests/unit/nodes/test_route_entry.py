"""Tests for the route_entry() conditional edge function."""

from scrum_agent.agent.nodes import route_entry
from scrum_agent.agent.state import (
    AcceptanceCriterion,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)
from tests._node_helpers import make_dummy_analysis


class TestRouteEntry:
    """Tests for the route_entry() conditional edge function."""

    def test_routes_to_intake_when_no_questionnaire(self):
        """No questionnaire in state -> route to 'project_intake'."""
        state = {"messages": []}
        assert route_entry(state) == "project_intake"

    def test_routes_to_intake_when_incomplete(self):
        """Questionnaire exists but not completed -> route to 'project_intake'."""
        qs = QuestionnaireState(current_question=5, completed=False)
        state = {"messages": [], "questionnaire": qs}
        assert route_entry(state) == "project_intake"

    def test_routes_to_analyzer_when_complete_no_analysis(self):
        """Questionnaire completed but no analysis -> route to 'project_analyzer'."""
        qs = QuestionnaireState(completed=True)
        state = {"messages": [], "questionnaire": qs}
        assert route_entry(state) == "project_analyzer"

    def test_routes_to_feature_generator_when_no_features(self):
        """Analysis present but no features -> route to 'feature_generator'."""
        qs = QuestionnaireState(completed=True)
        analysis = ProjectAnalysis(
            project_name="Test",
            project_description="desc",
            project_type="greenfield",
            goals=(),
            end_users=(),
            target_state="",
            tech_stack=(),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=3,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )
        state = {"messages": [], "questionnaire": qs, "project_analysis": analysis}
        assert route_entry(state) == "feature_generator"

    def test_routes_to_feature_generator_when_features_empty_list(self):
        """Analysis present but features is empty list -> route to 'feature_generator'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        state = {"messages": [], "questionnaire": qs, "project_analysis": analysis, "features": []}
        assert route_entry(state) == "feature_generator"

    def test_routes_to_feature_skip_when_skip_features_true(self):
        """Analysis with skip_features=True and no features -> route to 'feature_skip'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis(skip_features=True, target_sprints=1, goals=("Build API",))
        state = {"messages": [], "questionnaire": qs, "project_analysis": analysis}
        assert route_entry(state) == "feature_skip"

    def test_routes_to_feature_skip_when_skip_features_true_empty_list(self):
        """Analysis with skip_features=True and empty features list -> route to 'feature_skip'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis(skip_features=True)
        state = {"messages": [], "questionnaire": qs, "project_analysis": analysis, "features": []}
        assert route_entry(state) == "feature_skip"

    def test_routes_to_feature_generator_when_skip_features_false(self):
        """Analysis with skip_features=False (default) and no features -> route to 'feature_generator'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis(skip_features=False)
        state = {"messages": [], "questionnaire": qs, "project_analysis": analysis}
        assert route_entry(state) == "feature_generator"

    def test_routes_to_story_writer_when_no_stories(self):
        """Questionnaire + analysis + features but no stories -> route to 'story_writer'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        features = [Feature(id="F1", title="Core", description="Core features", priority=Priority.HIGH)]
        state = {"messages": [], "questionnaire": qs, "project_analysis": analysis, "features": features}
        assert route_entry(state) == "story_writer"

    def test_routes_to_story_writer_when_stories_empty_list(self):
        """Questionnaire + analysis + features + empty stories list -> route to 'story_writer'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        features = [Feature(id="F1", title="Core", description="Core features", priority=Priority.HIGH)]
        state = {"messages": [], "questionnaire": qs, "project_analysis": analysis, "features": features, "stories": []}
        assert route_entry(state) == "story_writer"

    def test_routes_to_task_decomposer_when_stories_no_tasks(self):
        """Questionnaire + analysis + features + stories but no tasks -> route to 'task_decomposer'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        features = [Feature(id="F1", title="Core", description="Core features", priority=Priority.HIGH)]
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="do something",
                benefit="value",
                acceptance_criteria=(AcceptanceCriterion(given="context", when="action", then="outcome"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.HIGH,
            )
        ]
        state = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
        }
        assert route_entry(state) == "task_decomposer"

    def test_routes_to_task_decomposer_when_tasks_empty_list(self):
        """Questionnaire + analysis + features + stories + empty tasks -> route to 'task_decomposer'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        features = [Feature(id="F1", title="Core", description="Core features", priority=Priority.HIGH)]
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="do something",
                benefit="value",
                acceptance_criteria=(AcceptanceCriterion(given="context", when="action", then="outcome"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.HIGH,
            )
        ]
        state = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
            "tasks": [],
        }
        assert route_entry(state) == "task_decomposer"

    def test_routes_to_sprint_planner_when_tasks_no_sprints(self):
        """Tasks present, no sprints -> route to 'sprint_planner'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        features = [Feature(id="F1", title="Core", description="Core features", priority=Priority.HIGH)]
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="do something",
                benefit="value",
                acceptance_criteria=(AcceptanceCriterion(given="context", when="action", then="outcome"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.HIGH,
            )
        ]
        tasks = [Task(id="T-US-F1-001-01", story_id="US-F1-001", title="Implement feature", description="Build it")]
        state = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
            "tasks": tasks,
        }
        assert route_entry(state) == "sprint_planner"

    def test_routes_to_sprint_planner_when_sprints_empty(self):
        """Tasks + empty sprints -> route to 'sprint_planner'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        features = [Feature(id="F1", title="Core", description="Core features", priority=Priority.HIGH)]
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="do something",
                benefit="value",
                acceptance_criteria=(AcceptanceCriterion(given="context", when="action", then="outcome"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.HIGH,
            )
        ]
        tasks = [Task(id="T-US-F1-001-01", story_id="US-F1-001", title="Implement feature", description="Build it")]
        state = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
            "tasks": tasks,
            "sprints": [],
        }
        assert route_entry(state) == "sprint_planner"

    def test_routes_to_sprint_planner_with_sprint_number_set(self):
        """Tasks + no sprints + starting_sprint_number set -> route to 'sprint_planner'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        features = [Feature(id="F1", title="Core", description="Core features", priority=Priority.HIGH)]
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="do something",
                benefit="value",
                acceptance_criteria=(AcceptanceCriterion(given="context", when="action", then="outcome"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.HIGH,
            )
        ]
        tasks = [Task(id="T-US-F1-001-01", story_id="US-F1-001", title="Implement feature", description="Build it")]
        state = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
            "tasks": tasks,
            "starting_sprint_number": 105,
        }
        assert route_entry(state) == "sprint_planner"

    def test_routes_to_agent_when_sprints_present(self):
        """Questionnaire + analysis + features + stories + tasks + sprints -> route to 'agent'."""
        qs = QuestionnaireState(completed=True)
        analysis = make_dummy_analysis()
        features = [Feature(id="F1", title="Core", description="Core features", priority=Priority.HIGH)]
        stories = [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="user",
                goal="do something",
                benefit="value",
                acceptance_criteria=(AcceptanceCriterion(given="context", when="action", then="outcome"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.HIGH,
            )
        ]
        tasks = [Task(id="T-US-F1-001-01", story_id="US-F1-001", title="Implement feature", description="Build it")]
        sprints = [
            Sprint(id="SP-1", name="Sprint 1", goal="Core features", capacity_points=3, story_ids=("US-F1-001",))
        ]
        state = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": analysis,
            "features": features,
            "stories": stories,
            "tasks": tasks,
            "sprints": sprints,
        }
        assert route_entry(state) == "agent"

    def test_does_not_mutate_state(self):
        """route_entry is a pure function -- it must not modify the state."""
        qs = QuestionnaireState(current_question=3, completed=False)
        state = {"messages": [], "questionnaire": qs}
        route_entry(state)
        assert qs.current_question == 3
        assert qs.completed is False


# -- route_entry import tests -------------------------------------------------


class TestRouteEntryImports:
    """Verify route_entry is importable from the expected locations."""

    def test_importable_from_agent_package(self):
        from scrum_agent.agent import route_entry as imported_fn

        assert imported_fn is route_entry

    def test_importable_from_nodes_module(self):
        from scrum_agent.agent.nodes import route_entry as imported_fn

        assert imported_fn is route_entry
