"""State schema for the LangGraph scrum agent.

Defines enums, artifact dataclasses, questionnaire state, and the main
ScrumState TypedDict that all graph nodes read from and write to.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Priority(StrEnum):
    """Priority levels for features and stories."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class StoryPointValue(IntEnum):
    """Allowed Fibonacci story-point values."""

    ONE = 1
    TWO = 2
    THREE = 3
    FIVE = 5
    EIGHT = 8


class Discipline(StrEnum):
    """Discipline tag for stories — indicates which team skillset owns the story.

    # See README: "Scrum Standards" — discipline tagging
    #
    # Used to classify each story by the primary skillset needed to implement it.
    # The LLM prompt asks for a discipline field; if missing or invalid,
    # _infer_discipline() in nodes.py guesses from keywords. Default is FULLSTACK
    # (the safe catch-all when discipline is unclear).
    """

    FRONTEND = "frontend"
    BACKEND = "backend"
    FULLSTACK = "fullstack"
    INFRASTRUCTURE = "infrastructure"
    DESIGN = "design"
    TESTING = "testing"


class QuestionnairePhase(StrEnum):
    """High-level phases that map to question ranges.

    Seven phases matching the intake questionnaire design in the README.
    Each phase groups related questions to create a natural conversation flow.
    # See README: "Scrum Standards" → questionnaire phases
    """

    PROJECT_CONTEXT = "project_context"  # Q1–Q5: project name, description, goals, users, scope
    TEAM_AND_CAPACITY = "team_and_capacity"  # Q6–Q10: team size, roles, velocity, sprint length
    TECHNICAL_CONTEXT = "technical_context"  # Q11–Q14: tech stack, architecture, integrations, constraints
    CODEBASE_CONTEXT = "codebase_context"  # Q15–Q20: repo URL, existing code, testing, CI/CD, docs
    RISKS_AND_UNKNOWNS = "risks_and_unknowns"  # Q21–Q23: risks, dependencies, unknowns
    PREFERENCES = "preferences"  # Q24–Q26: output format, naming conventions, process preferences
    CAPACITY_PLANNING = "capacity_planning"  # Q27–Q30: bank holidays, leave, unplanned %, onboarding


class TaskLabel(StrEnum):
    """Label classifying sub-tasks by the type of work involved.

    Auto-assigned by the task decomposer prompt based on task content.
    Used in REPL tables and TUI renderers to visually distinguish task types.
    # See README: "Scrum Standards" — task decomposition
    """

    CODE = "Code"
    DOCUMENTATION = "Documentation"
    INFRASTRUCTURE = "Infrastructure"
    TESTING = "Testing"


class ReviewDecision(StrEnum):
    """Possible outcomes when the user reviews generated artifacts."""

    ACCEPT = "accept"
    EDIT = "edit"
    REJECT = "reject"


class OutputFormat(StrEnum):
    """Supported export formats."""

    JIRA = "jira"
    MARKDOWN = "markdown"
    BOTH = "both"


# ---------------------------------------------------------------------------
# Phase-to-question mapping
# ---------------------------------------------------------------------------

PHASE_QUESTION_RANGES: dict[QuestionnairePhase, tuple[int, int]] = {
    QuestionnairePhase.PROJECT_CONTEXT: (1, 5),
    QuestionnairePhase.TEAM_AND_CAPACITY: (6, 10),
    QuestionnairePhase.TECHNICAL_CONTEXT: (11, 14),
    QuestionnairePhase.CODEBASE_CONTEXT: (15, 20),
    QuestionnairePhase.RISKS_AND_UNKNOWNS: (21, 23),
    QuestionnairePhase.PREFERENCES: (24, 26),
    QuestionnairePhase.CAPACITY_PLANNING: (27, 30),
}

TOTAL_QUESTIONS = 30

# ---------------------------------------------------------------------------
# Artifact dataclasses (frozen / immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcceptanceCriterion:
    """A single Given/When/Then acceptance criterion."""

    given: str
    when: str
    then: str


