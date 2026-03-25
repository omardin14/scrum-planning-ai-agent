"""Configuration and environment variable handling."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()


# ---------------------------------------------------------------------------
# User config directory (~/.scrum-agent/)
# ---------------------------------------------------------------------------


def get_config_dir() -> Path:
    """Return ~/.scrum-agent/, creating it if necessary."""
    d = Path.home() / ".scrum-agent"
    d.mkdir(exist_ok=True)
    return d


def get_config_file() -> Path:
    """Return path to ~/.scrum-agent/.env."""
    return get_config_dir() / ".env"


def get_sessions_db() -> Path:
    """Return path to ~/.scrum-agent/sessions.db (SQLite session store)."""
    return get_config_dir() / "sessions.db"


def load_user_config() -> None:
    """Load ~/.scrum-agent/.env without overriding existing env vars.

    Called once at CLI startup before any credential reads.
    dotenv's override=False means shell env vars and project .env always win
    — safe for CI/CD and developer overrides.
    """
    config_path = get_config_file()
    logger.info("Loading user config from %s", config_path)
    load_dotenv(config_path, override=False)
    logger.debug(
        "API keys — ANTHROPIC_API_KEY: %s, OPENAI_API_KEY: %s, GOOGLE_API_KEY: %s",
        "set" if os.getenv("ANTHROPIC_API_KEY") else "missing",
        "set" if os.getenv("OPENAI_API_KEY") else "missing",
        "set" if os.getenv("GOOGLE_API_KEY") else "missing",
    )
    logger.debug(
        "Integrations — GITHUB_TOKEN: %s, JIRA_API_TOKEN: %s, AZURE_DEVOPS_TOKEN: %s",
        "set" if os.getenv("GITHUB_TOKEN") else "missing",
        "set" if os.getenv("JIRA_API_TOKEN") else "missing",
        "set" if os.getenv("AZURE_DEVOPS_TOKEN") else "missing",
    )


def get_anthropic_api_key() -> str:
    """Return the Anthropic API key or raise if not set."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise OSError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
    return key


def is_langsmith_enabled() -> bool:
    """Check whether LangSmith tracing is enabled."""
    return os.getenv("LANGSMITH_TRACING", "").lower() == "true" and bool(os.getenv("LANGSMITH_API_KEY"))


# Proxy environment variables to check (both uppercase and lowercase conventions).
_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def detect_proxy() -> str | None:
    """Return the first proxy URL found in environment variables, or None."""
    for var in _PROXY_ENV_VARS:
        value = os.getenv(var)
        if value:
            logger.debug("Proxy detected via %s", var)
            return value
    logger.debug("No proxy detected")
    return None


def get_github_token() -> str | None:
    """Return the GitHub PAT, or None if not set (tools work for public repos without a token)."""
    return os.getenv("GITHUB_TOKEN") or None


def get_azure_devops_token() -> str | None:
    """Return the Azure DevOps PAT, or None if not set."""
    return os.getenv("AZURE_DEVOPS_TOKEN") or None


def get_azure_devops_org_url() -> str | None:
    """Return the Azure DevOps organization URL (e.g. https://dev.azure.com/myorg), or None if not set."""
    return os.getenv("AZURE_DEVOPS_ORG_URL") or None


def get_azure_devops_project() -> str | None:
    """Return the Azure DevOps project name, or None if not set."""
    return os.getenv("AZURE_DEVOPS_PROJECT") or None


def get_azure_devops_team() -> str | None:
    """Return the Azure DevOps team name, or None if not set.

    Defaults to "{project} Team" when not explicitly set (AzDO's default team naming).
    """
    team = os.getenv("AZURE_DEVOPS_TEAM")
    if team:
        return team
    project = get_azure_devops_project()
    if project:
        return f"{project} Team"
    return None


def get_jira_base_url() -> str | None:
    """Return the Jira Cloud base URL (e.g. https://org.atlassian.net), or None if not set."""
    return os.getenv("JIRA_BASE_URL") or None


