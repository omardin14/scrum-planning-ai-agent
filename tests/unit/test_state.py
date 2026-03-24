"""Tests for the scrum agent state schema."""

from dataclasses import FrozenInstanceError, asdict

import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph

from scrum_agent.agent.state import (
    PHASE_QUESTION_RANGES,
    TOTAL_QUESTIONS,
    AcceptanceCriterion,
    Discipline,
    Feature,
    OutputFormat,
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
    _merge_dicts,
)

# ── Enum tests ─────────────────────────────────────────────────────────


class TestPriority:
    def test_values(self):
        assert set(Priority) == {Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM, Priority.LOW}

    def test_is_str(self):
        assert isinstance(Priority.HIGH, str)
        assert Priority.HIGH == "high"


class TestStoryPointValue:
    def test_fibonacci_values(self):
        assert [v.value for v in StoryPointValue] == [1, 2, 3, 5, 8]

    def test_is_int(self):
        assert isinstance(StoryPointValue.FIVE, int)
        assert StoryPointValue.FIVE == 5


class TestQuestionnairePhase:
    def test_has_seven_phases(self):
        assert len(QuestionnairePhase) == 7

    def test_is_str(self):
        assert isinstance(QuestionnairePhase.PROJECT_CONTEXT, str)


class TestReviewDecision:
    def test_values(self):
        assert set(ReviewDecision) == {ReviewDecision.ACCEPT, ReviewDecision.EDIT, ReviewDecision.REJECT}


class TestOutputFormat:
    def test_values(self):
        assert set(OutputFormat) == {OutputFormat.JIRA, OutputFormat.MARKDOWN, OutputFormat.BOTH}


class TestDiscipline:
    """Tests for the Discipline StrEnum."""

    def test_values(self):
        """Discipline should have exactly 6 members."""
        assert len(Discipline) == 6
        assert set(Discipline) == {
            Discipline.FRONTEND,
            Discipline.BACKEND,
            Discipline.FULLSTACK,
            Discipline.INFRASTRUCTURE,
            Discipline.DESIGN,
            Discipline.TESTING,
        }

    def test_is_str(self):
        """Discipline members should be strings (StrEnum)."""
        assert isinstance(Discipline.BACKEND, str)
        assert Discipline.BACKEND == "backend"

    def test_default_discipline_on_user_story(self):
        """UserStory with no discipline kwarg should default to FULLSTACK."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-1",
            feature_id="F1",
            persona="dev",
            goal="do something",
            benefit="value",
            acceptance_criteria=(ac,),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )
        assert story.discipline == Discipline.FULLSTACK

    def test_explicit_discipline_on_user_story(self):
        """UserStory with explicit discipline should use the provided value."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-1",
            feature_id="F1",
            persona="dev",
            goal="build a UI component",
            benefit="better UX",
            acceptance_criteria=(ac,),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
            discipline=Discipline.FRONTEND,
        )
        assert story.discipline == Discipline.FRONTEND


# ── Artifact dataclass tests ──────────────────────────────────────────


class TestAcceptanceCriterion:
    def test_creation(self):
        ac = AcceptanceCriterion(given="a user", when="they log in", then="they see the dashboard")
        assert ac.given == "a user"

    def test_frozen(self):
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        with pytest.raises(FrozenInstanceError):
            ac.given = "mutated"  # type: ignore[misc]

    def test_asdict(self):
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        d = asdict(ac)
        assert d == {"given": "a", "when": "b", "then": "c"}


class TestFeature:
    def test_creation(self):
        e = Feature(id="F-1", title="Auth", description="Authentication system", priority=Priority.HIGH)
        assert e.id == "F-1"
        assert e.priority == Priority.HIGH

    def test_frozen(self):
        e = Feature(id="F-1", title="Auth", description="desc", priority=Priority.HIGH)
        with pytest.raises(FrozenInstanceError):
            e.title = "mutated"  # type: ignore[misc]


