"""CLI entry point for scrum-agent."""

import argparse
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from scrum_agent import __version__
from scrum_agent.config import (
    detect_proxy,
    disable_langsmith_tracing,
    get_log_level,
    is_langsmith_enabled,
    load_user_config,
)
from scrum_agent.formatters import build_theme
from scrum_agent.persistence import migrate_history_file
from scrum_agent.questionnaire_io import (
    build_questionnaire_from_answers,
    export_questionnaire_md,
    parse_questionnaire_md,
)
from scrum_agent.repl import run_repl
from scrum_agent.sessions import SessionStore, make_display_name, make_unique_display_names
from scrum_agent.setup_wizard import is_first_run, run_setup_wizard
from scrum_agent.ui.mode_select import select_mode
from scrum_agent.ui.splash import show_splash

# Default filename for exported questionnaire templates
DEFAULT_QUESTIONNAIRE_FILENAME = "scrum-questionnaire.md"

# Default DB path — inside the user config directory alongside history/config.
# Matches the path used by SessionStore in run_repl().
_SESSIONS_DB_DIR = Path.home() / ".scrum-agent"


def _summarise_scrum_md(console: Console, path: Path) -> None:
    """Print a brief pre-flight summary of the SCRUM.md file.
    Shows line count, URL count, and detected ## sections so users can
    confirm the right file was picked up before the analysis runs.
    """
    try:
        content = path.read_text()
    except OSError:
        console.print("[dim]  SCRUM.md detected — your project context will be included in the analysis.[/dim]")
        return

    lines = content.count("\n") + 1
    url_count = len(re.findall(r"https?://", content))
    sections = [ln.lstrip("#").strip() for ln in content.splitlines() if ln.startswith("## ")]

    stats = f"{lines} lines" + (f", {url_count} URL{'s' if url_count != 1 else ''}" if url_count else "")
    console.print(f"[dim]  SCRUM.md detected ({stats})[/dim]")
    if sections:
        console.print(f"[dim]    Sections: {' · '.join(sections)}[/dim]")


def _build_welcome_panel() -> Panel:
    """Build the branded welcome panel with version and quick-start hint.

    # See README: "Architecture" — the CLI layer is the outermost layer,
    # responsible for user-facing chrome like the welcome screen.
    """
    body = Text.from_markup(
        f"[bold cyan]Scrum AI Agent[/bold cyan]  [dim]v{__version__}[/dim]\n"
        "[white]Your AI-powered Scrum Master[/white]\n\n"
        "[dim]Describe your project to get started, or type [cyan]help[/cyan] for commands.[/dim]"
    )
    return Panel(body, border_style="cyan", padding=(1, 2))


# ---------------------------------------------------------------------------
# Session listing / picker helpers
# ---------------------------------------------------------------------------


def _build_sessions_table(sessions: list[dict], display_names: dict[str, str] | None = None) -> Table:
    """Build a Rich Table of saved sessions.

    Used by both --list-sessions and the interactive --resume picker.

    Args:
        sessions: List of session metadata dicts.
        display_names: Optional ``{session_id: unique_name}`` mapping from
            ``make_unique_display_names()``. When provided, the Project column
            shows the collision-free display name instead of the raw project_name.
    """
    table = Table(title="Saved sessions", show_lines=False, padding=(0, 1))
    table.add_column("#", style="bold", width=3)
    table.add_column("Project", style="cyan")
    table.add_column("Date", style="dim")
    table.add_column("Last Step", style="green")
    table.add_column("Session ID", style="dim")
    for i, meta in enumerate(sessions, 1):
        sid = meta.get("session_id", "")
        if display_names and sid in display_names:
            project = display_names[sid]
        else:
            project = meta.get("project_name") or "(unnamed)"
        date_str = meta.get("created_at", "")[:10]
        last_node = meta.get("last_node_completed") or "-"
        table.add_row(str(i), project, date_str, last_node, sid)
    return table


def _print_sessions_table(console: Console) -> None:
    """Print a table of all saved sessions and exit.

    Used by --list-sessions. Opens its own SessionStore so it works
    independently from the REPL.
    """
    _SESSIONS_DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _SESSIONS_DB_DIR / "sessions.db"
    with SessionStore(db_path) as store:
        sessions = store.list_sessions()
    if not sessions:
        console.print("[hint]No saved sessions found.[/hint]")
        return
    unique_names = make_unique_display_names(sessions)
    console.print(_build_sessions_table(sessions, display_names=unique_names))