def get_jira_email() -> str | None:
    """Return the Atlassian account email used for Jira basic auth, or None if not set."""
    return os.getenv("JIRA_EMAIL") or None


def get_jira_token() -> str | None:
    """Return the Jira API token, or None if not set."""
    return os.getenv("JIRA_API_TOKEN") or None


def get_jira_project_key() -> str | None:
    """Return the default Jira project key (e.g. 'MYPROJ'), or None if not set."""
    return os.getenv("JIRA_PROJECT_KEY") or None


def get_confluence_space_key() -> str | None:
    """Return the default Confluence space key (e.g. 'MYSPACE'), or None if not set."""
    return os.getenv("CONFLUENCE_SPACE_KEY") or None


# ---------------------------------------------------------------------------
# LLM provider configuration
# ---------------------------------------------------------------------------


def get_llm_provider() -> str:
    """Return the active LLM provider name (lowercase).

    Set LLM_PROVIDER in .env to switch providers. Defaults to 'anthropic'.
    Supported values: 'anthropic', 'openai', 'google'.
    """
    return os.getenv("LLM_PROVIDER", "anthropic").lower()


def get_llm_model() -> str | None:
    """Return the model ID override from LLM_MODEL env var, or None to use the provider default."""
    return os.getenv("LLM_MODEL") or None


def get_bedrock_region() -> str:
    """Return the AWS region for Bedrock API calls.

    Reads AWS_REGION, then AWS_DEFAULT_REGION from env. Defaults to 'us-east-1'.
    """
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def get_aws_profile() -> str | None:
    """Return the AWS profile to use for Bedrock API calls.

    Reads AWS_PROFILE from env. If not set, auto-detects from ~/.aws/config
    by finding the first profile with a region or role_arn configured.
    Returns None if only [default] is available (boto3 handles that automatically).
    """
    profile = os.getenv("AWS_PROFILE")
    if profile:
        return profile

    # Auto-detect: parse ~/.aws/config for non-default profiles
    try:
        config_path = Path.home() / ".aws" / "config"
        if config_path.exists():
            import configparser

            cfg = configparser.ConfigParser()
            cfg.read(config_path)
            for section in cfg.sections():
                # AWS config sections are [default] or [profile <name>]
                if section.startswith("profile "):
                    profile_name = section.removeprefix("profile ").strip()
                    if cfg.has_option(section, "role_arn") or cfg.has_option(section, "credential_source"):
                        return profile_name
    except Exception:
        pass

    return None


def get_openai_api_key() -> str | None:
    """Return the OpenAI API key, or None if not set."""
    return os.getenv("OPENAI_API_KEY") or None


def get_google_api_key() -> str | None:
    """Return the Google AI API key, or None if not set."""
    return os.getenv("GOOGLE_API_KEY") or None


def get_session_prune_days() -> int:
    """Return the number of days after which old sessions are pruned.

    Phase 8C: reads ``SESSION_PRUNE_DAYS`` env var.
    Default is 30. Set to 0 to disable pruning. Invalid values fall back to 30.
    """
    raw = os.getenv("SESSION_PRUNE_DAYS", "30")
    try:
        value = int(raw)
        return value if value >= 0 else 30
    except ValueError:
        return 30


def get_log_level() -> str:
    """Return the configured log level for the file logger.

    Reads ``LOG_LEVEL`` from .env. Defaults to ``WARNING``.
    Valid values: DEBUG, INFO, WARNING, ERROR.
    Invalid values fall back to WARNING.
    """
    raw = os.getenv("LOG_LEVEL", "WARNING").upper()
    if raw in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return raw
    return "WARNING"


def disable_langsmith_tracing() -> None:
    """Disable LangSmith by unsetting LANGSMITH_TRACING in the current process.

    LangSmith reads LANGSMITH_TRACING from os.environ at runtime, so removing
    it prevents the SDK from attempting to send traces for the rest of the process.
    """
    logger.info("Disabling LangSmith tracing for this process")
    os.environ.pop("LANGSMITH_TRACING", None)
