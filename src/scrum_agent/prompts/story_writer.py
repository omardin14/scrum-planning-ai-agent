"""Prompt template for the story_writer node.

# See README: "Prompt Construction" — ARC framework
# See README: "Scrum Standards" — story format, acceptance criteria, story points
#
# The story writer prompt takes the ProjectAnalysis fields + formatted feature list
# and asks the LLM to decompose each feature into 2-5 user stories with acceptance
# criteria, story points, and priorities.
#
# Same pattern as feature_generator.py:
# - Pre-formatted strings (not dataclass imports) to avoid circular imports
# - ARC framework: Actor, Rules, Context
# - Chain-of-thought reasoning steps
# - JSON schema embedded in the prompt
# - Constants for validation bounds
"""

# Bounds for the number of stories per feature.
# Minimum is 1 — small or focused features should stay as a single story rather
# than being artificially split. The 8-point cap handles the other direction.
MIN_STORIES_PER_FEATURE = 1
MAX_STORIES_PER_FEATURE = 5

# Maximum story points before a story should be split.
MAX_STORY_POINTS = 8

# Allowed Fibonacci story-point values — must match StoryPointValue IntEnum in agent/state.py.
# Hardcoded here (not imported) to avoid the same circular import issue as feature_generator.py.
_ALLOWED_STORY_POINTS = (1, 2, 3, 5, 8)

# Allowed priority values — must match Priority StrEnum in agent/state.py.
_ALLOWED_PRIORITIES = ("critical", "high", "medium", "low")

# Allowed discipline values — must match Discipline StrEnum in agent/state.py.
# Hardcoded here (not imported) to avoid the same circular import issue.
_ALLOWED_DISCIPLINES = ("frontend", "backend", "fullstack", "infrastructure", "design", "testing")

# ---------------------------------------------------------------------------
# Team-aware rule helpers — override defaults when calibration data is present
# ---------------------------------------------------------------------------


def _ac_count_rule(team_calibration: str) -> str:
    """Build the AC count rule, using team's median if available."""
    import re

    # Extract "Median acceptance criteria per story: N" from calibration text
    m = re.search(r"[Mm]edian acceptance criteria per story:\s*(\d+)", team_calibration)
    if m:
        median = int(m.group(1))
        return (
            f"7. Each story should have approximately {median} acceptance criteria "
            f"(matching team's median of {median}). Include at least 1 happy-path "
            "and 1 error-path scenario.\n"
        )
    return (
        "7. Each story must have at least 3 acceptance criteria:\n"
        "   - At least 1 happy-path scenario\n"
        "   - At least 1 negative/error-path scenario\n"
        "   - At least 1 edge case\n"
    )


def _ac_format_rule(team_calibration: str) -> str:
    """Build the AC format rule, respecting team's actual style."""
    # Check if team uses Given/When/Then
    if "Given/When/Then" in team_calibration or "uses_given_when_then" in team_calibration:
        return "8. Acceptance criteria must use Given/When/Then format (matching team style).\n"
    if "Writing patterns" in team_calibration:
        # Team has data but doesn't use GWT — use a flexible format
        return (
            "8. Write acceptance criteria as clear, testable statements. "
            "Use bullet points with specific expected outcomes. "
            "Do NOT use Given/When/Then format unless the team's analysis shows they use it.\n"
        )
    # Default: Given/When/Then
    return "8. Acceptance criteria must use Given/When/Then format.\n"


# ---------------------------------------------------------------------------
# JSON schema description embedded in the prompt so the LLM knows exactly
# what structure to produce. Returns a JSON *array* of story objects with
# nested acceptance_criteria arrays.
# ---------------------------------------------------------------------------

