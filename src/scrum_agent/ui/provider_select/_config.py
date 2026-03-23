"""Configuration persistence for the provider selection wizard.

# See README: "Architecture" — saves collected provider settings
# incrementally to ~/.scrum-agent/.env during the setup wizard.
"""

from __future__ import annotations

import os


def _save_progress(data: dict[str, str]) -> None:
    """Incrementally save collected values to ~/.scrum-agent/.env.

    Merges with existing config so we never lose previously saved values.
    When Bedrock is the provider, auto-detects the model ID from OpenClaw's
    config so scrum-agent uses the correct model (e.g. global.anthropic.claude-sonnet-4-6).
    """
    from scrum_agent.config import get_config_file

    config_file = get_config_file()
    existing: dict[str, str] = {}
    if config_file.exists():
        for line in config_file.read_text().splitlines():
            stripped = line.strip()
            if stripped and "=" in stripped and not stripped.startswith("#"):
                k, _, v = stripped.partition("=")
                existing[k.strip()] = v.strip()
    merged = {**existing, **data}

    # Auto-detect Bedrock model from OpenClaw if not already set
    if merged.get("LLM_PROVIDER") == "bedrock" and "LLM_MODEL" not in merged:
        from scrum_agent.setup_wizard import _detect_openclaw_bedrock_model

        detected = _detect_openclaw_bedrock_model()
        if detected:
            merged["LLM_MODEL"] = detected

    lines = [f"{k}={v}\n" for k, v in merged.items() if v]
    config_file.write_text("".join(lines))
    os.environ.update({k: v for k, v in merged.items() if v})
