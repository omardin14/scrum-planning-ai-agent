"""The room-shaped ceremonies (ceremonies/boards.py).

A board is hosted by the running app, so these engines are a client of it. What
matters is that they refuse honestly when nothing is running, that they hand
back the way in once the tunnel lands, and that an empty scope reads as the
configuration story it is rather than as a crash.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import BoardInvite
from yeaboi.app.handshake import Handshake
from yeaboi.ceremonies import boards, renderers

HS = Handshake(url="http://127.0.0.1:9999", token="t", pid=1, schema=1, version="0")


@pytest.fixture
def live(monkeypatch):
    """A running app, and the calls made to it."""
    monkeypatch.setattr(boards, "live_instance", lambda: HS)
    monkeypatch.setattr(boards.time, "sleep", lambda _seconds: None)
    seen: list[tuple[str, str, dict | None]] = []
    return seen


def _answers(monkeypatch, seen, table):
    def _call(_app, method, path, payload=None):
        seen.append((method, path, payload))
        for prefix, answer in table:
            if path.startswith(prefix):
                return answer
        raise AssertionError(f"unexpected call: {method} {path}")

    monkeypatch.setattr(boards, "_call", _call)


class TestNothingToHostIt:
    def test_a_dead_app_refuses_with_the_prerequisite(self, monkeypatch):
        monkeypatch.setattr(boards, "live_instance", lambda: None)
        with pytest.raises(boards.AppNotRunningError, match="leave the app open"):
            boards.open_retro_board()


class TestRetro:
    def test_opens_the_board_and_returns_the_way_in(self, monkeypatch, live):
        _answers(
            monkeypatch,
            live,
            [
                ("/api/boards/retro", {"board_id": "b1", "title": "Sprint 4 retro", "display_code": "ABC-DEF"}),
                ("/api/boards/b1/invite", {"invite": "https://x.trycloudflare.com/?c=1", "display_code": "ABC-DEF"}),
            ],
        )
        invite = boards.open_retro_board()
        assert invite.kind == "retro"
        assert invite.join_url == "https://x.trycloudflare.com/?c=1"
        assert ("POST", "/api/boards/retro", None) in live

    def test_a_tunnel_that_never_lands_still_returns_the_code(self, monkeypatch, live):
        monkeypatch.setattr(boards, "_LINK_WAIT_SECONDS", 0.01)
        _answers(
            monkeypatch,
            live,
            [
                ("/api/boards/retro", {"board_id": "b1", "display_code": "ABC-DEF"}),
                ("/api/boards/b1/invite", {"invite": "", "display_code": "ABC-DEF"}),
            ],
        )
        invite = boards.open_retro_board()
        assert invite.join_url == ""
        assert invite.display_code == "ABC-DEF"


class TestPoker:
    def test_fetches_a_scope_then_opens_the_table(self, monkeypatch, live):
        _answers(
            monkeypatch,
            live,
            [
                ("/api/poker/options", {"sources": [{"key": "jira"}, {"key": "demo"}]}),
                ("/api/poker/tickets", {"tickets": [{"key": "YB-1"}], "scope_label": "Sprint 9"}),
                ("/api/boards/poker", {"board_id": "b2", "title": "Poker", "display_code": "Z"}),
                ("/api/boards/b2/invite", {"invite": "https://join", "display_code": "Z"}),
            ],
        )
        invite = boards.open_poker_board()
        assert invite.detail == "1 tickets · Sprint 9"
        # The blank source took the first this machine offers, and the table was
        # opened over the tickets that came back rather than over a fresh fetch.
        assert ("POST", "/api/poker/tickets", {"source": "jira", "sprint": None}) in live
        opened = [call for call in live if call[1] == "/api/boards/poker"][0]
        assert opened[2]["tickets"] == [{"key": "YB-1"}]

    def test_an_empty_scope_is_the_configuration_story(self, monkeypatch, live):
        _answers(
            monkeypatch,
            live,
            [
                ("/api/poker/options", {"sources": [{"key": "demo"}]}),
                ("/api/poker/tickets", {"tickets": [], "message": "Jira returned no tickets for Sprint 9"}),
            ],
        )
        with pytest.raises(RuntimeError, match="no tickets for Sprint 9"):
            boards.open_poker_board()


class TestTheInviteReads:
    def test_the_link_is_on_the_summary_line(self):
        dispatch = renderers.board_invite_dispatch(
            BoardInvite(kind="retro", title="Sprint 4 retro", join_url="https://join")
        )
        assert "https://join" in dispatch.summary

    def test_without_a_link_it_says_where_to_go(self):
        dispatch = renderers.board_invite_dispatch(BoardInvite(kind="poker", title="Poker"))
        assert dispatch.summary
        assert "yeaboi" in dispatch.summary
