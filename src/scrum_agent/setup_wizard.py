"""First-run setup wizard for scrum-agent credentials.

# See README: "Architecture" — the CLI layer is responsible for user-facing
# chrome. The wizard runs once before any REPL loop starts, collecting
# credentials and storing them in ~/.scrum-agent/.env for future sessions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from prompt_toolkit import prompt
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from scrum_agent.config import get_config_file
from scrum_agent.ui.provider_select import select_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

# Each entry: display name, env var for the API key, value written to
# LLM_PROVIDER, expected key prefix (for soft format validation), and
# a URL hint so users know where to get the key.
_PROVIDERS: dict[str, dict[str, str]] = {
    "1": {
        "name": "Anthropic (Claude)",
        "env_var": "ANTHROPIC_API_KEY",
        "provider_val": "anthropic",
        "prefix": "sk-ant-",
        "instructions": "Get yours at: https://console.anthropic.com → API Keys",
    },
    "2": {
        "name": "OpenAI (GPT)",
        "env_var": "OPENAI_API_KEY",
        "provider_val": "openai",
        "prefix": "sk-",
        "instructions": "Get yours at: https://platform.openai.com → API keys",
    },
    "3": {
        "name": "Google (Gemini)",
        "env_var": "GOOGLE_API_KEY",
        "provider_val": "google",
        "prefix": "AIza",
        "instructions": "Get yours at: https://aistudio.google.com → Get API key",
    },
    "4": {
        "name": "AWS (Bedrock)",
        "env_var": "AWS_REGION",
        "provider_val": "bedrock",
        "prefix": "",
        "instructions": "Uses IAM credentials from instance role, ~/.aws/credentials, or env vars",
    },
}


def is_first_run() -> bool:
    """Return True if ~/.scrum-agent/.env is missing or has no key=value entries.

    A file with only whitespace or blank lines is treated as empty — this
    handles the case where save_config() writes a trailing newline but no
    actual credentials (e.g. user cancelled mid-wizard).
    """
    config = get_config_file()
    if not config.exists():
        return True
    content = config.read_text().strip()
    return len(content) == 0


def save_config(data: dict[str, str]) -> Path:
    """Write key=value pairs to ~/.scrum-agent/.env.

    Overwrites the file — safe because we read existing values first
    and merge them in run_setup_wizard() before calling save_config().
    Returns the path written.
    """
    config_file = get_config_file()
    lines = [f"{k}={v}\n" for k, v in data.items() if v]
    config_file.write_text("".join(lines))
    logger.info("Config saved to %s (keys: %s)", config_file, ", ".join(data.keys()))
    return config_file


def _read_existing_config(config_file: Path) -> dict[str, str]:
    """Parse key=value pairs from an existing config file."""
    if not config_file.exists():
        return {}
    result: dict[str, str] = {}
    for line in config_file.read_text().splitlines():
        stripped = line.strip()
        if stripped and "=" in stripped and not stripped.startswith("#"):
            k, _, v = stripped.partition("=")
            result[k.strip()] = v.strip()
    return result


def _collect_provider(console: Console) -> dict[str, str]:
    """Show full-screen provider selection and return the chosen provider info dict.

    # See README: "Architecture" — the CLI layer owns user-facing chrome.
    # This delegates to the full-screen Rich Live provider selector for an
    # interactive arrow-key selection experience.

    Falls back to inline text prompts if the terminal doesn't support raw mode
    (e.g. during testing when stdin isn't a real TTY).
    """
    result = select_provider(console)
    if result is not None:
        return result

    # User cancelled (q/Esc) — fall back to default (Anthropic)
    return _PROVIDERS["1"]


def _collect_api_key(console: Console, provider: dict[str, str]) -> str | None:
    """Prompt for the API key with format validation and retry loop.

    - Empty input → prints error, returns None (caller should return False)
    - Wrong prefix → warns and asks to re-enter; typing 'n' accepts the key as-is
    - Correct format → returns key immediately

    Returns the key string, or None if user provided an empty key.
    """
    console.print("\n[bold]Step 2/3[/bold] API Key [required]")
    console.print(f"  {provider['instructions']}")

    while True:
        key = prompt(f"  {provider['env_var']}: ", is_password=True).strip()
        if not key:
            console.print(f"[red]{provider['env_var']} is required. Exiting setup.[/red]")
            return None
        if not key.startswith(provider["prefix"]):
            console.print(
                f"[yellow]Warning: key doesn't look like a {provider['name']} key "
                f"(expected prefix: {provider['prefix']}...).[/yellow]"
            )
            retry = prompt("  Re-enter key? [Y/n]: ").strip().lower()
            if retry != "n":
                continue  # re-prompt for key
        return key  # valid format, or user explicitly declined retry


def run_setup_wizard(console: Console) -> bool:
    """Interactive credential setup wizard.

    Returns True if setup completed successfully, False if user cancelled.
    Collected values are written to ~/.scrum-agent/.env and then loaded
    into the current process via os.environ so they're immediately active.
    """
    logger.info("Setup wizard started")
    config_file = get_config_file()

    # Welcome panel
    body = Text.from_markup(
        "[bold cyan]Welcome to Scrum AI Agent — First-Time Setup[/bold cyan]\n\n"
        "We'll collect your API credentials now. Everything is stored locally\n"
        "in [cyan]~/.scrum-agent/.env[/cyan] — never sent anywhere else."
    )
    console.print(Panel(body, border_style="cyan", padding=(1, 2)))

    collected: dict[str, str] = {}

    # ── Steps 1 & 2: Provider selection + API key (full-screen UI) ──────────
    existing = _read_existing_config(config_file)
    result = select_provider(console, existing_config=existing)
    if result is None:
        logger.info("Setup wizard cancelled by user")
        return False

    # If the full-screen UI returned an api_key, use it directly.
    # Otherwise fall back to the inline prompt.
    provider = result
    collected["LLM_PROVIDER"] = provider["provider_val"]

    api_key = result.get("api_key")
    if api_key:
        collected[provider["env_var"]] = api_key
    else:
        key = _collect_api_key(console, provider)
        if key is None:
            return False
        collected[provider["env_var"]] = key

    # ── Step 3: Version control (collected in full-screen UI) ─────────────
    vc_env_var = result.get("vc_env_var")
    vc_token = result.get("vc_token")
    if vc_env_var and vc_token:
        collected[vc_env_var] = vc_token

    # ── Step 4: Issue tracking (collected in full-screen UI) ────────────
    issue_tracking = result.get("issue_tracking", {})
    collected.update(issue_tracking)

    # ── Merge with existing config and save ─────────────────────────────────
    # collected values win over existing so --setup re-runs update keys
    existing = _read_existing_config(config_file)
    merged = {**existing, **collected}
    save_config(merged)

    # Load into current process so keys are immediately active for this session
    os.environ.update({k: v for k, v in merged.items() if v})

    console.print(f"\n[green]Setup complete! Config saved to {config_file}[/green]")
    logger.info("Setup wizard completed successfully")
    return True
