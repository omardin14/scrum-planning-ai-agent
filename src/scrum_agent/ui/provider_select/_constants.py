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

# Version control providers — step 3
_VC_OPTIONS: list[dict[str, Any]] = [
    {
        "name": "GitHub",
        "env_var": "GITHUB_TOKEN",
        "prefix": "ghp_",
        "instructions": "Get yours at: https://github.com/settings/tokens",
        "color": "rgb(70,100,180)",
    },
    {
        "name": "Azure DevOps",
        "env_var": "AZURE_DEVOPS_TOKEN",
        "prefix": "",
        "instructions": "Get yours at: https://dev.azure.com \u2192 Personal Access Tokens",
        "color": "rgb(70,100,180)",
    },
]

# Issue tracking fields — step 4
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
