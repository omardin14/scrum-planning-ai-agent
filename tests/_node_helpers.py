"""Shared test helpers and JSON fixtures for node tests.

Extracted from test_nodes.py during the test reorganisation (Phase 12).
These are plain functions and constants, not pytest fixtures.
"""

from scrum_agent.agent.state import (
    TOTAL_QUESTIONS,
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    Sprint,
    StoryPointValue,
    UserStory,
)

# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_completed_questionnaire() -> QuestionnaireState:
    """Create a completed questionnaire with all 26 answers for analyzer tests."""
    qs = QuestionnaireState(completed=True, current_question=TOTAL_QUESTIONS + 1)
    for i in range(1, TOTAL_QUESTIONS + 1):
        qs.answers[i] = f"Answer for Q{i}"
        qs.answer_sources[i] = "direct"
    return qs


def make_dummy_analysis(**overrides: object) -> ProjectAnalysis:
    """Create a ProjectAnalysis with sensible defaults for tests."""
    defaults = {
        "project_name": "Test Project",
        "project_description": "A test project for unit tests",
        "project_type": "greenfield",
        "goals": ("Build a widget",),
        "end_users": ("developers",),
        "target_state": "Deployed to production",
        "tech_stack": ("Python", "FastAPI"),
        "integrations": ("GitHub API",),
        "constraints": ("Must use AWS",),
        "sprint_length_weeks": 2,
        "target_sprints": 3,
        "risks": ("Tight timeline",),
        "out_of_scope": ("Mobile app",),
        "assumptions": ("Default velocity assumed",),
    }
    defaults.update(overrides)
    return ProjectAnalysis(**defaults)


def make_sample_features() -> list[Feature]:
    """Create a sample list of features for story writer tests."""
    return [
        Feature(id="F1", title="User Authentication", description="Registration, login, JWT", priority=Priority.HIGH),
        Feature(id="F2", title="Task Management", description="CRUD operations for tasks", priority=Priority.HIGH),
        Feature(id="F3", title="Dashboard", description="Responsive dashboard", priority=Priority.MEDIUM),
    ]


def make_sample_stories() -> list[UserStory]:
    """Create a sample list of stories for task decomposer tests."""
    return [
        UserStory(
            id="US-F1-001",
            feature_id="F1",
            persona="end user",
            goal="register an account",
            benefit="I can access the application",
            acceptance_criteria=(
                AcceptanceCriterion(given="on registration page", when="submit valid data", then="account created"),
            ),
            story_points=StoryPointValue.FIVE,
            priority=Priority.HIGH,
            title="User Registration",
        ),
        UserStory(
            id="US-F1-002",
            feature_id="F1",
            persona="end user",
            goal="log in to my account",
            benefit="I can access my data",
            acceptance_criteria=(
                AcceptanceCriterion(given="have an account", when="enter correct credentials", then="logged in"),
            ),
            story_points=StoryPointValue.THREE,
            priority=Priority.HIGH,
            title="User Login",
        ),
    ]


def make_sample_sprints() -> list[Sprint]:
    """Create a sample list of sprints for tests."""
    return [
        Sprint(id="SP-1", name="Sprint 1", goal="Auth foundation", capacity_points=5, story_ids=("US-F1-001",)),
        Sprint(id="SP-2", name="Sprint 2", goal="Login flow", capacity_points=3, story_ids=("US-F1-002",)),
    ]


def make_story_for_inference(
    goal: str = "do something",
    persona: str = "user",
    benefit: str = "value",
    acs: tuple[AcceptanceCriterion, ...] | None = None,
) -> UserStory:
    """Build a minimal UserStory for discipline inference tests."""
    if acs is None:
        acs = (AcceptanceCriterion(given="context", when="action", then="outcome"),)
    return UserStory(
        id="US-F1-001",
        feature_id="F1",
        persona=persona,
        goal=goal,
        benefit=benefit,
        acceptance_criteria=acs,
        story_points=StoryPointValue.THREE,
        priority=Priority.MEDIUM,
    )


def make_valid_story(
    story_id: str = "US-F1-001",
    feature_id: str = "F1",
    num_acs: int = 3,
    discipline: Discipline = Discipline.BACKEND,
) -> UserStory:
    """Build a UserStory that passes all validation checks."""
    acs = tuple(
        AcceptanceCriterion(given=f"condition {i}", when=f"action {i}", then=f"outcome {i}")
        for i in range(1, num_acs + 1)
    )
    return UserStory(
        id=story_id,
        feature_id=feature_id,
        persona="developer",
        goal="implement the feature",
        benefit="value is delivered",
        acceptance_criteria=acs,
        story_points=StoryPointValue.FIVE,
        priority=Priority.HIGH,
        discipline=discipline,
    )


# ---------------------------------------------------------------------------
# JSON fixtures — valid LLM responses for each pipeline stage
# ---------------------------------------------------------------------------