def _clear_sessions(console: Console) -> None:
    """Interactively delete saved sessions.

    Shows a numbered list plus an [A] All option. The user picks a session
    number to delete one, or 'a'/'all' to wipe everything.
    """
    _SESSIONS_DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _SESSIONS_DB_DIR / "sessions.db"
    with SessionStore(db_path) as store:
        sessions = store.list_sessions()
        if not sessions:
            console.print("[hint]No saved sessions found.[/hint]")
            return
        unique_names = make_unique_display_names(sessions)
        console.print(_build_sessions_table(sessions, display_names=unique_names))
        console.print()
        console.print(
            "[hint]Enter a session number to delete, [command]all[/command] to clear everything, "
            "or [command]q[/command] to cancel.[/hint]"
        )
        pick_session: PromptSession[str] = PromptSession()
        while True:
            try:
                raw = pick_session.prompt("Clear> ")
            except (KeyboardInterrupt, EOFError):
                return
            raw = raw.strip().lower()
            if raw in ("q", "quit", "cancel"):
                return
            if raw in ("a", "all"):
                count = store.delete_all_sessions()
                console.print(f"[success]Deleted all {count} session{'s' if count != 1 else ''}.[/success]")
                return
            try:
                idx = int(raw)
            except ValueError:
                console.print("[warning]Please enter a number, 'all', or 'q' to cancel.[/warning]")
                continue
            if 1 <= idx <= len(sessions):
                picked = sessions[idx - 1]
                sid = picked["session_id"]
                name = unique_names.get(sid, sid)
                store.delete_session(sid)
                console.print(f"[success]Deleted session: {name}[/success]")
                return
            console.print(f"[warning]Please pick a number between 1 and {len(sessions)}.[/warning]")


