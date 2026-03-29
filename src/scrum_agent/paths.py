"""Centralised path definitions for ~/.scrum-agent directory structure.

All file and directory paths for the scrum-agent application should be
accessed through this module to ensure consistency across the codebase.

Directory structure:
    ~/.scrum-agent/
    ├── data/
    │   ├── sessions.db           # SQLite: sessions, team profiles, token usage
    │   ├── states/               # Legacy checkpoint JSON files
    │   └── projects.json         # Project metadata
    ├── exports/
    │   ├── analysis/             # Team analysis exports (HTML + MD)
    │   │   └── {project_key}/
    │   └── planning/             # Planning exports (HTML + MD + scrum-docs)
    │       └── {project_key}/
    ├── logs/
    │   ├── tui/                  # Main TUI log (scrum-agent.log + rotations)
    │   ├── analysis/             # Per-analysis-run logs
    │   └── planning/             # Per-planning-session logs
    ├── scrum-docs/               # SCRUM.md files for each project
    ├── .env                      # Environment variables
    └── repl-history              # REPL command history
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

ROOT_DIR = Path.home() / ".scrum-agent"

# ---------------------------------------------------------------------------
# Data (DB, states, project metadata)
# ---------------------------------------------------------------------------

DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "sessions.db"
STATES_DIR = DATA_DIR / "states"
PROJECTS_FILE = DATA_DIR / "projects.json"

# Legacy paths (for backward compatibility / migration)
LEGACY_DB_PATH = ROOT_DIR / "sessions.db"
LEGACY_STATES_DIR = ROOT_DIR / "states"
LEGACY_PROJECTS_FILE = ROOT_DIR / "projects.json"

# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

EXPORTS_DIR = ROOT_DIR / "exports"
ANALYSIS_EXPORTS_DIR = EXPORTS_DIR / "analysis"
PLANNING_EXPORTS_DIR = EXPORTS_DIR / "planning"

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

LOGS_DIR = ROOT_DIR / "logs"
TUI_LOGS_DIR = LOGS_DIR / "tui"
ANALYSIS_LOGS_DIR = LOGS_DIR / "analysis"
PLANNING_LOGS_DIR = LOGS_DIR / "planning"

# Legacy log paths
LEGACY_TUI_LOG = ROOT_DIR / "scrum-agent.log"

# ---------------------------------------------------------------------------
# Other
# ---------------------------------------------------------------------------

SCRUM_DOCS_DIR = ROOT_DIR / "scrum-docs"
ENV_FILE = ROOT_DIR / ".env"
REPL_HISTORY = ROOT_DIR / "repl-history"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_db_path() -> Path:
    """Return the sessions DB path, migrating from legacy location if needed."""
    if DB_PATH.exists():
        return DB_PATH
    if LEGACY_DB_PATH.exists():
        # Migrate: move to new location
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_DB_PATH.rename(DB_PATH)
        return DB_PATH
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DB_PATH


def get_analysis_export_dir(project_key: str) -> Path:
    """Return the analysis export directory for a project, creating it if needed."""
    d = ANALYSIS_EXPORTS_DIR / project_key.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_planning_export_dir(project_key: str) -> Path:
    """Return the planning export directory for a project, creating it if needed."""
    d = PLANNING_EXPORTS_DIR / project_key.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_tui_log_path() -> Path:
    """Return the main TUI log path."""
    TUI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return TUI_LOGS_DIR / "scrum-agent.log"


def get_analysis_log_dir() -> Path:
    """Return the analysis logs directory, creating it if needed."""
    ANALYSIS_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return ANALYSIS_LOGS_DIR


def get_planning_log_dir() -> Path:
    """Return the planning session logs directory, creating it if needed."""
    PLANNING_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return PLANNING_LOGS_DIR


def migrate_legacy_paths() -> None:
    """Migrate files from legacy flat structure to new organised structure.

    Called once at startup. Safe to call multiple times — skips if already migrated.
    """
    import shutil

    # Migrate sessions.db
    if LEGACY_DB_PATH.exists() and not DB_PATH.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_DB_PATH.rename(DB_PATH)

    # Migrate states/
    if LEGACY_STATES_DIR.exists() and not STATES_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_STATES_DIR.rename(STATES_DIR)

    # Migrate projects.json
    if LEGACY_PROJECTS_FILE.exists() and not PROJECTS_FILE.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_PROJECTS_FILE.rename(PROJECTS_FILE)

    # Migrate main log
    if LEGACY_TUI_LOG.exists() and not (TUI_LOGS_DIR / "scrum-agent.log").exists():
        TUI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_TUI_LOG.rename(TUI_LOGS_DIR / "scrum-agent.log")
        # Also move rotated logs
        for rot in ROOT_DIR.glob("scrum-agent.log.*"):
            rot.rename(TUI_LOGS_DIR / rot.name)

    # Migrate analysis logs (team-analysis-*.log → logs/analysis/)
    if LOGS_DIR.exists():
        for f in LOGS_DIR.glob("team-analysis-*.log"):
            ANALYSIS_LOGS_DIR.mkdir(parents=True, exist_ok=True)
            f.rename(ANALYSIS_LOGS_DIR / f.name)

    # Migrate planning session logs (UUID.log → logs/planning/)
    if LOGS_DIR.exists():
        import re

        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-.*\.log$")
        for f in LOGS_DIR.glob("*.log"):
            if uuid_re.match(f.name):
                PLANNING_LOGS_DIR.mkdir(parents=True, exist_ok=True)
                f.rename(PLANNING_LOGS_DIR / f.name)

    # Migrate exports/{project_key}/ → exports/analysis/{project_key}/
    if EXPORTS_DIR.exists():
        for d in EXPORTS_DIR.iterdir():
            if d.is_dir() and d.name not in ("analysis", "planning"):
                # Check if it has team-profile files (analysis exports)
                has_analysis = any(f.name.startswith("team-profile") for f in d.iterdir() if f.is_file())
                if has_analysis:
                    target = ANALYSIS_EXPORTS_DIR / d.name
                    if not target.exists():
                        ANALYSIS_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(d), str(target))