VALID_ANALYSIS_JSON = """\
{
  "project_name": "Todo App",
  "project_description": "A full-stack todo application",
  "project_type": "greenfield",
  "goals": ["Task management", "User authentication"],
  "end_users": ["developers", "project managers"],
  "target_state": "Deployed to production with CI/CD",
  "tech_stack": ["React", "FastAPI", "PostgreSQL"],
  "integrations": ["GitHub API"],
  "constraints": ["Must use AWS"],
  "sprint_length_weeks": 2,
  "target_sprints": 4,
  "risks": ["Tight timeline"],
  "out_of_scope": ["Mobile app"],
  "assumptions": ["Default velocity assumed"]
}"""

VALID_FEATURES_JSON = """\
[
  {"id": "F1", "title": "User Authentication", "description": "Registration, login, JWT", "priority": "high"},
  {"id": "F2", "title": "Task Management", "description": "CRUD operations for tasks", "priority": "high"},
  {"id": "F3", "title": "Dashboard", "description": "Responsive dashboard", "priority": "medium"},
  {"id": "F4", "title": "Infrastructure", "description": "CI/CD and deployment", "priority": "high"}
]"""

VALID_STORIES_JSON = """\
[
  {
    "id": "US-F1-001",
    "feature_id": "F1",
    "persona": "end user",
    "goal": "register an account",
    "benefit": "I can access the application",
    "acceptance_criteria": [
      {"given": "I am on the registration page", "when": "I submit valid credentials", "then": "my account is created"},
      {"given": "I am on the registration page", "when": "I submit an existing email", "then": "I see an error"},
      {"given": "I am on the registration page", "when": "I leave fields empty", "then": "validation errors show"}
    ],
    "story_points": 5,
    "priority": "high"
  },
  {
    "id": "US-F1-002",
    "feature_id": "F1",
    "persona": "end user",
    "goal": "log in to my account",
    "benefit": "I can access my data",
    "acceptance_criteria": [
      {"given": "I have an account", "when": "I enter correct credentials", "then": "I am logged in"},
      {"given": "I have an account", "when": "I enter wrong password", "then": "I see an error message"},
      {"given": "I am logged in", "when": "my session expires", "then": "I am redirected to login"}
    ],
    "story_points": 3,
    "priority": "high"
  },
  {
    "id": "US-F2-001",
    "feature_id": "F2",
    "persona": "end user",
    "goal": "create a new task",
    "benefit": "I can track my work",
    "acceptance_criteria": [
      {"given": "I am logged in", "when": "I fill out the task form", "then": "the task is created"},
      {"given": "I am logged in", "when": "I submit without a title", "then": "validation fails"},
      {"given": "I am logged in", "when": "I set a due date in the past", "then": "I see a warning"}
    ],
    "story_points": 3,
    "priority": "high"
  }
]"""

VALID_TASKS_JSON = """\
[
  {
    "id": "T-US-F1-001-01",
    "story_id": "US-F1-001",
    "title": "Create user registration API endpoint",
    "description": "Build POST /api/auth/register endpoint with email/password validation",
    "label": "Code",
    "test_plan": "Unit: POST /register returns 201, 409 for duplicate. Integration: registration e2e.",
    "ai_prompt": "You are a backend engineer on Todo App (FastAPI). Create POST /register with validation."
  },
  {
    "id": "T-US-F1-001-02",
    "story_id": "US-F1-001",
    "title": "Write tests for registration endpoint",
    "description": "Unit and integration tests for the registration flow",
    "label": "Testing",
    "test_plan": "",
    "ai_prompt": "You are a QA engineer on Todo App (pytest). Write tests for POST /register: valid, duplicate."
  },
  {
    "id": "T-US-F1-002-01",
    "story_id": "US-F1-002",
    "title": "Create login API endpoint",
    "description": "Build POST /api/auth/login endpoint with JWT token generation",
    "label": "Code",
    "test_plan": "Unit: POST /login returns JWT, 401 for invalid. Integration: login + protected route.",
    "ai_prompt": "You are a backend engineer on Todo App (FastAPI). Implement POST /login with JWT token generation."
  },
  {
    "id": "T-US-F1-002-02",
    "story_id": "US-F1-002",
    "title": "Write tests for login endpoint",
    "description": "Unit and integration tests for the login flow",
    "label": "Testing",
    "test_plan": "",
    "ai_prompt": "You are a QA engineer on Todo App (pytest). Write tests for POST /login: valid JWT, wrong password."
  }
]"""

VALID_SPRINTS_JSON = """\
[
  {
    "id": "SP-1",
    "name": "Sprint 1",
    "goal": "Establish authentication foundation",
    "capacity_points": 5,
    "story_ids": ["US-F1-001"]
  },
  {
    "id": "SP-2",
    "name": "Sprint 2",
    "goal": "Implement login functionality",
    "capacity_points": 3,
    "story_ids": ["US-F1-002"]
  }
]"""
