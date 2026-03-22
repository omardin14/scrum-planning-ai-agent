# Scrum AI Agent — TODO

Comprehensive task list for building the project. Check items off as they're completed.

---

## Phase 1: Project Setup

- [x] Initialise Python project structure (`src/`, `tests/`, `pyproject.toml`)
- [x] Set up virtual environment and dependency management (Poetry or pip)
- [x] Install core dependencies: `langchain`, `langgraph`, `langchain-anthropic`, `rich`, `prompt_toolkit`
- [x] Create entry point (`src/main.py` or `scrum_agent/cli.py`)
- [x] Set up environment variable handling (`.env` for API keys)
- [x] Configure LangSmith tracing (optional, for development)
- [x] Set up `pytest` and initial test structure

---

## Phase 2: CLI Shell

- [x] Build basic terminal REPL loop (read input → process → display output)
- [x] Integrate `rich` for markdown rendering in terminal
- [x] Integrate `prompt_toolkit` for input handling (history, multiline, autocomplete)
- [x] Implement streaming output (token-by-token display)
- [x] Build welcome screen / banner
- [x] Add `--resume` flag for session resumption
- [x] Add `--help` flag with usage instructions
- [x] Handle graceful exit (Ctrl+C, `exit`, `quit`)
- [x] Add phase headers / section dividers in terminal output (e.g., `─── Phase 1: Project Context ───`)

---

## Phase 3: LangGraph Agent — Single Node + REPL Integration

- [x] Define custom `StateGraph` state schema (messages + scrum state fields)
- [x] Create LLM instance with Anthropic Claude via `langchain-anthropic`
- [x] Write system prompt with Scrum Master persona and constraints from README
- [x] Build `call_model` node
- [x] Build `should_continue` routing function
- [x] Wire basic graph: `START → agent → END`
- [x] Compile graph and test with a simple project description → epics output
- [x] Wire REPL to LangGraph agent (replace echo loop with graph invocation)
- [x] Maintain conversation history across REPL turns
- [x] Handle API errors gracefully in REPL (network, auth, unexpected)
- [x] Test REPL-graph integration (monkeypatched)
- [x] Add graph visualisation for development (`draw_mermaid_png`) add the .png to the README.md

---

## Phase 4: Project Intake Questionnaire

- [x] Design questionnaire state (which questions asked, answers collected, current phase)
- [x] Build `project_intake` node that asks questions one at a time
- [x] Implement Phase 1 questions — Project Context (Q1–Q5)
- [x] Implement Phase 2 questions — Team & Capacity (Q6–Q10)
- [x] Implement Phase 3 questions — Technical Context (Q11–Q14)
- [x] Implement Phase 3a questions — Codebase Context (Q15–Q20)
- [x] Implement Phase 4 questions — Risks & Unknowns (Q21–Q23)
- [x] Implement Phase 5 questions — Preferences & Process (Q24–Q26)
- [x] Implement adaptive skip logic (skip questions already answered in initial description)
- [x] Implement follow-up probing for vague answers
- [x] Handle "skip" and "I don't know" responses with sensible defaults
- [x] Handle "skip" and adaptive skip logic in the UI
- [x] Build intake summary output (structured project overview)
- [x] Add user confirmation step before proceeding (`[Confirm / Edit]`)
- [x] Implement edit flow — let user revise specific answers from the summary
- [x] Calculate default velocity when not provided (engineers × 5)
- [x] Ability for the user to fill in the questionnaire at their own time as a .md file, extract and process it
- [x] Ability to Export Questionnaire with Answers as .md in the end

---

## Phase 5: Multi-Node Agent Graph

- [x] Build `project_analyzer` node — extracts scope, goals, constraints from intake answers
- [x] Build `epic_generator` node — decomposes scope into epics
- [x] Build `story_writer` node — breaks epics into user stories with ACs and points
- [x] Build `task_decomposer` node — breaks stories into sub-tasks
- [x] Build `sprint_planner` node — allocates stories to sprints based on capacity
- [x] Wire full graph with conditional edges between all nodes
- [x] Add human review checkpoints after each generation node
- [x] Implement `[Accept / Edit / Reject]` flow at each checkpoint
- [x] Implement re-planning on rejection (feed user feedback back into the node)
- [x] Add graph visualisation for development (`draw_mermaid_png`) update the .png in the README.md


### Story Writer — Scrum Standards Enforcement

- [x] Enforce story format: "As a [persona], I want to [goal], so that [benefit]"
- [x] Generate acceptance criteria in Given/When/Then format
- [x] Ensure AC coverage: happy path + negative path + edge cases + error states
- [x] Enforce story points on Fibonacci scale (1, 2, 3, 5, 8)
- [x] Implement 8-point cap — auto-split stories exceeding 8 points
- [x] Apply story splitting strategies (by workflow step, business rule, data type, etc.)
- [x] Validate stories against the Story Checklist before presenting to user
- [x] Assign priority levels (Critical, High, Medium, Low)
- [x] Tag stories by discipline where possible (frontend, backend, fullstack)

### Sprint Planner

- [x] Use provided velocity or calculate default (engineers × 5)
- [x] Allocate stories to sprints without exceeding capacity
- [x] Respect priority ordering (Critical/High first)
- [x] Schedule spike stories early to de-risk unknowns
- [x] Handle blocked stories — push to later sprints
- [x] Generate sprint focus/goal summary per sprint
- [x] Display total points and per-sprint breakdown
- [x] Validate no sprint exceeds capacity

---

## Phase 6: UX Overhaul

### 6A: Welcome Screen & Onboarding

**Problem**: Bare `Panel("Scrum AI Agent")` — no warmth, no guidance on what to type.

- [x] Branded welcome panel with tagline, quick-start hint, and version (`cli.py`) — replaced with animated splash screen (`ui/splash.py`)
- [x] Conversational opener before first prompt: *"Tell me about your project..."* (`repl.py`)
- [x] Interactive intake mode selection menu — [1] Smart / [2] Full / [3] Quick / [4] Offline with export/import sub-menu (`repl.py`, `prompts/intake.py`)
- [x] Add `__version__` to `__init__.py`
- [x] Wire `--version` CLI flag (`cli.py`)

### 6B: Questionnaire Overhaul

**Problem**: 26 free-text questions asked one-by-one feels like a government form.

- [x] Add `QUESTION_METADATA` to `prompts/intake.py` (type: `free_text` | `single_choice` | `yes_no`, options, defaults)
- [x] Add `PHASE_INTROS` — conversational phase openers (`prompts/intake.py`)
- [x] Refactor `project_intake` node for conversational phrasing and option metadata (`agent/nodes.py`)
- [x] Render numbered option menus for choice questions in REPL (e.g. `[1] 1 week  [2] 2 weeks *(default)*  [3] 3 weeks`)
- [x] Support `defaults` command to batch-accept all defaults for a phase (skip ahead)
- [x] Add optional `_question_meta` transient field for passing question type to REPL (`agent/state.py`)
- [x] Dynamic follow-up choices: vague-answer probes now show 2-4 LLM-generated options as numbered menu

**Questions becoming selection menus** (6 of 26):

| Q | Topic | Options |
|---|-------|---------|
| Q2 | Project type | Greenfield / Existing codebase / Hybrid |
| Q8 | Sprint length | 1 week / 2 weeks / 3 weeks / 4 weeks |
| Q16 | Code hosting | GitHub / Azure DevOps / GitLab / Bitbucket / Local |
| Q18 | Repo structure | Monorepo / Multi-repo / Microservices / Monolith |
| Q24 | Estimation style | Fibonacci points / T-shirt sizes / No estimates |
| Q26 | Output format | Jira / Markdown / Both |

### 6C: Output Formatting with Rich Tables

**Problem**: Pipeline output (epics, stories, tasks, sprints) is dumped as raw markdown — hard to scan.

