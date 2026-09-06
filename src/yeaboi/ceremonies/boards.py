"""Opening a live board on a cadence.

A retro or a poker table is a room, not a report: the ceremony's job is to open
it at the hour and hand round the link. The room is hosted by the running
``yeaboi app`` — a launchd job exits in seconds and would take the board with it
— so these engines ask that process to open one. That makes "yeaboi is running"
the prerequisite for scheduling either, and a fire with nothing to ask records a
failure that says so.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from yeaboi.agent.state import BoardInvite
from yeaboi.app.handshake import Handshake
from yeaboi.app.instancelock import live_instance

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30.0
#: How long to wait for the shareable address. The board is open either way —
#: the tunnel is what makes it reachable from someone else's machine, and it
#: usually lands in a couple of seconds.
_LINK_WAIT_SECONDS = 25.0
_LINK_POLL_SECONDS = 1.0

NOT_RUNNING = (
    "yeaboi is not running, so there is nothing to host the board — "
    "leave the app open in the background for a board ceremony to fire"
)


class AppNotRunningError(RuntimeError):
    """No live backend to open a board in."""


def _call(app: Handshake, method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - loopback URL from our own 0600 handshake file
        f"{app.url}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {app.token}",
            **({"Content-Type": "application/json"} if body else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _app() -> Handshake:
    app = live_instance()
    if app is None:
        raise AppNotRunningError(NOT_RUNNING)
    return app


def _way_in(app: Handshake, snapshot: dict) -> tuple[str, str]:
    """(join url, display code) once the tunnel lands, or what there is without it."""
    board_id = str(snapshot.get("board_id", ""))
    code = str(snapshot.get("display_code", ""))
    deadline = time.monotonic() + _LINK_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            answer = _call(app, "GET", f"/api/boards/{board_id}/invite")
        except (urllib.error.URLError, OSError, ValueError):
            break
        invite = str(answer.get("invite", ""))
        if invite:
            return invite, str(answer.get("display_code", code))
        time.sleep(_LINK_POLL_SECONDS)
    logger.warning("board ceremony: no shareable link for %s — the code is all there is", board_id)
    return "", code


def open_retro_board(session_id: str = "") -> BoardInvite:
    """Open the retro board for the latest session, and say how to join it."""
    app = _app()
    snapshot = _call(app, "POST", "/api/boards/retro")
    join_url, code = _way_in(app, snapshot)
    logger.info("board ceremony: retro %s open", snapshot.get("board_id"))
    return BoardInvite(
        kind="retro",
        title=str(snapshot.get("title") or "Retro"),
        board_id=str(snapshot.get("board_id", "")),
        join_url=join_url,
        display_code=code,
        detail=str(snapshot.get("project_name", "")),
    )


def open_poker_board(source: str = "", sprint: str = "", session_id: str = "") -> BoardInvite:
    """Open a poker table over a scope, and say how to join it.

    A blank source takes the first this machine offers, which is the tracker
    that is configured and the demo tickets when none is.
    """
    app = _app()
    if not source:
        options = _call(app, "GET", "/api/poker/options").get("sources") or []
        source = str(options[0]["key"]) if options else "demo"

    fetched = _call(app, "POST", "/api/poker/tickets", {"source": source, "sprint": sprint or None})
    tickets = fetched.get("tickets") or []
    if not tickets:
        # The empty-scope sentence the surfaces already show, so the ledger row
        # reads like the screen would have.
        raise RuntimeError(fetched.get("message") or f"no tickets to estimate in {source}")

    snapshot = _call(
        app,
        "POST",
        "/api/boards/poker",
        {"source": source, "scope_label": fetched.get("scope_label", ""), "tickets": tickets},
    )
    join_url, code = _way_in(app, snapshot)
    logger.info("board ceremony: poker %s open (%d tickets)", snapshot.get("board_id"), len(tickets))
    return BoardInvite(
        kind="poker",
        title=str(snapshot.get("title") or "Planning poker"),
        board_id=str(snapshot.get("board_id", "")),
        join_url=join_url,
        display_code=code,
        detail=f"{len(tickets)} tickets · {fetched.get('scope_label', '') or source}",
    )