class TestUserStory:
    @pytest.fixture()
    def story(self):
        ac = AcceptanceCriterion(given="a user", when="they log in", then="they see the dashboard")
        return UserStory(
            id="US-1",
            feature_id="F-1",
            persona="developer",
            goal="log in with SSO",
            benefit="I save time",
            acceptance_criteria=(ac,),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )

    def test_text_property(self, story: UserStory):
        assert story.text == "As a developer, I want to log in with SSO, so that I save time."

    def test_frozen(self, story: UserStory):
        with pytest.raises(FrozenInstanceError):
            story.persona = "mutated"  # type: ignore[misc]

    def test_asdict_serializable(self, story: UserStory):
        d = asdict(story)
        assert d["persona"] == "developer"
        assert isinstance(d["acceptance_criteria"], (list, tuple))


class TestTask:
    def test_creation(self):
        t = Task(id="T-1", story_id="US-1", title="Implement login", description="Wire up SSO")
        assert t.story_id == "US-1"

    def test_frozen(self):
        t = Task(id="T-1", story_id="US-1", title="t", description="d")
        with pytest.raises(FrozenInstanceError):
            t.title = "mutated"  # type: ignore[misc]

    def test_default_label_is_code(self):
        t = Task(id="T-1", story_id="US-1", title="t", description="d")
        assert t.label == TaskLabel.CODE

    def test_explicit_label(self):
        t = Task(id="T-1", story_id="US-1", title="t", description="d", label=TaskLabel.TESTING)
        assert t.label == TaskLabel.TESTING

    def test_ai_prompt_default(self):
        t = Task(id="T-1", story_id="US-1", title="t", description="d")
        assert t.ai_prompt == ""

    def test_ai_prompt_set(self):
        prompt = "You are a backend engineer. Implement the login endpoint using FastAPI."
        t = Task(id="T-1", story_id="US-1", title="t", description="d", ai_prompt=prompt)
        assert t.ai_prompt == prompt


class TestTaskLabel:
    def test_all_values(self):
        assert set(TaskLabel) == {TaskLabel.CODE, TaskLabel.DOCUMENTATION, TaskLabel.INFRASTRUCTURE, TaskLabel.TESTING}

    def test_string_values(self):
        assert TaskLabel.CODE.value == "Code"
        assert TaskLabel.DOCUMENTATION.value == "Documentation"
        assert TaskLabel.INFRASTRUCTURE.value == "Infrastructure"
        assert TaskLabel.TESTING.value == "Testing"

    def test_from_string(self):
        assert TaskLabel("Code") == TaskLabel.CODE
        assert TaskLabel("Testing") == TaskLabel.TESTING


class TestSprint:
    def test_creation(self):
        s = Sprint(id="S-1", name="Sprint 1", goal="Auth MVP", capacity_points=20, story_ids=("US-1", "US-2"))
        assert s.capacity_points == 20
        assert len(s.story_ids) == 2

    def test_frozen(self):
        s = Sprint(id="S-1", name="Sprint 1", goal="g", capacity_points=20, story_ids=())
        with pytest.raises(FrozenInstanceError):
            s.name = "mutated"  # type: ignore[misc]


