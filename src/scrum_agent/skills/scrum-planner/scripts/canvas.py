#!/usr/bin/env python3
"""
canvas.py — Slack Canvas operations for the scrum-planner skill.

Creates or updates a channel canvas with the sprint plan content.
Reads the bot token from ~/.openclaw/openclaw.json at runtime.

Usage:
    python canvas.py create-channel-canvas --channel C1234567 --content @/tmp/plan.md
    python canvas.py update --canvas-id F1234567 --content @/tmp/plan.md
    python canvas.py get-channel-canvas --channel C1234567
"""

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path


def get_bot_token() -> str:
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found at {config_path}")
    config = json.loads(config_path.read_text())
    token = config.get("channels", {}).get("slack", {}).get("botToken", "")
    if not token:
        raise ValueError("No Slack bot token found in openclaw.json")
    return token


def slack_api(method: str, payload: dict, token: str) -> dict:
    url = f"https://slack.com/api/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()}") from e
    if not result.get("ok"):
        raise RuntimeError(f"Slack API error ({method}): {result.get('error', 'unknown')} — {json.dumps(result)}")
    return result


def format_canvas_markdown(raw: str) -> str:
    """
    Post-process the plan markdown so it renders well in Slack Canvas.

    Slack Canvas supports a subset of markdown:
    - # H1, ## H2, ### H3 headings
    - **bold**, *italic*
    - - bullet lists (unordered)
    - 1. numbered lists
    - `inline code`
    - ``` code blocks
    - --- horizontal rules
    - > blockquotes
    - [text](url) links

    What it does NOT support well:
    - Markdown tables (render as raw text)
    - • bullet character (use - instead)
    - → arrow character (use - or numbered list)
    - Emoji shortcodes like :white_check_mark: (use actual emoji ✅)
    - HTML tags
    """
    import re

    lines = raw.split("\n")
    out = []

    for line in lines:
        # Slack Canvas only supports H1/H2/H3 — downgrade deeper headings
        line = re.sub(r"^####\s+", "### ", line)
        line = re.sub(r"^#####\s+", "### ", line)
        line = re.sub(r"^######\s+", "### ", line)

        # Convert bullet • to -
        line = re.sub(r"^(\s*)•\s+", r"\1- ", line)

        # Convert → task arrows to indented bullets
        line = re.sub(r"^(\s*)→\s+", r"\1- ", line)

        # Convert emoji shortcodes to actual emoji
        emoji_map = {
            ":white_check_mark:": "✅",
            ":x:": "❌",
            ":warning:": "⚠️",
            ":rocket:": "🚀",
            ":clipboard:": "📋",
            ":gear:": "⚙️",
            ":hammer:": "🔨",
            ":bug:": "🐛",
            ":lock:": "🔒",
            ":chart_with_upwards_trend:": "📈",
            ":books:": "📚",
            ":pencil:": "✏️",
            ":mag:": "🔍",
            ":link:": "🔗",
        }
        for code, emoji in emoji_map.items():
            line = line.replace(code, emoji)

        out.append(line)

    result = "\n".join(out)

    # Ensure section breaks have proper spacing around ---
    result = re.sub(r"\n---\n", "\n\n---\n\n", result)

    # Ensure headings have a blank line before them (Canvas renders better)
    result = re.sub(r"\n(#{1,3} )", r"\n\n\1", result)

    return result.strip()


def create_channel_canvas(channel_id: str, content: str, token: str) -> dict:
    """Create a canvas and share it to the channel."""
    formatted = format_canvas_markdown(content)

    # Extract title from first H1 line
    title = "Sprint Plan"
    for line in formatted.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # canvases.create produces better rendering than conversations.canvases.create
    result = slack_api(
        "canvases.create",
        {
            "title": title,
            "document_content": {"type": "markdown", "markdown": formatted},
        },
        token,
    )

    canvas_id = result.get("canvas_id")
    if canvas_id:
        slack_api(
            "canvases.access.set",
            {
                "canvas_id": canvas_id,
                "access_level": "write",
                "channel_ids": [channel_id],
            },
            token,
        )

    return result


def update_canvas(canvas_id: str, content: str, token: str) -> dict:
    """Update an existing canvas with new content."""
    formatted = format_canvas_markdown(content)
    result = slack_api(
        "canvases.edit",
        {
            "canvas_id": canvas_id,
            "changes": [
                {
                    "operation": "replace",
                    "document_content": {
                        "type": "markdown",
                        "markdown": formatted,
                    },
                }
            ],
        },
        token,
    )
    return result


def get_channel_canvas(channel_id: str, token: str) -> dict | None:
    """Get the canvas attached to a channel, or None if none exists."""
    try:
        result = slack_api(
            "conversations.info",
            {
                "channel": channel_id,
                "include_num_members": False,
            },
            token,
        )
        canvas = result.get("channel", {}).get("properties", {}).get("canvas", {})
        return canvas if canvas.get("file_id") else None
    except RuntimeError:
        return None


def main():
    parser = argparse.ArgumentParser(description="Slack Canvas operations")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-channel-canvas")
    p_create.add_argument("--channel", required=True)
    p_create.add_argument("--content", required=True, help="Markdown string or @filepath")

    p_update = sub.add_parser("update")
    p_update.add_argument("--canvas-id", required=True)
    p_update.add_argument("--content", required=True, help="Markdown string or @filepath")

    p_get = sub.add_parser("get-channel-canvas")
    p_get.add_argument("--channel", required=True)

    args = parser.parse_args()
    token = get_bot_token()

    def resolve(raw: str) -> str:
        return Path(raw[1:]).read_text() if raw.startswith("@") else raw

    if args.command == "create-channel-canvas":
        result = create_channel_canvas(args.channel, resolve(args.content), token)
        print(json.dumps(result, indent=2))
    elif args.command == "update":
        result = update_canvas(args.canvas_id, resolve(args.content), token)
        print(json.dumps(result, indent=2))
    elif args.command == "get-channel-canvas":
        canvas = get_channel_canvas(args.channel, token)
        print(json.dumps(canvas or {"ok": False, "error": "no_canvas"}, indent=2))


if __name__ == "__main__":
    main()