- [x] Create `src/scrum_agent/formatters.py` with Rich `Table`/`Panel` rendering for all artifacts
- [x] `render_analysis_panel(analysis)` → Panel with sections
- [x] `render_epics_table(epics)` → Table: ID, Title, Priority (colour-coded), Description
- [x] `render_stories_table(stories, epics)` → Table grouped by epic: ID, Story, Points, Priority, Discipline
- [x] `render_tasks_table(tasks, stories)` → Table grouped by story: ID, Title, Description
- [x] `render_sprint_plan(sprints, velocity)` → Per-sprint panels with capacity bar
- [x] Priority colour map: critical=red, high=yellow, medium=blue, low=dim
- [x] Wire formatters into REPL — render structured artifacts instead of streaming raw markdown
- [x] `render_intake_summary(qs)` → compact Rich Tables per phase with short labels, source tags, stats line
- [x] Wire intake summary formatter into REPL (3 display paths: pre-loaded, import, main loop transition)

### 6C½: Smart Intake — Reduce 26 Questions to 2-4

**Problem**: Even with accelerators (skip, defaults, suggestions), every question is shown one-by-one. 26 questions feels too long.

- [x] Add intake mode constants (ESSENTIAL_QUESTIONS, SMART/QUICK_ESSENTIALS, Q2_TO_Q15_MAP, MERGED_Q3_Q4_PROMPT, QUICK_FALLBACK_DEFAULTS) to `prompts/intake.py`
- [x] Add `intake_mode`, `extracted_questions`, `_pending_merged_questions` fields to `QuestionnaireState`
- [x] Add `--quick` and `--full-intake` CLI flags (mutually exclusive)
- [x] Wire `intake_mode` through `run_repl()` → graph state → `project_intake` node
- [x] Implement smart mode: auto-apply extractions + defaults, only ask essential gaps (2-4 Qs typically)
- [x] Implement quick mode: only Q6 (team size) and Q11 (tech stack) asked, everything else auto-filled
- [x] Merge Q3+Q4 into single prompt when both are gaps
- [x] Auto-derive Q15 from Q2 (deterministic mapping, no LLM call)
- [x] Enhance intake summary with provenance markers (extracted / defaulted / answered stats)
- [x] Standard mode (26-Q flow) preserved unchanged via `--full-intake` flag
- [x] Tests for all new helpers, modes, CLI flags, and state fields (36 new tests)

### 6D: Spinners & Progress Indicators

**Problem**: No feedback during LLM calls — app looks frozen.

- [x] Wrap `graph.invoke()` with `console.status(spinner="dots")` and contextual messages
- [x] Spinner messages: *"Processing your answer..."* / *"Analysing project..."* / *"Generating epics..."* / etc.
- [x] Pipeline progress line after questionnaire: `[2/5] Generating epics...`
- [x] Elapsed time shown after each step: `(took 3.2s)`

### 6E: Interactive Review Menus

**Problem**: Accept/Edit/Reject is text-based with fragile keyword matching — unrecognised text triggers accidental rejection.

- [x] Replace free-text review with numbered inline selector: `[1] Accept  [2] Edit  [3] Reject`
- [x] Accept both numbers (`1`/`2`/`3`) and existing keywords — eliminates accidental rejection from typos

### 6F: CLI Flags & Error Messages

- [x] `--quick` flag — minimal intake (replaces `--no-questionnaire`), only asks team size and tech stack
- [x] `--export-only` flag — auto-accept all review checkpoints, output markdown
- [x] Improved `--help` with usage examples
- [x] Actionable error messages (network errors → "Check ANTHROPIC_API_KEY", rate limits → auto-retry with countdown)
- [x] Consistent colour vocabulary: green=success, yellow=warning, red=error, blue=info, dim=hints

### 6G: Nice-to-Have (post-must-haves)

- [x] Style intake question messages with visual hierarchy — dim preamble (extraction summary, remaining count), stream question text
- [x] Status bar via `prompt_toolkit` `bottom_toolbar` (project name, phase, session)
- [x] `/compact` and `/verbose` toggle for output detail level
- [x] Terminal bell after long operations
- [x] Dark/light `--theme` flag
- [x] Show "Includes: X, Y, Z" line on export so user knows it is cumulative (all content generated so far)

---

## Phase 7: Tools

### Source Control Integration Tools

- [x] `github_read_repo` — read repo structure, file tree, and key files via GitHub API
- [x] `github_read_file` — fetch a specific file's contents from a GitHub repo
- [x] `github_list_issues` — list open issues and PRs to understand current work in progress
- [x] `github_read_readme` — fetch README and contributing docs for project context
- [x] `azdevops_read_repo` — read repo structure, file tree, and key files via Azure DevOps API
- [x] `azdevops_read_file` — fetch a specific file's contents from an Azure DevOps repo
- [x] `azdevops_list_work_items` — list existing work items / backlog for context
- [x] Set up authentication for GitHub (PAT or GitHub App token)
- [x] Set up authentication for Azure DevOps (PAT)
- [x] Auto-detect platform from repo URL provided during questionnaire
- [x] Rate limiting and pagination handling for API calls
- [x] Feed repo scan results into `project_analyzer` and `epic_generator` nodes for grounded output

### Pure Python Tools

- [x] `read_codebase` — scan local repo structure, identify languages, frameworks, key files
- [x] `export_markdown` — export full Scrum plan (epics, stories, tasks, sprints) as `.md` file

### LLM-Powered Tools

- [x] `estimate_complexity` — analyze code/requirements for story point estimation
- [x] `generate_acceptance_criteria` — write ACs from story descriptions

### Atlassian (Jira + Confluence) Integration Tools

#### Jira

- [x] Set up Jira authentication (API token, base URL, project key)
- [x] `jira_read_board` — read existing board state (sprints, backlog, velocity)
- [x] `jira_create_epic` — create epics in Jira
- [x] `jira_create_story` — create stories with ACs, points, priority, and sprint assignment
- [x] `jira_create_sprint` — create and manage sprints
- [x] Add user confirmation before any Jira write operation
- [x] Handle Jira API errors gracefully (auth failures, rate limits, network issues)
- [x] Map internal story IDs to Jira ticket keys after creation
- [x] Link stories to their parent epics in Jira
- [x] Add labels to the stories that have code

#### Confluence

- [x] Set up Confluence authentication (shared Atlassian API token + base URL + space key)
- [x] `confluence_search_docs` — search for pages by keyword/label so the agent can locate relevant documentation before planning
- [x] `confluence_read_page` — fetch and parse a Confluence page (strips ADF/HTML to plain text for LLM context)
- [x] `confluence_read_space` — list pages in a space to discover architecture docs, ADRs, runbooks, and product specs
- [x] Feed Confluence context into `project_analyzer` — surface relevant docs in the analysis prompt alongside repo and Jira data
- [x] `confluence_create_page` — publish the generated sprint plan or project brief as a Confluence page (with user confirmation)
- [x] `confluence_update_page` — update an existing page (e.g. append a new sprint plan to a running sprint log)
- [x] Handle Confluence API errors gracefully (401/403/404/429, page not found, space not found)
- [x] Add user confirmation before any Confluence write operation
- [x] Truncate large pages at 8 000 chars (same pattern as GitHub/AzDO file tools)

### Tool Registration

- [x] Register all tools with `@tool` decorator and descriptive docstrings
- [x] Create `ToolNode` and bind tools to LLM with `bind_tools`
- [x] Implement tool risk level routing (auto-execute / log / human approval)
- [x] Regenerate the Graph image in the Readme.md with the new tools

---

## Phase 7.2: UI & General Improvements

### App
- [x] Can we make the application LLM Agnostic? so it works with for example openai, anthropic, gemini...etc

### UI
- [x] adding a config markdown file to contain any urls, screenshots, documents, text ..etc (Similar to the .claude folder)
    - [x] LLM to scan that file and use it for more context in the beginning of the process
- [x] Questionnaire question of greenfield or hybrid if answered as exisiting or hybrid needs a url or repo confirmation or the user to input one

### Landing Page and Set up Expereince
- [x] I plan to expand the agent to handle multiple things in the future, such as coding, sprint review..etc so would be nice if the startupo page was choose which option you want (`e.g 1. Project Planning`) the work we did so far should be under project planning.
- [x] Welcome screen for first time setting up if no creds are available
    - [x] Ask users to provide the credentials either via terminal or creating a ~ directory (.scrum-agent)  and explain what they are and how to create and what permissions they need.
    - [x] Store those credentials in the ~ directory (.scrum-agent) and read them from there on startup.

### Full-Screen Dashboard UI Overhaul

