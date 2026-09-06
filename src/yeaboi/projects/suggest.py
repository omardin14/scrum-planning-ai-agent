"""Recommended projects — what this machine's connections say is worth starting.

The Projects door's empty ledger offers up to three descriptions computed from
the sources this machine is wired to: the trackers (Jira, Azure DevOps,
Linear), the GitHub repos of the configured owners, the local repos the coding
agents have been working in (from the last agentwatch ingest, never a fresh
scan), and the Confluence and Notion pages edited lately. Each source is read
under its own guard, so one that fails becomes a warning, never an empty sheet.

The wording is one model call over every chosen card; without a provider, or
when the call fails, a card is worded from its facts alone. The desk in front
of it is stale-while-revalidate, copied from the news desk: a request always
answers at once, and a stale cache starts one background refresh.

Deliberately not in ``engine.py``: the engine glob would force every public
name here into the parity registry as a capability of its own.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yeaboi.paths import get_project_suggestions_cache_path

logger = logging.getLogger(__name__)

#: How long a computed sheet stands before the next request refreshes it.
CACHE_TTL_SECONDS = 6 * 60 * 60
#: Recent-activity window for the agent repos and the doc platforms.
WINDOW_DAYS = 14
#: Cards on the sheet, and how many one source may fill.
MAX_SUGGESTIONS = 3
MAX_PER_SOURCE = 2
#: Item titles a card carries to the model; the wire never sees them.
MAX_TITLES = 12
#: Repos the GitHub read looks at, most recently pushed first.
MAX_REPOS = 5

SOURCE_LABELS: dict[str, str] = {
    "github": "GitHub",
    "jira": "Jira",
    "azdevops": "Azure DevOps",
    "linear": "Linear",
    "confluence": "Confluence",
    "notion": "Notion",
    "agents": "Your agents",
}

#: Trackers and code outrank docs: a page edited is a weaker sign than a ticket open.
_DOC_WEIGHT = 0.25
#: Branches that say nothing about the work.
_TRUNK_BRANCHES = frozenset({"main", "master", "develop", "HEAD"})


@dataclass(frozen=True)
class Signal:
    """One gathered fact set from one source; the model's card."""

    source: str
    label: str
    subject: str
    facts: str
    url: str = ""
    repo_path: str = ""
    titles: tuple[str, ...] = ()
    weight: float = 0.0


@dataclass(frozen=True)
class Suggestion:
    """One row on the ledger: the description and the facts it came from."""

    id: str
    text: str
    source: str
    source_label: str
    subject: str
    facts: str
    url: str = ""
    repo_path: str = ""
    wording: str = "facts"  # "ai" | "facts"


@dataclass(frozen=True)
class SuggestionSheet:
    """What the route answers with. Every field defaulted: an empty sheet is
    the honest answer for a fresh install."""

    suggestions: tuple[Suggestion, ...] = ()
    sources: tuple[str, ...] = ()  # labels read; on the first stale sheet, the ones being read
    warnings: tuple[str, ...] = ()  # one sentence per source that could not be
    computed_at: str = ""
    stale: bool = False
    connected: bool = False  # False only when nothing at all can be read


