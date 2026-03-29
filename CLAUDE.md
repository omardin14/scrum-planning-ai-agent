# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Terminal-based AI Scrum Master agent built with LangGraph, LangChain, and Anthropic Claude (with OpenAI, Google, and AWS Bedrock as alternative providers). Decomposes projects into epics, user stories, tasks, and sprint plans. Version 1.2.0. Deployed on AWS Lightsail via OpenClaw with Bedrock.

## Commands

```bash
make test                 # Unit + integration + contract tests (full suite, no API keys needed)
make test-fast            # Unit tests only (< 3s, tight edit-test loop)
make test-v               # Full suite verbose
make test-all             # Everything including golden evaluators
make lint                 # Lint with ruff
make format               # Format with ruff
make run                  # Run the CLI (ARGS="--flag" to pass arguments)
make run-dry              # Run TUI with fake delays, no LLM calls
make eval                 # Run golden dataset evaluators
make contract             # Run contract tests (recorded API responses)
make smoke-test           # Live API smoke tests (requires real credentials)
make snapshot-update      # Update syrupy snapshot baselines after formatter changes
make budget-report        # Show prompt token counts for trend monitoring
make graph                # Generate agent graph visualisation PNG
make build                # Build sdist + wheel into dist/
make publish              # Publish to PyPI
make record               # Re-record VCR cassettes against real APIs
make clean                # Remove build artifacts and caches
```

Run a single test: `uv run pytest tests/unit/test_state.py -v`
Run a single test class: `uv run pytest tests/unit/test_state.py::TestPriority -v`

## Code Style

- Python 3.11+, ruff for linting/formatting (line-length 120)
- Imports sorted by ruff (isort rules: stdlib, third-party, local)
- Tests in `tests/`, source in `src/scrum_agent/`

## REQUIRED: Learning-First Development

This is the developer's first AI agent. These are NOT optional — follow them on every implementation task.

1. **ALWAYS add `# See README: <section name>` comments** when introducing a LangGraph or LangChain concept for the first time in a file. Cross-reference the relevant README section so the developer can look up the theory.
2. **ALWAYS explain LangGraph/LangChain concepts in code comments** on first use — what a reducer does, why `add_messages` exists, what `StateGraph` expects, what `bind_tools` does, etc. Do NOT assume familiarity with these frameworks.
3. **ALWAYS explain architectural decisions** in your response — when choosing between approaches, state the trade-offs and why this approach was chosen.

Key README sections to reference:
- "Architecture" — four layers, three design principles, agent graph, TUI system
- "The ReAct Loop" — Thought → Action → Observation pattern
- "Agentic Blueprint Reference" — core graph setup, two core nodes, wiring, tools, memory, streaming
- "Prompt Construction" — ARC framework, few-shot, chain-of-thought, flipped prompt
- "Session Management" — SQLite persistence, --resume, session IDs
- "Guardrails" — input guardrails (4 layers), output guardrails (4 layers), human-in-the-loop
- "Tools" — 30 tools, tool types, risk levels
- "Scrum Standards" — story format, acceptance criteria, story points, DoD, discipline tagging

## REQUIRED: Progress Tracking

After completing any implementation step, IMMEDIATELY update `TODO.md`:
- Change `- [ ]` to `- [x]` for the completed item
- Do NOT wait for the developer to ask — do it automatically as part of the workflow

## REQUIRED: Verification

After every code change, ALWAYS run:
1. `make test` — all tests must pass
2. `make lint` — must be clean

Do NOT commit until both pass.

## REQUIRED: Observability & Test Coverage

Every new feature MUST include all three pillars before it can be considered complete:

### 1. Logging
- **Every user action** must have a `logger.info()` — entry, exit, key decisions, errors
- **Every LLM call** must log via `_llm_invoke()` (which calls `track_usage()` automatically)
- **Every external API call** (Jira, AzDO, GitHub) must log start + result
- **Every error path** must log at `warning` or `error` level with context
- Use `logger.debug()` for detailed data (response payloads, intermediate calculations)