class TestProjectAnalysis:
    """Tests for the ProjectAnalysis frozen dataclass."""

    @pytest.fixture()
    def analysis(self):
        return ProjectAnalysis(
            project_name="Todo App",
            project_description="A full-stack todo application",
            project_type="greenfield",
            goals=("Task management", "User auth"),
            end_users=("developers",),
            target_state="Deployed to production",
            tech_stack=("React", "FastAPI"),
            integrations=("GitHub API",),
            constraints=("Must use AWS",),
            sprint_length_weeks=2,
            target_sprints=4,
            risks=("Tight timeline",),
            out_of_scope=("Mobile app",),
            assumptions=("Default velocity",),
        )

    def test_creation(self, analysis: ProjectAnalysis):
        assert analysis.project_name == "Todo App"
        assert analysis.sprint_length_weeks == 2
        assert analysis.target_sprints == 4
        assert len(analysis.goals) == 2

    def test_frozen(self, analysis: ProjectAnalysis):
        with pytest.raises(FrozenInstanceError):
            analysis.project_name = "mutated"  # type: ignore[misc]

    def test_asdict(self, analysis: ProjectAnalysis):
        d = asdict(analysis)
        assert d["project_name"] == "Todo App"
        assert isinstance(d["goals"], (list, tuple))
        assert d["sprint_length_weeks"] == 2

    def test_tuple_fields_immutable(self, analysis: ProjectAnalysis):
        """Tuple fields should be immutable (no append)."""
        assert isinstance(analysis.goals, tuple)
        assert isinstance(analysis.tech_stack, tuple)

    def test_skip_features_defaults_false(self):
        """skip_features should default to False when not specified."""
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
        assert analysis.skip_features is False

    def test_skip_features_true(self):
        """skip_features=True should be stored correctly."""
        analysis = ProjectAnalysis(
            project_name="Tiny API",
            project_description="A small REST API",
            project_type="greenfield",
            goals=("Build API",),
            end_users=("developers",),
            target_state="Deployed",
            tech_stack=("Python",),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=1,
            risks=(),
            out_of_scope=(),
            assumptions=(),
            skip_features=True,
        )
        assert analysis.skip_features is True

    def test_prompt_quality_defaults_none(self, analysis: ProjectAnalysis):
        """prompt_quality should default to None when not specified."""
        assert analysis.prompt_quality is None

    def test_prompt_quality_attached(self):
        """ProjectAnalysis should accept a PromptQualityRating."""
        quality = PromptQualityRating(
            score_pct=74,
            grade="B",
            answered_count=14,
            extracted_count=6,
            defaulted_count=4,
            skipped_count=2,
            probed_count=3,
            suggestions=("Specify your tech stack (Q11)",),
        )
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
            prompt_quality=quality,
        )
        assert analysis.prompt_quality is not None
        assert analysis.prompt_quality.grade == "B"
        assert analysis.prompt_quality.score_pct == 74


class TestPromptQualityRating:
    """Tests for the PromptQualityRating frozen dataclass."""

    def test_creation(self):
        rating = PromptQualityRating(
            score_pct=85,
            grade="A",
            answered_count=20,
            extracted_count=4,
            defaulted_count=2,
            skipped_count=0,
            probed_count=1,
            suggestions=(),
        )
        assert rating.score_pct == 85
        assert rating.grade == "A"
        assert rating.answered_count == 20

    def test_frozen(self):
        rating = PromptQualityRating(
            score_pct=50,
            grade="C",
            answered_count=10,
            extracted_count=5,
            defaulted_count=5,
            skipped_count=6,
            probed_count=0,
            suggestions=("Add more detail",),
        )
        with pytest.raises(FrozenInstanceError):
            rating.score_pct = 100  # type: ignore[misc]

    def test_asdict(self):
        rating = PromptQualityRating(
            score_pct=70,
            grade="B",
            answered_count=15,
            extracted_count=3,
            defaulted_count=4,
            skipped_count=4,
            probed_count=2,
            suggestions=("Suggestion 1",),
        )
        d = asdict(rating)
        assert d["score_pct"] == 70
        assert d["grade"] == "B"
        assert isinstance(d["suggestions"], (list, tuple))


# ── QuestionnaireState tests ──────────────────────────────────────────