def _resolve_resume(console: Console, resume_arg: str) -> tuple[dict | None, str | None]:
    """Resolve --resume into a (graph_state, session_id) tuple.

    Args:
        console: Rich Console for output.
        resume_arg: The value of args.resume — "__pick__" for interactive,
            "latest" for most recent, or a specific session ID.

    Returns:
        (graph_state, session_id) on success, (None, None) on failure/cancel.

    # See README: "Memory & State" — session persistence, --resume
    """
    _SESSIONS_DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _SESSIONS_DB_DIR / "sessions.db"
    with SessionStore(db_path) as store:
        if resume_arg == "latest":
            sid = store.get_latest_session_id()
            if sid is None:
                console.print("[warning]No saved sessions found.[/warning]")
                return None, None
            state = store.load_state(sid)
            if state is None:
                console.print(f"[warning]Session {sid} has no saved state or is corrupt.[/warning]")
                return None, None
            meta = store.get_session(sid)
            name = make_display_name(meta) if meta else sid
            console.print(f"[success]Loading session:[/success] {name}")
            return state, sid

        if resume_arg == "__pick__":
            sessions = store.list_sessions()
            if not sessions:
                console.print("[hint]No saved sessions found.[/hint]")
                return None, None
            unique_names = make_unique_display_names(sessions)
            console.print(_build_sessions_table(sessions, display_names=unique_names))
            console.print()
            pick_session: PromptSession[str] = PromptSession()
            while True:
                try:
                    raw = pick_session.prompt("Pick a session number (or 'q' to cancel): ")
                except (KeyboardInterrupt, EOFError):
                    return None, None
                raw = raw.strip().lower()
                if raw in ("q", "quit", "cancel"):
                    return None, None
                # Try numeric index first, then match by display name.
                try:
                    idx = int(raw)
                except ValueError:
                    # Match against display names (case-insensitive, partial match)
                    matches = [(i, s) for i, s in enumerate(sessions) if raw in unique_names[s["session_id"]].lower()]
                    if len(matches) == 1:
                        idx = matches[0][0] + 1  # convert to 1-based
                    elif len(matches) > 1:
                        console.print(f"[warning]'{raw}' matches multiple sessions. Be more specific.[/warning]")
                        continue
                    else:
                        console.print("[warning]No match. Enter a number or session name (or 'q' to cancel).[/warning]")
                        continue
                if 1 <= idx <= len(sessions):
                    picked = sessions[idx - 1]
                    sid = picked["session_id"]
                    state = store.load_state(sid)
                    if state is None:
                        console.print(f"[warning]Session {sid} has no saved state or is corrupt.[/warning]")
                        return None, None
                    name = make_display_name(picked)
                    console.print(f"[success]Loading session:[/success] {name}")
                    return state, sid
                console.print(f"[warning]Please pick a number between 1 and {len(sessions)}.[/warning]")

        # Specific session ID
        state = store.load_state(resume_arg)
        if state is None:
            console.print(f"[warning]Session '{resume_arg}' not found or has no saved state.[/warning]")
            return None, None
        meta = store.get_session(resume_arg)
        name = make_display_name(meta) if meta else resume_arg
        console.print(f"[success]Loading session:[/success] {name}")
        return state, resume_arg


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="scrum-agent",
        description="AI-powered Scrum Master — decomposes projects into epics, stories, tasks, and sprint plans.",
        epilog=(
            "examples:\n"
            "  scrum-agent                        interactive mode (recommended)\n"
            "  scrum-agent --quick                quick intake (2 questions only)\n"
            "  scrum-agent --full-intake          full 30-question intake\n"
            "  scrum-agent --questionnaire q.md   import pre-filled questionnaire\n"
            "  scrum-agent --export-only --quick  non-interactive, auto-accept all\n"
            "  scrum-agent --resume               resume last session (interactive picker)\n"
            "  scrum-agent --resume latest         resume most recent session\n"
            "  scrum-agent --list-sessions         list all saved sessions\n"
            "  scrum-agent --clear-sessions        delete saved sessions\n"
            '  scrum-agent --non-interactive --description "Build a todo app"  headless mode\n'
            '  scrum-agent --non-interactive --description "..." --output json  JSON to stdout'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        nargs="?",
        const="__pick__",
        default=None,
        help="Resume a previous session. Without an argument, shows an interactive session picker. "
        "Pass 'latest' to resume the most recent session, or a session ID to resume a specific one.",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        default=False,
        help="List all saved sessions and exit.",
    )
    parser.add_argument(
        "--clear-sessions",
        action="store_true",
        default=False,
        help="Interactively delete saved sessions (pick one or clear all) and exit.",
    )
    parser.add_argument(
        "--export-questionnaire",
        metavar="PATH",
        nargs="?",
        const=DEFAULT_QUESTIONNAIRE_FILENAME,
        default=None,
        help=f"Export a blank questionnaire template as Markdown (default: {DEFAULT_QUESTIONNAIRE_FILENAME}).",
    )
    parser.add_argument(
        "--questionnaire",
        metavar="PATH",
        default=None,
        help="Import a filled-in questionnaire Markdown file and jump to confirmation.",
    )

    # Intake mode flags — mutually exclusive.
    # Smart mode is the default when neither flag is given.
    # See README: "Project Intake Questionnaire" — smart intake
    intake_group = parser.add_mutually_exclusive_group()
    intake_group.add_argument(
        "--quick",
        action="store_true",
        default=False,
        help="Quick intake — only ask team size and tech stack, auto-fill everything else.",
    )
    intake_group.add_argument(
        "--full-intake",
        action="store_true",
        default=False,
        help="Full 30-question intake (standard mode).",
    )

    parser.add_argument(
        "--export-only",
        action="store_true",
        default=False,
        help="Auto-accept all review checkpoints and exit after the full plan is generated. "
        "Combine with --quick or --questionnaire for fully non-interactive runs.",
    )

    parser.add_argument(
        "--no-bell",
        action="store_true",
        default=False,
        help="Disable terminal bell after pipeline steps.",
    )

    parser.add_argument(
        "--theme",
        choices=["dark", "light"],
        default="dark",
        help="Terminal colour theme (default: dark). Use 'light' for white/cream backgrounds.",
    )

    parser.add_argument(
        "--mode",
        choices=["project-planning"],  # extend this list as new modes ship
        default=None,
        help="Skip the startup menu and launch directly into a specific mode.",
    )

    parser.add_argument(
        "--setup",
        action="store_true",
        default=False,
        help="Re-run the first-time setup wizard to update credentials.",
    )

    parser.add_argument(
        "--install-skill",
        metavar="DIR",
        nargs="?",
        const="__auto__",
        default=None,
        help="Install the bundled OpenClaw scrum-planner skill. "
        "Optionally specify a target directory (default: ~/.openclaw/skills/).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run the TUI with mock data and fake delays — no LLM calls. For UI development.",
    )

    # ── Non-interactive / headless mode ──────────────────────────────────
    # Runs the full pipeline without user interaction. Requires --description.
    # Combine with --output to control output format (default: markdown).
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        default=False,
        help="Run the full pipeline headlessly (no user interaction). Requires --description.",
    )
    parser.add_argument(
        "--output",
        choices=["markdown", "json", "html"],
        default=None,
        help="Output format for the generated plan. Only valid with --non-interactive or --export-only.",
    )
    parser.add_argument(
        "--description",
        metavar="TEXT",
        default=None,
        help="Project description for headless mode. Use @file.txt to read from a file.",
    )
    parser.add_argument(
        "--team-size",
        metavar="N",
        type=int,
        default=None,
        help="Team size (maps to intake Q6). Only used with --non-interactive.",
    )
    parser.add_argument(
        "--sprint-length",
        metavar="WEEKS",
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        help="Sprint length in weeks (maps to intake Q8). Only used with --non-interactive.",
    )

    return parser


