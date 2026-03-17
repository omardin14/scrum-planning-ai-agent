# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Terminal-based AI Scrum Master agent built with LangGraph, LangChain, and Anthropic Claude. Decomposes projects into epics, user stories, tasks, and sprint plans.

## Commands

- `make test` — unit + integration + contract tests (full suite, no API keys needed)
- `make test-fast` — unit tests only (< 3s, tight edit-test loop)
- `make test-v` — full suite verbose
- `make test-all` — everything including golden evaluators
- `make lint` — lint with ruff
- `make format` — format with ruff
- `make run` — run the CLI (`ARGS="--flag"` to pass arguments)
- `make run-dry` — run TUI with fake delays, no LLM calls
- `make eval` — run golden dataset evaluators
- `make contract` — run contract tests (recorded API responses)
- `make smoke-test` — live API smoke tests (requires real credentials)
- `make snapshot-update` — update syrupy snapshot baselines after formatter changes
- `make budget-report` — show prompt token counts for trend monitoring
- Run a single test: `uv run pytest tests/unit/test_state.py -v`
- Run a single test class: `uv run pytest tests/unit/test_state.py::TestPriority -v`

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
- "Architecture" — four layers, three design principles, agent graph
- "The ReAct Loop" — Thought → Action → Observation pattern
- "Agentic Blueprint Reference" — core graph setup, two core nodes, wiring, tools, memory, streaming
- "Prompt Construction" — ARC framework, few-shot, chain-of-thought, flipped prompt
- "Memory & State" — MemorySaver, thread_id, session persistence
- "Guardrails" — three lines of defence, human-in-the-loop pattern
- "Tools" — tool types, risk levels, MCP
- "Scrum Standards" — story format, acceptance criteria, story points, DoD

## REQUIRED: Progress Tracking

After completing any implementation step, IMMEDIATELY update `TODO.md`:
- Change `- [ ]` to `- [x]` for the completed item
- Do NOT wait for the developer to ask — do it automatically as part of the workflow

## REQUIRED: Verification

After every code change, ALWAYS run:
1. `make test` — all tests must pass
2. `make lint` — must be clean

Do NOT commit until both pass.

## Project Structure

```
src/scrum_agent/
  cli.py              — CLI entry point (argparse, session mgmt, TUI mode/provider selection)
  config.py           — Environment/config (API keys, LangSmith)
  persistence.py      — Session persistence layer (checkpoint system)
  sessions.py         — SessionStore and session management
  setup_wizard.py     — First-time setup flow
  html_exporter.py    — Export plans to HTML
  questionnaire_io.py — Import/export questionnaire templates
  formatters.py       — Rich Table/Panel rendering
  input_guardrails.py — Input validation and safety checks
  output_guardrails.py— Output filtering and validation
  agent/              — LangGraph agent (state schema, graph, nodes)
  prompts/            — System prompts and prompt templates
  tools/              — Tool definitions (@tool decorated functions)
  repl/               — Legacy REPL package (prompt_toolkit, rich streaming)
  ui/                 — New TUI system (screen-based, composable)
    mode_select/      — TUI mode selection screens
    provider_select/  — LLM/tool provider selection
    session/          — Main session UI (phases, editor, accordion, pipeline)
    shared/           — Shared UI components (animations, ascii font, input)
tests/
  unit/               — Fast unit tests (one file per source module)
  integration/        — Graph compilation, multi-node flows, CLI, REPL
  contract/           — Contract tests with recorded API responses (VCR cassettes)
  smoke/              — Live API smoke tests (requires credentials)
  golden/             — Golden dataset evaluators
  fixtures/           — Test data files (SCRUM.md, questionnaire-answers.md)
  _node_helpers.py    — Shared factory functions + JSON fixtures for node tests
```

- Agent logic lives in `agent/` — state, graph wiring, and node functions
- Prompts are separate from agent logic in `prompts/`
- Tools are separate in `tools/` — each tool gets a `@tool` decorator with a descriptive docstring
- Re-export public APIs from `__init__.py` (e.g. `from scrum_agent.agent import ScrumState`)
- The `ui/` package is the new TUI system with 4 subsystems (mode_select, provider_select, session, shared); `repl/` is the legacy REPL kept for backwards compatibility

## App Flow

CLI (`cli.py`) → splash screen → mode selection TUI → provider selection → session REPL. Sessions can be listed (`--list-sessions`) and resumed (`--resume`). `--dry-run` runs the TUI with fake delays and no LLM calls.

## State Schema Conventions

- **ScrumState** is a `TypedDict` — this is the LangGraph convention for graph state
- `messages` is the only required field, using `Annotated[list[BaseMessage], add_messages]` for append semantics
- All other fields are optional (`total=False`) and populated progressively as nodes run
- **Frozen dataclasses** for artifacts (Epic, UserStory, Task, Sprint) — immutable once created, serializable via `asdict()`
- **Mutable dataclass** for QuestionnaireState — updated incrementally by the intake node
- Artifact lists use `Annotated[list[...], operator.add]` so nodes can return new items that get appended
- When adding new state fields: add to `ScrumState`, add tests in `test_state.py`, update `__init__.py` exports if public

## Testing Conventions

- One test file per source module: `repl.py` → `test_repl.py`, `state.py` → `test_state.py`
- Group related tests in classes: `TestGracefulExit`, `TestStreaming`, `TestPriority`
- Use `pytest` fixtures for shared setup (e.g. `_make_console()` for rich Console with StringIO buffer)
- Use `monkeypatch` to avoid filesystem writes, network calls, and delays in tests
- Test both happy path and edge cases (empty input, boundary values, immutability)
- Node tests live in `tests/unit/nodes/` (split from a large test_nodes.py into 9 files)
- Shared node test helpers in `tests/_node_helpers.py`
- **Never modify `tests/integration/test_repl.py`** — it monkeypatches 10 names in `scrum_agent.repl` and is fragile
- Pytest markers: `slow` (graph compilation), `eval` (golden evaluators), `vcr` (contract tests), `smoke` (live API)

## Environment Setup

- `ANTHROPIC_API_KEY` — required, used for Claude LLM calls
- `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` — optional, enables LangSmith tracing for development
- Copy `.env.example` to `.env` and fill in keys (`make env`)
- Never commit `.env` or API keys

## Git Conventions

- **Commit messages**: lowercase imperative (e.g. "add streaming output", "fix import sorting")
- **Branch naming**: `feature/<description>` for feature work
- **PRs**: feature branches merge to `main` via pull request
- Include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` on AI-assisted commits