class TestQuestionnaireState:
    def test_defaults(self):
        qs = QuestionnaireState()
        assert qs.current_question == 1
        assert qs.answers == {}
        assert qs.skipped_questions == set()
        assert qs.completed is False
        # PTO sub-loop fields default correctly
        assert qs._planned_leave_entries == []
        assert qs._awaiting_leave_input is False
        assert qs._leave_input_stage == ""
        assert qs._leave_input_buffer == {}

    def test_progress_empty(self):
        qs = QuestionnaireState()
        assert qs.progress == 0.0

    def test_progress_partial(self):
        # 5 answers out of 30 questions ≈ 0.1667
        qs = QuestionnaireState(answers={1: "a", 2: "b", 3: "c", 4: "d", 5: "e"})
        assert qs.progress == pytest.approx(5 / TOTAL_QUESTIONS)

    def test_progress_complete(self):
        qs = QuestionnaireState(answers={i: f"a{i}" for i in range(1, TOTAL_QUESTIONS + 1)})
        assert qs.progress == pytest.approx(1.0)

    def test_progress_with_skipped(self):
        # 3 answered + 2 skipped = 5 out of 30
        qs = QuestionnaireState(answers={1: "a", 2: "b", 3: "c"}, skipped_questions={4, 5})
        assert qs.progress == pytest.approx(5 / TOTAL_QUESTIONS)

    def test_current_phase_first(self):
        qs = QuestionnaireState(current_question=1)
        assert qs.current_phase == QuestionnairePhase.PROJECT_CONTEXT

    def test_current_phase_mid(self):
        qs = QuestionnaireState(current_question=10)
        assert qs.current_phase == QuestionnairePhase.TEAM_AND_CAPACITY

    def test_current_phase_codebase(self):
        qs = QuestionnaireState(current_question=15)
        assert qs.current_phase == QuestionnairePhase.CODEBASE_CONTEXT

    def test_current_phase_last(self):
        qs = QuestionnaireState(current_question=26)
        assert qs.current_phase == QuestionnairePhase.PREFERENCES

    def test_mutable(self):
        qs = QuestionnaireState()
        qs.current_question = 5
        qs.answers[1] = "answer"
        assert qs.current_question == 5
        assert qs.answers[1] == "answer"

    def test_skipped_questions_default(self):
        qs = QuestionnaireState()
        assert qs.skipped_questions == set()

    def test_skipped_questions_mutable(self):
        qs = QuestionnaireState()
        qs.skipped_questions.add(3)
        qs.skipped_questions.add(7)
        assert qs.skipped_questions == {3, 7}

    def test_probed_questions_default_empty(self):
        """probed_questions should default to an empty set."""
        qs = QuestionnaireState()
        assert qs.probed_questions == set()

    def test_probed_questions_mutable(self):
        """probed_questions should be mutable (add/remove elements)."""
        qs = QuestionnaireState()
        qs.probed_questions.add(1)
        qs.probed_questions.add(5)
        assert qs.probed_questions == {1, 5}

    def test_probed_questions_does_not_affect_progress(self):
        """Probing a question should NOT change progress — only answers and skips count."""
        qs = QuestionnaireState(answers={1: "a", 2: "b"}, probed_questions={1, 2})
        assert qs.progress == pytest.approx(2 / TOTAL_QUESTIONS)

    def test_defaulted_questions_default_empty(self):
        """defaulted_questions should default to an empty set."""
        qs = QuestionnaireState()
        assert qs.defaulted_questions == set()

    def test_defaulted_questions_mutable(self):
        """defaulted_questions should be mutable (add/remove elements)."""
        qs = QuestionnaireState()
        qs.defaulted_questions.add(5)
        qs.defaulted_questions.add(8)
        assert qs.defaulted_questions == {5, 8}

    def test_defaulted_questions_dont_double_count_progress(self):
        """Defaulted questions store their default in answers — progress counts the answer, not the default marker."""
        # Q5 defaulted (answer stored), Q1 answered normally → progress = 2/30
        qs = QuestionnaireState(answers={1: "a", 5: "No hard deadlines"}, defaulted_questions={5})
        assert qs.progress == pytest.approx(2 / TOTAL_QUESTIONS)

    def test_editing_question_defaults_none(self):
        """editing_question should default to None."""
        qs = QuestionnaireState()
        assert qs.editing_question is None

    def test_editing_question_mutable(self):
        """editing_question should be mutable (set and clear)."""
        qs = QuestionnaireState()
        qs.editing_question = 6
        assert qs.editing_question == 6
        qs.editing_question = None
        assert qs.editing_question is None

    def test_awaiting_confirmation_defaults_false(self):
        """awaiting_confirmation should default to False."""
        qs = QuestionnaireState()
        assert qs.awaiting_confirmation is False

    def test_awaiting_confirmation_mutable(self):
        """awaiting_confirmation should be mutable."""
        qs = QuestionnaireState()
        qs.awaiting_confirmation = True
        assert qs.awaiting_confirmation is True

    def test_phase_ranges_cover_all_questions(self):
        covered = set()
        for start, end in PHASE_QUESTION_RANGES.values():
            covered.update(range(start, end + 1))
        assert covered == set(range(1, TOTAL_QUESTIONS + 1))