### 2. Log Directory
- **New TUI pages** (Usage, Settings, etc.) use the appropriate log directory from `paths.py`
- **Analysis mode** logs go to `paths.ANALYSIS_LOGS_DIR` (~/.scrum-agent/logs/analysis/)
- **Planning mode** session logs go to `paths.PLANNING_LOGS_DIR` (~/.scrum-agent/logs/planning/)
- **TUI-level logs** go to `paths.TUI_LOGS_DIR` (~/.scrum-agent/logs/tui/)
- **Exports** use `paths.get_analysis_export_dir()` or `paths.get_planning_export_dir()`
- All paths MUST come from `src/scrum_agent/paths.py` — never hardcode `Path.home() / ".scrum-agent"`

### 3. Tests
- **Every new function** gets at least one unit test (happy path + error case)
- **Every new screen builder** (`_build_*_screen`) gets render tests (returns Panel, handles empty data, scrollable)
- **Every LLM-dependent function** gets mock tests (successful response, error fallback, code fence handling)
- **Every new state field** gets serialization round-trip tests
- **Secret/sensitive data** rendering must be tested for proper masking
- Tests live in `tests/unit/` — one file per source module or grouped by feature

## Project Structure

```
src/scrum_agent/
  __init__.py           — Version (__version__), LangSmith noise suppression
  cli.py                — CLI entry point (argparse, 20 flags, headless mode, session mgmt)
  config.py             — Environment/config (API keys, LangSmith, proxy detection)
  persistence.py        — Session persistence layer (checkpoint system)
  sessions.py           — SessionStore (SQLite), state serialization, schema versioning
  setup_wizard.py       — First-time setup flow (provider selection, API key validation)
  formatters.py         — Rich Table/Panel rendering (dark/light themes)
  html_exporter.py      — Export plans to self-contained HTML
  json_exporter.py      — Export plans to clean JSON (for CI/CD pipelines)
  jira_sync.py          — Batch Jira creation (idempotent, cascade, progress callbacks)
  azdevops_sync.py      — Batch Azure DevOps creation (idempotent, cascade, progress callbacks)
  questionnaire_io.py   — Import/export questionnaire templates as Markdown
  input_guardrails.py   — Input validation (length, injection, profanity, relevance)
  output_guardrails.py  — Output validation (story format, AC coverage, sprint capacity)
  agent/
    state.py            — ScrumState TypedDict, artifact dataclasses, enums
    graph.py            — Graph compilation and wiring (create_graph())
    nodes.py            — Node functions (intake, analyzer, generators, planner)
    llm.py              — LLM provider factory (Anthropic/OpenAI/Google/Bedrock, lazy imports)
  prompts/
    system.py           — Base system prompt (Scrum Master persona)
    intake.py           — 30 questions, smart/standard modes, adaptive templates, validation
    analyzer.py         — Project analysis prompt
    feature_generator.py— Feature generation prompt
    story_writer.py     — Story writing prompt with few-shot examples
    task_decomposer.py  — Task decomposition prompt
    sprint_planner.py   — Sprint planning prompt
  tools/
    __init__.py         — get_tools() factory (lazy imports all tool modules)
    github.py           — GitHub repo/file/issues/readme (4 tools)
    azure_devops.py     — Azure DevOps repo/file/work items/board/velocity/create (9 tools)
    jira.py             — Jira board/velocity/sprint/epic/story (6 tools)
    confluence.py       — Confluence search/read/write (5 tools)
    codebase.py         — Local repo scanning (3 tools)
    calendar_tools.py   — Bank holiday detection (1 tool)
    llm_tools.py        — LLM-powered estimation and AC generation (2 tools)
  repl/                 — Legacy REPL (used for CLI-flag-driven flows)
    __init__.py         — run_repl() entry point
    _intake_menu.py     — Intake mode selection
    _io.py              — Artifact rendering, file import/export, markdown export
    _mode_menu.py       — Mode selection menu
    _questionnaire.py   — Questionnaire UI (one-at-a-time flow)
    _review.py          — Review checkpoint UI (accept/edit/reject)
    _ui.py              — Pipeline progress, streaming, spinner, toolbar
  ui/                   — Full-screen TUI system
    splash.py           — Animated intro
    mode_select/        — Mode selection screens, project cards, project list
    provider_select/    — LLM/tool provider setup, verification
    session/            — Main session (phases, editor, pipeline, Jira export, dry-run)
    shared/             — Animations, ASCII font, components, mouse input
tests/
  unit/                 — Fast unit tests (one file per source module)
    nodes/              — Node tests split into ~9 files (analyzer, route, tasks, etc.)
  integration/          — Graph compilation, multi-node flows, CLI, REPL
  contract/             — Contract tests with recorded API responses (VCR cassettes)
  smoke/                — Live API smoke tests (requires credentials)
  golden/               — Golden dataset evaluators
  fixtures/             — Test data files (SCRUM.md, questionnaire-answers.md)
  _node_helpers.py      — Shared factory functions + JSON fixtures for node tests
```