def _run_headless(args: argparse.Namespace) -> None:
    """Run the full pipeline headlessly — no TUI, no interactive input.

    Pre-populates a QuestionnaireState from CLI args (--description,
    --team-size, --sprint-length), then delegates to run_repl() with
    export_only=True and non_interactive=True.

    When --output json, Rich console output goes to stderr so only
    JSON is written to stdout.

    # See README: "Architecture" — headless mode for CI/CD pipelines
    """
    from scrum_agent.formatters import build_theme

    output_format = args.output or "markdown"

    # When JSON output, redirect console to stderr so stdout is clean JSON
    if output_format == "json":
        console = Console(theme=build_theme(args.theme), file=sys.stderr)
    else:
        console = Console(theme=build_theme(args.theme))

    # Pre-populate questionnaire from CLI args
    answers: dict[int, str] = {}
    # Q1 gets the project description
    answers[1] = args.description
    if args.team_size is not None:
        answers[6] = str(args.team_size)
    if args.sprint_length is not None:
        answers[8] = str(args.sprint_length)

    # Load SCRUM.md from working directory if present — fills gaps the CLI
    # args didn't cover (e.g., tech stack, integrations, constraints).
    # Uses deterministic keyword extraction only (no LLM call) to stay fast.
    # CLI args always take priority over SCRUM.md.
    try:
        from scrum_agent.agent.nodes import _keyword_extract_fallback, _load_user_context

        scrum_md_content, _ = _load_user_context()
        if scrum_md_content:
            scrum_extracted: dict[int, str] = {}
            _keyword_extract_fallback(scrum_md_content, scrum_extracted)
            # Merge: CLI args win over SCRUM.md
            for q_num, answer in scrum_extracted.items():
                if q_num not in answers:
                    answers[q_num] = answer
    except Exception:
        pass  # best-effort — never block headless mode

    questionnaire = build_questionnaire_from_answers(answers)

    run_repl(
        console=console,
        questionnaire=questionnaire,
        intake_mode="quick",
        export_only=True,
        bell=False,
        theme=args.theme,
        non_interactive=True,
        output_format=output_format,
    )