@dataclass(frozen=True)
class Feature:
    """A high-level feature grouping related user stories."""

    id: str
    title: str
    description: str
    priority: Priority


# Definition of Done — standard checklist applied to every user story.
# The LLM evaluates which items apply to each story and marks the rest as N/A.
# Rendered in the story table with strikethrough for non-applicable items.
# See README: "Scrum Standards" — Definition of Done
DOD_ITEMS: tuple[str, ...] = (
    "Acceptance Criteria Met",
    "Documentation",
    "Proper Testing",
    "Code Merged to Main",
    "Released via SDLC",
    "Stakeholder Sign-off",
    "Knowledge Sharing",
)


@dataclass(frozen=True)
class UserStory:
    """A user story following the persona/goal/benefit template."""

    id: str
    feature_id: str
    persona: str
    goal: str
    benefit: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    story_points: StoryPointValue
    priority: Priority
    # Short summary title for the story, e.g. "Create Bookmark Endpoint".
    # Displayed in sprint views and used as headings in exports.
    # Default "" ensures backward compatibility with existing saved sessions.
    title: str = ""
    # Discipline tag — which team skillset owns this story.
    # Default is FULLSTACK so existing code (and fallback stories) work without changes.
    # See README: "Scrum Standards" — discipline tagging
    discipline: Discipline = Discipline.FULLSTACK
    # Definition of Done flags — one bool per DOD_ITEMS entry.
    # True = applies to this story, False = not applicable (shown with strikethrough).
    # Default all-True so existing tests and fallback stories work without changes.
    dod_applicable: tuple[bool, ...] = (True, True, True, True, True, True, True)
    # LLM's reasoning for the story point estimate — explains what complexity,
    # uncertainty, or effort factors led to the assigned value. Used to calibrate
    # the AI's estimation against engineer expectations over time.
    points_rationale: str = ""
    # Confidence that the point estimate matches the team's historical data.
    # "high" (≥15 samples), "medium" (≥5), "low" (<5), "" (no data).
    points_confidence: str = ""

    @property
    def text(self) -> str:
        """Standard user-story sentence."""
        return f"As a {self.persona}, I want to {self.goal}, so that {self.benefit}."


@dataclass(frozen=True)
class Task:
    """A concrete implementation task tied to a user story."""

    id: str
    story_id: str
    title: str
    description: str
    # Auto-assigned by the task decomposer based on task content.
    # Default is CODE — the most common task type. The LLM picks the label
    # from the TaskLabel enum; the parser falls back to CODE if invalid.
    # See README: "Scrum Standards" — task decomposition
    label: TaskLabel = TaskLabel.CODE
    # Auto-generated test plan for tasks labelled Code or Infrastructure.
    # Lists what to test (unit, integration, edge cases) so developers know
    # what verification is expected. Empty string for non-code tasks.
    # See README: "Scrum Standards" — task decomposition, testing
    test_plan: str = ""
    # Self-contained instruction for AI coding assistants (Cursor, Claude Code,
    # GitHub Copilot). Includes project context, tech stack, and specific guidance
    # so a developer can paste it directly into an AI tool and start working.
    # See README: "Scrum Standards" — task decomposition
    ai_prompt: str = ""


@dataclass(frozen=True)
class Sprint:
    """A sprint containing a subset of stories."""

    id: str
    name: str
    goal: str
    capacity_points: int
    story_ids: tuple[str, ...]