Conventions:
- Agent logic lives in `agent/` — state, graph wiring, and node functions
- Prompts are separate from agent logic in `prompts/`
- Tools are separate in `tools/` — each tool gets a `@tool` decorator with a descriptive docstring
- Re-export public APIs from `__init__.py` (e.g. `from scrum_agent.agent import ScrumState`)
- The `ui/` package is the full-screen TUI; `repl/` is the legacy REPL for CLI-flag-driven flows (`--quick`, `--full-intake`, `--questionnaire`, `--mode`)
- Files prefixed with `_` inside `repl/` and `ui/` subpackages are internal — not public API

## App Flow

Two paths:

1. **Interactive TUI**: `cli.py` → splash → mode selection TUI → provider selection → session TUI
2. **CLI-flag / headless**: `cli.py` → `run_repl()` (for `--quick`, `--full-intake`, `--questionnaire`, `--mode`) or `_run_headless()` (for `--non-interactive`)

Sessions can be listed (`--list-sessions`), resumed (`--resume`), and cleared (`--clear-sessions`). `--dry-run` runs the TUI with fake delays and no LLM calls. `--non-interactive` runs the full pipeline headlessly with `--description` as input and `--output {json,html,markdown}` for format.

## CLI Flags

Key flags to know about when modifying the CLI:

| Flag | Description |
|------|-------------|
| `--non-interactive` | Headless mode (requires `--description`) |
| `--description TEXT` | Project description; `@file.txt` reads from file |
| `--output {markdown,json,html}` | Output format (only with `--non-interactive` or `--export-only`) |
| `--team-size N` | Maps to intake Q6 |
| `--sprint-length {1,2,3,4}` | Maps to intake Q8 |
| `--quick` / `--full-intake` | Mutually exclusive intake modes |
| `--questionnaire PATH` | Import filled-in questionnaire |
| `--export-only` | Auto-accept all review checkpoints |
| `--resume [ID]` | Resume session (no arg = picker, `latest` = most recent) |
| `--theme {dark,light}` | Terminal colour theme |
| `--dry-run` | TUI with mock data, no LLM calls |
| `--setup` | Re-run first-time setup wizard |
| `--install-skill [DIR]` | Install bundled OpenClaw skill to `~/.openclaw/skills/` (or custom dir) |

Validation rules in `main()`:
- `--non-interactive` requires `--description`
- `--output` requires `--non-interactive` or `--export-only`
- `--export-only` requires `--quick` or `--questionnaire`

## Node Conventions

Nodes are plain functions in `agent/nodes.py` taking `ScrumState` and returning a dict. Key patterns:

### Parse → Fallback → Format

Every generation node follows this three-helper pattern:
- `_parse_*_response(text)` — extract JSON from LLM response, handle markdown fences
- `_build_fallback_*()` — deterministic fallback artifacts when LLM fails (no LLM call)
- `_format_*()` — Rich rendering for REPL display

### Error handling