def _sync_bedrock_config() -> None:
    """Detect Bedrock model ID and region from OpenClaw's config and sync to scrum-agent's .env.

    Reads ~/.openclaw/agents/main/agent/models.json to find the Bedrock model ID
    and region, then writes LLM_PROVIDER, LLM_MODEL, and AWS_REGION to
    ~/.scrum-agent/.env if not already set.
    """
    import json as json_mod

    models_json = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"
    env_path = Path.home() / ".scrum-agent" / ".env"

    if not models_json.exists():
        print("[3/5] OpenClaw models.json not found — skipped Bedrock config sync")
        return

    try:
        config = json_mod.loads(models_json.read_text())
    except (json_mod.JSONDecodeError, OSError):
        print("[3/5] Could not parse OpenClaw models.json — skipped")
        return

    # Extract Bedrock model ID and region from OpenClaw config.
    # The provider key may be "bedrock" or "amazon-bedrock" depending on OpenClaw version.
    providers = config.get("providers", {})
    bedrock = providers.get("bedrock") or providers.get("amazon-bedrock") or {}
    models = bedrock.get("models", [])
    base_url = bedrock.get("baseUrl", "")

    if not models:
        print("[3/5] No Bedrock models found in OpenClaw config — skipped")
        return

    model_id = models[0].get("id", "")

    # Extract region from baseUrl: https://bedrock-runtime.{region}.amazonaws.com
    region = ""
    if "bedrock-runtime." in base_url:
        try:
            region = base_url.split("bedrock-runtime.")[1].split(".amazonaws.com")[0]
        except IndexError:
            pass

    if not model_id:
        # Fallback: scan all providers for any model with "anthropic" or "claude" in the ID
        for prov in providers.values():
            if isinstance(prov, dict):
                for m in prov.get("models", []):
                    mid = m.get("id", "")
                    if "anthropic" in mid or "claude" in mid:
                        model_id = mid
                        break
                if model_id:
                    break

    if not model_id:
        print("[3/5] No Bedrock model ID found in OpenClaw config — skipped")
        return

    # Read existing .env to avoid overwriting user settings
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text() if env_path.exists() else ""

    additions = []
    if "LLM_PROVIDER" not in existing:
        additions.append("LLM_PROVIDER=bedrock")

    # Always ensure LLM_MODEL is set to the OpenClaw model
    if "LLM_MODEL" not in existing:
        additions.append(f"LLM_MODEL={model_id}")
    elif model_id not in existing:
        # LLM_MODEL exists but with a different value — update it
        lines = existing.splitlines()
        lines = [f"LLM_MODEL={model_id}" if line.startswith("LLM_MODEL=") else line for line in lines]
        existing = "\n".join(lines) + "\n"
        env_path.write_text(existing)

    if region and "PLACEHOLDER" not in region and "AWS_REGION" not in existing:
        additions.append(f"AWS_REGION={region}")

    if additions:
        with open(env_path, "a") as f:
            f.write("\n".join(additions) + "\n")

    print(f"[3/5] Bedrock config synced: model={model_id}" + (f", region={region}" if region else ""))