_JSON_SCHEMA = """\
[
  {
    "id": "string — sequential ID per feature: US-F1-001, US-F1-002, ...",
    "feature_id": "string — parent feature ID: F1, F2, ...",
    "title": "string — short summary of the story (3-7 words, e.g. 'Create Bookmark Endpoint')",
    "persona": "string — the user role (e.g. 'developer', 'admin', 'end user')",
    "goal": "string — what the user wants to do (verb phrase, no 'to' prefix)",
    "benefit": "string — why this matters to the user (no 'so that' prefix)",
    "acceptance_criteria": [
      {
        "given": "string — precondition or initial context",
        "when": "string — action or trigger",
        "then": "string — expected outcome"
      }
    ],
    "story_points": "integer — Fibonacci value: 1, 2, 3, 5, or 8",
    "points_rationale": "string — 1-2 sentences explaining the point value. Describe complexity, unknowns, or effort.",
    "priority": "string — one of: critical, high, medium, low",
    "discipline": "string — one of: frontend, backend, fullstack, infrastructure, design, testing",
    "dod_applicable": [true, true, true, true, true, true, true]
  }
]

The 7 booleans in dod_applicable map in order to:
  [0] Acceptance Criteria Met
  [1] Documentation
  [2] Proper Testing
  [3] Code Merged to Main
  [4] Released via SDLC
  [5] Stakeholder Sign-off
  [6] Knowledge Sharing
Set to false when the item clearly does not apply to this specific story."""