- `_is_llm_auth_or_billing_error(e)` checks if an exception is auth/billing — these are **re-raised** (user must fix credentials). All other LLM errors trigger **fallback artifacts** and a warning log.
- Rate-limit errors (429) are retried with exponential backoff in the REPL layer (`_handle_rate_limit`), not in nodes.

### Human-in-the-loop review

When a generation node produces output, it sets `pending_review` to the node name. The REPL intercepts the next user input and routes to `[1] Accept  [2] Edit  [3] Reject`. On edit, feedback is packed as `"{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}"` so the node can extract both the feedback and previous generation.

## Prompt Conventions

Prompts live in `prompts/` with a factory function per file:
- `get_system_prompt()`, `get_analyzer_prompt(questionnaire, ...)`, `get_feature_generator_prompt(analysis)`, etc.
- Each factory takes parameters (not the full state) and returns a string
- Prompts use the ARC framework: Ask (what to do), Requirements (constraints), Context (background)
- `# See README: "..."` comments cross-reference theory sections

## Tool Conventions

### Registration

All tools are registered via `get_tools()` in `tools/__init__.py`. This single factory function imports all tool modules and returns a flat list of `BaseTool` instances.

### Lazy imports

Tool modules are imported **inside** `get_tools()`, not at module level. This is because tool dependencies (PyGithub, azure-devops, jira SDK) may not be installed. Lazy import means `from scrum_agent.tools import get_tools` always succeeds; ImportError surfaces only when `get_tools()` is called.

### Adding a new tool

1. Create or extend a file in `tools/` with `@tool`-decorated functions
2. Import and append to the tools list in `get_tools()`
3. The docstring is critical — the LLM reads it to decide when to use the tool
4. Set risk level via the tool's position in the graph routing (auto-execute for read, human confirmation for write)

## LLM Provider Conventions

`agent/llm.py` provides `get_llm()` — a factory supporting Anthropic (default), OpenAI, Google, and AWS Bedrock:
- Each provider is **lazy-imported** inside an if-branch so the module works even if optional packages aren't installed
- Provider selected via `LLM_PROVIDER` env var; model override via `LLM_MODEL`
- Default models: `claude-sonnet-4-20250514` (Anthropic), `gpt-4o` (OpenAI), `gemini-2.0-flash` (Google), `us.anthropic.claude-sonnet-4-20250514-v1:0` (Bedrock)
- Install optional providers: `uv sync --extra openai` / `--extra google` / `--extra bedrock`
- **Bedrock** uses IAM credentials (no API key) — auto-detects AWS profile from `~/.aws/config` via `get_aws_profile()` in `config.py`. On Lightsail, uses `[profile assumed]` with `credential_source=Ec2InstanceMetadata`. The boto3 session is created with explicit profile + increased read timeout (300s) for cross-region inference profiles.

## State Schema Conventions

- **ScrumState** is a `TypedDict` — this is the LangGraph convention for graph state
- `messages` is the only required field, using `Annotated[list[BaseMessage], add_messages]` for append semantics
- All other fields are optional (`total=False`) and populated progressively as nodes run
- **Frozen dataclasses** for artifacts (Feature, UserStory, Task, Sprint, ProjectAnalysis) — immutable once created, serializable via `asdict()`
- **Mutable dataclass** for QuestionnaireState — updated incrementally by the intake node
- Artifact lists use `Annotated[list[...], operator.add]` so nodes can return new items that get appended

### Adding new state fields

1. Add to `ScrumState` in `agent/state.py`
2. Add tests in `test_state.py`
3. Update `__init__.py` exports if public
4. If the field should persist across `--resume`, it serializes automatically (only `messages` is skipped)
5. If adding a field to a **frozen dataclass**, always provide a default value for backward compatibility with saved sessions (e.g., `title: str = ""`, `discipline: Discipline = Discipline.FULLSTACK`)

### Frozen dataclass backward compatibility

