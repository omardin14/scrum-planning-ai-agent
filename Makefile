UV := $(or $(shell command -v uv 2>/dev/null),$(HOME)/.local/bin/uv)

.PHONY: install dev test test-fast test-v test-all lint format run run-dry clean env pre-commit graph eval contract record smoke-test snapshot-update budget-report help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install uv (if missing) and project dependencies
	@command -v uv >/dev/null 2>&1 || (echo "Installing uv..." && curl -LsSf https://astral.sh/uv/install.sh | sh)
	$(UV) venv
	$(UV) pip install -e ".[dev]"

env: ## Copy .env.example to .env (won't overwrite existing)
	@if [ -f .env ]; then echo ".env already exists — skipping"; else cp .env.example .env && echo "Created .env from .env.example — fill in your keys"; fi

test-fast: ## Unit tests only — < 3s, no graph compilation (tight edit-test loop)
	$(UV) run pytest tests/unit/ --tb=short -q
	@echo "✓ Unit tests passed"

test: ## Unit + integration + contract tests — full suite, no API keys needed
	$(UV) run pytest tests/unit/ tests/integration/ tests/contract/ --tb=short
	@echo "✓ All tests passed"

test-v: ## Unit + integration + contract tests (verbose)
	$(UV) run pytest tests/unit/ tests/integration/ tests/contract/ -v

test-all: ## Everything including golden evaluators (requires make eval separately for golden)
	$(UV) run pytest --ignore=tests/smoke --tb=short
	@echo "✓ Full test suite passed"

lint: ## Lint with ruff
	$(UV) run ruff check src/ tests/

format: ## Format with ruff
	$(UV) run ruff format src/ tests/

pre-commit: ## Install pre-commit hooks
	$(UV) run pre-commit install

run: ## Run the scrum-agent CLI (use ARGS="--flag" to pass arguments)
	$(UV) run scrum-agent $(ARGS)

run-dry: ## Run the TUI with fake delays — no LLM calls
	$(UV) run scrum-agent --dry-run $(ARGS)

eval: ## Run golden dataset evaluators
	$(UV) run pytest tests/golden/ -v

contract: ## Run contract tests (recorded API responses + LLM provider parsing)
	$(UV) run pytest tests/contract/ -v

smoke-test: ## Run smoke tests against live APIs (requires real credentials)
	$(UV) run pytest tests/smoke/ -v -m smoke

record: ## Re-record VCR cassettes against real APIs (requires API keys)
	$(UV) run pytest tests/ -v --record-mode=rewrite -m vcr

snapshot-update: ## Update syrupy snapshot baselines after intentional formatter changes
	$(UV) run pytest tests/unit/test_formatters.py --snapshot-update -v

budget-report: ## Show live prompt token counts for trend monitoring (runs token budget tests with -s)
	$(UV) run pytest tests/unit/test_token_budgets.py -v -s

graph: ## Generate agent graph visualisation PNG
	$(UV) run python scripts/generate_graph_png.py

clean: ## Remove build artifacts and caches
	rm -rf .venv build dist .pytest_cache .ruff_cache *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
