#!/usr/bin/env python3
"""
canvas_push.py — Watch /workspace/scrum_plan.md and push to Slack canvas on change.

Run as a background process. When the plan file changes, automatically creates
or updates the canvas in #scrum-planner.

Usage:
    python3 canvas_push.py [--channel CHANNEL_ID] [--workspace /path/to/workspace]
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


CHANNEL_ID = os.environ.get("SCRUM_CHANNEL_ID", "C0AMR19S057")
# Support both host path and container mount path
_WS_CANDIDATES = [
    Path(os.environ.get("OPENCLAW_WORKSPACE", "")),
    Path("/workspace"),
    Path("/home/ubuntu/.openclaw/workspace"),
]
WORKSPACE = next((p for p in _WS_CANDIDATES if p.exists() and p != Path("")), Path("/workspace"))
PLAN_FILE = WORKSPACE / "scrum_plan.md"
STATE_FILE = WORKSPACE / ".canvas_state.json"
POLL_INTERVAL = 3  # seconds


def get_bot_token() -> str:
    # Try multiple locations — container runs as root, host as ubuntu
    candidates = [
        Path("/home/ubuntu/.openclaw/openclaw.json"),
        Path.home() / ".openclaw" / "openclaw.json",
    ]
    for p in candidates:
        if p.exists():
            return json.loads(p.read_text())["channels"]["slack"]["botToken"]
    raise FileNotFoundError(f"Config not found in: {candidates}")


def slack_api(method: str, payload: dict, token: str) -> dict:
    import ssl
    url = f"https://slack.com/api/{method}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("CURL_CA_BUNDLE")
    ctx = ssl.create_default_context(cafile=ca) if ca and Path(ca).exists() else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            result = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "body": e.read().decode()}
    return result


def format_canvas(raw: str) -> str:
    lines = raw.split("\n")
    out = []
    for line in lines:
        line = re.sub(r"^#{4,}\s+", "### ", line)
        line = re.sub(r"^(\s*)•\s+", r"\1- ", line)
        line = re.sub(r"^(\s*)→\s+", r"\1- ", line)
        out.append(line)
    result = "\n".join(out)
    result = re.sub(r"\n---\n", "\n\n---\n\n", result)
    result = re.sub(r"\n(#{1,3} )", r"\n\n\1", result)
    return result.strip()


def extract_title(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "Sprint Plan"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def push_to_canvas(content: str, token: str, existing_canvas_id: str | None) -> str | None:
    """Create or update the channel canvas. Returns canvas_id on success."""
    formatted = format_canvas(content)
    title = extract_title(content)

    # Try to update existing canvas first
    if existing_canvas_id:
        r = slack_api("canvases.edit", {
            "canvas_id": existing_canvas_id,
            "changes": [{"operation": "replace", "document_content": {"type": "markdown", "markdown": formatted}}]
        }, token)
        if r.get("ok"):
            print(f"[canvas_push] Updated canvas {existing_canvas_id}")
            return existing_canvas_id
        print(f"[canvas_push] Update failed ({r.get('error')}) — creating new")

    # conversations.canvases.create attaches to the channel canvas tab (visible in sidebar)
    r = slack_api("conversations.canvases.create", {
        "channel_id": CHANNEL_ID,
        "document_content": {"type": "markdown", "markdown": formatted},
    }, token)

    if r.get("ok"):
        # conversations.canvases.create doesn't return a canvas_id directly
        # fetch it from conversations.info
        r2 = slack_api("conversations.info", {"channel": CHANNEL_ID, "include_num_members": False}, token)
        canvas_id = r2.get("channel", {}).get("properties", {}).get("canvas", {}).get("file_id")
        if canvas_id:
            print(f"[canvas_push] Created channel canvas {canvas_id}: {title}")
            return canvas_id
        # fallback — no id but creation succeeded
        print(f"[canvas_push] Created channel canvas (id unknown): {title}")
        return "channel-canvas"

    # Last resort: standalone canvas shared to channel
    print(f"[canvas_push] conversations.canvases.create failed ({r.get('error')}), trying standalone")
    r3 = slack_api("canvases.create", {
        "title": title,
        "document_content": {"type": "markdown", "markdown": formatted},
    }, token)
    canvas_id = r3.get("canvas_id")
    if canvas_id:
        slack_api("canvases.access.set", {
            "canvas_id": canvas_id, "access_level": "write", "channel_ids": [CHANNEL_ID]
        }, token)
        print(f"[canvas_push] Created standalone canvas {canvas_id}: {title}")
    else:
        print(f"[canvas_push] All canvas methods failed: {r3.get('error')}")
    return canvas_id


def run_once():
    """Check if plan file changed since last push and push if so."""
    state = load_state()
    if not PLAN_FILE.exists():
        print(f"[canvas_push] No plan file at {PLAN_FILE}")
        return

    mtime = PLAN_FILE.stat().st_mtime
    last_mtime = state.get("last_mtime", 0)

    if mtime <= last_mtime:
        print(f"[canvas_push] No change since last push")
        return

    content = PLAN_FILE.read_text().strip()
    if not content:
        print(f"[canvas_push] Plan file is empty")
        return

    print(f"[canvas_push] Pushing to canvas...")
    token = get_bot_token()
    canvas_id = push_to_canvas(content, token, state.get("canvas_id"))
    if canvas_id:
        state["canvas_id"] = canvas_id
        state["last_mtime"] = mtime
        state["last_push"] = time.time()
        save_state(state)
        print(f"[canvas_push] Done — canvas_id={canvas_id}")


def run_watch():
    print(f"[canvas_push] Watching {PLAN_FILE} → #{CHANNEL_ID}")
    last_mtime = None
    state = load_state()

    while True:
        try:
            if PLAN_FILE.exists():
                mtime = PLAN_FILE.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    content = PLAN_FILE.read_text().strip()
                    if content:
                        print(f"[canvas_push] Change detected, pushing to canvas...")
                        token = get_bot_token()
                        canvas_id = push_to_canvas(content, token, state.get("canvas_id"))
                        if canvas_id:
                            state["canvas_id"] = canvas_id
                            state["last_push"] = time.time()
                            save_state(state)
        except Exception as e:
            print(f"[canvas_push] Error: {e}")

        time.sleep(POLL_INTERVAL)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Check once and exit (don't watch)")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_watch()


if __name__ == "__main__":
    main()