New fields on frozen dataclasses MUST have defaults so deserialization of old JSON doesn't fail:
```python
# Good — old sessions without this field still deserialize
title: str = ""
discipline: Discipline = Discipline.FULLSTACK
test_plan: str = ""

# Bad — breaks --resume for sessions saved before this field existed
title: str   # no default!
```

The `_dict_to_*()` functions in `sessions.py` use `.get()` for optional fields so missing keys don't raise KeyError.

## Session Persistence

### Serialization

`sessions.py` handles state serialization for `--resume`:
- `messages` is the only field skipped (not needed for resume; re-initialized to `[]`)
- Custom `_StateEncoder` handles: frozen dataclasses (`asdict()`), enums (`.value`), sets (→ lists), tuples (→ lists)
- Reconstruction functions: `_dict_to_analysis()`, `_dict_to_story()`, `_dict_to_task()`, `_dict_to_sprint()`, `_dict_to_questionnaire()` — each handles type conversion (enum parsing, tuple reconstruction)

### Schema versioning

- `CURRENT_SCHEMA_VERSION = 5` tracked in a `schema_info` table (v3=team_profiles, v4=session_mode, v5=token_usage)
- On startup: if stored version > current → `schema_mismatch = True` (warn user); if stored version < current → run migrations
- Session IDs: internal `new-<8hex>-<YYYY-MM-DD>`, display `<project-slug>-<YYYY-MM-DD>`

## Testing Conventions

- One test file per source module: `repl.py` → `test_repl.py`, `state.py` → `test_state.py`
- Group related tests in classes: `TestGracefulExit`, `TestStreaming`, `TestPriority`
- Use `pytest` fixtures for shared setup (e.g. `_make_console()` for rich Console with StringIO buffer)
- Use `monkeypatch` to avoid filesystem writes, network calls, and delays in tests
- Test both happy path and edge cases (empty input, boundary values, immutability)
- Node tests live in `tests/unit/nodes/` — split into ~9 files by node (analyzer, route, tasks, etc.)
- Shared node test helpers in `tests/_node_helpers.py`: `make_completed_questionnaire()`, `make_dummy_analysis()`, `make_sample_features()`, `make_sample_stories()`, `make_sample_sprints()`
- **Never modify `tests/integration/test_repl.py`** — it monkeypatches 10+ names in `scrum_agent.repl` and is the only test file with this level of coupling. Future tests should avoid this pattern.
- Pytest markers: `slow` (graph compilation), `eval` (golden evaluators), `vcr` (contract tests), `smoke` (live API)

## Logging

- **TUI log**: `~/.scrum-agent/logs/tui/scrum-agent.log` (rotates at 2 MB, 3 backups)
- **Analysis logs**: `~/.scrum-agent/logs/analysis/team-analysis-{project}-{timestamp}.log`
- **Planning logs**: `~/.scrum-agent/logs/planning/{session-id}.log`
- Log level controlled by `LOG_LEVEL` env var (default: `WARNING`; set to `DEBUG` for full diagnostics)
- All paths defined in `src/scrum_agent/paths.py` — use `get_tui_log_path()`, `get_analysis_log_dir()`, `get_planning_log_dir()`
- LangSmith 429 rate-limit errors are auto-suppressed via a custom logging filter in `__init__.py`
- Token usage is tracked via `track_usage()` in `agent/llm.py` and persisted to `token_usage` table in SQLite

## Environment Setup