# See README: "Scrum Standards" — prompt quality rating
@dataclass(frozen=True)
class PromptQualityRating:
    """Deterministic quality score for the user's intake questionnaire input.

    Computed purely from QuestionnaireState tracking sets (no LLM call).
    Displayed on the analysis review screen alongside assumptions.

    Scoring: 7 essential questions (Q1-Q4, Q6, Q11, Q15) worth 5 pts each,
    19 other questions worth 2 pts each, plus 1 pt per probed question.
    Answered/extracted = full points, defaulted = 40%, skipped = 0.
    """

    score_pct: int  # 0-100 percentage
    grade: str  # A, B, C, or D
    answered_count: int
    extracted_count: int
    defaulted_count: int
    skipped_count: int
    probed_count: int
    suggestions: tuple[str, ...]
    low_confidence_areas: tuple[str, ...] = ()  # QUESTION_SHORT_LABELS for defaulted essentials


# See README: "Scrum Standards" — project analysis
@dataclass(frozen=True)
class ProjectAnalysis:
    """Structured synthesis of all 30 intake answers.

    Produced once by the project_analyzer node after the user confirms the
    questionnaire. Downstream nodes (feature_generator, story_writer, sprint_planner)
    read this instead of re-parsing raw conversation history.

    Frozen (immutable) — same pattern as Feature, UserStory, Task, Sprint.
    Uses tuple[str, ...] for list fields (same pattern as Sprint.story_ids).
    """

    project_name: str
    project_description: str
    project_type: str  # "greenfield", "existing codebase", etc.
    goals: tuple[str, ...]
    end_users: tuple[str, ...]
    target_state: str  # What "done" looks like
    tech_stack: tuple[str, ...]
    integrations: tuple[str, ...]
    constraints: tuple[str, ...]
    sprint_length_weeks: int
    target_sprints: int
    risks: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    assumptions: tuple[str, ...]  # Defaults/skipped answers flagged
    # When True, the project is small enough for a single feature instead of 3-6.
    # The analyzer LLM sets this based on project scope (guideline: target_sprints ≤ 2
    # AND goals ≤ 3). Default False so existing projects are unaffected.
    # See README: "Scrum Standards" — feature generation
    skip_features: bool = False
    scrum_md_contributions: tuple[str, ...] = ()  # JSON field names enriched by SCRUM.md
    # Deterministic quality rating for the user's intake input. Computed by
    # compute_prompt_quality() in nodes.py from QuestionnaireState tracking sets.
    # None until the project_analyzer node runs. Displayed on the analysis review screen.
    prompt_quality: PromptQualityRating | None = None


# ---------------------------------------------------------------------------
# Questionnaire state (mutable — updated incrementally by intake node)
# ---------------------------------------------------------------------------