# ── ScrumState TypedDict tests ────────────────────────────────────────


class TestScrumState:
    def test_required_keys(self):
        assert "messages" in ScrumState.__required_keys__

    def test_optional_keys_present(self):
        optional = ScrumState.__optional_keys__
        for key in (
            "project_name",
            "features",
            "stories",
            "tasks",
            "sprints",
            "questionnaire",
            "output_format",
            "project_analysis",
            "repo_context",
            "user_context",
        ):
            assert key in optional

    def test_repo_context_accepts_string(self):
        """repo_context should accept a raw repo scan string."""
        state: ScrumState = {
            "messages": [],
            "repo_context": "## File Tree\n- src/\n- README.md",
        }
        assert state["repo_context"] == "## File Tree\n- src/\n- README.md"

    def test_user_context_accepts_string(self):
        """user_context should accept free-form SCRUM.md content."""
        state: ScrumState = {
            "messages": [],
            "user_context": "# My Project\nWe use React + FastAPI. See https://docs.example.com",
        }
        assert "React" in state["user_context"]

    def test_minimal_creation(self):
        state: ScrumState = {"messages": []}
        assert state["messages"] == []

    def test_full_creation(self):
        state: ScrumState = {
            "messages": [HumanMessage(content="hi")],
            "project_name": "My Project",
            "features": [],
            "stories": [],
            "tasks": [],
            "sprints": [],
            "team_size": 5,
            "sprint_length_weeks": 2,
        }
        assert state["project_name"] == "My Project"
        assert state["team_size"] == 5


class TestPendingReview:
    """Tests for the pending_review field on ScrumState."""

    def test_pending_review_accepts_string(self):
        """pending_review should accept a node name string."""
        state: ScrumState = {
            "messages": [HumanMessage(content="hi")],
            "pending_review": "feature_generator",
        }
        assert state["pending_review"] == "feature_generator"

    def test_pending_review_with_review_decision(self):
        """pending_review should work alongside review decision fields."""
        state: ScrumState = {
            "messages": [HumanMessage(content="hi")],
            "pending_review": "story_writer",
            "last_review_decision": ReviewDecision.REJECT,
            "last_review_feedback": "need more detail",
        }
        assert state["pending_review"] == "story_writer"
        assert state["last_review_decision"] == ReviewDecision.REJECT


class TestQuestionnaireIntakeMode:
    """Tests for new intake_mode, extracted_questions, and _pending_merged_questions fields."""

    def test_intake_mode_default(self):
        qs = QuestionnaireState()
        assert qs.intake_mode == "standard"

    def test_extracted_questions_default(self):
        qs = QuestionnaireState()
        assert qs.extracted_questions == set()

    def test_pending_merged_questions_default(self):
        qs = QuestionnaireState()
        assert qs._pending_merged_questions == []

    def test_follow_up_choices_default(self):
        qs = QuestionnaireState()
        assert qs._follow_up_choices == {}

    def test_intake_mode_can_be_set(self):
        qs = QuestionnaireState(intake_mode="smart")
        assert qs.intake_mode == "smart"