Replacing inline text prompts with full-screen, block-character dashboard screens using Rich Live + raw terminal input. Rounded borders, consistent padding, arrow-key navigation.

- [x] Create `ui/` package (`src/scrum_agent/ui/__init__.py`)
- [x] Create `ui/_logos.py` — block-character ASCII art logos (Claude, Gemini, OpenAI)
- [x] Create `ui/provider_select.py` — full-screen provider selection (Step 1 of setup wizard)
- [x] Wire `_collect_provider()` in `setup_wizard.py` to use new full-screen selector
- [ ] Polish provider select layout — centering, card sizing, brand colours
- [ ] Full-screen API key entry screen (Step 2 of setup wizard)
- [ ] Full-screen integrations screen (Step 3 of setup wizard)
- [x] Full-screen mode selection menu (replace inline numbered menu)
- [x] Full-screen offline sub-menu (Export/Import) with export success screen and import file path input
- [x] Full-screen TUI session (`ui/session.py`) — replaces REPL for Smart/Full intake with description input, intake questions, summary review, pipeline stages, and chat screens
- [ ] Full-screen welcome/landing screen

### TUI Visual Polish

Pipeline artifact rendering, scrolling, animations, and layout refinements.

- [x] TUI-specific renderers for pipeline artifacts (stories, epics, tasks, sprint plan) — replaces shared `formatters.py` table-based rendering with text-block layouts
- [x] Stories: rounded boxes per story with metadata header (ID · pts · priority · discipline), story text, Given/When/Then ACs, DoD checklist
- [x] Epics: rounded boxes per epic with header line (E1 · Title · priority), description in grey
- [x] Tasks: rounded boxes per story group with story header, story text, individual tasks with descriptions
- [x] Sprint plan: summary line at top, each sprint in rounded box with capacity bar + points, goal, story list
- [x] Analysis: TUI-specific renderer with styled key-value fields, bullet sections, assumptions in yellow panel
- [x] Sticky group headers in pipeline viewport — epic titles pin at top when scrolling, with decryption-style morph animation between sections
- [x] Scrollbar for pipeline stages and summary review — vertical `│` track with `┃` thumb, right-aligned column
- [x] Fixed viewport height calculation (removed stale scroll indicator line budget)
- [x] Project card border: one-shot white pulse on Enter instead of continuous animation
- [x] Project resume: fixed enum deserialization crash (`Priority`, `StoryPointValue`, `Discipline` restored from JSON)
- [x] Given/When/Then and DoD styling — proper colour hierarchy instead of dim
- [x] Bottom border fix when sticky headers reduce viewport height
- [ ] Rework intra-generation loading animation
- [ ] Implement editing of each pipeline item (epic, story, task, sprint)

---

## Phase 8: Memory & Session Persistence

- [x] Save project metadata to `~/.scrum-agent/projects.json` (name, description, pipeline progress, artifact counts, Jira sync)
- [x] Load and display saved projects in Planning menu with real data
- [x] Rename REPL history file from `history` to `repl-history`
- [x] Auto-migrate old history file at startup
- [x] Add save points in TUI session (after description, intake review, each pipeline stage, chat)
- [x] Show pipeline progress on project cards (e.g. "3/7 stages complete" with color-coded status)
- [x] Launch TUI session when selecting an existing project (fresh session — no full state resume yet)
- [x] Viewport scrolling for project list with half-card peek stubs at edges
### 8A: SQLite Persistence
- [x] Add `langgraph-checkpoint-sqlite` dependency
- [x] Create `SessionStore` (custom SQLite metadata table, not `SqliteSaver` as graph checkpointer — avoids `operator.add` reducer incompatibility and zero-arg `create_graph` test constraint)
- [x] Assign human-readable session IDs: `<project-slug>-<YYYY-MM-DD>` (e.g. `lendflow-2026-03-06`); internal ID `new-<8hex>-<YYYY-MM-DD>` for uniqueness
- [x] Store session metadata (project name, created_at, last_modified, last_node_completed)
- [x] Show "Session saved: lendflow-2026-03-06" confirmation on clean exit
- [x] Write `tests/test_sessions.py` (17 tests — round-trip, slug, display name, persistence)

### 8B: Session Resumption
- [x] Implement `--resume` flag: load session by ID or `latest` keyword
- [x] If `--resume` passed without ID → interactive session picker (project name, date, last completed step)
- [x] Resume from last completed node — skip already-done steps, don't re-run epics if sprint planning failed
- [x] Handle stale/corrupt sessions gracefully (warn and offer fresh start instead of crash)
- [x] Add `--list-sessions` flag to show all saved sessions in a Rich table

### 8C: Session Lifecycle
- [x] Handle session ID collisions (same project run twice on same day → append `-2`, `-3`, etc.)
- [x] Handle schema version mismatch — store `schema_version` in metadata, warn and offer fresh start on mismatch
- [x] Auto-prune sessions older than 30 days (configurable, opt-out via config)

### 8D: Tests
- [x] Test `SqliteSaver` round-trip (save state → reload → all fields match)
- [x] Test `--resume latest` picks the most recent session
- [x] Test resume skips already-completed nodes
- [x] Test stale/corrupt session fallback behaviour
- [x] Test session ID collision handling (`-2`, `-3` suffix)

---

## Phase 9: UI Overhaul Niky addition
- [ ]
- [ ]
- [ ]

---

## Phase 10: Context Enrichment

_Replaces the original "RAG — Codebase Ingestion" plan. The codebase scanner (`tools/codebase.py`),
GitHub/Azure DevOps repo tools, and Confluence tools already provide context to `project_analyzer`.
Vector-store RAG (Chroma/embeddings) is deferred until there's evidence the direct-context approach
is insufficient for real projects._

### Already Done
- [x] Local codebase scanner with language detection and tree output (`read_codebase`)
- [x] Skip binary files, node_modules, build artifacts (`_SKIP_DIRS`)
- [x] Codebase context fed into `project_analyzer` via `_scan_repo_context`
- [x] Confluence page ingestion for project docs
- [x] Questionnaire file import (`--questionnaire` flag)

### Remaining
- [x] Targeted file content retrieval — `read_local_file` tool lets the LLM read specific files from local repos
- [x] Large codebase handling — budget-limited tree output (`_MAX_TREE_CHARS`), auto-collapses large dirs
- [x] PRD/design doc ingestion — `scrum-docs/` directory for .md/.txt/.rst files (export from Google Docs, Notion, etc.)
- [x] PDF support in `scrum-docs/` via `pymupdf` optional dependency (`uv sync --extra pdf`)
---

## Phase 11: Guardrails

### Input Guardrails

- [x] Validate project descriptions are not empty or too vague (follow-up probing via `probed_questions`)
- [x] Trigger follow-up questions for insufficient context (vague-answer detection + dynamic choices)
- [x] Detect and handle prompt injection attempts (`input_guardrails.py` — heuristic pattern blocklist)
- [x] Cap input length to prevent abuse (`MAX_INPUT_CHARS = 5000` in `input_guardrails.py`)

### Tool Guardrails

- [x] Implement human-in-the-loop routing in the graph (`pending_review` checkpoints after each pipeline step)
- [x] Require explicit user confirmation for high-risk tools (review gate before Jira/Confluence writes)
- [x] Auto-execute low-risk tools (read, search, export — no confirmation needed)
- [x] Log and display medium-risk tool outputs (`_display_tool_activity` in REPL — shows tool name + result snippet)

### Output Guardrails

- [x] Validate story format matches "As a [persona], I want to [goal], so that [benefit]" (enforced in prompts)
- [x] Validate all stories have Given/When/Then acceptance criteria (enforced in prompts)
- [x] Enforce story points in 1–8 range, auto-split if >8 (enforced in prompts + `auto_split_stories`)
- [x] Programmatic validation of story format (`validate_story_format` in `output_guardrails.py`)
- [x] Validate AC coverage (happy, negative, edge, error) — `validate_ac_coverage` checks for negative keywords
- [x] Validate sprint load does not exceed capacity — `validate_sprint_capacity` compares points vs velocity
- [x] Flag when generated scope exceeds stated project scope — `validate_scope_vs_capacity` checks total pts vs planned capacity
- [x] Push back on unrealistic sprint loads — warnings displayed after artifact rendering in REPL