def get_story_writer_prompt(
    project_name: str,
    project_description: str,
    project_type: str,
    goals: str,
    end_users: str,
    tech_stack: str,
    constraints: str,
    features_block: str,
    *,
    out_of_scope: str = "",
    team_calibration: str = "",
    review_feedback: str | None = None,
    review_mode: str | None = None,
    previous_output: str | None = None,
) -> str:
    """Build the story writer prompt with injected project analysis and feature fields.

    # See README: "Prompt Construction" — ARC framework
    #
    # The prompt uses the ARC pattern:
    # - Actor: "Senior Scrum Master" with user story decomposition expertise
    # - Rules: 2-5 stories/feature, nested ACs, Fibonacci points, 8-point cap
    # - Context: Pre-formatted project analysis + feature list
    #
    # Why a function (not a string constant)?
    # All parameters are dynamic — they come from the ProjectAnalysis and Feature list
    # produced by earlier nodes. A function cleanly injects these into the template.

    Args:
        project_name: Project name from analysis.
        project_description: 1-2 sentence project summary.
        project_type: "greenfield", "existing codebase", etc.
        goals: Pre-formatted bullet list of project goals.
        end_users: Pre-formatted bullet list of target users.
        tech_stack: Pre-formatted bullet list of technologies.
        constraints: Pre-formatted bullet list of constraints.
        features_block: Pre-formatted text block of features (from _format_features_for_prompt).
        out_of_scope: Pre-formatted bullet list of out-of-scope items.
        review_feedback: User feedback from a previous review (reject/edit).
        review_mode: "reject" or "edit" — controls how feedback is framed.
        previous_output: Previous output text for edit mode reference.

    Returns:
        The complete prompt string ready to send to the LLM.
    """
    from scrum_agent.prompts.feature_generator import _build_review_section

    task_instruction = (
        f"Decompose each feature into {MIN_STORIES_PER_FEATURE}-{MAX_STORIES_PER_FEATURE} user stories. "
        f"Return a JSON array matching this exact schema:\n\n"
        f"```json\n{_JSON_SCHEMA}\n```\n\n"
    )
    count_rule = (
        f"1. Produce {MIN_STORIES_PER_FEATURE}-{MAX_STORIES_PER_FEATURE} stories per feature — no fewer, no more.\n"
    )
    id_rule = "2. Use sequential IDs per feature: US-F1-001, US-F1-002, US-F2-001, etc.\n"

    base = (
        "You are a Senior Scrum Master with expertise in user story decomposition.\n\n"
        "## Project Context\n\n"
        f"**Project:** {project_name}\n"
        f"**Description:** {project_description}\n"
        f"**Type:** {project_type}\n\n"
        f"### Goals\n{goals}\n\n"
        f"### End Users\n{end_users}\n\n"
        f"### Tech Stack\n{tech_stack}\n\n"
        f"### Constraints\n{constraints}\n\n"
        f"### Out of Scope\n{out_of_scope}\n\n"
        "## Features to Decompose\n\n"
        f"{features_block}\n\n" + (team_calibration + "\n" if team_calibration else "") + "## Task\n\n"
        f"{task_instruction}"
        "## Rules\n\n"
        f"{count_rule}"
        f"{id_rule}"
        "3. Follow the user story format: persona + goal + benefit.\n"
        f"4. Story points must be Fibonacci: {', '.join(str(v) for v in _ALLOWED_STORY_POINTS)}.\n"
        f"5. No story may exceed {MAX_STORY_POINTS} points — split larger stories.\n"
        f"6. Priority must be one of: {', '.join(_ALLOWED_PRIORITIES)}.\n"
        f"{_ac_count_rule(team_calibration)}"
        f"{_ac_format_rule(team_calibration)}"
        "9. Stories within a feature should not overlap — each covers a distinct slice.\n"
        "10. Give each story a short title (3-7 words) summarising the core deliverable.\n"
        "11. Inherit priority from the parent feature unless there's a reason to differ.\n"
        f"12. Tag each story with a discipline: {', '.join(_ALLOWED_DISCIPLINES)}. "
        "Use 'fullstack' if the story spans multiple disciplines or is unclear.\n"
        "13. Set dod_applicable as a 7-element boolean array. Mark false when an item clearly "
        "does not apply — for example:\n"
        "    - Non-code stories (docs, design, research): Code Merged = false, SDLC = false\n"
        "    - Small or low-risk stories: Knowledge Sharing = false\n"
        "    - Internal/automated stories: Stakeholder Sign-off = false\n"
        "    Default to true when in doubt.\n"
        "14. Do NOT create stories for items listed under Out of Scope — "
        "assume these already exist or are handled elsewhere.\n"
        "15. **Prefer fewer, meatier stories over many thin ones.** Consolidate related work "
        "into a single story when the combined effort is ≤ 8 points. Examples:\n"
        "    - Multiple API connection setups (e.g. Jenkins + Slack) → one 'Configure External API Connections' story\n"
        "    - Triggering a job and monitoring its result → one story (they're the same workflow)\n"
        "    - Generating content and formatting it → one story (formatting is not standalone)\n"
        "    - Success notifications and failure escalation → one 'Implement Notification & Escalation' story\n"
        "    Only split when the work is genuinely independent and would be reviewed/deployed separately.\n\n"
        "## Story Splitting Strategies\n\n"
        "When a story feels too large (> 8 points), split by:\n"
        "- **Workflow step:** separate creation, editing, deletion, viewing\n"
        "- **Business rule:** separate validation, authorization, notification\n"
        "- **Data type:** separate handling for different entity types\n"
        "- **Interface:** separate API endpoint, UI component, background job\n\n"
        "## Chain of Thought\n\n"
        "Think step by step for each feature:\n"
        "1. Identify the key workflows and user interactions in this feature.\n"
        "2. Draft initial story candidates — start broad, then check if any need splitting.\n"
        "3. **Consolidation check:** can any candidates be merged and still fit within 8 points? "
        "If two stories share the same persona, same system boundary, or are always done together, merge them.\n"
        "4. Write acceptance criteria covering happy path, error path, and edge cases.\n"
        "5. Estimate story points based on complexity, uncertainty, and effort.\n"
        "6. If any story exceeds 8 points, split it using the strategies above.\n"
        "7. Assign priority based on the parent feature's priority and business value.\n\n"
        "Return ONLY the JSON array, no other text."
    )

    return base + _build_review_section(review_feedback, review_mode, previous_output)