class TestStateGraphCompatibility:
    def test_stategraph_accepts_scrum_state(self):
        """LangGraph's StateGraph must accept ScrumState without errors."""
        graph = StateGraph(ScrumState)
        assert graph is not None


# ── _merge_dicts reducer ────────────────────────────────────────────────


class TestMergeDicts:
    def test_merges_two_dicts(self):
        assert _merge_dicts({"a": "1"}, {"b": "2"}) == {"a": "1", "b": "2"}

    def test_b_overwrites_on_collision(self):
        assert _merge_dicts({"a": "old"}, {"a": "new"}) == {"a": "new"}

    def test_empty_a(self):
        assert _merge_dicts({}, {"x": "y"}) == {"x": "y"}

    def test_empty_b(self):
        assert _merge_dicts({"x": "y"}, {}) == {"x": "y"}

    def test_both_empty(self):
        assert _merge_dicts({}, {}) == {}

    def test_does_not_mutate_inputs(self):
        a = {"k": "v"}
        b = {"k2": "v2"}
        result = _merge_dicts(a, b)
        assert a == {"k": "v"}
        assert b == {"k2": "v2"}
        assert result == {"k": "v", "k2": "v2"}


# ── Jira key mapping state fields ───────────────────────────────────────


class TestJiraKeyMappingFields:
    def test_jira_feature_keys_field_present_in_scrumstate(self):
        # ScrumState is a TypedDict — check the field is declared (via __annotations__).
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_feature_keys" in all_annotations

    def test_jira_story_keys_field_present_in_scrumstate(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_story_keys" in all_annotations

    def test_jira_task_keys_field_present_in_scrumstate(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_task_keys" in all_annotations

    def test_jira_sprint_keys_field_present_in_scrumstate(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_sprint_keys" in all_annotations

    def test_jira_epic_key_field_present_in_scrumstate(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_epic_key" in all_annotations

    def test_jira_task_keys_uses_merge_dicts_reducer(self):
        """jira_task_keys should merge via _merge_dicts like the other Jira dict fields."""
        a = {"t1": "PROJ-10"}
        b = {"t2": "PROJ-11"}
        assert _merge_dicts(a, b) == {"t1": "PROJ-10", "t2": "PROJ-11"}

    def test_jira_sprint_keys_uses_merge_dicts_reducer(self):
        a = {"s1": "42"}
        b = {"s2": "43"}
        assert _merge_dicts(a, b) == {"s1": "42", "s2": "43"}

    def test_stategraph_accepts_jira_key_fields(self):
        """StateGraph must compile without errors when ScrumState has Jira dict fields."""
        graph = StateGraph(ScrumState)
        assert graph is not None


# ── Azure DevOps key mapping state fields ──────────────────────────────


class TestAzDevOpsKeyMappingFields:
    def test_azdevops_epic_id_field_present(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "azdevops_epic_id" in all_annotations

    def test_azdevops_story_keys_field_present(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "azdevops_story_keys" in all_annotations

    def test_azdevops_task_keys_field_present(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "azdevops_task_keys" in all_annotations

    def test_azdevops_iteration_keys_field_present(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "azdevops_iteration_keys" in all_annotations

    def test_azdevops_dict_fields_use_merge_dicts_reducer(self):
        """azdevops dict fields should merge via _merge_dicts."""
        a = {"s1": "100"}
        b = {"s2": "101"}
        assert _merge_dicts(a, b) == {"s1": "100", "s2": "101"}

    def test_stategraph_accepts_azdevops_key_fields(self):
        """StateGraph must compile without errors when ScrumState has Azure DevOps dict fields."""
        graph = StateGraph(ScrumState)
        assert graph is not None