def _install_skill(target_arg: str) -> None:
    """Install the bundled scrum-planner skill into OpenClaw.

    Full installation flow:
    1. Copy SKILL.md into the OpenClaw skills registry (for gateway discovery)
    2. Copy SKILL.md into the sandbox workspace (so the agent can read it at runtime)
    3. Symlink scrum-agent into /usr/local/bin/ so the sandbox can find the binary
    4. Offer to restart the OpenClaw gateway to pick up the new skill

    OpenClaw has two separate filesystem concerns:
    - Skills registry: /usr/lib/node_modules/openclaw/skills/ (gateway reads at startup)
    - Sandbox workspace: ~/.openclaw/workspace/ (agent reads at runtime)
    Both need the SKILL.md for the skill to work end-to-end.

    Args:
        target_arg: "__auto__" to auto-detect, or a custom path.
    """
    import importlib.resources
    import shutil
    import subprocess

    # OpenClaw paths
    openclaw_skills_dir = Path("/usr/lib/node_modules/openclaw/skills")
    openclaw_workspace_dir = Path.home() / ".openclaw" / "workspace"

    if target_arg == "__auto__":
        if openclaw_skills_dir.is_dir():
            target_dir = openclaw_skills_dir / "scrum-planner"
        else:
            target_dir = Path.home() / ".openclaw" / "skills" / "scrum-planner"
    else:
        target_dir = Path(target_arg) / "scrum-planner"

    # Locate the bundled skill files inside the installed package.
    # hatch force-include puts them at scrum_agent/skills/scrum-planner/.
    try:
        skill_pkg = importlib.resources.files("scrum_agent") / "skills" / "scrum-planner"
    except (TypeError, ModuleNotFoundError):
        skill_pkg = Path(__file__).resolve().parent.parent.parent / "skills" / "scrum-planner"

    source_path = Path(str(skill_pkg))
    if not source_path.is_dir():
        repo_root = Path(__file__).resolve().parent.parent.parent
        source_path = repo_root / "skills" / "scrum-planner"
        if not source_path.is_dir():
            print(f"Error: bundled skill not found at {source_path}", file=sys.stderr)
            sys.exit(1)

    # ── Step 1: Copy to skills registry (gateway discovery) ──────────────────
    # /usr/lib/node_modules/openclaw/skills/ is root-owned, so may need sudo.
    def _copy_to(dest: Path, label: str, step: str) -> int:
        try:
            dest.mkdir(parents=True, exist_ok=True)
            count = 0
            for src_file in source_path.iterdir():
                if src_file.is_file():
                    shutil.copy2(src_file, dest / src_file.name)
                    count += 1
        except PermissionError:
            subprocess.run(["sudo", "mkdir", "-p", str(dest)], check=True)
            count = 0
            for src_file in source_path.iterdir():
                if src_file.is_file():
                    subprocess.run(["sudo", "cp", str(src_file), str(dest / src_file.name)], check=True)
                    count += 1
        print(f"[{step}] {label}: {dest}")
        return count

    _copy_to(target_dir, "Skill registry", "1/5")

    # ── Step 2: Copy to sandbox workspace (agent runtime access) ─────────────
    # The OpenClaw sandbox only has access to ~/.openclaw/workspace/.
    # The agent needs to read SKILL.md at runtime to follow the instructions.
    workspace_skill_dir = openclaw_workspace_dir / "skills" / "scrum-planner"
    if openclaw_workspace_dir.is_dir():
        _copy_to(workspace_skill_dir, "Sandbox workspace", "2/5")
    else:
        print("[2/5] Sandbox workspace not found — skipped (not an OpenClaw instance?)")

    # ── Step 3: Sync Bedrock model config from OpenClaw ─────────────────────
    # OpenClaw's models.json has the exact Bedrock model ID and region.
    # Detect these and write to ~/.scrum-agent/.env so scrum-agent uses the
    # same model as OpenClaw (e.g. global.anthropic.claude-sonnet-4-6).
    _sync_bedrock_config()

    # ── Step 4: Symlink scrum-agent into /usr/local/bin/ ────────────────────
    # The OpenClaw sandbox may not see ~/.local/bin/ or pipx paths.
    # A symlink in /usr/local/bin/ makes the binary accessible from the sandbox.
    symlink_path = Path("/usr/local/bin/scrum-agent")
    scrum_agent_bin = shutil.which("scrum-agent")

    already_correct = (
        scrum_agent_bin and symlink_path.is_symlink() and symlink_path.resolve() == Path(scrum_agent_bin).resolve()
    )
    if already_correct:
        print("[4/5] scrum-agent already symlinked in /usr/local/bin/ — skipped")
    elif scrum_agent_bin:
        try:
            if symlink_path.exists() or symlink_path.is_symlink():
                symlink_path.unlink()
            symlink_path.symlink_to(scrum_agent_bin)
            print(f"[4/5] Symlinked scrum-agent → {symlink_path}")
        except PermissionError:
            try:
                subprocess.run(
                    ["sudo", "ln", "-sf", scrum_agent_bin, str(symlink_path)],
                    check=True,
                    capture_output=True,
                )
                print(f"[4/5] Symlinked scrum-agent → {symlink_path} (via sudo)")
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(
                    f"[4/5] Warning: could not symlink to {symlink_path} — "
                    f"run manually: sudo ln -s {scrum_agent_bin} {symlink_path}"
                )
    else:
        print("[4/5] Warning: scrum-agent not found on PATH — skipping symlink")

    # ── Step 5: Restart OpenClaw gateway ─────────────────────────────────────
    # OpenClaw loads skills at startup, so a gateway restart is needed.
    gateway_available = shutil.which("openclaw") is not None
    if not gateway_available:
        print("[5/5] OpenClaw CLI not found — skip gateway restart (run 'openclaw gateway restart' manually)")
        return

    try:
        answer = input("\n[5/5] Restart OpenClaw gateway to load the skill? [Y/n] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nSkipped gateway restart. Run 'openclaw gateway restart' manually.")
        return

    if answer in ("", "y", "yes"):
        try:
            subprocess.run(["openclaw", "gateway", "restart"], check=True)
            print("\nOpenClaw gateway restarted. The scrum-planner skill is ready to use.")
        except subprocess.CalledProcessError as e:
            print(f"\nGateway restart failed (exit code {e.returncode}). Try: openclaw gateway restart")
    else:
        print("Skipped. Run 'openclaw gateway restart' when ready.")