---

## Phase 12: Testing

### Unit Tests

- [x] Test state transitions in the graph (`test_state.py`, `test_nodes.py`)
- [x] Test velocity calculation (default and provided) (`test_nodes.py`)
- [x] Test story point validation (reject >8, accept 1–8) (`test_state.py`)
- [x] Test sprint capacity allocation (`test_nodes.py`)
- [x] Test prompt formatting and template rendering (`test_*_prompt.py` files)
- [x] Test tool input/output validation (`test_tools_jira.py`, `test_tools_llm.py` — input edge cases)
- [x] Test auto-split logic (`test_nodes.py::TestAutoSplitBoundary` — boundary, redistribution, enum)

### Integration Tests

- [x] Test session save and resume (`test_sessions.py`, `test_cli.py` Phase 8D)
- [x] Test Confluence integration with mock API (`test_tools_confluence.py`)
- [x] Test Jira integration with mock API (`test_tools_jira.py`)
- [x] Test full graph execution with mock LLM responses
- [x] Test questionnaire flow end-to-end
- [x] Test epic → story → task → sprint pipeline

### E2E Tests — Graph-Level (priority: high)

Drive the compiled graph with scripted inputs and mocked LLM, assert on full pipeline output.
No REPL, no UI — tests routing, state flow, and node chaining end-to-end.

- [x] Create shared E2E helpers: multi-stage LLM mock (returns different JSON per pipeline stage), state builder
- [x] Test full questionnaire → pipeline flow (Q1–Q26 → analyzer → epics → stories → tasks → sprints)
- [x] Test quick intake mode skips to pipeline with defaults
- [x] Test smart intake mode extracts answers and skips answered questions
- [x] Test review loop: reject epics → re-generate with feedback → accept
- [x] Test review loop: edit stories → re-generate with edits → accept
- [x] Test fallback path: garbage LLM responses at every stage still produce valid artifacts
- [x] Test resume: save state mid-pipeline, reload, continue from where it left off

### E2E Tests — REPL-Level (priority: medium)

Drive `run_repl()` with fake PromptSession inputs and captured Rich console output.
Tests the full stack: input handling, Rich panels, review checkpoints, toolbar, exit.

- [x] Test happy path: intake → pipeline → "Goodbye" (assert key panels appear in output)
- [x] Test Ctrl-C / Ctrl-D graceful exit at each pipeline stage
- [x] Test `/export` command produces valid JSON/Markdown output
- [x] Test `/resume` command lists sessions and resumes selected one
- [x] Test error recovery: LLM error mid-pipeline shows message, doesn't crash REPL

### Golden Datasets (priority: high)

Curated project descriptions with expected outputs, run as regression tests.
Uses LangSmith Datasets + Evaluators or plain pytest with structural assertions.

- [x] Create 3–5 curated project descriptions as test fixtures (e.g. todo app, SaaS platform, mobile app, API gateway, ML pipeline)
- [x] Define structural evaluators: epic count (3–6), stories per epic (2–5), story point range (1–8 Fibonacci), AC format (Given/When/Then)
- [x] Validate epic generation: titles relevant to project, priorities assigned, no duplicates
- [x] Validate story generation: correct epic_id references, personas match end_users, points are Fibonacci
- [x] Validate task generation: correct story_id references, 2–5 tasks per story
- [x] Validate sprint planning: all stories allocated, capacity ≤ velocity, no orphans
- [x] Add `make eval` command to run golden dataset suite separately from unit tests
- [x] Run golden datasets in CI after every change to catch regressions

### Contract Tests — Recorded API Responses (priority: high)

Use `pytest-recording` (VCR.py) to record real API responses once, replay in CI.
Catches schema changes and SDK mismatches without network calls on every push.
Covers all integrations: Jira, Confluence, GitHub, Azure DevOps, and LLM providers.

