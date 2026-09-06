"""Recommended projects: what is configured, what each source yields, how cards
are chosen and worded, and the desk in front of it."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from yeaboi.projects import suggest
from yeaboi.projects.suggest import (
    Signal,
    SuggestDesk,
    SuggestionSheet,
    available_sources,
    build_sheet,
    choose,
    gather,
    read_cache,
    word,
    write_cache,
)

NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)

_CREDS = (
    "JIRA_BASE_URL",
    "JIRA_API_TOKEN",
    "AZURE_DEVOPS_ORG_URL",
    "AZURE_DEVOPS_PAT",
    "LINEAR_API_KEY",
    "GITHUB_TOKEN",
    "CONFLUENCE_BASE_URL",
    "CONFLUENCE_API_TOKEN",
    "NOTION_TOKEN",
)


@pytest.fixture
def bare(monkeypatch):
    """No credentials, no agent sessions."""
    for env in _CREDS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(suggest, "_agents_have_sessions", lambda since: False)


class TestAvailableSources:
    def test_nothing_configured_is_empty(self, bare):
        assert available_sources(now=NOW) == {}

    def test_each_source_is_named_by_its_credential(self, bare, monkeypatch):
        monkeypatch.setattr("yeaboi.analysis.setup.available_trackers", lambda: ["jira", "azdevops"])
        monkeypatch.setattr("yeaboi.analysis.setup.available_doc_sources", lambda: ["notion"])
        monkeypatch.setattr("yeaboi.config.get_linear_api_key", lambda: "lin_x")
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "ghp_x")
        monkeypatch.setattr(suggest, "_agents_have_sessions", lambda since: True)
        assert available_sources(now=NOW) == {
            "jira": "Jira",
            "azdevops": "Azure DevOps",
            "linear": "Linear",
            "github": "GitHub",
            "notion": "Notion",
            "agents": "Your agents",
        }

    def test_a_broken_config_still_leaves_the_agent_repos(self, bare, monkeypatch):
        def boom():
            raise RuntimeError("no config")

        monkeypatch.setattr("yeaboi.analysis.setup.available_trackers", boom)
        monkeypatch.setattr(suggest, "_agents_have_sessions", lambda since: True)
        assert available_sources(now=NOW) == {"agents": "Your agents"}


class TestGather:
    def test_every_source_is_read_and_one_failure_is_a_warning(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "SHOP")
        monkeypatch.setattr(
            "yeaboi.tools.jira.jira_open_tickets",
            lambda key, limit=200: [{"title": "Cart loses items"}, {"title": "Order email"}],
        )
        monkeypatch.setattr("yeaboi.tools.jira.jira_active_sprint_progress", lambda key: {"sprint_name": "Sprint 12"})

        def github_down(now):
            raise RuntimeError("401 Bad credentials ghp_secret")

        monkeypatch.setitem(suggest._READERS, "github", github_down)
        signals, warnings, read = gather({"jira": "Jira", "github": "GitHub"}, now=NOW)
        assert warnings == ("GitHub could not be read",)
        assert read == ("Jira",)
        assert len(signals) == 1
        jira = signals[0]
        assert jira.source == "jira" and jira.subject == "SHOP"
        assert jira.facts == "2 open tickets, sprint Sprint 12 in progress"
        assert jira.titles == ("Cart loses items", "Order email")
        assert jira.weight == 2

    def test_github_repos_carry_their_milestone_and_skip_quiet_ones(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: ("yeaboi-ai",))
        monkeypatch.setattr(
            "yeaboi.tools.github.github_analysis_inventory",
            lambda owners, days=120, include_trees=True: [
                {"name": "yeaboi-ai/shop", "url": "https://gh/shop", "updated_at": "2026-09-04", "active": True},
                {"name": "yeaboi-ai/quiet", "url": "https://gh/quiet", "updated_at": "2026-09-03", "active": True},
                {"name": "yeaboi-ai/old", "url": "https://gh/old", "updated_at": "2026-01-01", "active": False},
            ],
        )
        overviews = {
            "yeaboi-ai/shop": {
                "open_issues": 14,
                "milestone": "4.2",
                "milestone_due": "2026-09-12",
                "milestone_open": 9,
                "issue_titles": ["Cart loses items on refresh"],
            },
            "yeaboi-ai/quiet": {
                "open_issues": 0,
                "milestone": "",
                "milestone_due": "",
                "milestone_open": 0,
                "issue_titles": [],
            },
        }
        monkeypatch.setattr("yeaboi.tools.github.github_repo_overview", lambda slug, **kw: overviews[slug])
        monkeypatch.setattr("yeaboi.tools.github.github_list_owners", lambda: pytest.fail("owners are configured"))
        signals, warnings, read = gather({"github": "GitHub"}, now=NOW)
        assert warnings == () and read == ("GitHub",)
        assert [s.subject for s in signals] == ["yeaboi-ai/shop"]
        shop = signals[0]
        assert shop.facts == "14 open issues, milestone 4.2 due 12 Sep"
        assert shop.weight == 28  # a dated milestone doubles the weight
        assert shop.url == "https://gh/shop"

    def test_a_failed_github_discovery_is_a_warning_not_a_quiet_sheet(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: ("yeaboi-ai",))
        monkeypatch.setattr(
            "yeaboi.tools.github.github_analysis_inventory",
            lambda owners, days=120, include_trees=True: [
                {
                    "name": "yeaboi-ai",
                    "active": True,
                    "discovery_error": True,
                    "error": "repository discovery failed: 401 Bad credentials",
                }
            ],
        )
        monkeypatch.setattr(
            "yeaboi.tools.github.github_repo_overview",
            lambda slug, **kw: pytest.fail("a discovery sentinel is not a repo"),
        )
        signals, warnings, read = gather({"github": "GitHub"}, now=NOW)
        assert signals == () and read == ()
        assert warnings == ("GitHub could not be read",)

    def test_agent_repos_are_grouped_by_directory_and_missing_ones_skipped(self, monkeypatch, tmp_path):
        here = tmp_path / "yeaboi-desktop"
        here.mkdir()
        rows = [{"project_path": str(here)}] * 3 + [{"project_path": str(tmp_path / "gone")}]

        class FakeStore:
            def __init__(self, path):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def list_sessions(self, *, since=""):
                return rows

        monkeypatch.setattr("yeaboi.agentwatch.store.AgentWatchStore", FakeStore)
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        monkeypatch.setattr(
            "yeaboi.tools.local_git.local_git_recent_commits",
            lambda path, days=1, since=None: [{"title": "add the ledger sheet"}, {"title": "fix the dots"}],
        )
        signals, warnings, _read = gather({"agents": "Your agents"}, now=NOW)
        assert warnings == ()
        assert len(signals) == 1
        assert signals[0].subject == "yeaboi-desktop"
        assert signals[0].repo_path == str(here)
        assert signals[0].facts == "3 agent sessions and 2 commits in 14 days"
        assert signals[0].titles == ("add the ledger sheet", "fix the dots")

    def test_an_agent_repo_the_sandbox_hides_falls_back_to_its_branches(self, monkeypatch, tmp_path):
        here = tmp_path / "notes"
        here.mkdir()

        class FakeStore:
            def __init__(self, path):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def list_sessions(self, *, since=""):
                return [
                    {"project_path": str(here), "git_branch": "main"},
                    {"project_path": str(here), "git_branch": "desktop/project-page"},
                    {"project_path": str(here), "git_branch": "desktop/project-page"},
                    {"project_path": str(here), "git_branch": "home-screen"},
                ]

        monkeypatch.setattr("yeaboi.agentwatch.store.AgentWatchStore", FakeStore)
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        monkeypatch.setattr("yeaboi.tools.local_git.local_git_recent_commits", lambda path, days=1, since=None: [])
        (signal,), _, _ = gather({"agents": "Your agents"}, now=NOW)
        assert signal.facts == "4 agent sessions in 14 days"
        # The sandbox hid the repo, so the branches say what the work was; trunk says nothing.
        assert signal.titles == ("desktop/project-page", "home-screen")

    def test_doc_pages_rank_low(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.notion.notion_recent_pages",
            lambda days=1, **kw: [{"title": "Q4 plan"}, {"title": "Q4 plan"}, {"title": "Onboarding"}],
        )
        signals, _, _ = gather({"notion": "Notion"}, now=NOW)
        assert signals[0].facts == "2 pages edited in 14 days"
        assert signals[0].titles == ("Q4 plan", "Onboarding")
        assert signals[0].weight < 1


def _signal(source, subject, weight, **over):
    return Signal(source, suggest.SOURCE_LABELS[source], subject, "some facts", weight=weight, **over)


class TestChoose:
    def test_top_three_by_weight_with_at_most_two_per_source(self):
        chosen = choose(
            (
                _signal("github", "a", 10),
                _signal("github", "b", 9),
                _signal("github", "c", 8),
                _signal("jira", "SHOP", 5),
                _signal("notion", "Notion", 0.5),
            )
        )
        assert [(s.source, s.subject) for s in chosen] == [("github", "a"), ("github", "b"), ("jira", "SHOP")]

    def test_ties_break_on_subject_so_the_sheet_is_stable(self):
        chosen = choose((_signal("agents", "zeta", 3), _signal("agents", "alpha", 3), _signal("jira", "X", 3)))
        assert [s.subject for s in chosen] == ["alpha", "zeta", "X"]

    def test_nothing_in_nothing_out(self):
        assert choose(()) == ()


class TestWord:
    CARD = _signal("github", "yeaboi-ai/shop", 14, titles=("Cart loses items",))

    def test_without_a_provider_the_facts_are_the_text(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        (row,) = word((self.CARD,))
        assert row.wording == "facts"
        assert row.text == "yeaboi-ai/shop: some facts."
        assert row.source_label == "GitHub" and row.facts == "some facts"

    def test_the_model_words_the_cards_it_answers_for(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **kw: object())
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda response, **kw: None)
        seen: list[str] = []
        other = _signal("jira", "SHOP", 3)
        first_id = suggest._suggestion_id("github", "yeaboi-ai/shop")

        def fake_invoke(llm, prompt, images):
            seen.append(prompt)
            body = json.dumps({"suggestions": [{"id": first_id, "text": "Fix the cart that loses items on refresh."}]})
            return SimpleNamespace(content=f"```json\n{body}\n```")

        monkeypatch.setattr("yeaboi.agent.llm.invoke_with_images", fake_invoke)
        rows = word((self.CARD, other))
        assert [r.wording for r in rows] == ["ai", "facts"]
        assert rows[0].text == "Fix the cart that loses items on refresh."
        assert rows[1].text == "SHOP: some facts."
        assert len(seen) == 1 and "Cart loses items" in seen[0]

    def test_a_failing_model_keeps_the_facts_wording(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **kw: object())

        def boom(llm, prompt, images):
            raise RuntimeError("overloaded")

        monkeypatch.setattr("yeaboi.agent.llm.invoke_with_images", boom)
        (row,) = word((self.CARD,))
        assert row.wording == "facts"

    def test_no_cards_no_call(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: pytest.fail("no call without cards"))
        assert word(()) == ()


class TestBuildSheet:
    def test_connected_follows_the_sources(self, bare, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        sheet = build_sheet(now=NOW)
        assert sheet == SuggestionSheet(computed_at=NOW.isoformat(), connected=False)

    def test_sources_on_the_sheet_are_labels(self, bare, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        monkeypatch.setattr(suggest, "_agents_have_sessions", lambda since: True)
        monkeypatch.setitem(suggest._READERS, "agents", lambda now: [])
        sheet = build_sheet(now=NOW)
        assert sheet.sources == ("Your agents",) and sheet.connected is True and sheet.suggestions == ()

    def test_a_source_that_fails_is_a_warning_and_not_a_source(self, bare, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_dead")
        monkeypatch.setattr(suggest, "_agents_have_sessions", lambda since: True)
        monkeypatch.setitem(suggest._READERS, "agents", lambda now: [])

        def github_down(now):
            raise RuntimeError("401")

        monkeypatch.setitem(suggest._READERS, "github", github_down)
        sheet = build_sheet(now=NOW)
        assert sheet.sources == ("Your agents",)
        assert sheet.warnings == ("GitHub could not be read",)
        assert sheet.connected is True


class TestDesk:
    def _desk(self, tmp_path, *, clock, build, ttl=100.0):
        path = tmp_path / "project_suggestions.json"
        return SuggestDesk(
            cache_path=lambda: path,
            clock=clock,
            build=build,
            available=lambda now=None: {"github": "GitHub"},
            spawn=lambda target, **kw: SimpleNamespace(start=target),
            ttl=ttl,
        )

    def test_first_call_is_an_empty_stale_sheet_and_refreshes_once(self, tmp_path):
        calls: list[datetime] = []
        sheet = SuggestionSheet(suggestions=(), sources=("github",), computed_at="x", connected=True)

        def build(*, now):
            calls.append(now)
            return sheet

        desk = self._desk(tmp_path, clock=lambda: 1000.0, build=build)
        first, refreshing = desk.get()
        # The stale first sheet names what the refresh is reading.
        assert first == SuggestionSheet(stale=True, connected=True, sources=("GitHub",))
        assert refreshing is True  # the synchronous spawn ran the refresh to completion
        assert calls == [datetime.fromtimestamp(1000.0, tz=timezone.utc)]
        second, refreshing = desk.get()
        assert second == sheet and refreshing is False
        assert calls and len(calls) == 1

    def test_an_expired_cache_answers_stale_and_refreshes(self, tmp_path):
        ticks = iter([500.0, 500.0, 500.0, 500.0, 500.0])
        clock = lambda: next(ticks)  # noqa: E731
        old = SuggestionSheet(sources=("github",), computed_at="old", connected=True)
        new = SuggestionSheet(sources=("github",), computed_at="new", connected=True)
        path = tmp_path / "project_suggestions.json"
        write_cache(path, old, 0.0)
        desk = self._desk(tmp_path, clock=clock, build=lambda *, now: new)
        answered, refreshing = desk.get()
        assert answered.computed_at == "old" and answered.stale is True
        assert read_cache(path)[0].computed_at == "new"

    def test_refresh_is_forced_and_a_failed_build_keeps_the_last_sheet(self, tmp_path):
        old = SuggestionSheet(sources=("github",), computed_at="old", connected=True)
        path = tmp_path / "project_suggestions.json"
        write_cache(path, old, 1000.0)

        def build(*, now):
            raise RuntimeError("github down")

        desk = self._desk(tmp_path, clock=lambda: 1000.0, build=build)
        answered, _ = desk.get(refresh=True)
        assert answered.computed_at == "old" and answered.stale is True
        assert read_cache(path)[0] == old

    def test_a_refresh_in_flight_starts_no_second_one(self, tmp_path):
        started: list[str] = []

        class Never:
            def __init__(self, target, **kw):
                started.append("thread")

            def start(self):
                pass

        desk = SuggestDesk(
            cache_path=lambda: tmp_path / "s.json",
            clock=lambda: 1.0,
            build=lambda *, now: SuggestionSheet(),
            available=lambda now=None: {},
            spawn=Never,
            ttl=10.0,
        )
        desk.get()
        desk.get()
        assert started == ["thread"]
        assert desk._refreshing.locked()
        desk._refreshing.release()

    def test_cache_round_trip_drops_unknown_keys(self, tmp_path):
        from yeaboi.projects.suggest import Suggestion

        sheet = SuggestionSheet(
            suggestions=(Suggestion("id1", "text", "jira", "Jira", "SHOP", "3 open tickets", wording="ai"),),
            sources=("jira",),
            warnings=("Notion could not be read",),
            computed_at="2026-09-05",
            connected=True,
        )
        path = tmp_path / "s.json"
        write_cache(path, sheet, 42.0)
        raw = json.loads(path.read_text())
        raw["sheet"]["suggestions"][0]["later_field"] = "ignored"
        raw["sheet"]["later_top"] = 1
        path.write_text(json.dumps(raw))
        assert read_cache(path) == (sheet, 42.0)

    def test_an_unreadable_cache_is_none(self, tmp_path):
        path = tmp_path / "s.json"
        assert read_cache(path) is None
        path.write_text("{not json")
        assert read_cache(path) is None

    def test_the_lock_is_a_real_lock(self, tmp_path):
        desk = self._desk(tmp_path, clock=lambda: 1.0, build=lambda *, now: SuggestionSheet())
        assert isinstance(desk._refreshing, type(threading.Lock()))