- `ANTHROPIC_API_KEY` — required when using Anthropic (default provider)
- `OPENAI_API_KEY` — required when `LLM_PROVIDER=openai`
- `GOOGLE_API_KEY` — required when `LLM_PROVIDER=google`
- `AWS_REGION` — required when `LLM_PROVIDER=bedrock` (auto-detected from `~/.aws/config` on Lightsail)
- `AWS_PROFILE` — optional, auto-detected from `~/.aws/config` (looks for profiles with `credential_source` or `role_arn`)
- `LLM_PROVIDER` — `anthropic` (default), `openai`, `google`, `bedrock`
- `LLM_MODEL` — optional model override for the selected provider
- `GITHUB_TOKEN`, `AZURE_DEVOPS_TOKEN` — optional, for repo context tools
- `AZURE_DEVOPS_ORG_URL`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_TEAM` — optional, for Azure DevOps board sync
- `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` — optional, for Jira integration
- `CONFLUENCE_SPACE_KEY` — optional, shares Atlassian auth with Jira
- `SESSION_PRUNE_DAYS` — auto-prune sessions older than N days (default: 30, 0 = disabled)
- `LOG_LEVEL` — file logger level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)
- `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` — optional, enables LangSmith tracing
- Copy `.env.example` to `.env` and fill in keys (`make env`)
- Never commit `.env` or API keys

## Version Management

Version is defined in two places that **must be kept in sync**:
- `src/scrum_agent/__init__.py`: `__version__ = "1.2.0"`
- `pyproject.toml`: `version = "1.2.0"`

The `__version__` is imported by `cli.py` for the `--version` flag. Package entry point: `scrum-agent = "scrum_agent.cli:main"`.

Releasing: bump version in both files and merge to main. The `publish.yml` workflow triggers on `v*` tag push → PyPI → GitHub Release → Homebrew tap update.

## CI/CD

Workflows in `.github/workflows/`:

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Every push | Lint + test |
| `publish.yml` | Tag push `v*` | test → build → PyPI publish (OIDC) → GitHub Release → Homebrew tap update |
| `smoke.yml` | Weekly cron | Live API smoke tests |
| `claude.yml` | PR | Claude Code review |
| `claude-code-review.yml` | PR | Claude Code review (alternate) |

The `publish.yml` dispatches a `repository_dispatch` event to `omardin14/homebrew-tap` to auto-update the formula SHA256 and version on each release.

## OpenClaw Skill

The `skills/scrum-planner/` directory contains an OpenClaw skill that replicates the smart intake TUI experience conversationally. OpenClaw acts as the front-end (asks questions, handles follow-ups), then invokes `scrum-agent --non-interactive` as the back-end.

**How it works:**
1. OpenClaw asks ~7 essential questions (matching `SMART_ESSENTIALS` from `prompts/intake.py`)
2. Answers map to: Q1/Q6/Q8 → CLI args, everything else → temp `SCRUM.md` in CWD
3. Invokes: `scrum-agent --non-interactive --description "Q1" --team-size Q6 --sprint-length Q8 --output json`
4. `_load_user_context()` in `nodes.py` reads the SCRUM.md from CWD, `_keyword_extract_fallback()` does keyword scanning
5. JSON output is parsed and presented to the user

**Key files:**
- `skills/scrum-planner/SKILL.md` — agent instructions (persona, conversation flow, SCRUM.md generation, CLI invocation, output formatting)
- `skills/scrum-planner/README.md` — installation and usage docs
- `skills/scrum-planner/scripts/` — helper scripts for the skill
- `skills/scrum-planner/references/` — reference material

**Installing the skill:**
```bash
scrum-agent --install-skill          # installs to ~/.openclaw/skills/
scrum-agent --install-skill /path    # custom directory
```

## Deployment (AWS Lightsail)

scrum-agent is deployed on AWS Lightsail via the OpenClaw blueprint:
- OpenClaw comes pre-installed on the Lightsail instance
- Uses Amazon Bedrock (Claude Sonnet 4.6) via IAM instance role — no API key needed
- Bedrock IAM setup script: `curl -s https://d25b4yjpexuuj4.cloudfront.net/scripts/lightsail/setup-lightsail-openclaw-bedrock-role.sh | bash -s -- <instance-name> <region>`
- The setup wizard auto-detects the AWS region from `~/.aws/config` and the Bedrock model from OpenClaw's `models.json`
- See README section "Deploy on AWS Lightsail (OpenClaw)" for full guide

## Git Conventions

- **Commit messages**: lowercase imperative (e.g. "add streaming output", "fix import sorting")
- **Branch naming**: `feature/<description>` for feature work
- **PRs**: feature branches merge to `main` via pull request
- Include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` on AI-assisted commits
