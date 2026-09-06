"""Project references — the concrete things a description can point at.

Behind ``@`` in the desktop's project composer: one row per Jira issue (or the
project itself), GitHub repo, Linear issue, Azure DevOps work item, Confluence
or Notion page, searched as the reader types. Each source is read under its own
guard, so one that fails answers with a warning, never a broken picker. The
trackers are listed once and filtered here; the doc platforms take the query to
their own search. The desk in front caches each read for a minute so a keystroke
never costs a fresh 200-row fetch.

Deliberately not in ``engine.py``: the engine glob would force every public
name here into the parity registry as a capability of its own.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from yeaboi.projects.suggest import SOURCE_LABELS as _ALL_LABELS

logger = logging.getLogger(__name__)

SOURCES = ("jira", "github", "azdevops", "linear", "confluence", "notion")
SOURCE_LABELS: dict[str, str] = {key: _ALL_LABELS[key] for key in SOURCES}
#: Rows a picker shows by default, and the most it may ask for.
DEFAULT_LIMIT = 8
MAX_LIMIT = 25
#: How long one read stands in the desk.
CACHE_TTL_SECONDS = 60
#: Window for the doc platforms' recent pages when there is no query.
RECENT_DAYS = 90
#: Rows the tracker lists fetch; filtered here.
LIST_LIMIT = 200
#: Sources whose readers take the query to the API; everything else is listed then filtered.
LIVE_SOURCES = frozenset({"confluence", "notion"})
#: Reads the desk keeps before dropping the oldest.
_CACHE_ROWS = 64


@dataclass(frozen=True)
class Reference:
    """One thing a project can point at."""

    id: str  # "<source>:<key>", stable across reads
    subject: str  # what the desktop stores: "PROJ-123", "owner/repo", a page id
    label: str  # "PROJ-123 Fix login", "owner/repo", the page title
    detail: str = ""  # status, description or space
    url: str = ""


@dataclass(frozen=True)
class ReferenceSheet:
    """What the route answers with."""

    source: str
    source_label: str
    items: tuple[Reference, ...] = ()
    warning: str = ""  # one sentence when the source could not be read


def _ref(source: str, key: str, label: str, *, subject: str = "", detail: str = "", url: str = "") -> Reference:
    return Reference(
        id=f"{source}:{key}",
        subject=" ".join((subject or key).split()),
        label=" ".join(label.split()),
        detail=" ".join(str(detail).split()),
        url=str(url or ""),
    )


# ---------------------------------------------------------------------------
# Readers: (query, now) -> rows
# ---------------------------------------------------------------------------


def _jira(q: str, now: datetime) -> list[Reference]:
    from yeaboi.config import get_jira_base_url, get_jira_project_key
    from yeaboi.tools.jira import jira_open_tickets

    key = get_jira_project_key() or ""
    base = (get_jira_base_url() or "").rstrip("/")
    out: list[Reference] = []
    if key:
        out.append(
            _ref("jira", key, f"{key} (project)", detail="Jira project", url=f"{base}/projects/{key}" if base else "")
        )
    for ticket in jira_open_tickets(key, limit=LIST_LIMIT):
        issue = str(ticket.get("key", "") or "")
        if issue:
            out.append(
                _ref(
                    "jira",
                    issue,
                    f"{issue} {ticket.get('title', '')}",
                    detail=str(ticket.get("status", "") or ""),
                    url=str(ticket.get("url", "") or ""),
                )
            )
    return out


def _github(q: str, now: datetime) -> list[Reference]:
    from yeaboi.config import get_team_analysis_github_owners
    from yeaboi.tools.github import github_analysis_inventory, github_list_owners

    owners = tuple(get_team_analysis_github_owners() or ()) or tuple(github_list_owners())
    inventory = github_analysis_inventory(owners, days=365, include_trees=False)
    repos = [r for r in inventory if not r.get("archived") and not r.get("discovery_error")]
    failed = [r for r in inventory if r.get("discovery_error")]
    if failed and not repos:
        raise RuntimeError(str(failed[0].get("error") or "repository discovery failed"))
    repos.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
    return [
        _ref(
            "github",
            str(r.get("name", "")),
            str(r.get("name", "")),
            detail=str(r.get("description", "") or ""),
            url=str(r.get("url", "") or ""),
        )
        for r in repos
        if r.get("name")
    ]


def _azdevops(q: str, now: datetime) -> list[Reference]:
    from yeaboi.config import get_azure_devops_project
    from yeaboi.tools.azure_devops import azdevops_open_work_items

    project = get_azure_devops_project() or ""
    out: list[Reference] = []
    for item in azdevops_open_work_items(project, limit=LIST_LIMIT):
        key = str(item.get("key", "") or "")
        if key:
            out.append(
                _ref(
                    "azdevops",
                    key,
                    f"{key} {item.get('title', '')}",
                    subject=f"{project} {key}".strip(),
                    detail=str(item.get("status", "") or ""),
                    url=str(item.get("url", "") or ""),
                )
            )
    return out


def _linear(q: str, now: datetime) -> list[Reference]:
    from yeaboi.config import get_linear_team_key
    from yeaboi.tools.linear import linear_open_issues

    out: list[Reference] = []
    for issue in linear_open_issues(get_linear_team_key() or "", limit=LIST_LIMIT):
        key = str(issue.get("key", "") or "")
        if key:
            out.append(
                _ref(
                    "linear",
                    key,
                    f"{key} {issue.get('title', '')}",
                    detail=str(issue.get("state", "") or ""),
                    url=str(issue.get("url", "") or ""),
                )
            )
    return out


def _pages(source: str, rows: list, *, detail: str = "") -> list[Reference]:
    out: list[Reference] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key", "") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            _ref(source, key, str(row.get("title", "") or "Untitled"), detail=detail, url=str(row.get("url", "") or ""))
        )
    return out


def _confluence(q: str, now: datetime) -> list[Reference]:
    from yeaboi.config import get_confluence_space_key, get_team_analysis_confluence_spaces
    from yeaboi.tools.confluence import confluence_recent_pages, confluence_search_pages

    spaces = tuple(get_team_analysis_confluence_spaces() or ()) or (get_confluence_space_key() or "",)
    out: list[Reference] = []
    for space in spaces:
        if q:
            rows = confluence_search_pages(q, space_key=space, limit=MAX_LIMIT)
        else:
            rows = [
                r
                for r in confluence_recent_pages(space, days=RECENT_DAYS, include_version_history=False)
                if isinstance(r, dict) and r.get("kind") == "page"
            ]
        out.extend(_pages("confluence", rows, detail=space))
    return out


def _notion(q: str, now: datetime) -> list[Reference]:
    from yeaboi.tools.notion import notion_recent_pages, notion_search_page_rows

    rows = notion_search_page_rows(q, limit=MAX_LIMIT) if q else notion_recent_pages(days=RECENT_DAYS)
    return _pages("notion", list(rows))


Reader = Callable[[str, datetime], list[Reference]]

_READERS: dict[str, Reader] = {
    "jira": _jira,
    "github": _github,
    "azdevops": _azdevops,
    "linear": _linear,
    "confluence": _confluence,
    "notion": _notion,
}


# ---------------------------------------------------------------------------
# Reading, narrowing, the desk
# ---------------------------------------------------------------------------


def read(
    source: str, q: str = "", *, now: datetime | None = None, readers: dict[str, Reader] | None = None
) -> ReferenceSheet:
    """Every row the source gives for ``q`` (the whole list for a tracker); a warning when it cannot be read."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    label = SOURCE_LABELS[source]
    reader = (readers or _READERS)[source]
    q = " ".join(q.split())
    try:
        rows = reader(q, now or datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001 - one source down must not take the picker down
        logger.warning("project references: %s could not be read: %s", source, exc)
        return ReferenceSheet(source, label, warning=f"{label} could not be read")
    logger.info("project references: %s gave %d row(s) for %r", source, len(rows), q)
    return ReferenceSheet(source, label, tuple(rows))


def matches(row: Reference, q: str) -> bool:
    """Every token of ``q`` appears somewhere in the row, case-folded."""
    hay = " ".join((row.subject, row.label, row.detail)).casefold()
    return all(token in hay for token in q.casefold().split())


def narrow(sheet: ReferenceSheet, q: str, limit: int = DEFAULT_LIMIT) -> ReferenceSheet:
    """The rows that match ``q``, at most ``limit``. A live source already searched, so only the cap applies."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    rows = sheet.items if sheet.source in LIVE_SOURCES else tuple(r for r in sheet.items if matches(r, q))
    return ReferenceSheet(sheet.source, sheet.source_label, rows[:limit], sheet.warning)


def search(
    source: str, q: str = "", *, limit: int = DEFAULT_LIMIT, now: datetime | None = None, readers=None
) -> ReferenceSheet:
    """Read then narrow: the whole computation for one query."""
    q = " ".join(q.split())
    return narrow(read(source, q if source in LIVE_SOURCES else "", now=now, readers=readers), q, limit)


class ReferenceDesk:
    """One per app process: a minute of memory per read, so typing costs one fetch, not one per key."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        reader: Callable[..., ReferenceSheet] = read,
        ttl: float = CACHE_TTL_SECONDS,
    ) -> None:
        self._clock = clock
        self._read = reader
        self._ttl = ttl
        self._cache: dict[tuple[str, str], tuple[ReferenceSheet, float]] = {}
        self._lock = threading.Lock()

    def get(self, source: str, q: str = "", *, limit: int = DEFAULT_LIMIT) -> ReferenceSheet:
        q = " ".join(q.split())
        key = (source, q if source in LIVE_SOURCES else "")
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None and self._clock() - cached[1] < self._ttl:
            return narrow(cached[0], q, limit)
        sheet = self._read(source, key[1], now=datetime.fromtimestamp(self._clock(), tz=timezone.utc))
        with self._lock:
            self._cache[key] = (sheet, self._clock())
            while len(self._cache) > _CACHE_ROWS:
                del self._cache[next(iter(self._cache))]
        return narrow(sheet, q, limit)