@dataclass
class QuestionnaireState:
    """Tracks progress through the 30-question intake flow.

    The questionnaire has 7 phases (see QuestionnairePhase). As the intake node
    runs, it updates current_question, records answers, and optionally marks
    questions as skipped (e.g. when the initial project description already
    covers them, or the user explicitly skips).

    Both answered and skipped questions count toward progress so the progress
    bar reflects true forward movement through the questionnaire.
    """

    current_question: int = 1
    answers: dict[int, str] = field(default_factory=dict)
    # Tracks questions the agent auto-skipped (already answered in the initial
    # description) or the user explicitly skipped. Needed for adaptive skip
    # logic — see TODO Phase 4: "Implement adaptive skip logic".
    skipped_questions: set[int] = field(default_factory=set)
    # Stores LLM-extracted answers from the initial project description as
    # confirmable suggestions. Instead of silently skipping extracted questions,
    # each is presented with its suggestion so the user can press Enter/Y to
    # confirm or type a different answer. Cleared per-question once confirmed.
    # See README: "Project Intake Questionnaire" — adaptive skip logic
    suggested_answers: dict[int, str] = field(default_factory=dict)
    # Tracks which questions have already been probed with a follow-up.
    # Max 1 follow-up per question — if the answer is still vague after
    # probing, accept it and move on.
    # See README: "Project Intake Questionnaire" — follow-up probing
    probed_questions: set[int] = field(default_factory=set)
    # Tracks which questions used a sensible default (user said "skip" / "I don't
    # know"). Needed to flag assumptions in the intake summary. Defaulted questions
    # have an entry in `answers` (the default value) so they don't affect progress
    # calculation — progress counts answer keys and skipped questions as usual.
    # See README: "Project Intake Questionnaire" — adaptive behavior
    defaulted_questions: set[int] = field(default_factory=set)
    completed: bool = False
    # True after the last question is answered but before the user confirms
    # the summary. The intake node re-shows the summary until the user types
    # "confirm" (or similar). Only then does completed flip to True.
    # See README: "Project Intake Questionnaire" — confirmation gate
    awaiting_confirmation: bool = False
    # Tracks which question the user is currently editing (via "Q6" or "edit Q6"
    # from the confirmation summary). Separate from current_question to avoid
    # corrupting the forward-progress model. None when not editing.
    # See README: "Project Intake Questionnaire" — edit flow
    editing_question: int | None = None
    # Intake mode — controls how many questions are shown interactively.
    # "standard" (default) preserves backward compat for existing tests.
    # The REPL explicitly sets "smart" (the new default UX).
    # See README: "Project Intake Questionnaire" — smart intake
    intake_mode: str = "standard"  # "smart" | "standard" | "quick"
    # Tracks which question numbers had answers auto-applied from the
    # initial description (via LLM extraction). Used for provenance
    # markers in the intake summary ("from your description").
    extracted_questions: set[int] = field(default_factory=set)
    # Transient: when asking a merged question (e.g. Q3+Q4 combined),
    # this tracks which question numbers the current prompt covers.
    # Cleared after the answer is recorded.
    _pending_merged_questions: list[int] = field(default_factory=list)
    # Transient: LLM-generated choices for follow-up probes on vague answers.
    # Maps question number → tuple of 2-4 option strings. The REPL renders
    # these as a numbered menu so the user can pick instead of typing.
    # Cleared after the follow-up answer is recorded (same lifecycle as probed_questions).
    # See README: "Project Intake Questionnaire" — follow-up probing
    _follow_up_choices: dict[int, tuple[str, ...]] = field(default_factory=dict)
    # Transient: bank holiday count auto-detected during Q27 processing.
    # Stored here so _extract_capacity_deductions can read it at confirmation time
    # and populate capacity_bank_holiday_days in ScrumState.
    # See README: "Scrum Standards" — capacity planning
    _detected_bank_holiday_days: int = 0
    # Transient: structured holiday data from get_bank_holidays_structured().
    # Each dict has {"date": date, "name": str, "weekday": str}.
    # Used by _compute_per_sprint_velocities to map holidays to sprint windows
    # so only the sprints that contain bank holidays get reduced velocity.
    _detected_bank_holidays: list[dict] = field(default_factory=list)
    # Transient: user's velocity override from the confirmation gate velocity
    # accept/override choice menu. None means the computed velocity was accepted.
    # See README: "Scrum Standards" — capacity planning
    _velocity_override: int | None = None
    # Transient: True when the user picked "Override" from the velocity choice
    # menu and we're waiting for them to enter a custom number.
    _awaiting_velocity_input: bool = False
    # Transient: per-developer velocity from Jira (team avg / team size).
    # Stored so that Q6 changes at the confirmation gate trigger recomputation
    # of the feature velocity (per_dev × feature_team_size).
    # See README: "Scrum Standards" — capacity planning
    _jira_per_dev_velocity: float | None = None
    # Transient: PTO/planned leave entries collected via the leave sub-loop.
    # Each entry: {"person": str, "start_date": str (ISO), "end_date": str (ISO), "working_days": int}
    # PTO is per-person (unlike bank holidays which affect the whole team).
    # See README: "Scrum Standards" — capacity planning
    _planned_leave_entries: list[dict] = field(default_factory=list)
    # Transient: True when in the PTO collection sub-loop after Q28.
    _awaiting_leave_input: bool = False
    # Transient: current stage of the leave sub-loop state machine.
    # Stages: "ask", "person", "start", "end", "more?"
    _leave_input_stage: str = ""
    # Transient: partial entry being built during the leave sub-loop.
    _leave_input_buffer: dict = field(default_factory=dict)
    # Transient: active sprint number from Jira (e.g. 104). Used to compute
    # the start date offset when the user selects a future sprint (e.g. Sprint 107).
    # Set during Q27 processing; None when Jira is not configured.
    _active_sprint_number: int | None = None
    # Transient: active sprint start date from Jira (ISO string, e.g. "2026-03-02").
    # Used with _active_sprint_number to compute exact start dates for future sprints.
    _active_sprint_start_date: str | None = None
    # Transient: total Jira org team size (unique assignees from closed sprints).
    # Used to cap the "increase team" recommendation so we never suggest more
    # engineers than exist on the board. Set even when velocity is zero.
    _jira_org_team_size: int | None = None
    # Transient: tracks which question numbers were auto-populated from SCRUM.md
    # content (as opposed to the user's typed description). Used for provenance
    # markers in the intake preamble ("N from SCRUM.md").
    _scrum_md_questions: set[int] = field(default_factory=set)
    # Unified answer provenance — maps question number to AnswerSource value.
    # Populated alongside the existing tracking sets (extracted_questions,
    # defaulted_questions, probed_questions) for backward compatibility.
    # See README: "Project Intake Questionnaire" — answer confidence signalling
    answer_sources: dict[int, str] = field(default_factory=dict)
    # Transient: preferred tracker for velocity/sprint data when both Jira and
    # Azure DevOps are configured. Set by the user at the start of intake via
    # a choice prompt. Values: "jira", "azdevops", or "" (not yet chosen).
    # When only one tracker is configured, this is ignored.
    _preferred_tracker: str = ""
    # Transient: True when waiting for the user to pick a tracker (before Q1).
    _awaiting_tracker_choice: bool = False

    @property
    def current_phase(self) -> QuestionnairePhase:
        """Return the phase that the current question belongs to."""
        for phase, (start, end) in PHASE_QUESTION_RANGES.items():
            if start <= self.current_question <= end:
                return phase
        return QuestionnairePhase.PREFERENCES  # clamp to last phase

    @property
    def progress(self) -> float:
        """Return completion ratio from 0.0 to 1.0.

        Both answered and skipped questions count toward progress so the
        progress bar reflects true forward movement through the questionnaire.
        Uses a union of answer keys and skipped questions to avoid double-
        counting questions that were auto-extracted (present in both sets).
        """
        completed_questions = set(self.answers.keys()) | self.skipped_questions
        return len(completed_questions) / TOTAL_QUESTIONS