**Setup:**
- [x] Add `pytest-recording` (VCR.py wrapper) to dev dependencies
- [x] Create `tests/cassettes/` directory for recorded response fixtures
- [x] Add `make record` command to re-record cassettes against real APIs
- [x] Add cassette files to git (they're test fixtures, not secrets — scrub tokens before committing)

**Jira (atlassian-python-api / jira):**
- [x] Record `jira_read_board` — board info, active sprint, issues list
- [x] Record `jira_create_epic` — epic creation with summary, description, priority
- [x] Record `jira_create_story` — story creation with AC, points, epic link
- [x] Record `jira_create_sprint` — sprint creation with name, start/end dates
- [x] Record error responses: 401 (bad token), 404 (missing project), 429 (rate limit)

**Confluence (atlassian-python-api):**
- [x] Record `confluence_search_docs` — CQL search returning page titles and URLs
- [x] Record `confluence_read_page` — page content with HTML → plain text conversion
- [x] Record `confluence_read_space` — space page listing
- [x] Record `confluence_create_page` — page creation with storage format body
- [x] Record error responses: 401 (bad token), 404 (missing space)

**GitHub (PyGithub):**
- [x] Record `github_read_repo` — repo tree listing with file types
- [x] Record `github_read_file` — file content retrieval
- [x] Record `github_list_issues` — issues with labels, pagination
- [x] Record `github_read_readme` — README.md content
- [x] Record error responses: 401 (bad PAT), 404 (missing repo), 403 (rate limit)

**Azure DevOps (azure-devops):**
- [x] Record `azdevops_read_repo` — repo file tree listing
- [x] Record `azdevops_read_file` — file content retrieval
- [x] Record `azdevops_list_work_items` — work items with types, states, assignees
- [x] Record error responses: 401 (bad PAT), 404 (missing project)

**LLM Providers (langchain-anthropic / langchain-openai / langchain-google-genai):**
- [x] Record Claude (Anthropic) — analyzer prompt → JSON response, streaming tokens
- [x] Record GPT-4o (OpenAI) — same prompt, compare response schema compatibility
- [x] Record Gemini (Google) — same prompt, compare response schema compatibility
- [x] Record error responses: 401 (bad API key), 429 (rate limit), 529 (overloaded)
- [x] Record off-topic classifier responses (cheap models: Haiku, gpt-4o-mini, Gemini Flash)
- [x] Verify all providers return parseable JSON for each pipeline stage


**Add to CI:**
- [x] Add Contract Tests to CI

### Smoke Tests — Real APIs on Schedule (priority: low)

Run against real APIs on a weekly cron job, not on every push.
Catches token expiry, API deprecations, and SDK drift.

- [x] Create `tests/smoke/` directory for smoke test files
- [x] Add `make smoke-test` command (runs only smoke tests with real credentials)
- [x] Add GitHub Actions workflow: weekly cron (Monday 6am), uses repository secrets
- [x] Alert on failure: Slack/email notification when smoke tests break using the Slack App in Github
- [x] Smoke: Jira — create and delete a test epic in a sandbox project
- [x] Smoke: Confluence — create and delete a test page in a sandbox space
- [x] Smoke: GitHub — read a known public repo (e.g. the project's own repo)
- [x] Smoke: Azure DevOps — read a known project/repo in the test org
- [x] Smoke: Anthropic Claude — send a simple prompt, assert non-empty response
- [x] Smoke: OpenAI GPT-4o — send a simple prompt, assert non-empty response (if configured)
- [x] Smoke: Google Gemini — send a simple prompt, assert non-empty response (if configured)

### Test Infrastructure Improvements (priority: high → medium)

**Fix test_repl.py flaky isolation (priority: high):**
18 tests pass in isolation but fail in the full suite — shared mutable state leaking between tests.
- [x] Identify the leaking module-level state in `scrum_agent.repl` (likely graph instance, session, or questionnaire)
- [x] Add `tests/integration/conftest.py` with autouse fixture to reset module-level state before each test
- [x] Verify all 18 flaky tests pass reliably in full suite after fix
- [x] Add CI check: `make test` must have zero failures (not just "known flaky" exceptions)

**ReAct loop integration test (priority: high):**
The LLM → tool → LLM feedback loop is a critical path with no dedicated test.
- [x] Test: LLM returns tool_calls → ToolNode executes tool → result fed back → LLM produces final answer
- [x] Test: LLM calls multiple tools in sequence (e.g. read_board then create_epic)
- [x] Test: tool raises error → LLM sees error message → responds gracefully (no crash, no infinite loop)
- [x] Test: human_review node intercepts high-risk tool → confirmation flow → tool executes

**Graph topology validation (priority: medium):**
Catch wiring mistakes when nodes or edges are added/removed.
- [x] Test: every node is reachable from START (no orphan nodes)
- [x] Test: every node has at least one outgoing edge or reaches END (no dead ends)
- [x] Test: all node names in conditional edges match registered nodes
- [x] Test: adding a new node without an edge fails at compile time (verify LangGraph enforces this)

**Tool registration sync check (priority: medium):**
Ensure every `@tool`-decorated function in `src/scrum_agent/tools/` is registered in `get_tools()`.
- [x] Scan all modules in the tools package for `@tool`-decorated functions
- [x] Assert every discovered tool name appears in `get_tools()` result
- [x] Assert no tool is registered twice (duplicate detection)

**Snapshot testing for Rich output (priority: medium):**
Catch visual regressions in terminal panel rendering.
- [x] Add `syrupy` to dev dependencies (pytest snapshot assertion plugin)
- [x] Snapshot `_format_epics()` output with sample data
- [x] Snapshot `_format_stories()` output with sample data
- [x] Snapshot `_format_tasks()` output with sample data
- [x] Snapshot `_format_sprints()` output with sample data
- [x] Snapshot project analysis panel output
- [x] Add `--snapshot-update` to `make test` docs for when intentional format changes are made

**Token budget assertions (priority: low):**
Catch prompt size regressions that increase LLM cost.
- [x] Assert analyzer prompt stays under 20K chars (~5K tokens) for a typical questionnaire
- [x] Assert epic generator prompt stays under 15K chars
- [x] Assert story writer prompt stays under 20K chars (includes all epics)
- [x] Assert sprint planner prompt stays under 15K chars
- [x] Assert system prompt stays under 5K chars
- [x] Log actual token counts in CI output for trend monitoring

**Schema validation on contract tests (priority: low):**
When using VCR recorded responses, validate response schemas before parsing.
- [x] Define expected response schemas for each Jira endpoint (fields, types)
- [x] Define expected response schemas for each Confluence endpoint
- [x] Define expected response schemas for each GitHub endpoint
- [x] Define expected response schemas for LLM JSON output (analysis, epics, stories, tasks, sprints)
- [x] Assert recorded responses match schemas — catch silent field additions/removals

### Red Teaming (priority: medium)

Adversarial inputs to test guardrails, fallbacks, and edge cases.

- [x] Test with vague / empty project descriptions → fallback analysis still usable
- [x] Test with contradictory requirements → agent flags conflicts or picks sensible defaults
- [x] Test with absurdly large scope (50+ features) → still produces ≤ 6 epics, stories capped
- [x] Test prompt injection attempts → input guardrails block, system prompt holds
- [x] Test with extremely long inputs → length check rejects, no crash
- [x] Test with gibberish / non-English inputs → off-topic classifier blocks or agent handles gracefully

---

## Phase 13: v1.0 — Production Release (Project Planning Agent)

_Ship the existing project planning agent as a usable product. Everything
needed to let real users plan projects: polish, reliability, deployment,
and documentation. New agents come after this is live._

### 13A: Reliability & Edge Cases

#### Quick wins (group together — small, isolated changes)
- [x] Ability to choose the sprint you want (default to the next one)
- [x] Ask sprint length in Smart Intake (give options with 2 weeks a default) followed by another question of how many sprints you aim to complete it in as a choice question in the smart questionnaire (default: no preference / let the agent decide)
- [x] A dedicated Sub Task for Documentation (For user stories that have docuemntation in D.O.D) which includes in the description key elements to document and the link to the confulence docs and readme (IF PROVIDED)
- [x] Sub Tasks labels: "Code", "Documentation", "Infrastructure", "Testing" auto-tagged
- [x] Testing plans included in sub tasks — auto-generate test plan section in each subtask that has code involved
- [x] Handle projects that don't need epics (small scope → stories only)
- [x] AI coding prompt per task — add `ai_prompt` field to Task with ARC-structured instruction for Cursor/Claude Code
- [x] Add a Prompt rating for your input to say how good the prompt was

#### Dynamic capacity retrieval
_Replace manual velocity input with data-driven defaults. Based on analysis of Capacity_Plan_Template.xlsx — real feature capacity is ~24% of gross after deductions._

**Capacity calculator (intake Q27–Q30):**
- [x] Add intake questions for capacity deductions: bank holidays (days), planned leave (days), unplanned leave (%), onboarding (dev-sprints)
- [x] Add intake questions for KTLO/BAU: dedicated KTLO engineers, expected unplanned work (dev-sprints)
- [x] Add discovery % deductions (defaults: 5% discovery)
- [x] Auto-detect bank holidays using real sprint window dates (not intake-time locale guess)
- [x] Add support for detecting bank holidays by region/country (100+ countries via `holidays` package with 3-layer locale fallback)
- [x] Fix GB subdivision holidays (Easter Monday, August bank holiday) — use ENG default subdivision
- [x] Compute net velocity per sprint with all deductions (bank holidays, leave, unplanned %, onboarding)
- [x] Compute net feature capacity: gross - deductions - KTLO - platform/discovery tax
- [x] Use net capacity instead of raw velocity in sprint_planner capacity checks
- [x] Surface capacity breakdown in analysis review screen (gross → deductions → net)
- [x] Per-sprint velocity — only sprints with bank holidays get reduced capacity, others keep full velocity
- [x] Convert inline helpers to @tools: _fetch_jira_velocity, _fetch_active_sprint_number, _load_user_context
- [x] Add capacity breakdown to scrum-plan.md export and HTML report
- [x] Persist all capacity fields in session save/load whitelists

**Jira-based velocity:**
- [x] Pull sprint history from Jira (completed points per sprint)
- [x] Calculate rolling average velocity (last 3–5 sprints)
- [x] Handle edge cases: new teams with no history, outlier sprints

**Task enrichment:**
- [x] Auto-tagged task labels: Code, Documentation, Infrastructure, Testing — `TaskLabel` enum + colour-coded display in REPL tables, TUI, and markdown
- [x] Auto-generated test plan per Code/Infrastructure task — parser extracts `test_plan` field, all 3 renderers display it
- [x] Dedicated documentation sub-task for stories with Documentation in DoD — includes key elements to document + Confluence/README URLs from intake
- [x] AI coding prompt per task (`ai_prompt` field) — ARC-structured instruction for Cursor/Claude Code/Copilot, includes project context and tech stack
- [x] Fix TUI task editor silently resetting label, test_plan, ai_prompt to defaults on save

**Small project handling:**
- [x] Analyzer LLM sets `skip_epics` for small projects (≤2 sprints AND ≤3 goals)
- [x] Deterministic guardrail: `skip_epics` only allowed when scope is genuinely small, regardless of LLM output
- [x] Sentinel epic uses project name as title (not generic "Project Backlog")
- [x] Use E1 instead of E0 so 1-epic and multi-epic projects share same UI and rendering paths
- [x] Removed all E0 special-casing — same validation bounds, renderers, story writer rules for all projects

**Prompt quality rating:**
- [x] Deterministic scoring from QuestionnaireState tracking sets (no LLM call)
- [x] Letter grade (A/B/C/D) + percentage with breakdown (answered/extracted/defaulted/skipped/probed)
- [x] Actionable suggestions including SCRUM.md hint and high-value question tips
- [x] Displayed on analysis review screen in both TUI and REPL

**Infrastructure & reliability:**
- [x] Per-session log files — cleaned up on project deletion
- [x] Surface API errors in TUI — user-friendly error panels for auth/billing/network errors instead of silent return to project select
- [x] Fix CodeQL false positives: use full string assertions instead of domain substrings in tests
- [x] Fix locale fallback test for CI where LANG env var differs
- [x] Fix Q10 range parsing to use upper bound consistently

**Story & sprint UX (uncommitted — this session):**
- [x] Add `title` field to UserStory — short summary (3-7 words) shown in sprint views instead of epic name
- [x] Sprint TUI: highlight bank-holiday-impacted sprints with amber border + holiday annotation
- [x] Sprint TUI: use per-sprint net velocity in capacity bars (not flat velocity)
- [x] Ask team availability during smart intake — per-person PTO/leave tracking with date-based sub-loop after bank holidays
- [x] Capacity overflow: 3 options — extend sprints (recommended), increase team size, or keep as-is (overloaded, not recommended)

**How to verify — all features on `feature/phase-13A-continued`:**

_Task enrichment:_
1. **Task labels** — `make run`, complete a plan. In the task review screen, each task should show a colour-coded label badge (Code/Documentation/Infrastructure/Testing). In `scrum-plan.md` export, tasks show `**Label:** Code` etc.
2. **Test plans** — tasks labelled Code or Infrastructure should have a "Test Plan" section listing what to test (unit, integration, edge cases). Documentation/Testing tasks should have no test plan.
3. **Documentation sub-task** — for any story with "Documentation" marked as applicable in its DoD, the last task should be a consolidated documentation sub-task. Its description should reference Confluence/README URLs if they were provided during intake (Q14).
4. **AI prompt** — every task should have an `ai_prompt` field. In the TUI task detail, it appears as a collapsible section. In `scrum-plan.md`, it appears under each task. The prompt should include project name, tech stack, and ARC-structured guidance.

_Small project handling:_
5. **Epic skip** — `make run` with a very small project (e.g. "build a calculator app, 1 sprint, 2 goals"). The analyzer should create a single epic named after the project. The epic review step still appears. Stories use standard 2-5 per-epic bounds.

_Prompt quality:_
6. **Prompt rating** — after intake, the analysis review screen shows a letter grade (A/B/C/D) with percentage, breakdown counts, and suggestions. Try skipping questions to get a lower grade. Providing a SCRUM.md file should improve the score.

_Capacity planning:_
7. **Capacity breakdown** — after intake, the analysis screen should show "Capacity Planning" with gross velocity, deductions (bank holidays, leave, unplanned %, onboarding, KTLO, discovery), and net velocity. If bank holidays are detected, a per-sprint breakdown appears.
8. **Bank holiday detection** — the system auto-detects holidays based on locale. UK users near Easter should see Easter Monday detected. The intake summary shows detected holidays.
9. **Bank holiday sprint highlighting** — in the sprint TUI view:
   - Impacted sprints have an **amber border** on the header box
   - A `⚠ −1d capacity: Easter Monday` annotation appears below the header
   - The capacity bar uses the **reduced velocity** (e.g. 4/4 pts instead of 4/5 pts)
   - Non-impacted sprints remain white-bordered with full velocity

_PTO / planned leave:_
10. **PTO sub-loop in smart mode** — `make run`, complete intake in smart mode. After bank holidays are resolved (Q28), a "Does anyone have planned leave?" prompt appears with [1] Yes / [2] No. Typing an invalid option (e.g. "4") should re-prompt, not skip.
11. **PTO date collection** — choose Yes, enter a name, start date (DD/MM/YYYY), and end date. Verify:
    - Invalid date formats are rejected with a helpful message
    - End date before start date is rejected
    - Dates outside the planning window (past dates, dates beyond sprint range) are rejected
    - After entering, a summary shows (e.g. "Omar: 17/04 – 18/04 (2 working days)") with [1] Add another / [2] Done
12. **PTO in capacity breakdown** — after accepting intake, the analysis screen "Capacity" section should show PTO in per-sprint breakdown (e.g. "Sprint 1: 3 pts (PTO: Omar 2d)"). The deductions line should show "Planned leave: 2 day(s) (Omar 2d)".
13. **PTO in sprint TUI** — in the sprint plan view, sprints impacted by PTO should show a 📋 PTO annotation line (e.g. "PTO: Omar 2d") similar to the bank holiday ⚠ annotation.
14. **PTO in exports** — `scrum-plan.md` per-sprint velocity section should include PTO annotations alongside bank holidays (e.g. "Sprint 1: **3 pts** — PTO: Omar 2d").
15. **PTO + bank holidays combined** — enter PTO that overlaps with a bank-holiday sprint. Both should appear in annotations, and velocity should reflect both deductions without double-counting.
16. **Quick mode skips PTO** — `make run` in quick mode. The PTO question should NOT appear; planned leave defaults to 0.
17. **No PTO** — answer "No" to the PTO question. Behaviour should be identical to before the feature was added (planned_leave = 0, no PTO annotations).

_Capacity overflow:_
18. **3-option overflow screen** — `make run` with a small team (1 engineer) and many stories that exceed 1-sprint capacity. The overflow screen should show 3 options: (1) Extend to N sprints (recommended), (2) Keep M sprints — increase team to K engineers, (3) Keep M sprints, 1 engineer (sprints will exceed velocity).
19. **Extend sprints** — choose option 1. Sprints should extend to the recommended count, velocity unchanged.
20. **Increase team** — re-run, choose option 2. Sprint count stays at original target, velocity scales up (e.g. 3 engineers × 5 pts = 15 pts/sprint). Sprint plan header shows "Team expanded from 1 to 3 engineer(s)". No sprint should have "HARD DEADLINE" in prompt.
21. **Keep as-is (overload)** — re-run, choose option 3. Sprint count stays at original, velocity unchanged, sprints may exceed velocity cap.
22. **Jira team size cap** — when Jira is connected, the "increase team" option should never suggest more engineers than the Jira org has. If the team is already at the Jira cap (or the computed team size equals the current team), option 2 is replaced with a note: "Increase team is unavailable — your Jira board has N team member(s), which is already the maximum."
23. **Jira velocity JQL fallback** — when Jira's `completedPoints` from the sprint report is zero (common with next-gen/team-managed boards), the system should fall back to summing `customfield_10016` (story points) from Done issues via JQL. Check the debug log for `JQL fallback story_points=` lines confirming the fallback fired. Velocity should reflect actual completed work, not zero.

_Context sources & SCRUM.md:_
24. **Context sources in TUI** — `make run`, complete intake, reach analysis. The analysis panel should show a "Context Sources" section with ✓/✗/— indicators for SCRUM.md, Repository, and Confluence. If SCRUM.md exists, it shows ✓ green; if missing, ✗ red.
25. **SCRUM.md auto-population** — populate SCRUM.md with project context (tech stack, sprint length, constraints, etc.), then `make run` in smart mode. The preamble should say "N from SCRUM.md" and those questions should be pre-filled. Check the intake summary for `*(from SCRUM.md)*` provenance markers.
26. **Description wins over SCRUM.md** — type a project description that mentions a different tech stack than SCRUM.md. The description's tech stack should take priority; SCRUM.md fills only gaps.
27. **No SCRUM.md** — delete or rename SCRUM.md and run. Only description extraction should fire (no SCRUM.md preamble line). Everything works as before.

_Story titles:_
28. **Story titles in sprint view** — each story in the sprint TUI shows its own title (e.g. "Create Bookmark Endpoint") instead of the epic name. Stories without titles fall back to the goal text.
29. **Exports** — `scrum-plan.md` story headings use `## US-E1-001: Create Bookmark Endpoint` (title). HTML export card titles use the short title.

_Infrastructure:_
30. **Log files** — after running, check `~/.scrum-agent/logs/` for per-session log files. Deleting a project should clean up its logs.
31. **API error handling** — set an invalid `ANTHROPIC_API_KEY` and run. The TUI should show a user-friendly error panel (not a traceback or silent failure).

_Backward compatibility:_
32. **Resume old sessions** — resume a session saved before these changes. Stories with no `title` field should render with `story.goal` in sprint views. No crash on missing capacity fields.

_Tests:_
33. `make test-fast` — 2207 pass, `make lint` — clean, `make snapshot-update` — 2 snapshots updated for formatter column change.


#### Smart intake improvements - For Exisiting Repo Track
_Make the adaptive questionnaire smarter — extract more, ask less, validate answers._

**Smarter extraction:**
- [x] Relax extraction rules — infer Q2 (project type) from keywords like "refactor", "migrate", "legacy"
- [x] Extract integrations (Q12) from tech stack keywords (Stripe, Auth0, Firebase, etc.)
- [x] Extract architectural constraints (Q13) from infra signals (Kubernetes, microservices, AWS)

**Adaptive question text:**
- [x] Personalise Q7 (roles) based on Q6 team size — "You said 5 engineers; what are their roles?"
- [x] Personalise Q12 (integrations) with hints from Q11 tech stack
- [x] Reference Q2 answer in Q13 — greenfield vs existing has different constraint concerns

**Cross-question validation:**
- [x] Detect contradictions — Q2="Greenfield" but Q17 has an existing repo URL
- [x] Flag unrealistic combos — Q8 sprint length × Q10 target sprints > 6 months → confirm scope
- [x] Sanity-check velocity (Q9) against team size (Q6) — flag if wildly off

**Follow-up quality:**
- [x] Custom follow-up templates per question (not just generic "tell me more")
- [x] Q3 (problem): "Who experiences this problem? Give 2-3 user personas"
- [x] Q11 (tech stack): "What's the primary language and framework?"
- [x] Q21 (risks): "Which risk should be addressed earliest?"

**Answer confidence signalling:**
- [x] Track answer source: `direct`, `extracted`, `defaulted`, `probed` per question
- [x] Show breakdown in intake summary: "12 direct, 3 extracted, 7 defaulted"
- [x] Pass confidence hints to downstream nodes — low-confidence areas → recommend spikes


#### "Create in Jira" option
_Push artifacts to Jira. Two modes: inline (during pipeline) and selective (post-plan menu)._
- [x] Add `jira_epic_key`, `jira_task_keys`, `jira_sprint_keys` to ScrumState
- [x] Add `create_subtask()` and `add_issues_to_sprint()` helpers to `tools/jira.py`
- [x] Create `jira_sync.py` — batch sync orchestration with idempotency
- [x] Update persistence for new state fields (serialization + deserialization)
- [x] Add "Jira" button to TUI pipeline review (stories, tasks, sprints stages)
- [x] Confirmation screen before Jira creation (shows what will be created/skipped)
- [x] Progress screen during Jira creation (animated, per-item status)
- [x] Wire project list Jira export button (full sync: Epic + Stories + Tasks + Sprints)
- [x] Jira button disabled/dimmed when JIRA_API_TOKEN not configured
- [x] Features → Jira Labels (not separate issues)
- [x] Stories: create Epic + Stories linked to Epic + feature labels
- [x] Tasks: create Sub-tasks linked to parent Stories + task labels
- [x] Sprints: create Sprints + assign stories to sprints
- [x] Idempotency: check `jira_*_keys` before creating, skip existing
- [x] Cascade: Tasks stage creates Stories if not done; Sprints stage creates Stories if not done
- [x] Tests for jira_sync module (unit + idempotency + error handling)
- [x] Tests for new state fields and persistence round-trip



### 13B: Packaging & Distribution (Homebrew)

_Ship the CLI as a Homebrew-installable app so anyone can `brew install scrum-agent` and go. This is a prerequisite for 13C (OpenClaw) — the skill needs an installable CLI to invoke._

#### PyPI release (prerequisite for Homebrew)
- [x] Finalise `pyproject.toml` metadata (name, version, description, author, license, classifiers, URLs)
- [x] Add `[project.scripts]` entry point: `scrum-agent = "scrum_agent.cli:main"`
- [x] Build sdist + wheel with `uv build` / `python -m build`
- [x] Add MIT LICENSE file
- [x] Add `make build` and `make publish` targets
- [x] Publish to PyPI — `publish.yml` workflow triggered on `v*` tag push (OIDC trusted publishing)
- [x] Verify `pipx install scrum-agent` works end-to-end (tested locally from built wheel)

#### Homebrew formula
- [x] Create Homebrew formula (`Formula/scrum-agent.rb`) — `Language::Python::Virtualenv` pattern
- [x] Add formula to a personal tap (`homebrew-tap` repo): `gh repo create omardin14/homebrew-tap --public`
- [x] Caveats block: "Run `scrum-agent --setup` to configure API keys"
- [x] `brew test` block — `--version` and `--help` assertions
- [x] Automate formula version bumps via GitHub Actions on new PyPI release (`update-formula.yml` triggered by `repository_dispatch`)
- [ ] `brew install omardin14/tap/scrum-agent` — requires PyPI publish first (formula SHA256 placeholder until first release)

#### Non-interactive / headless mode (prerequisite for OpenClaw skill)
- [x] Add `--non-interactive` flag — runs full pipeline with no TUI, auto-accepts all gates
- [x] Add `--output json` flag — structured JSON to stdout (epics, stories, tasks, sprints)
- [x] Add `--output html` and `--output markdown` output formats
- [x] Accept project description and key params via CLI args (`--description`, `--team-size`, `--sprint-length`)
- [x] Support `--description @file.txt` to read from file
- [x] Combine with existing `--quick` mode for minimal-question headless runs
- [x] JSON exporter (`json_exporter.py`) with clean user-facing schema
- [x] Tests for JSON exporter and all new CLI flags

#### CI/CD pipeline
- [x] GitHub Actions workflow: lint → test → build → publish to PyPI on tagged releases (`publish.yml`)
- [x] Auto-update Homebrew formula SHA and version on successful publish (repository_dispatch)
- [x] `.env.example` review — all 56 lines covering providers, integrations, session management, and logging

#### Documentation
- [x] README quick-start section (Homebrew, pipx, headless mode)
- [x] README comprehensive update — all features from Phases 1–13B documented
- [x] Version bumped to 1.0.0 in `pyproject.toml` and `__init__.py`
- [ ] Push tag `v1.0.0` to trigger publish workflow (final step — do when ready to go live)

---

## Phase 14: OpenClaw x Slack

_Deploy OpenClaw on AWS Lightsail, secure with Teleport, and build a skill that runs scrum-agent planning via Slack conversations — posting results as a Slack Canvas. Manual setup first (Phases 14A–C), then IaC (14D), then polish (14E–F)._

### 14A: OpenClaw on Lightsail

_OpenClaw comes pre-installed on the Lightsail OpenClaw blueprint. Use Claude with AWS MCP to provision and configure._

#### Lightsail Instance (via Claude + AWS MCP)

- [x] Create Lightsail instance using OpenClaw blueprint (pre-installed, Bedrock-configured)
- [x] Attach static IP to the instance
- [x] Run Bedrock IAM setup script via CloudShell: `curl -s https://d25b4yjpexuuj4.cloudfront.net/scripts/lightsail/setup-lightsail-openclaw-bedrock-role.sh | bash -s -- OpenClaw-1 eu-west-2`
- [x] Complete Anthropic FTU (First Time Use) form for Bedrock access if first time
- [x] SSH in and pair browser with OpenClaw dashboard
- [x] Verify OpenClaw runs a basic skill end-to-end via dashboard
- [x] Add "Deploy on AWS Lightsail" section to README.md with manual setup instructions

#### Slack Connection (Socket Mode)

- [ ] Create Slack App in workspace — bot token scopes: `chat:write`, `channels:read`, `channels:history`, `canvases:write`, `files:write`
- [ ] Enable Socket Mode — outbound-only WebSocket, no public webhook URLs needed
- [ ] Configure app-level token (`xapp-`) and bot token (`xoxb-`) in OpenClaw
- [ ] Subscribe to events: `message.channels`, `app_mention`
- [ ] Test: bot responds to a mention in a Slack channel

#### scrum-agent Installation

- [ ] Install scrum-agent on the instance (`pipx install scrum-agent`)
- [ ] Configure `ANTHROPIC_API_KEY` (or use Bedrock credentials already on instance)
- [ ] Verify headless mode: `scrum-agent --non-interactive --description "Build a todo app" --output json`
- [ ] Verify SCRUM.md keyword extraction works with a sample file

---

### 14B: Teleport (Zero-Trust Access)

_Deploy Teleport on the Lightsail instance, proxy OpenClaw through it, close all public ports except Teleport HTTPS._

#### Teleport Cluster

- [ ] Install Teleport on the Lightsail instance (self-hosted, single-node)
- [ ] Configure with domain name and Let's Encrypt TLS
- [ ] Create initial admin user and role
- [ ] Verify SSH access through Teleport (`tsh ssh`)

#### Application Proxy

- [ ] Configure Teleport app proxy for OpenClaw web UI (internal port → Teleport HTTPS)
- [ ] Create roles: `openclaw-admin` (full access), `openclaw-user` (skill execution only)
- [ ] Test: access OpenClaw UI through Teleport app proxy with SSO

#### Lock Down

- [ ] Close SSH (22) in Lightsail firewall — all access through Teleport
- [ ] Close OpenClaw's direct port — only accessible via Teleport app proxy
- [ ] Verify: only Teleport HTTPS (443) is publicly accessible
- [ ] Verify: Slack Socket Mode still works (outbound-only, unaffected by firewall changes)
- [ ] End-to-end test: Teleport login → OpenClaw UI → trigger skill → Slack response

---

### 14C: OpenClaw Skill — scrum-planner

_Build the skill that conducts conversational scrum planning intake in Slack, calls scrum-agent headless, and posts results as a Slack Canvas._

#### Skill Definition (SKILL.md)

- [x] Create `SKILL.md` for the scrum-planner skill (OpenClaw skill format)
- [x] Define trigger: Slack mention or slash command with project description (becomes Q1)
- [x] Define skill persona: friendly Scrum Master conducting a quick intake

#### Conversational Intake

_Ask ~5-7 essential questions conversationally in a Slack thread. Q1 comes from the trigger message._

- [x] Map essential questions to Slack thread conversation:
  - Q1: project description (from trigger message)
  - Q2: greenfield / existing / hybrid (choice buttons)
  - Q3+Q4 merged: problem, users, and definition of done (single free-text)
  - Q6: team size (free-text or buttons: 1-3, 4-6, 7-10, 10+)
  - Q8: sprint length (buttons: 1 week, 2 weeks, 3 weeks, 4 weeks)
  - Q11: tech stack (free-text)
- [x] Implement question flow with Slack Block Kit interactive messages
- [x] Support "skip" / "use defaults" to fast-track remaining questions
- [x] Collect answers into a structured dict matching scrum-agent's intake format

#### SCRUM.md Bridge

_Write non-CLI answers to a temp SCRUM.md so `scrum-agent --non-interactive` picks them up via keyword extraction._

- [x] Generate temp SCRUM.md from collected answers (tech stack → `## Tech Stack`, constraints → `## Constraints`, etc.)
- [x] Map Q6 → `--team-size`, Q8 → `--sprint-length` as CLI args
- [x] Map Q1 → `--description` as CLI arg
- [x] Write remaining answers to temp SCRUM.md in working directory
- [x] Call: `scrum-agent --non-interactive --description "<Q1>" --team-size <Q6> --sprint-length <Q8> --output json`
- [x] Parse JSON output into structured plan data
- [x] Clean up temp SCRUM.md after run

#### Slack Canvas Output

_Plans exceed the 50-block message limit; Canvas has no limit. Fallback chain: Canvas → threaded messages → file upload._

- [x] Format plan JSON into Slack Canvas document (rich text with headers, tables, bullet points)
- [x] Canvas sections: Project Summary, Epics, User Stories (grouped by epic), Tasks, Sprint Plan
- [x] Create Canvas in the channel via `canvases.create` API
- [x] Implement fallback chain:
  1. Try Canvas → if API unavailable or permissions missing
  2. Fall back to threaded messages (chunked to stay under 50 blocks per message)
  3. Final fallback: upload as formatted Markdown file
- [x] Post summary message in channel linking to Canvas: "Sprint plan ready — X epics, Y stories across Z sprints"

#### Error Handling

- [x] Handle scrum-agent CLI failures (non-zero exit, timeout after 5 minutes)
- [ ] Handle Slack API errors (rate limits, permissions, Canvas API unavailability)
- [ ] Show progress updates in thread: "Analysing project...", "Generating stories...", "Building sprint plan..."
- [x] Surface actionable error messages in Slack (e.g., "API key not configured — ask an admin")

---

### 14D: Infrastructure as Code

_Terraform modules and CI/CD in this repo under `infra/`, not a separate repo._

#### Directory Setup

- [ ] Create `infra/` directory in scrum-jira-agent:
  ```
  infra/
    terraform/
      modules/
        lightsail/       — instance (OpenClaw blueprint), static IP, firewall, DNS
        teleport/        — cluster config, roles, app proxy
        networking/      — Route53 DNS records, TLS cert automation
      environments/
        prod/            — root module composing all modules
      variables.tf       — shared variables
    scripts/
      user-data.sh       — post-boot config (scrum-agent, Teleport, Slack tokens)
      update-openclaw.sh — pull latest, restart service
  ```
- [ ] Add `infra/.gitignore` (`.terraform/`, `*.tfstate`, `*.tfstate.backup`)

#### Terraform Modules

- [ ] **lightsail** module — OpenClaw blueprint instance, static IP, firewall rules
- [ ] **teleport** module — Teleport config generation, app proxy config, user/role setup
- [ ] **networking** module — Route53 DNS records (or Lightsail DNS), TLS cert automation
- [ ] Root module (`environments/prod/`) composing all modules with variables

#### User-Data Script

- [ ] scrum-agent installation (`pipx install scrum-agent`)
- [ ] Teleport installation and initial config
- [ ] Pull Slack tokens from AWS Secrets Manager
- [ ] Systemd service files for Teleport

#### CI/CD Workflows

- [ ] `infra-plan.yml` — on PR (changes to `infra/`): `terraform plan` → post as PR comment
- [ ] `infra-apply.yml` — on merge to main (changes to `infra/`): `terraform apply -auto-approve`
- [ ] AWS credentials via GitHub OIDC (no long-lived keys)
- [ ] State stored in S3 + DynamoDB lock table

---

### 14E: Productionize

_Secrets, monitoring, backups, security hardening, cost optimization._

#### Secrets Management

- [ ] Store all secrets in AWS Secrets Manager (Slack tokens, API keys, Teleport join token)
- [ ] User-data script pulls secrets at boot — no secrets in Terraform state or git
- [ ] Rotate Slack tokens and API keys on a schedule (or document manual rotation)

#### Monitoring & Alerting

- [ ] CloudWatch agent on Lightsail instance (CPU, memory, disk)
- [ ] CloudWatch alarms: CPU > 80%, disk > 90%, instance health check failed
- [ ] Application-level health check endpoint (OpenClaw + Teleport)
- [ ] SNS topic for alerts → email or Slack notification

#### Backups

- [ ] Automated Lightsail snapshots (daily, retain 7 days)
- [ ] Teleport audit log export (S3 or CloudWatch Logs)
- [ ] Document restore procedure from snapshot

#### Security Hardening

- [ ] Teleport session recording enabled for audit trail
- [ ] Fail2ban or equivalent for Teleport's HTTPS endpoint
- [ ] Regular OS security updates (unattended-upgrades)
- [ ] Principle of least privilege: IAM role scoped to Bedrock + Secrets Manager + S3 only
- [ ] Review and document Slack App permissions (minimal scopes)

#### Cost Optimization

- [ ] Right-size Lightsail plan based on actual usage (start $10/mo, monitor for 2 weeks)
- [ ] Lightsail bandwidth included (2 TB) — verify Slack traffic stays well within
- [ ] Bedrock pay-per-use — monitor token costs via CloudWatch
- [ ] Set AWS billing alarm at $30/mo threshold

---

### 14F: Polish & Documentation

_README, architecture diagram, runbook, skill docs._

#### Documentation

- [ ] Architecture diagram (Lightsail + Teleport + OpenClaw + Slack data flow)
- [ ] Runbook: common operations (restart services, rotate secrets, update scrum-agent, restore from snapshot)
- [ ] Skill documentation: how to trigger, expected questions, output format, troubleshooting

#### Developer Experience

- [ ] Makefile targets: `make infra-plan`, `make infra-apply`, `make ssh` (via Teleport), `make logs`
- [ ] Local development setup: run skill locally against a test Slack workspace
- [ ] Document how to add new OpenClaw skills alongside scrum-planner

#### Nice-to-Have (post-launch)

- [ ] Slack slash command (`/plan`) as an alternative trigger to @mention
- [ ] Plan history — store past plans in S3, list recent plans via Slack command
- [ ] Multi-workspace support — serve multiple Slack workspaces from one instance
- [ ] Plan diff — re-run planning and show what changed since last run
- [ ] Integration with Jira — after Canvas review, one-click push to Jira from Slack
