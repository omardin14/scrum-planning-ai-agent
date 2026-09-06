"""Project references: what each source yields, how a query narrows it, and the desk in front."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from yeaboi.mcp.runtime import to_jsonable
from yeaboi.projects import references
from yeaboi.projects.references import (
    DEFAULT_LIMIT,
    LIVE_SOURCES,
    MAX_LIMIT,
    SOURCE_LABELS,
    SOURCES,
    Reference,
    ReferenceDesk,
    ReferenceSheet,
    matches,
    narrow,
    read,
)

NOW = datetime(2026, 9, 6, 20, 0, tzinfo=timezone.utc)


def _rows(n: int, source: str = "jira") -> list[Reference]:
    return [Reference(f"{source}:K-{i}", f"K-{i}", f"K-{i} Row {i}", "Open", f"https://x/{i}") for i in range(n)]


class TestShapes:
    def test_every_source_has_a_label_and_the_wire_keys_are_pinned(self):
        from dataclasses import fields

        assert set(SOURCES) == set(SOURCE_LABELS)
        assert [f.name for f in fields(Reference)] == ["id", "subject", "label", "detail", "url"]
        assert [f.name for f in fields(ReferenceSheet)] == ["source", "source_label", "items", "warning"]

    def test_to_jsonable_round_trip(self):
        sheet = ReferenceSheet("jira", "Jira", tuple(_rows(1)))
        assert to_jsonable(sheet) == {
            "source": "jira",
            "source_label": "Jira",
            "items": [
                {"id": "jira:K-0", "subject": "K-0", "label": "K-0 Row 0", "detail": "Open", "url": "https://x/0"}
            ],
            "warning": "",
        }


class TestRead:
    def test_an_unknown_source_is_a_value_error(self):
        with pytest.raises(ValueError, match="unknown source"):
            read("aws", now=NOW)

    def test_a_reader_that_raises_is_a_warning_not_an_error(self):
        def boom(q, now):
            raise RuntimeError("401")

        sheet = read("jira", now=NOW, readers={"jira": boom})
        assert sheet.items == () and sheet.warning == "Jira could not be read"
        assert sheet.source_label == "Jira"

    def test_rows_and_the_normalised_query_reach_the_reader(self):
        seen = {}

        def reader(q, now):
            seen["q"], seen["now"] = q, now
            return _rows(2)

        sheet = read("notion", "  login   page ", now=NOW, readers={"notion": reader})
        assert seen == {"q": "login page", "now": NOW}
        assert len(sheet.items) == 2 and sheet.warning == ""


class TestNarrow:
    def test_every_token_must_appear_case_folded(self):
        row = Reference("jira:K-1", "K-1", "K-1 Fix the Login flow", "In Progress", "")
        assert matches(row, "login fix")
        assert matches(row, "PROGRESS")
        assert not matches(row, "login logout")
        assert matches(row, "")

    def test_a_tracker_is_filtered_and_capped(self):
        sheet = ReferenceSheet("jira", "Jira", tuple(_rows(30)))
        assert [r.subject for r in narrow(sheet, "row 1", 5).items] == ["K-1", "K-10", "K-11", "K-12", "K-13"]
        assert len(narrow(sheet, "", 999).items) == MAX_LIMIT
        assert len(narrow(sheet, "", 0).items) == 1
        assert len(narrow(sheet, "").items) == DEFAULT_LIMIT

    def test_a_live_source_is_only_capped(self):
        sheet = ReferenceSheet("notion", "Notion", tuple(_rows(3, "notion")))
        assert len(narrow(sheet, "no such words", 2).items) == 2

    def test_the_warning_survives_narrowing(self):
        sheet = ReferenceSheet("jira", "Jira", warning="Jira could not be read")
        assert narrow(sheet, "x").warning == "Jira could not be read"


class TestSearch:
    def test_a_tracker_reads_the_whole_list_once_and_filters_here(self):
        seen = []

        def reader(q, now):
            seen.append(q)
            return _rows(3)

        sheet = narrow(read("jira", "", now=NOW, readers={"jira": reader}), "row 2")
        assert seen == [""] and [r.subject for r in sheet.items] == ["K-2"]

    def test_a_live_source_takes_the_query_with_it(self):
        seen = []

        def reader(q, now):
            seen.append(q)
            return _rows(1, "confluence")

        sheet = narrow(read("confluence", "adr", now=NOW, readers={"confluence": reader}), "adr")
        assert seen == ["adr"] and len(sheet.items) == 1


class TestReaders:
    def test_jira_lists_the_project_first_then_each_ticket(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "OPS")
        monkeypatch.setattr("yeaboi.config.get_jira_base_url", lambda: "https://x.atlassian.net/")
        monkeypatch.setattr(
            "yeaboi.tools.jira.jira_open_tickets",
            lambda key, limit: [
                {"key": "OPS-12", "title": "Fix  login", "status": "In Progress", "url": "https://x/browse/OPS-12"},
                {"key": "", "title": "no key"},
            ],
        )
        rows = references._jira("", NOW)
        assert [r.id for r in rows] == ["jira:OPS", "jira:OPS-12"]
        assert rows[0].label == "OPS (project)" and rows[0].url == "https://x.atlassian.net/projects/OPS"
        assert rows[1] == Reference(
            "jira:OPS-12", "OPS-12", "OPS-12 Fix login", "In Progress", "https://x/browse/OPS-12"
        )

    def test_jira_without_a_base_url_has_no_project_link(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "OPS")
        monkeypatch.setattr("yeaboi.config.get_jira_base_url", lambda: None)
        monkeypatch.setattr("yeaboi.tools.jira.jira_open_tickets", lambda key, limit: [])
        assert references._jira("", NOW)[0].url == ""

    def test_github_skips_archived_sorts_newest_first_and_raises_when_all_failed(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: ["yeaboi-ai"])
        inventory = [
            {"name": "yeaboi-ai/old", "archived": True, "updated_at": "2026-09-01", "url": "u1"},
            {"name": "yeaboi-ai/a", "updated_at": "2026-08-01", "url": "u2", "description": " A repo "},
            {"name": "yeaboi-ai/b", "updated_at": "2026-09-05", "url": "u3"},
        ]
        monkeypatch.setattr(
            "yeaboi.tools.github.github_analysis_inventory", lambda owners, days, include_trees: inventory
        )
        rows = references._github("", NOW)
        assert [r.subject for r in rows] == ["yeaboi-ai/b", "yeaboi-ai/a"]
        assert rows[1].detail == "A repo" and rows[1].url == "u2"
        monkeypatch.setattr(
            "yeaboi.tools.github.github_analysis_inventory",
            lambda owners, days, include_trees: [{"name": "yeaboi-ai", "discovery_error": True, "error": "bad token"}],
        )
        with pytest.raises(RuntimeError, match="bad token"):
            references._github("", NOW)

    def test_azdevops_subject_carries_the_project(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Shop")
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_open_work_items",
            lambda project, limit: [{"key": "#4711", "title": "Checkout", "status": "Active", "url": "https://z/4711"}],
        )
        rows = references._azdevops("", NOW)
        assert rows == [Reference("azdevops:#4711", "Shop #4711", "#4711 Checkout", "Active", "https://z/4711")]

    def test_linear_rows(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_linear_team_key", lambda: "ENG")
        monkeypatch.setattr(
            "yeaboi.tools.linear.linear_open_issues",
            lambda team, limit: [{"key": "ENG-7", "title": "Ship it", "state": "Todo", "url": "https://l/ENG-7"}],
        )
        assert references._linear("", NOW) == [
            Reference("linear:ENG-7", "ENG-7", "ENG-7 Ship it", "Todo", "https://l/ENG-7")
        ]

    def test_confluence_recent_without_a_query_and_search_with_one(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_team_analysis_confluence_spaces", lambda: ["DOCS"])
        monkeypatch.setattr("yeaboi.config.get_confluence_space_key", lambda: "")
        recent = [
            {"kind": "page", "key": "1", "title": "ADR 1", "url": "https://c/1"},
            {"kind": "page", "key": "1", "title": "ADR 1", "url": "https://c/1"},
            {"kind": "page-created", "key": "2", "title": "New", "url": "https://c/2"},
        ]
        calls = {}
        monkeypatch.setattr(
            "yeaboi.tools.confluence.confluence_recent_pages",
            lambda space, days, include_version_history: calls.setdefault("recent", (space, days)) and recent,
        )
        monkeypatch.setattr(
            "yeaboi.tools.confluence.confluence_search_pages",
            lambda q, space_key, limit: (
                calls.setdefault("search", (q, space_key)) and [{"key": "9", "title": "Found", "url": ""}]
            ),
        )
        rows = references._confluence("", NOW)
        assert rows == [Reference("confluence:1", "1", "ADR 1", "DOCS", "https://c/1")] and calls["recent"] == (
            "DOCS",
            90,
        )
        rows = references._confluence("adr", NOW)
        assert rows == [Reference("confluence:9", "9", "Found", "DOCS", "")] and calls["search"] == ("adr", "DOCS")

    def test_notion_recent_without_a_query_and_search_with_one(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.notion.notion_recent_pages",
            lambda days: [{"key": "p1", "title": "Roadmap", "url": "https://n/p1"}],
        )
        monkeypatch.setattr(
            "yeaboi.tools.notion.notion_search_page_rows", lambda q, limit: [{"key": "p2", "title": q, "url": ""}]
        )
        assert references._notion("", NOW) == [Reference("notion:p1", "p1", "Roadmap", "", "https://n/p1")]
        assert references._notion("Spec", NOW) == [Reference("notion:p2", "p2", "Spec", "", "")]


class TestDesk:
    def _desk(self, reader, **kw):
        clock = {"t": 1000.0}
        desk = ReferenceDesk(clock=lambda: clock["t"], reader=reader, **kw)
        return desk, clock

    def test_a_tracker_is_read_once_per_minute_whatever_is_typed(self):
        reads = []

        def reader(source, q, *, now):
            reads.append((source, q))
            return ReferenceSheet(source, SOURCE_LABELS[source], tuple(_rows(3)))

        desk, clock = self._desk(reader)
        assert [r.subject for r in desk.get("jira", "row 1").items] == ["K-1"]
        assert [r.subject for r in desk.get("jira", "row 2").items] == ["K-2"]
        assert reads == [("jira", "")]
        clock["t"] += 61
        desk.get("jira", "row 0")
        assert reads == [("jira", ""), ("jira", "")]

    def test_a_live_source_is_read_per_query(self):
        reads = []

        def reader(source, q, *, now):
            reads.append(q)
            return ReferenceSheet(source, SOURCE_LABELS[source], tuple(_rows(1, source)))

        desk, _ = self._desk(reader)
        desk.get("notion", "a")
        desk.get("notion", "a")
        desk.get("notion", "b")
        assert reads == ["a", "b"]
        assert "notion" in LIVE_SOURCES

    def test_the_read_sees_the_desk_clock_and_a_warning_is_cached_too(self):
        reads = []

        def reader(source, q, *, now):
            reads.append(now)
            return ReferenceSheet(source, "Jira", warning="Jira could not be read")

        desk, _ = self._desk(reader)
        assert desk.get("jira", "x").warning == "Jira could not be read"
        desk.get("jira", "y")
        assert len(reads) == 1 and reads[0] == datetime.fromtimestamp(1000.0, tz=timezone.utc)

    def test_the_cache_is_bounded(self):
        def reader(source, q, *, now):
            return ReferenceSheet(source, "Notion")

        desk, _ = self._desk(reader)
        for i in range(references._CACHE_ROWS + 10):
            desk.get("notion", f"q{i}")
        assert len(desk._cache) == references._CACHE_ROWS