def main(argv: list[str] | None = None) -> None:
    """Entry point for the scrum-agent CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # ── --install-skill: copy bundled skill and exit ─────────────────────────
    if args.install_skill is not None:
        _install_skill(args.install_skill)
        return

    # Load ~/.scrum-agent/.env before any credential reads.
    # override=False means shell env vars and project .env always take precedence.
    load_user_config()

    # ── Validation for --non-interactive mode ────────────────────────────────
    if args.non_interactive and not args.description:
        print("Error: --non-interactive requires --description", file=sys.stderr)
        sys.exit(1)

    if args.output and not args.non_interactive and not args.export_only:
        print("Error: --output is only valid with --non-interactive or --export-only", file=sys.stderr)
        sys.exit(1)

    # Resolve --description @file.txt → read file contents
    if args.description and args.description.startswith("@"):
        desc_path = Path(args.description[1:])
        if not desc_path.exists():
            print(f"Error: description file not found: {desc_path}", file=sys.stderr)
            sys.exit(1)
        args.description = desc_path.read_text().strip()

    # ── Non-interactive headless flow ────────────────────────────────────────
    # Runs the full pipeline without any TUI, splash, or interactive input.
    if args.non_interactive:
        _run_headless(args)
        return

    # ── File-based logging ────────────────────────────────────────────────────
    # Writes to ~/.scrum-agent/scrum-agent.log so developers can diagnose
    # issues without interfering with the TUI display. Rotates at 2 MB.
    # Log level is controlled by LOG_LEVEL in .env (default: WARNING).
    # Set LOG_LEVEL=DEBUG for full diagnostics.
    _log_dir = Path.home() / ".scrum-agent"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_path = _log_dir / "scrum-agent.log"
    _log_level = getattr(logging, get_log_level(), logging.WARNING)
    _file_handler = logging.handlers.RotatingFileHandler(
        _log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"),
    )
    _file_handler.setLevel(_log_level)
    logging.getLogger("scrum_agent").addHandler(_file_handler)
    logging.getLogger("scrum_agent").setLevel(_log_level)

    # Create the console with the requested theme so semantic style names
    # ([command], [hint], [success], etc.) resolve correctly throughout the
    # REPL. Must be created after arg parsing so args.theme is available.
    console = Console(theme=build_theme(args.theme))

    # Rename legacy history file (~/.scrum-agent/history → repl-history).
    # See README: "Memory & State" — clearer naming for the REPL history file.
    migrate_history_file()

    # See README: "Architecture" — splash replaces the static welcome panel.
    # The animated intro runs before any interactive UI (wizard / mode select).
    show_splash(console)

    # ── First-run setup wizard ────────────────────────────────────────────────
    # Triggers when ~/.scrum-agent/.env is absent (first run) or --setup is passed.
    # If the user cancels the wizard, exit early — the agent can't run without
    # a valid ANTHROPIC_API_KEY.
    # Runs immediately after splash — both use fullscreen Live, so no console
    # prints happen in between (avoids visible flicker on alt-screen exit).
    if args.setup or is_first_run():
        completed = run_setup_wizard(console)
        if not completed:
            return

    # Determine early whether we'll use the old REPL or the fullscreen TUI.
    # The TUI path keeps alt-screen active from splash → select_mode seamlessly.
    # The old REPL path needs to exit alt-screen and print info to the terminal.
    use_old_repl = args.mode is not None or args.quick or args.full_intake or args.questionnaire is not None

    if use_old_repl or args.resume is not None or args.list_sessions or args.clear_sessions:
        # Leave alt-screen before printing to the normal terminal
        if console.is_alt_screen:
            console.set_alt_screen(False)

        # Informational prints — only shown for non-TUI paths
        scrum_md = Path(os.getcwd()) / "SCRUM.md"
        if scrum_md.is_file():
            _summarise_scrum_md(console, scrum_md)
        else:
            console.print(
                "[dim]  Tip: create a [cyan]SCRUM.md[/cyan] in this directory to add project notes, "
                "URLs, and design decisions that the agent will read automatically. "
                "See [cyan]SCRUM.md.example[/cyan] for a template.[/dim]"
            )

        if is_langsmith_enabled():
            proxy = detect_proxy()
            if proxy:
                disable_langsmith_tracing()
                console.print(f"[yellow]Warning: proxy detected ({proxy}) — LangSmith tracing auto-disabled[/yellow]")
            else:
                console.print("[dim]LangSmith tracing enabled[/dim]")

    # ── --list-sessions: print all sessions and exit ──────────────────────────
    if args.list_sessions:
        _print_sessions_table(console)
        return

    # ── --clear-sessions: interactive delete and exit ─────────────────────────
    if args.clear_sessions:
        _clear_sessions(console)
        return

    # --export-questionnaire: write a blank template and exit (no REPL)
    if args.export_questionnaire is not None:
        path = export_questionnaire_md(None, Path(args.export_questionnaire))
        console.print(f"[green]Questionnaire template exported to {path}[/green]")
        return

    # ── --resume: load saved session and skip mode menu ───────────────────────
    # Phase 8B: when --resume is passed, load the saved state and go directly
    # to run_repl() with the resume_state — no mode selection needed since
    # resumed sessions are always project-planning.
    # See README: "Memory & State" — session persistence, --resume
    if args.resume is not None:
        resume_state, resume_session_id = _resolve_resume(console, args.resume)
        if resume_state is None:
            return  # user cancelled or no sessions
        run_repl(
            console=console,
            bell=not args.no_bell,
            theme=args.theme,
            resume_state=resume_state,
            resume_session_id=resume_session_id,
        )
        return  # skip mode menu — resume goes straight to REPL

    # --questionnaire: import a filled file, build state, pass to REPL
    questionnaire = None
    if args.questionnaire is not None:
        qpath = Path(args.questionnaire)
        if not qpath.exists():
            console.print(f"[red]Error: file not found: {qpath}[/red]")
            sys.exit(1)
        try:
            parsed = parse_questionnaire_md(qpath)
            questionnaire = build_questionnaire_from_answers(parsed)
            console.print(f"[green]Loaded {len(parsed)} answers from {qpath}[/green]")
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)

    # Determine intake mode from flags.
    # Default is None — triggers the interactive mode selection menu in the REPL.
    # CLI flags bypass the menu for power users who know what they want.
    # See README: "Project Intake Questionnaire" — smart intake
    if args.quick:
        intake_mode = "quick"
    elif args.full_intake:
        intake_mode = "standard"
    else:
        intake_mode = None

    # --export-only: validate that a non-interactive intake source is provided.
    # Without --quick or --questionnaire the intake requires interactive input,
    # which defeats the purpose of --export-only.
    if args.export_only and not args.quick and questionnaire is None:
        console.print("[red]Error: --export-only requires --quick or --questionnaire to supply intake answers.[/red]")
        sys.exit(1)

    # ── Top-level mode selection ──────────────────────────────────────────────
    # Full-screen mode selector with ASCII art titles and typewriter descriptions.
    #
    # Three paths:
    #   1. --mode flag → bypass UI, use old REPL (backwards compat for scripted runs)
    #   2. --quick/--full-intake/--questionnaire → bypass UI, use old REPL
    #   3. Interactive → full-screen TUI mode selector, which launches the TUI
    #      session (run_session) for Smart/Full intake inside its Live context
    #
    # See README: "Architecture" — mode selection is a CLI-layer concern.
    if use_old_repl:
        startup_mode = args.mode or "project-planning"
        if startup_mode == "project-planning":
            run_repl(
                console=console,
                questionnaire=questionnaire,
                intake_mode=intake_mode,
                export_only=args.export_only,
                bell=not args.no_bell,
                theme=args.theme,
            )
        else:
            console.print(f"\n[warning]Unknown mode '{startup_mode}'.[/warning]")
    else:
        # Interactive TUI flow — select_mode() launches the full session when
        # the user picks Smart or Full intake. It returns None when done.
        # Alt-screen stays active from splash through select_mode to avoid
        # flicker; clean it up when the TUI exits.
        # Mouse tracking captures scroll-wheel events so they scroll within
        # the app instead of the terminal's own scrollback buffer.
        from scrum_agent.ui.shared._input import disable_mouse_tracking, enable_mouse_tracking

        enable_mouse_tracking()
        try:
            mode_result = select_mode(console, dry_run=args.dry_run)
        except KeyboardInterrupt:
            mode_result = None
        finally:
            disable_mouse_tracking()
            if console.is_alt_screen:
                console.set_alt_screen(False)
        if mode_result is None:
            return
        # mode_result is non-None only for offline import (questionnaire path)
        startup_mode, ui_intake, questionnaire_path = mode_result
        if ui_intake is not None and intake_mode is None:
            intake_mode = ui_intake
        if questionnaire_path and questionnaire is None:
            qpath = Path(questionnaire_path)
            try:
                parsed = parse_questionnaire_md(qpath)
                questionnaire = build_questionnaire_from_answers(parsed)
                console.print(f"[green]Loaded {len(parsed)} answers from {qpath}[/green]")
            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                sys.exit(1)
        # Import flow falls through to REPL for review
        if startup_mode == "project-planning":
            run_repl(
                console=console,
                questionnaire=questionnaire,
                intake_mode=intake_mode,
                export_only=args.export_only,
                bell=not args.no_bell,
                theme=args.theme,
            )


if __name__ == "__main__":
    main()
