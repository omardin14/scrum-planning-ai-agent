"""Configuration data for provider selection screens.

# See README: "Architecture" — constants for the setup wizard UI.
# Defines LLM providers, version control options, and issue tracking fields.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Provider definitions (order matters — matches row layout top-to-bottom)
# ---------------------------------------------------------------------------

_PROVIDER_CARDS: list[dict[str, Any]] = [
    {
        "name": "Anthropic",
        "full_name": "Anthropic (Claude)",
        "env_var": "ANTHROPIC_API_KEY",
        "provider_val": "anthropic",
        "prefix": "sk-ant-",
        "instructions": "Get yours at: https://console.anthropic.com \u2192 API Keys",
        "color": "rgb(70,100,180)",
    },
    {
        "name": "Gemini",
        "full_name": "Google (Gemini)",
        "env_var": "GOOGLE_API_KEY",
        "provider_val": "google",
        "prefix": "AIza",
        "instructions": "Get yours at: https://aistudio.google.com \u2192 Get API key",
        "color": "rgb(70,100,180)",
    },
    {
        "name": "OpenAI",
        "full_name": "OpenAI (GPT)",
        "env_var": "OPENAI_API_KEY",
        "provider_val": "openai",
        "prefix": "sk-",
        "instructions": "Get yours at: https://platform.openai.com \u2192 API keys",
        "color": "rgb(70,100,180)",
    },
    {
        "name": "Bedrock",
        "full_name": "AWS (Bedrock)",
        "env_var": "AWS_REGION",
        "provider_val": "bedrock",
        "prefix": "",
        "instructions": "Uses IAM credentials from instance role, ~/.aws/credentials, or env vars",
        "color": "rgb(70,100,180)",
        "is_region_input": True,
    },
]

# Version control providers — GitHub only (Azure DevOps PAT is collected
# in the Issue Tracking step alongside org URL and project name).
_VC_OPTIONS: list[dict[str, Any]] = [
    {
        "name": "GitHub",
        "env_var": "GITHUB_TOKEN",
        "prefix": "ghp_",
        "instructions": "Get yours at: https://github.com/settings/tokens",
        "color": "rgb(70,100,180)",
    },
    {
        "name": "Skip",
        "env_var": "",
        "prefix": "",
        "instructions": "",
        "color": "rgb(70,100,180)",
    },
]

# Issue tracking fields — step 4 (Jira)
_ISSUE_TRACKING_FIELDS: list[dict[str, Any]] = [
    {
        "env_var": "JIRA_BASE_URL",
        "label": "Jira Base URL",
        "placeholder": "https://org.atlassian.net",
        "masked": False,
        "required": True,
    },
    {
        "env_var": "JIRA_EMAIL",
        "label": "Jira Email",
        "placeholder": "you@company.com",
        "masked": False,
        "required": True,
    },
    {
        "env_var": "JIRA_API_TOKEN",
        "label": "Jira API Token",
        "placeholder": "",
        "masked": True,
        "required": True,
    },
    {
        "env_var": "JIRA_PROJECT_KEY",
        "label": "Project Key",
        "placeholder": "MYPROJ",
        "masked": False,
        "required": True,
    },
    {
        "env_var": "CONFLUENCE_SPACE_KEY",
        "label": "Confluence Space Key",
        "placeholder": "MYSPACE",
        "masked": False,
        "required": False,
    },
]

# Issue tracking fields — Azure DevOps Boards
_AZDEVOPS_TRACKING_FIELDS: list[dict[str, Any]] = [
    {
        "env_var": "AZURE_DEVOPS_ORG_URL",
        "label": "Organization URL",
        "placeholder": "https://dev.azure.com/myorg",
        "masked": False,
        "required": True,
    },
    {
        "env_var": "AZURE_DEVOPS_PROJECT",
        "label": "Project Name",
        "placeholder": "MyProject",
        "masked": False,
        "required": True,
    },
    {
        "env_var": "AZURE_DEVOPS_TOKEN",
        "label": "Personal Access Token",
        "placeholder": "",
        "masked": True,
        "required": True,
    },
    {
        "env_var": "AZURE_DEVOPS_TEAM",
        "label": "Team Name",
        "placeholder": "MyProject Team",
        "masked": False,
        "required": False,
    },
]

# Issue tracking provider options — user picks one before seeing fields
_ISSUE_TRACKING_OPTIONS: list[dict[str, Any]] = [
    {"name": "Jira", "fields": _ISSUE_TRACKING_FIELDS},
    {"name": "Azure DevOps Boards", "fields": _AZDEVOPS_TRACKING_FIELDS},
    {"name": "Skip", "fields": []},
]