def _suggestion_id(source: str, subject: str) -> str:
    return hashlib.sha256(f"{source}:{subject}".encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# What is configured
# ---------------------------------------------------------------------------


def _agents_have_sessions(since: str) -> bool:
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    path = get_db_path()
    if not Path(path).exists():
        return False
    with AgentWatchStore(path) as store:
        return bool(store.list_sessions(since=since))


def available_sources(*, now: datetime | None = None) -> dict[str, str]:
    """Source → label for everything this machine can read. Env reads and one
    SQLite query; no network."""
    from yeaboi.analysis.setup import available_doc_sources, available_trackers
    from yeaboi.config import get_github_token, get_linear_api_key

    keys: list[str] = []
    try:
        keys.extend(available_trackers())
        if get_linear_api_key():
            keys.append("linear")
        if get_github_token():
            keys.append("github")
        keys.extend(available_doc_sources())
    except Exception:  # noqa: BLE001 - a broken config still leaves the agent repos
        logger.warning("project suggestions: reading the configured sources failed", exc_info=True)
    try:
        since = ((now or datetime.now(timezone.utc)) - timedelta(days=WINDOW_DAYS)).isoformat()
        if _agents_have_sessions(since):
            keys.append("agents")
    except Exception:  # noqa: BLE001
        logger.warning("project suggestions: agentwatch store unreadable", exc_info=True)
    return {key: SOURCE_LABELS[key] for key in keys if key in SOURCE_LABELS}


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _day(value: str) -> str:
    """``12 Sep`` from an ISO date or datetime; the value as given when it is neither."""
    from yeaboi.timeparse import parse_datetime

    try:
        return parse_datetime(str(value)).strftime("%-d %b")
    except (ValueError, TypeError):
        return str(value)


def _titles(items, key: str = "title") -> tuple[str, ...]:
    out: list[str] = []
    for item in items:
        title = " ".join(str(item.get(key, "") if isinstance(item, dict) else "").split())
        if title and title not in out:
            out.append(title)
        if len(out) >= MAX_TITLES:
            break
    return tuple(out)


def _github(now: datetime) -> list[Signal]:
    from yeaboi.config import get_team_analysis_github_owners
    from yeaboi.tools.github import github_analysis_inventory, github_list_owners, github_repo_overview

    owners = tuple(get_team_analysis_github_owners() or ()) or tuple(github_list_owners())
    inventory = github_analysis_inventory(owners, days=90, include_trees=False)
    repos = [r for r in inventory if r.get("active") and not r.get("discovery_error")]
    failed = [r for r in inventory if r.get("discovery_error")]
    if failed and not repos:
        raise RuntimeError(str(failed[0].get("error") or "repository discovery failed"))
    repos.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
    out: list[Signal] = []
    for repo in repos[:MAX_REPOS]:
        slug = str(repo.get("name", ""))
        overview = github_repo_overview(slug)
        open_issues = int(overview.get("open_issues", 0))
        if open_issues == 0:
            continue
        facts = _plural(open_issues, "open issue")
        weight = float(open_issues)
        if overview.get("milestone"):
            facts += f", milestone {overview['milestone']}"
            if overview.get("milestone_due"):
                facts += f" due {_day(str(overview['milestone_due']))}"
                weight *= 2
        out.append(
            Signal(
                "github",
                SOURCE_LABELS["github"],
                slug,
                facts,
                url=str(repo.get("url", "")),
                titles=tuple(str(t) for t in overview.get("issue_titles", ())[:MAX_TITLES]),
                weight=weight,
            )
        )
    return out


def _jira(now: datetime) -> list[Signal]:
    from yeaboi.config import get_jira_project_key
    from yeaboi.tools.jira import jira_active_sprint_progress, jira_open_tickets

    key = get_jira_project_key() or ""
    tickets = jira_open_tickets(key, limit=200)
    if not tickets:
        return []
    facts = _plural(len(tickets), "open ticket")
    sprint = jira_active_sprint_progress(key).get("sprint_name", "")
    if sprint:
        facts += f", sprint {sprint} in progress"
    return [Signal("jira", SOURCE_LABELS["jira"], key or "Jira", facts, titles=_titles(tickets), weight=len(tickets))]


def _azdevops(now: datetime) -> list[Signal]:
    from yeaboi.config import get_azure_devops_project
    from yeaboi.tools.azure_devops import azdevops_open_work_items

    project = get_azure_devops_project() or ""
    items = azdevops_open_work_items(project, limit=200)
    if not items:
        return []
    facts = _plural(len(items), "open work item")
    return [
        Signal(
            "azdevops",
            SOURCE_LABELS["azdevops"],
            project or "Azure DevOps",
            facts,
            titles=_titles(items),
            weight=len(items),
        )
    ]


def _linear(now: datetime) -> list[Signal]:
    from yeaboi.config import get_linear_team_key
    from yeaboi.tools.linear import fetch_team_cycles, linear_open_issues

    team = get_linear_team_key() or ""
    issues = linear_open_issues(team, limit=200)
    if not issues:
        return []
    facts = _plural(len(issues), "open issue")
    cycles = fetch_team_cycles(("active",))
    if cycles:
        cycle = cycles[0]
        facts += f", cycle {cycle.get('name') or cycle.get('number')}"
        if cycle.get("end_date"):
            facts += f" ends {_day(str(cycle['end_date']))}"
    return [
        Signal("linear", SOURCE_LABELS["linear"], team or "Linear", facts, titles=_titles(issues), weight=len(issues))
    ]


def _pages(source: str, subject: str, items: list[dict]) -> list[Signal]:
    if not items:
        return []
    by_title: dict[str, int] = {}
    for item in items:
        title = " ".join(str(item.get("title", "")).split())
        if title:
            by_title[title] = by_title.get(title, 0) + 1
    titles = tuple(sorted(by_title, key=lambda t: (-by_title[t], t))[:MAX_TITLES])
    facts = f"{_plural(len(by_title), 'page')} edited in {WINDOW_DAYS} days"
    return [Signal(source, SOURCE_LABELS[source], subject, facts, titles=titles, weight=len(by_title) * _DOC_WEIGHT)]


def _confluence(now: datetime) -> list[Signal]:
    from yeaboi.config import get_confluence_space_key, get_team_analysis_confluence_spaces
    from yeaboi.tools.confluence import confluence_recent_pages

    spaces = tuple(get_team_analysis_confluence_spaces() or ()) or (get_confluence_space_key() or "",)
    out: list[Signal] = []
    for space in spaces:
        items = confluence_recent_pages(space, days=WINDOW_DAYS, include_version_history=False)
        out.extend(_pages("confluence", space or "Confluence", [i for i in items if isinstance(i, dict)]))
    return out


def _notion(now: datetime) -> list[Signal]:
    from yeaboi.tools.notion import notion_recent_pages

    items = notion_recent_pages(days=WINDOW_DAYS)
    return _pages("notion", "Notion", [i for i in items if isinstance(i, dict)])


def _agents(now: datetime) -> list[Signal]:
    from yeaboi.agentwatch.engine import _project_label
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path
    from yeaboi.tools.local_git import local_git_recent_commits

    since = (now - timedelta(days=WINDOW_DAYS)).isoformat()
    with AgentWatchStore(get_db_path()) as store:
        sessions = store.list_sessions(since=since)
    counts: dict[str, int] = {}
    branches: dict[str, dict[str, int]] = {}
    for session in sessions:
        path = str(session.get("project_path") or "")
        if not path or not os.path.isdir(path):
            continue
        counts[path] = counts.get(path, 0) + 1
        branch = str(session.get("git_branch") or "").strip()
        if branch and branch not in _TRUNK_BRANCHES:
            seen = branches.setdefault(path, {})
            seen[branch] = seen.get(branch, 0) + 1
    ranked = sorted(counts, key=lambda p: (-counts[p], p))[:MAX_SUGGESTIONS]
    out: list[Signal] = []
    for path in ranked:
        # What the work is about: the commit subjects where the sandbox lets us
        # read the repo, else the branches the sessions were on.
        commits = local_git_recent_commits(path, days=WINDOW_DAYS)
        facts = f"{_plural(counts[path], 'agent session')} in {WINDOW_DAYS} days"
        if commits:
            facts = (
                f"{_plural(counts[path], 'agent session')} and {_plural(len(commits), 'commit')} in {WINDOW_DAYS} days"
            )
        by_branch = branches.get(path, {})
        titles = _titles(commits) or tuple(sorted(by_branch, key=lambda b: (-by_branch[b], b))[:MAX_TITLES])
        out.append(
            Signal(
                "agents",
                SOURCE_LABELS["agents"],
                _project_label(path),
                facts,
                repo_path=path,
                titles=titles,
                weight=float(counts[path]),
            )
        )
    return out


_READERS: dict[str, Callable[[datetime], list[Signal]]] = {
    "github": _github,
    "jira": _jira,
    "azdevops": _azdevops,
    "linear": _linear,
    "confluence": _confluence,
    "notion": _notion,
    "agents": _agents,
}


def gather(sources: dict[str, str], *, now: datetime) -> tuple[tuple[Signal, ...], tuple[str, ...], tuple[str, ...]]:
    """Every configured source's signals, one warning per source that failed,
    and the labels of the sources that were read."""
    signals: list[Signal] = []
    warnings: list[str] = []
    read: list[str] = []
    for key, label in sources.items():
        reader = _READERS.get(key)
        if reader is None:
            continue
        try:
            found = reader(now)
        except Exception as exc:  # noqa: BLE001 - one source down must not take the sheet down
            logger.warning("project suggestions: %s could not be read: %s", key, exc)
            warnings.append(f"{label} could not be read")
            continue
        logger.info("project suggestions: %s gave %d signal(s)", key, len(found))
        signals.extend(found)
        read.append(label)
    return tuple(signals), tuple(warnings), tuple(read)


def choose(signals: tuple[Signal, ...]) -> tuple[Signal, ...]:
    """The top cards by weight, at most two from one source; ties by subject."""
    chosen: list[Signal] = []
    per_source: dict[str, int] = {}
    for signal in sorted(signals, key=lambda s: (-s.weight, s.source, s.subject)):
        if per_source.get(signal.source, 0) >= MAX_PER_SOURCE:
            continue
        chosen.append(signal)
        per_source[signal.source] = per_source.get(signal.source, 0) + 1
        if len(chosen) >= MAX_SUGGESTIONS:
            break
    return tuple(chosen)


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def _facts_wording(signal: Signal) -> str:
    return f"{signal.subject}: {signal.facts}."


def _suggestion(signal: Signal, text: str, wording: str) -> Suggestion:
    return Suggestion(
        id=_suggestion_id(signal.source, signal.subject),
        text=text,
        source=signal.source,
        source_label=signal.label,
        subject=signal.subject,
        facts=signal.facts,
        url=signal.url,
        repo_path=signal.repo_path,
        wording=wording,
    )


def _parse_wordings(raw: str) -> dict[str, str]:
    """``{id: text}`` from the model's JSON, tolerating markdown fences; empty when unusable."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    try:
        parsed = json.loads(raw.strip())
    except (json.JSONDecodeError, TypeError):
        logger.warning("project suggestions: could not parse the model's JSON")
        return {}
    items = parsed.get("suggestions") if isinstance(parsed, dict) else None
    out: dict[str, str] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text", "")).split()).strip()
        if text:
            out[str(item.get("id", ""))] = text
    return out


def word(chosen: tuple[Signal, ...]) -> tuple[Suggestion, ...]:
    """A description per card: the model's when it answers, the facts otherwise."""
    if not chosen:
        return ()
    fallback = tuple(_suggestion(s, _facts_wording(s), "facts") for s in chosen)
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("project suggestions: LLM not configured (%s) — wording from facts", why)
        return fallback
    from yeaboi.agent.llm import get_llm, invoke_with_images, track_usage
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error
    from yeaboi.prompts.project_suggestions import get_project_suggestions_prompt

    cards = [
        {"id": s.id, "source": s.source_label, "subject": s.subject, "facts": s.facts, "titles": list(sig.titles)}
        for s, sig in zip(fallback, chosen, strict=True)
    ]
    try:
        # See docs: "Agentic Blueprint Reference" — invoking the LLM directly
        response = invoke_with_images(get_llm(temperature=0.3), get_project_suggestions_prompt(cards), None)
        track_usage(response)
        wordings = _parse_wordings(response.content)
    except Exception as exc:  # noqa: BLE001 - the facts wording is always available
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("project suggestions: LLM auth/billing error — wording from facts: %s", exc)
        else:
            logger.warning("project suggestions: model call failed — wording from facts: %s", exc)
        return fallback
    worded = sum(1 for s in fallback if s.id in wordings)
    logger.info("project suggestions: model worded %d of %d card(s)", worded, len(fallback))
    return tuple(replace(s, text=wordings[s.id], wording="ai") if s.id in wordings else s for s in fallback)


def build_sheet(*, now: datetime | None = None) -> SuggestionSheet:
    """Gather, choose and word: the whole computation, from what is configured."""
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    sources = available_sources(now=now)
    signals, warnings, read = gather(sources, now=now)
    suggestions = word(choose(signals))
    logger.info(
        "project suggestions: %d suggestion(s) from %d source(s), %d warning(s), %.1fs",
        len(suggestions),
        len(sources),
        len(warnings),
        time.monotonic() - started,
    )
    return SuggestionSheet(
        suggestions=suggestions,
        sources=read,
        warnings=warnings,
        computed_at=now.isoformat(),
        connected=bool(sources),
    )


# ---------------------------------------------------------------------------
# The desk: stale-while-revalidate over one JSON file
# ---------------------------------------------------------------------------


def _sheet_from_dict(raw: object) -> SuggestionSheet | None:
    if not isinstance(raw, dict):
        return None
    allowed = set(Suggestion.__dataclass_fields__)
    suggestions = []
    for row in raw.get("suggestions") or []:
        if isinstance(row, dict):
            try:
                suggestions.append(Suggestion(**{k: v for k, v in row.items() if k in allowed}))
            except TypeError:
                continue
    return SuggestionSheet(
        suggestions=tuple(suggestions),
        sources=tuple(str(s) for s in raw.get("sources") or ()),
        warnings=tuple(str(w) for w in raw.get("warnings") or ()),
        computed_at=str(raw.get("computed_at", "")),
        connected=bool(raw.get("connected", False)),
    )


def read_cache(path: Path) -> tuple[SuggestionSheet, float] | None:
    """The cached sheet and when it was written; None when there is none or it is unreadable."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sheet = _sheet_from_dict(raw.get("sheet") if isinstance(raw, dict) else None)
    if sheet is None:
        return None
    return sheet, float(raw.get("written_at", 0.0) or 0.0)


def write_cache(path: Path, sheet: SuggestionSheet, written_at: float) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema": 1, "written_at": written_at, "sheet": asdict(sheet)}, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except OSError:
        logger.warning("project suggestions: could not write the cache", exc_info=True)
        try:
            os.unlink(tmp)
        except OSError:
            pass


class SuggestDesk:
    """One per app process. Every collaborator is injectable for the tests."""

    def __init__(
        self,
        *,
        cache_path: Callable[[], Path] = get_project_suggestions_cache_path,
        clock: Callable[[], float] = time.time,
        build: Callable[..., SuggestionSheet] = build_sheet,
        available: Callable[..., dict[str, str]] = available_sources,
        spawn: Callable[..., threading.Thread] = threading.Thread,
        ttl: float = CACHE_TTL_SECONDS,
    ) -> None:
        self._cache_path = cache_path
        self._clock = clock
        self._build = build
        self._available = available
        self._spawn = spawn
        self._ttl = ttl
        self._refreshing = threading.Lock()

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc)

    def get(self, *, refresh: bool = False) -> tuple[SuggestionSheet, bool]:
        """The sheet to answer with now, and whether a refresh is running for it."""
        cached = read_cache(self._cache_path())
        if cached is not None and not refresh and self._clock() - cached[1] < self._ttl:
            return cached[0], self._refreshing.locked()
        started = self._start_refresh()
        refreshing = started or self._refreshing.locked()
        if cached is not None:
            return replace(cached[0], stale=True), refreshing
        try:
            available = dict(self._available(now=self._now()))
        except Exception:  # noqa: BLE001
            logger.warning("project suggestions: could not tell what is connected", exc_info=True)
            available = {}
        # The sources the refresh is reading, so the desktop can say so while it waits.
        return SuggestionSheet(stale=True, connected=bool(available), sources=tuple(available.values())), refreshing

    def _start_refresh(self) -> bool:
        """Start one background refresh; False when one is already running."""
        if not self._refreshing.acquire(blocking=False):
            return False
        thread = self._spawn(target=self._refresh_holding_lock, name="project-suggestions", daemon=True)
        thread.start()
        return True

    def _refresh_holding_lock(self) -> None:
        try:
            self._refresh()
        finally:
            self._refreshing.release()

    def refresh_now(self) -> SuggestionSheet:
        """Refresh synchronously — the thread's body, and the test entry point."""
        with self._refreshing:
            return self._refresh()

    def _refresh(self) -> SuggestionSheet:
        from yeaboi.logging_setup import mode_log

        with mode_log("projects"):
            logger.info("project suggestions: refresh started")
            try:
                sheet = self._build(now=self._now())
            except Exception:  # noqa: BLE001 - the last sheet stays; the log says why there is no new one
                logger.warning("project suggestions: refresh failed", exc_info=True)
                cached = read_cache(self._cache_path())
                return cached[0] if cached is not None else SuggestionSheet()
            write_cache(self._cache_path(), sheet, self._clock())
            logger.info("project suggestions: refresh finished — %d suggestion(s)", len(sheet.suggestions))
            return sheet