# ---------------------------------------------------------------------------
# ScrumState TypedDict (LangGraph graph state)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Custom state reducers
# ---------------------------------------------------------------------------


def _merge_dicts(a: dict, b: dict) -> dict:
    """Merge two dicts, with b's values overwriting a's on key collisions.

    Used as the reducer for Jira key-mapping dicts in ScrumState so that each
    node can return only the new mappings it created (a partial dict) and
    LangGraph merges them into the running total — the same append-semantics
    pattern that operator.add provides for list fields like `features` and `stories`.
    # See README: "Memory & State" — reducers, Annotated fields
    """
    return {**a, **b}


class _RequiredState(TypedDict):
    """Keys that must always be present in the state."""

    messages: Annotated[list[BaseMessage], add_messages]


class ScrumState(_RequiredState, total=False):
    """Full scrum-agent graph state.

    `messages` is required (inherited); everything else is optional and
    populated progressively as the agent runs through its nodes.
    """

    # Project metadata
    project_name: str
    project_description: str

    # Questionnaire
    questionnaire: QuestionnaireState
    # Intake mode — passed from REPL to the intake node on first invocation.
    # Stored as a ScrumState field so LangGraph doesn't strip it.
    # See README: "Project Intake Questionnaire" — smart intake
    _intake_mode: str

    # Project analysis — structured synthesis of intake answers.
    # Set once by project_analyzer node; no reducer needed (single value).
    project_analysis: ProjectAnalysis

    # Artifacts (append-semantics via operator.add)
    features: Annotated[list[Feature], operator.add]
    stories: Annotated[list[UserStory], operator.add]
    tasks: Annotated[list[Task], operator.add]
    sprints: Annotated[list[Sprint], operator.add]

    # Team / planning knobs
    team_size: int
    sprint_length_weeks: int
    velocity_per_sprint: int
    target_sprints: int
    # Analysis profile selected by user in planning mode profile picker.
    # When set, intake auto-fills Q6/Q8/Q9 from the profile and nodes
    # use this profile for team calibration. Empty string = no profile selected.
    analysis_profile_id: str
    # Starting sprint number — set by the sprint_selector node after fetching
    # the active Jira sprint and asking the user which sprint to plan for.
    # e.g. if active sprint is "Sprint 104" and user picks next → 105.
    # When 0 (default), sprint_planner uses generic "Sprint 1, Sprint 2, ...".
    # See README: "Scrum Standards" — sprint planning
    starting_sprint_number: int

    # Capacity override — set by sprint_planner when total story points exceed
    # what fits in the user's target sprint range (Q10).
    # See README: "Guardrails" — human-in-the-loop pattern
    #   0       → not yet checked (default)
    #   < -1    → capacity warning pending; abs(value) = recommended sprint count
    #   -1      → user rejected recommendation; proceed with original target
    #   > 0     → user accepted; use this value as the new target sprint count
    capacity_override_target: int

    # Original target sprint count — set alongside capacity_override_target
    # when a capacity overflow is detected. Lets the TUI show "Keep N sprints"
    # in the choice popup so the user knows what the original target was.
    _original_target_sprints: int

    # Recommended team size to fit scope in original sprint count — computed
    # during capacity overflow detection: ceil(total_points / (vel_per_eng × target)).
    # Displayed as option 2 in the capacity overflow choice screen.
    # See README: "Guardrails" — human-in-the-loop pattern
    _recommended_team_size: int

    # Team size override chosen by the user via the capacity overflow screen.
    # When > 0, sprint_planner recalculates velocity = vel_per_eng × this value
    # instead of using enforce_target. 0 = not set (default).
    # See README: "Guardrails" — human-in-the-loop pattern
    _capacity_team_override: int

    # Capacity deductions — all collected during intake (Phase 6: Capacity Planning).
    # Q27 (sprint selection / bank holidays auto-detected), Q28 (planned leave),
    # Q29 (unplanned %), Q30 (onboarding). Net velocity computed at intake confirmation.
    # Used by sprint_planner to compute net feature capacity (gross - deductions).
    # See README: "Scrum Standards" — capacity planning
    capacity_bank_holiday_days: int  # Total bank/public holiday days in planning window
    capacity_planned_leave_days: int  # Total planned leave days (vacation, training)
    capacity_unplanned_leave_pct: int  # Percentage lost to unplanned absences (0–100)
    capacity_onboarding_engineer_sprints: int  # Engineer-sprints lost to ramp-up
    capacity_ktlo_engineers: int  # Engineers dedicated to KTLO/BAU work (default 0)
    capacity_discovery_pct: int  # Discovery/design tax percentage (default 5)
    net_velocity_per_sprint: int  # Adjusted velocity after capacity deductions (min of per-sprint)
    velocity_source: str  # Provenance: "jira", "manual", or "estimated"
    sprint_start_date: str  # ISO date string for first sprint start (e.g. "2026-03-16")

    # Per-sprint velocity breakdown — only sprints with bank holidays or PTO get
    # reduced capacity. Each entry is a dict with keys: sprint_index (0-based),
    # bank_holiday_days, bank_holiday_names (list[str]), pto_days, pto_entries,
    # net_velocity. When empty, the flat net_velocity_per_sprint is used everywhere.
    # See README: "Scrum Standards" — capacity planning
    sprint_capacities: list[dict]

    # Structured per-person leave entries — persisted for rendering in exports
    # and TUI. Each entry: {"person": str, "start_date": str, "end_date": str,
    # "working_days": int}. PTO is per-person (1 × days), unlike bank holidays
    # (team_size × days). See README: "Scrum Standards" — capacity planning
    planned_leave_entries: list[dict]

    # Repo context — raw string from tool scan, populated by project_analyzer
    # and read by epic_generator. None if no URL was provided or scan failed.
    repo_context: str

    # Confluence context — concatenated plain-text content from confluence_search_docs
    # and confluence_read_page tool calls during the intake phase. Populated by the
    # agent as it reads relevant docs; surfaced in the project_analyzer prompt alongside
    # repo context. Empty string if no Confluence tools were called.
    # See README: "Tools" — tool types, read-only tool pattern
    confluence_context: str

    # User-provided context from SCRUM.md — free-form markdown the user places in
    # their project root (URLs, design notes, screenshots as links, tech decisions,
    # team conventions). Read once by project_analyzer; injected into the prompt so
    # the LLM can ground analysis in the user's own documentation.
    user_context: str

    # Review loop
    # See README: "Guardrails" — human-in-the-loop pattern
    # pending_review holds the name of the generation node awaiting user review
    # (e.g. "feature_generator"). When set, the REPL intercepts user input and
    # routes it through the [Accept / Edit / Reject] flow instead of invoking
    # the graph. Cleared after the user makes a decision.
    pending_review: str
    last_review_decision: ReviewDecision
    last_review_feedback: str

    # Output
    output_format: OutputFormat

    # Context source diagnostics — populated by project_analyzer to show the user
    # which external sources (repo scan, Confluence, SCRUM.md) were used, skipped,
    # or failed. Each entry is a dict with keys: name, status, detail.
    # Rendered by the REPL after the analysis panel for transparency.
    context_sources: list[dict]

    # Jira key mappings — populated after jira_create_epic / jira_create_story calls.
    # jira_feature_keys: maps internal feature IDs → Jira Epic keys (e.g. "PROJ-5").
    # jira_story_keys: maps internal story IDs → Jira story keys.
    # jira_task_keys: maps internal task IDs → Jira sub-task keys.
    # jira_sprint_keys: maps internal sprint IDs → Jira sprint IDs.
    # jira_epic_key: single project-level Epic key (e.g. "PROJ-42").
    # The _merge_dicts reducer appends new entries without overwriting existing ones,
    # so each node/tool call can return only the mappings it just created.
    # See README: "Tools" — tool types, write tools, human-in-the-loop pattern
    jira_feature_keys: Annotated[dict[str, str], _merge_dicts]
    jira_story_keys: Annotated[dict[str, str], _merge_dicts]
    jira_task_keys: Annotated[dict[str, str], _merge_dicts]
    jira_sprint_keys: Annotated[dict[str, str], _merge_dicts]
    jira_epic_key: str

    # Azure DevOps key mappings — populated after azdevops_create_epic / azdevops_create_story calls.
    # azdevops_epic_id: project-level Epic work item ID.
    # azdevops_story_keys: maps internal story IDs → AzDO work item IDs.
    # azdevops_task_keys: maps internal task IDs → AzDO work item IDs.
    # azdevops_iteration_keys: maps internal sprint IDs → AzDO iteration paths.
    # The _merge_dicts reducer appends new entries without overwriting existing ones,
    # so each node/tool call can return only the mappings it just created.
    # See README: "Tools" — tool types, write tools, human-in-the-loop pattern
    azdevops_epic_id: str
    azdevops_story_keys: Annotated[dict[str, str], _merge_dicts]
    azdevops_task_keys: Annotated[dict[str, str], _merge_dicts]
    azdevops_iteration_keys: Annotated[dict[str, str], _merge_dicts]
