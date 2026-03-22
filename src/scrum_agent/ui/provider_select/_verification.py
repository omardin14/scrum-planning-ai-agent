"""Validation and API verification for provider setup.

# See README: "Architecture" — verification layer for the setup wizard.
# Handles format validation of API keys and live verification via API calls.
"""

from __future__ import annotations

from typing import Any


def _validate_key(provider: dict[str, Any], value: str) -> tuple[str, str]:
    """Realtime format validation of an API key (or region for Bedrock).

    Returns (status, hint_message) where status is one of:
    - "empty": no input yet
    - "bad_prefix": wrong prefix
    - "too_short": right prefix but too short
    - "valid_format": passes format checks (needs live verification)
    """
    # Bedrock uses a region name, not an API key
    if provider.get("is_region_input"):
        if not value:
            return "empty", ""
        # Basic region format check: e.g. us-east-1, eu-west-2
        if "-" in value and len(value) >= 7:
            return "valid_format", "Press Enter to verify \u2014 edit region or confirm"
        return "too_short", "Enter an AWS region (e.g. us-east-1, eu-west-2)"

    prefix = provider["prefix"]
    name = provider["full_name"]

    if not value:
        return "empty", ""

    min_lengths = {"sk-ant-": 40, "sk-": 30, "AIza": 30}
    min_len = min_lengths.get(prefix, 30)

    if not value.startswith(prefix):
        return "bad_prefix", f"Expected prefix: {prefix}..."

    if len(value) < min_len:
        return "too_short", f"Too short \u2014 {name} keys are typically {min_len}+ chars"

    return "valid_format", "Format looks good \u2014 press Enter to verify"


def _verify_api_key(provider: dict[str, Any], api_key: str) -> tuple[bool, str]:
    """Make a lightweight API call to verify the key actually works.

    Returns (success, message).
    """
    provider_val = provider["provider_val"]

    try:
        if provider_val == "anthropic":
            import httpx

            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10,
            )
            if resp.status_code in (200, 201):
                return True, "Key verified"
            if resp.status_code == 401:
                return False, "Invalid API key"
            if resp.status_code == 403:
                return False, "Key lacks permissions"
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "openai":
            import httpx

            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Key verified"
            if resp.status_code == 401:
                return False, "Invalid API key"
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "google":
            import httpx

            resp = httpx.get(
                f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Key verified"
            if resp.status_code in (400, 401, 403):
                return False, "Invalid API key"
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "bedrock":
            # Bedrock verification — api_key is actually the region name.
            # Uses IAM credentials from instance role, ~/.aws/credentials, or env vars.
            # Auto-detects the AWS profile from ~/.aws/config (e.g. Lightsail's
            # [profile assumed] with credential_source=Ec2InstanceMetadata).
            import boto3

            from scrum_agent.config import get_aws_profile

            profile = get_aws_profile()
            session = boto3.Session(profile_name=profile, region_name=api_key)
            client = session.client("bedrock", region_name=api_key)
            resp = client.list_foundation_models(byOutputModality="TEXT")
            if resp.get("modelSummaries") is not None:
                return True, "AWS credentials verified"
            return False, "Unexpected response from Bedrock"

    except Exception as e:
        err_str = str(e)
        if "NoCredentialsError" in type(e).__name__ or "NoCredentialsError" in err_str:
            return False, "No AWS credentials found \u2014 configure IAM role, ~/.aws/credentials, or env vars"
        if "InvalidIdentityToken" in err_str or "AccessDenied" in err_str or "403" in err_str:
            return False, "AWS credentials lack Bedrock permissions"
        return False, f"Connection error: {e}"

    return False, "Unknown provider"


def _verify_vc_token(vc: dict[str, Any], token: str) -> tuple[bool, str]:
    """Verify a version control PAT token with a lightweight API call."""
    env_var = vc["env_var"]
    try:
        import httpx

        if env_var == "GITHUB_TOKEN":
            resp = httpx.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Token verified"
            if resp.status_code == 401:
                return False, "Invalid token"
            if resp.status_code == 403:
                return False, "Token lacks permissions"
            return False, f"Unexpected response: {resp.status_code}"

        elif env_var == "AZURE_DEVOPS_TOKEN":
            # Azure DevOps PAT — check against the profile endpoint.
            # Uses Basic auth with empty username and PAT as password.
            import base64

            b64 = base64.b64encode(f":{token}".encode()).decode()
            resp = httpx.get(
                "https://app.vssps.visualstudio.com/_apis/profile/profiles/me?api-version=7.0",
                headers={"Authorization": f"Basic {b64}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Token verified"
            if resp.status_code in (401, 403):
                return False, "Invalid token"
            return False, f"Unexpected response: {resp.status_code}"

    except Exception as e:
        return False, f"Connection error: {e}"

    return False, "Unknown provider"


def _verify_jira(base_url: str, email: str, token: str) -> tuple[bool, str]:
    """Verify Jira credentials with a lightweight API call."""
    try:
        import httpx

        url = f"{base_url.rstrip('/')}/rest/api/3/myself"
        import base64

        b64 = base64.b64encode(f"{email}:{token}".encode()).decode()
        resp = httpx.get(
            url,
            headers={"Authorization": f"Basic {b64}", "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "Jira verified"
        if resp.status_code in (401, 403):
            return False, "Invalid Jira credentials"
        return False, f"Unexpected response: {resp.status_code}"
    except Exception as e:
        return False, f"Connection error: {e}"
