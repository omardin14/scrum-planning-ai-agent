"""Team learning tools — analyse historical sprint data and compare plans to actuals.

# See README: "Tools" — tool types, @tool decorator, risk levels
# See README: "Scrum Standards" — team learning, self-calibrating estimates
#
# These tools connect to Jira or Azure DevOps to pull historical sprint data,
# compute team calibration metrics, and compare generated plans against actual
# outcomes. The results feed into TeamProfile for future prompt injection.
#
# Risk level: low — read-only queries against Jira/AzDO APIs.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from langchain_core.tools import tool

from scrum_agent.team_profile import (
    DailyScopeSnapshot,
    DoDSignal,
    EpicPattern,
    ScopeChangeEvent,
    SpilloverStats,
    SprintScopeTimeline,
    StoryPointCalibration,
    StoryShapePattern,
    TeamProfile,
    WritingPatterns,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_float(val: object) -> float:
    """Convert a value to float, returning 0.0 on failure."""
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse an ISO-ish date string into a datetime, or None."""
    if not date_str:
        return None
    try:
        fmts = ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"]
        # Do not truncate: Jira uses e.g. ...000+0000 (28+ chars); [:26] breaks %z parsing
        # and yields None cycle times → misleading 0d averages in the TUI.
        s = date_str.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+0000"
        for fmt in fmts:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _cycle_time_days(start: str | None, end: str | None) -> float | None:
    """Compute time in days between two ISO-ish date strings.

    Returns None if either date is missing or unparseable.
    """
    s = _parse_date(start)
    e = _parse_date(end)
    if s and e:
        return max((e - s).total_seconds() / 86400, 0.0)
    return None


def _stddev(values: list[float]) -> float:
    """Compute population standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


# Patterns that indicate recurring/ceremony tickets — not real delivery work.
# These inflate spillover and skew calibration if counted as regular stories.
_RECURRING_PATTERNS = re.compile(
    r"\b("
    r"training|ceremony|retro(?:spective)?|standup|stand-up|stand up|"
    r"planning\s*meeting|sprint\s*review|sprint\s*demo|grooming|"
    r"refinement|backlog\s*review|1[:\s]*1|one.on.one|"
    r"admin|overhead|maintenance\s*window|on.?call|"
    r"interrupted\s*work|blocked\s*time|leave|holiday|"
    r"PTO|time.?off|sick|vacation|"
    r"KTLO|keep.the.lights?.on|BAU|business.as.usual|"
    r"toil|operational\s*support|support\s*rota|"
    r"tech.?debt\s*day|hack.?day|innovation\s*day"
    r")\b",
    re.IGNORECASE,
)


def _is_recurring(story: dict) -> bool:
    """Detect if a story is a recurring/ceremony ticket, not delivery work."""
    summary = story.get("summary", "") or ""
    if _RECURRING_PATTERNS.search(summary):
        return True
    # Detect exact duplicate titles across sprints (same title appears 3+ times)
    # This is checked at the batch level, not per-story — see _tag_recurring_batch
    return False


def _tag_recurring_batch(all_stories: list[dict]) -> None:
    """Tag stories as recurring based on pattern matching and duplicate detection.

    Mutates story dicts in-place by adding `"is_recurring": True/False`.
    A title appearing in 3+ different sprints is considered recurring even
    if it doesn't match the keyword patterns.
    """
    # Normalise titles for duplicate detection
    title_sprints: dict[str, set] = defaultdict(set)
    for s in all_stories:
        title = (s.get("summary", "") or "").strip().lower()
        sprint = s.get("sprint_name", "") or ""
        if title and sprint:
            title_sprints[title].add(sprint)

    # Titles appearing in 3+ distinct sprints → recurring
    recurring_titles = {title for title, sprints in title_sprints.items() if len(sprints) >= 3}

    for s in all_stories:
        title = (s.get("summary", "") or "").strip().lower()
        s["is_recurring"] = _is_recurring(s) or title in recurring_titles

    tagged = sum(1 for s in all_stories if s.get("is_recurring"))
    logger.debug(
        "Recurring detection: %d of %d stories tagged (%d by keyword, %d by duplicate title)",
        tagged,
        len(all_stories),
        sum(1 for s in all_stories if _is_recurring(s)),
        sum(
            1
            for s in all_stories
            if (s.get("summary", "") or "").strip().lower() in recurring_titles and not _is_recurring(s)
        ),
    )


def _azdo_assignee_name(fields: dict) -> str:
    """Extract display name from AzDO System.AssignedTo (can be str or dict)."""
    val = fields.get("System.AssignedTo")
    if not val:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("displayName", "") or val.get("uniqueName", "")
    return getattr(val, "display_name", "") or str(val)


def _azdo_work_item_link_target_id(link: object) -> int | None:
    """Resolve work item id from get_iteration_work_items / WorkItemLink target."""
    target = getattr(link, "target", None)
    if target is None:
        return None
    tid = getattr(target, "id", None)
    if tid is not None:
        try:
            return int(tid)
        except (TypeError, ValueError):
            pass
    if isinstance(target, dict):
        raw = target.get("id")
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return None


def _wit_get_work_items_batch(
    wit_client: object,
    project: str,
    batch_ids: list[int],
    fields: list[str],
    *,
    want_relations: bool = True,
) -> list:
    """Fetch work items for team analysis.

    Some Azure DevOps API configurations reject ``$expand=Relations`` together
    with an explicit ``fields`` list; that path used to fail silently and left
    every sprint with zero stories. We pass ``project`` (required for scoped
    PATs / some orgs) and fall back to a plain get when Relations expand fails.
    """
    if not batch_ids:
        return []
    if want_relations:
        try:
            return (
                wit_client.get_work_items(
                    batch_ids,
                    project=project,
                    fields=fields,
                    expand="Relations",
                )
                or []
            )
        except Exception as e:
            logger.warning(
                "AzDO get_work_items ($expand=Relations) failed for %d ids (e.g. %s): %s — retrying without expand",
                len(batch_ids),
                batch_ids[:3],
                e,
            )
    try:
        return (
            wit_client.get_work_items(
                batch_ids,
                project=project,
                fields=fields,
            )
            or []
        )
    except Exception as e:
        logger.error(
            "AzDO get_work_items failed for project=%r, %d ids: %s",
            project,
            len(batch_ids),
            e,
        )
        return []


def _build_profile_from_sprint_data(
    source: str,
    project_key: str,
    sprint_data: list[dict],
) -> TeamProfile:
    """Build a TeamProfile from collected sprint data.

    Args:
        source: "jira" or "azdevops"
        project_key: Project identifier
        sprint_data: List of sprint dicts, each with keys:
            - sprint_name, completed_points (float)
            - stories: list of dicts with keys: points, cycle_time_days,
              discipline, task_count, ac_count, epic_key, point_changed
    """
    team_id = f"{source}-{project_key}"

    # Velocity stats
    velocities = [sd["completed_points"] for sd in sprint_data if sd.get("completed_points", 0) > 0]
    velocity_avg = sum(velocities) / len(velocities) if velocities else 0.0
    velocity_stddev = _stddev(velocities)

    # Collect all stories
    all_stories: list[dict] = []
    for sd in sprint_data:
        all_stories.extend(sd.get("stories", []))

    # Point calibrations — group stories by point value
    by_points: dict[int, list[dict]] = defaultdict(list)
    for s in all_stories:
        pts = int(_safe_float(s.get("points", 0)))
        if pts in (1, 2, 3, 5, 8):
            by_points[pts].append(s)

    calibrations = []
    for pts in (1, 2, 3, 5, 8):
        stories_at_pts = by_points.get(pts, [])
        if not stories_at_pts:
            calibrations.append(StoryPointCalibration(point_value=pts))
            continue
        cycle_times = [s["cycle_time_days"] for s in stories_at_pts if s.get("cycle_time_days") is not None]
        avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else 0.0
        task_counts = [s.get("task_count", 0) for s in stories_at_pts]
        avg_tasks = sum(task_counts) / len(task_counts) if task_counts else 0.0
        changed = sum(1 for s in stories_at_pts if s.get("point_changed", False))
        overshoot = (changed / len(stories_at_pts) * 100) if stories_at_pts else 0.0
        calibrations.append(
            StoryPointCalibration(
                point_value=pts,
                avg_cycle_time_days=round(avg_ct, 1),
                sample_count=len(stories_at_pts),
                typical_task_count=round(avg_tasks, 1),
                overshoot_pct=round(overshoot, 1),
            )
        )

    # Story shape patterns — group by discipline
    by_discipline: dict[str, list[dict]] = defaultdict(list)
    for s in all_stories:
        disc = s.get("discipline", "fullstack")
        by_discipline[disc].append(s)

    shapes = []
    for disc, stories_in_disc in sorted(by_discipline.items()):
        pts_vals = [_safe_float(s.get("points", 0)) for s in stories_in_disc]
        ac_vals = [_safe_float(s.get("ac_count", 0)) for s in stories_in_disc]
        task_vals = [_safe_float(s.get("task_count", 0)) for s in stories_in_disc]
        shapes.append(
            StoryShapePattern(
                discipline=disc,
                avg_points=round(sum(pts_vals) / len(pts_vals), 1) if pts_vals else 0.0,
                avg_ac_count=round(sum(ac_vals) / len(ac_vals), 1) if ac_vals else 0.0,
                avg_task_count=round(sum(task_vals) / len(task_vals), 1) if task_vals else 0.0,
                sample_count=len(stories_in_disc),
            )
        )

    # Epic patterns — group by epic key
    by_epic: dict[str, list[dict]] = defaultdict(list)
    for s in all_stories:
        ek = s.get("epic_key", "")
        if ek:
            by_epic[ek].append(s)

    if by_epic:
        epic_story_counts = [len(stories) for stories in by_epic.values()]
        epic_point_totals = [sum(_safe_float(s.get("points", 0)) for s in stories) for stories in by_epic.values()]
        epic_pattern = EpicPattern(
            avg_stories_per_epic=round(sum(epic_story_counts) / len(epic_story_counts), 1),
            avg_points_per_epic=round(sum(epic_point_totals) / len(epic_point_totals), 1),
            typical_story_count_range=(min(epic_story_counts), max(epic_story_counts)),
            sample_count=len(by_epic),
        )
    else:
        epic_pattern = EpicPattern()

    # Estimation accuracy — stories where points didn't change
    stories_with_pts = [s for s in all_stories if _safe_float(s.get("points", 0)) > 0]
    unchanged = sum(1 for s in stories_with_pts if not s.get("point_changed", False))
    estimation_accuracy = (unchanged / len(stories_with_pts) * 100) if stories_with_pts else 0.0

    # Sprint completion rate — stories completed vs planned
    completion_rates = []
    for sd in sprint_data:
        planned = sd.get("planned_count", 0)
        completed = sd.get("completed_count", 0)
        if planned > 0:
            completion_rates.append(completed / planned * 100)
    sprint_completion = sum(completion_rates) / len(completion_rates) if completion_rates else 0.0

    return TeamProfile(
        team_id=team_id,
        source=source,
        project_key=project_key,
        sample_sprints=len(sprint_data),
        sample_stories=len(all_stories),
        velocity_avg=round(velocity_avg, 1),
        velocity_stddev=round(velocity_stddev, 1),
        point_calibrations=tuple(calibrations),
        story_shapes=tuple(shapes),
        epic_pattern=epic_pattern,
        estimation_accuracy_pct=round(estimation_accuracy, 1),
        sprint_completion_rate=round(sprint_completion, 1),
    )


# ---------------------------------------------------------------------------
# Parallel deep analysis — 4 workers for richer profiling
# ---------------------------------------------------------------------------

_GWT_RE = re.compile(r"\b(given|when|then)\b", re.IGNORECASE)
_PR_RE = re.compile(r"(pull\s*request|PR\s*#?\d+|/pull/\d+|merge\s*request)", re.IGNORECASE)

# Patterns to extract repository names from PR/MR links in ticket text
_REPO_PATTERNS = [
    # GitHub: github.com/org/repo/pull/N or github.com/org/repo/pulls
    re.compile(r"github\.com/[\w.\-]+/([\w.\-]+)/pull", re.IGNORECASE),
    # AzDO: dev.azure.com/org/project/_git/repo/pullrequest/N
    re.compile(r"_git/([\w.\-]+)/pullrequest", re.IGNORECASE),
    # AzDO short: org.visualstudio.com/project/_git/repo
    re.compile(r"_git/([\w.\-]+)(?:/|$)", re.IGNORECASE),
    # Bitbucket: bitbucket.org/workspace/repo/pull-requests/N
    re.compile(r"bitbucket\.org/[\w.\-]+/([\w.\-]+)/pull-requests", re.IGNORECASE),
    # GitLab: gitlab.com/group/repo/-/merge_requests/N
    re.compile(r"gitlab\.com/(?:[\w.\-]+/)+([\w.\-]+)/-/merge_requests", re.IGNORECASE),
]


def _extract_repos(text: str) -> list[str]:
    """Extract repository names from PR/MR links in a text string."""
    if not text:
        return []
    repos: list[str] = []
    for pat in _REPO_PATTERNS:
        for m in pat.finditer(text):
            repo = m.group(1).strip()
            if repo and len(repo) > 1 and repo.lower() not in repos:
                repos.append(repo.lower())
    return repos


def _story_add_repos(story: dict, new_repos: list[str], source: str) -> None:
    """Merge unique repo slugs onto story['repos'] and record provenance in story['repo_sources']."""
    if not new_repos:
        return
    rs = story.setdefault("repos", [])
    for r in new_repos:
        if r and r not in rs:
            rs.append(r)
    srcs = story.setdefault("repo_sources", [])
    if source not in srcs:
        srcs.append(source)


def _collect_dev_panel_url_strings(obj: object, out: list[str]) -> None:
    """Walk Jira dev-status JSON and collect URL-like strings for _extract_repos."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.strip():
                if k in ("url", "uri", "href", "displayId"):
                    out.append(v)
                elif "http" in v or "_git/" in v.lower():
                    out.append(v)
            elif isinstance(v, (dict, list)):
                _collect_dev_panel_url_strings(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_dev_panel_url_strings(item, out)


def _jira_development_url_blob(jira: object, issue: object) -> str:
    """Fetch Jira Software dev-status detail and return text suitable for _extract_repos."""
    try:
        from scrum_agent.config import is_team_analysis_jira_dev_links_enabled

        if not is_team_analysis_jira_dev_links_enabled():
            return ""
        server = (getattr(jira, "_options", {}) or {}).get("server") or ""
        server = str(server).rstrip("/")
        if not server:
            return ""
        issue_id = str(getattr(issue, "id", "") or "")
        if not issue_id:
            return ""
        paths = (
            "/rest/dev-status/1.0/issue/detail",
            "/rest/dev-status/latest/issue/detail",
        )
        session = getattr(jira, "_session", None)
        if session is None:
            return ""
        for path in paths:
            resp = session.get(f"{server}{path}", params={"issueId": issue_id}, timeout=30)
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            buf: list[str] = []
            _collect_dev_panel_url_strings(data, buf)
            if buf:
                return " ".join(buf)
    except Exception:
        logger.debug("Jira dev-status fetch failed", exc_info=True)
    return ""


def _extract_repos_from_azdo_relations(work_item: object) -> list[str]:
    """Parse Azure DevOps work item relations (linked PRs/commits) for repo slugs."""
    rels = getattr(work_item, "relations", None) or []
    chunks: list[str] = []
    for rel in rels:
        url = getattr(rel, "url", None) or ""
        if url:
            chunks.append(url)
        attrs = getattr(rel, "attributes", None)
        if isinstance(attrs, dict):
            for key in ("comment", "name", "resourceId"):
                val = attrs.get(key)
                if isinstance(val, str) and val.strip():
                    chunks.append(val)
    return _extract_repos(" ".join(chunks))


def _azdo_pr_matches_work_item(pr: object, work_item_id: str) -> bool:
    """True if PR is linked to the work item or mentions its id in branch/title/body."""
    if not work_item_id:
        return False
    refs = getattr(pr, "work_item_refs", None) or []
    for ref in refs:
        rid = getattr(ref, "id", None)
        if rid is not None and str(rid) == work_item_id:
            return True
    hay = " ".join(
        x
        for x in (
            getattr(pr, "source_ref_name", None) or "",
            getattr(pr, "title", None) or "",
            getattr(pr, "description", None) or "",
        )
        if x
    ).lower()
    return work_item_id.lower() in hay


def _azdo_enrich_repos_from_git_pull_requests(
    connection: object,
    project: str,
    sprint_data: list[dict],
) -> None:
    """Scan recent PRs in Git repos for work item links / id in branch (optional, env-gated)."""
    from scrum_agent.config import (
        get_team_analysis_azdo_pr_search_max_repos,
        get_team_analysis_azdo_pr_search_top,
        get_team_analysis_azdo_repo_allowlist,
        is_team_analysis_azdo_pr_search_enabled,
    )

    if not is_team_analysis_azdo_pr_search_enabled():
        return

    try:
        from azure.devops.v7_1.git.models import GitPullRequestSearchCriteria
    except Exception:
        return

    git_client = connection.clients.get_git_client()
    allow = get_team_analysis_azdo_repo_allowlist()
    max_repos = get_team_analysis_azdo_pr_search_max_repos()
    pr_top = get_team_analysis_azdo_pr_search_top()

    try:
        all_git_repos = git_client.get_repositories(project) or []
    except Exception as e:
        logger.debug("get_repositories failed: %s", e)
        return

    grepos = [r for r in all_git_repos if getattr(r, "name", None)]
    if allow is not None:
        grepos = [r for r in grepos if (r.name or "").lower() in allow]
    grepos = grepos[:max_repos]

    pr_cache: dict[str, list] = {}
    for grepo in grepos:
        rid = grepo.id
        name_lower = (grepo.name or "").lower()
        seen_ids: set[int] = set()
        combined: list = []
        for status in ("completed", "active"):
            try:
                crit = GitPullRequestSearchCriteria(status=status)
                chunk = git_client.get_pull_requests(rid, crit, project=project, top=pr_top) or []
            except Exception:
                chunk = []
            for pr in chunk:
                pid = getattr(pr, "pull_request_id", None)
                if pid is None or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                combined.append(pr)
        pr_cache[name_lower] = combined

    for sd in sprint_data:
        for s in sd.get("stories", []) or []:
            if s.get("is_recurring"):
                continue
            if s.get("repos"):
                continue
            ikey = str(s.get("issue_key", "") or "")
            if not ikey.isdigit():
                continue
            for rname, prs in pr_cache.items():
                for pr in prs:
                    if _azdo_pr_matches_work_item(pr, ikey):
                        _story_add_repos(s, [rname], "azdo_pr_work_items")
                        break

    if pr_cache:
        logger.info(
            "AzDO PR repo enrichment: scanned %d Git repos for work item ↔ PR links",
            len(pr_cache),
        )


_REVIEW_RE = re.compile(r"\b(reviewed|LGTM|approved|code\s*review)\b", re.IGNORECASE)
_TEST_RE = re.compile(r"\b(tested|QA\s*pass|verified|no\s*regressions|tests?\s*pass)\b", re.IGNORECASE)
_DEPLOY_RE = re.compile(r"\b(deployed|released|merged\s*to\s*main|shipped|staging)\b", re.IGNORECASE)
_DOC_RE = re.compile(
    r"\b(documented|readme|runbook|confluence|wiki|updated\s*docs?|documentation)\b",
    re.IGNORECASE,
)
_AC_VERIFY_RE = re.compile(
    r"\b(acceptance\s*criteria|AC\s*met|AC\s*verified|criteria\s*met|demo['']?d|stakeholder\s*sign.?off)\b",
    re.IGNORECASE,
)
_SECURITY_RE = re.compile(
    r"\b(security\s*review|vulnerability|pen\s*test|OWASP|threat\s*model|secret\s*scan)\b",
    re.IGNORECASE,
)
_PERF_RE = re.compile(
    r"\b(performance\s*test|load\s*test|benchmark|latency|throughput|perf\s*check)\b",
    re.IGNORECASE,
)
_MONITORING_RE = re.compile(
    r"\b(monitoring|alert|dashboard|observability|logging|metric|grafana|datadog)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# LLM-powered ticket structure parser
# ---------------------------------------------------------------------------

_TICKET_PARSE_SCHEMA = """\
[
  {
    "key": "ISSUE-123",
    "description": "The actual problem/feature description",
    "acceptance_criteria": ["AC item 1", "AC item 2"],
    "ac_specificity": "precise",
    "risks": ["Risk 1"],
    "justification": "Why this matters",
    "dod_signals": ["Code reviewed", "Deployed to staging"],
    "personas": ["developer", "admin"],
    "gwt": false,
    "discipline": "backend",
    "work_type": "create/build",
    "is_recurring": false,
    "spillover_risk": "none"
  }
]"""

_TICKET_PARSE_PROMPT = """\
Analyse each work item below and extract structured sections.
Return a JSON array with one object per work item matching this schema:

{schema}

Section extraction — look for these patterns in the description:
- "What is this about?" / "Description" / problem statement → description
- "What does done look like?" / "Acceptance criteria" / "AC" → acceptance_criteria
- "Are there any risks?" / "Blockers" / "Dependencies" → risks
- "Why does it matter?" / "Business value" / "Impact" → justification
- Definition of done items (testing, review, deployment steps) → dod_signals
- User personas ("As a developer...", "As an admin...") → personas

Classification fields:
- discipline: infer from content — one of "backend", "frontend", "infrastructure", \
"design", "testing", "data", "devops", "fullstack"
- work_type: classify the nature of work — one of "create/build", "fix/resolve", \
"update/modify", "investigate/research", "automate/script", "migrate", \
"refactor", "configure/setup", "monitor/observe", "review/audit"
- is_recurring: true if this is a ceremony, KTLO, sprint admin, training, standup, \
retro, BAU, on-call, or operational ticket — NOT delivery work
- ac_specificity: assess the acceptance criteria quality — one of "precise" \
(measurable, specific values/endpoints/thresholds), "moderate" (clear intent but \
not fully measurable), "vague" (subjective language like "should work correctly"), \
or "none" (no ACs found)
- spillover_risk: assess completion risk — one of "low" (clear scope, small), \
"medium" (some unknowns or dependencies), "high" (large scope, external \
dependencies, unclear requirements), or "none" (can't determine)

Rules:
- acceptance_criteria should be individual testable items, not the full section text
- If ACs use Given/When/Then format, set gwt to true
- If a section is missing or empty, return empty string/list
- Return ONLY the JSON array, no other text

Work items:

{items}"""


def _strip_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace for LLM input."""
    clean = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", clean).strip()


def _parse_tickets_with_llm(
    stories: list[dict],
    progress: list[str],
    batch_size: int = 6,
) -> dict[str, dict]:
    """Parse tickets using LLM in batches. Returns {issue_key: parsed_dict}.

    Falls back to empty dict if LLM is unavailable or fails.
    """
    if not stories:
        return {}

    try:
        from langchain_core.messages import HumanMessage

        from scrum_agent.agent.llm import get_llm
    except Exception:
        logger.debug("LLM not available for ticket parsing, using regex fallback")
        return {}

    # Build batches
    batches: list[list[dict]] = []
    for i in range(0, len(stories), batch_size):
        batches.append(stories[i : i + batch_size])

    results: dict[str, dict] = {}

    def _parse_batch(batch: list[dict]) -> dict[str, dict]:
        """Parse a single batch of stories."""
        items_parts: list[str] = []
        for s in batch:
            key = s.get("issue_key", "?")
            title = s.get("summary", "")
            desc = _strip_html(s.get("description", "") or "")
            # Truncate to avoid token bloat
            if len(desc) > 800:
                desc = desc[:800] + "..."
            # Include metadata for better classification
            pts = s.get("points", 0)
            tasks = s.get("task_count", 0)
            carried = "yes" if s.get("carried_over") else "no"
            meta = f"[{pts}pts, {tasks} tasks, carried_over={carried}]"
            items_parts.append(f"--- {key}: {title} {meta} ---\n{desc}")

        items_block = "\n\n".join(items_parts)
        prompt = _TICKET_PARSE_PROMPT.format(schema=_TICKET_PARSE_SCHEMA, items=items_block)

        try:
            response = get_llm(temperature=0.0).invoke([HumanMessage(content=prompt)])
            text = response.content if hasattr(response, "content") else str(response)
            # Extract JSON from response (handle markdown fences)
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                return {}
            batch_results: dict[str, dict] = {}
            for item in parsed:
                if isinstance(item, dict) and item.get("key"):
                    batch_results[item["key"]] = item
            return batch_results
        except Exception as exc:
            logger.debug("LLM ticket parse batch failed: %s", exc)
            return {}

    # Run batches in parallel
    progress.append("Parsing ticket structure\u2026")
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_parse_batch, b): i for i, b in enumerate(batches)}
            for future in as_completed(futures):
                try:
                    batch_result = future.result(timeout=30)
                    results.update(batch_result)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("LLM ticket parsing failed entirely: %s", exc)
        return {}

    logger.info("LLM parsed %d/%d stories", len(results), len(stories))
    return results


def _enrich_stories_with_parsed(
    stories: list[dict],
    parsed: dict[str, dict],
) -> None:
    """Mutate story dicts in-place with LLM-parsed fields."""
    _valid_disciplines = {
        "backend",
        "frontend",
        "infrastructure",
        "design",
        "testing",
        "data",
        "devops",
        "fullstack",
    }
    for s in stories:
        key = s.get("issue_key", "")
        p = parsed.get(key)
        if p:
            # Acceptance criteria
            acs = p.get("acceptance_criteria", [])
            if isinstance(acs, list) and acs:
                s["ac_count"] = len(acs)
                s["parsed_ac"] = acs
            s["ac_specificity"] = p.get("ac_specificity", "none")

            # DoD and context
            s["parsed_risks"] = p.get("risks", [])
            s["parsed_dod_signals"] = p.get("dod_signals", [])
            s["parsed_justification"] = p.get("justification", "")
            s["parsed_personas"] = p.get("personas", [])
            if p.get("gwt"):
                s["uses_given_when_then"] = True

            # Discipline — override the tag-based default if LLM inferred one
            llm_disc = (p.get("discipline") or "").lower().strip()
            if llm_disc in _valid_disciplines and s.get("discipline") == "fullstack":
                s["discipline"] = llm_disc

            # Work type classification
            work_type = p.get("work_type", "")
            if work_type:
                s["work_type"] = work_type

            # Recurring detection — override only if LLM says it's recurring
            # but regex didn't catch it (don't un-flag regex positives)
            if p.get("is_recurring") and not s.get("is_recurring"):
                s["is_recurring"] = True

            # Spillover risk
            s["spillover_risk"] = p.get("spillover_risk", "none")

            s["parse_source"] = "llm"
        else:
            s["parse_source"] = "regex"


def _normalize_title(title: str) -> str:
    """Normalize a story title for duplicate detection across sprints.

    Strips sprint numbers, common prefixes, and normalizes whitespace
    so "Script the PR creation v2" matches "Script the PR creation".
    """
    t = title.lower().strip()
    # Strip sprint references like "Sprint 24", "S24", "Sprint-24"
    t = re.sub(r"\b(?:sprint|s)\s*[-_]?\s*\d+\b", "", t, flags=re.IGNORECASE)
    # Strip version suffixes like "v2", "(part 2)", "#2"
    t = re.sub(r"\s*(?:v\d+|\(part\s*\d+\)|#\d+)\s*", "", t)
    # Strip leading ticket-like prefixes
    t = re.sub(r"^[A-Z]+-\d+\s*[-:]\s*", "", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _detect_shadow_spillover(sprint_data: list[dict]) -> list[dict]:
    """Detect stories that were closed but re-created in the next sprint.

    Uses normalized title **exact** equality only (same normalized string in
    consecutive sprints), not word-overlap fuzzy matching.

    Each item: {title, from_sprint, to_sprint, issue_key, issue_url,
                from_issue_key, from_title, similarity}
    """
    confirmed: list[dict] = []
    logger.debug("Checking shadow spillover across %d consecutive sprint pairs", max(0, len(sprint_data) - 1))

    for i in range(len(sprint_data) - 1):
        curr = sprint_data[i]
        next_sp = sprint_data[i + 1]

        curr_done: list[tuple[str, dict]] = []
        for s in curr.get("stories", []):
            if s.get("is_recurring"):
                continue
            if not s.get("carried_over"):
                norm = _normalize_title(s.get("summary", ""))
                if norm and len(norm) > 5:
                    curr_done.append((norm, s))

        for s_next in next_sp.get("stories", []):
            if s_next.get("is_recurring"):
                continue
            n_next = _normalize_title(s_next.get("summary", ""))
            if not n_next or len(n_next) < 5:
                continue

            best_match: dict | None = None
            for n_prev, s_prev in curr_done:
                if n_next == n_prev:
                    best_match = s_prev
                    break

            if best_match is None:
                continue

            confirmed.append(
                {
                    "title": s_next.get("summary", "")[:55],
                    "from_title": best_match.get("summary", "")[:55],
                    "from_sprint": curr.get("sprint_name", ""),
                    "to_sprint": next_sp.get("sprint_name", ""),
                    "issue_key": s_next.get("issue_key", ""),
                    "issue_url": s_next.get("issue_url", ""),
                    "from_issue_key": best_match.get("issue_key", ""),
                    "from_issue_url": best_match.get("issue_url", ""),
                    "similarity": 1.0,
                }
            )

    if confirmed:
        logger.info("Shadow spillover (exact title match): %d stories", len(confirmed))
    return confirmed


def _worker_sprint_velocity(sprint_data: list[dict], progress: list[str]) -> dict:
    """Worker 1: Compute velocity stats, sprint completion, and per-sprint detail.

    All metrics exclude recurring/ceremony tickets (is_recurring=True) so
    velocity, completion rates, and sprint breakdowns reflect delivery work only.
    """
    progress.append("Fetching sprint history\u2026")

    # Detect shadow spillover first so we can mark affected sprints.
    confirmed_shadows = _detect_shadow_spillover(sprint_data)

    # Build a set of sprint names that had confirmed shadow spillover
    shadow_from_sprints: set[str] = set()
    shadow_by_sprint: dict[str, list[dict]] = defaultdict(list)
    for sh in confirmed_shadows:
        shadow_from_sprints.add(sh["from_sprint"])
        shadow_by_sprint[sh["from_sprint"]].append(sh)

    fully = 0
    partially = 0
    velocities = []
    sprint_details = []

    for sd in sprint_data:
        name = sd.get("sprint_name", "?")
        all_stories = sd.get("stories", [])

        # Split into delivery vs recurring
        delivery = [s for s in all_stories if not s.get("is_recurring")]
        done_delivery = [s for s in delivery if not s.get("carried_over")]
        planned = len(delivery)
        completed = len(done_delivery)
        pts = sum(_safe_float(s.get("points", 0)) for s in done_delivery)

        if pts > 0:
            velocities.append(pts)

        rate = round(completed / planned * 100) if planned > 0 else 0

        # A sprint is NOT truly done if it has shadow spillover —
        # stories were closed but re-created in the next sprint.
        has_shadow = name in shadow_from_sprints
        is_done = planned > 0 and completed >= planned and not has_shadow

        if is_done:
            fully += 1
        elif planned > 0:
            partially += 1

        # Incomplete delivery stories for drill-down
        incomplete = []
        for s in delivery:
            if s.get("carried_over"):
                incomplete.append(
                    {
                        "issue_key": s.get("issue_key", ""),
                        "issue_url": s.get("issue_url", ""),
                        "summary": (s.get("summary", "") or "")[:50],
                        "points": _safe_float(s.get("points", 0)),
                    }
                )
        # Add shadow spillover items to the incomplete list
        for sh in shadow_by_sprint.get(name, []):
            incomplete.append(
                {
                    "issue_key": sh.get("issue_key", ""),
                    "issue_url": sh.get("issue_url", ""),
                    "summary": sh.get("title", "")[:50],
                    "points": 0,
                    "shadow": True,
                }
            )

        sprint_details.append(
            {
                "name": name,
                "planned": planned,
                "completed": completed,
                "points": round(pts, 1),
                "rate": rate,
                "done": is_done,
                "has_shadow": has_shadow,
                "incomplete": incomplete[:5],
            }
        )

    velocity_avg = sum(velocities) / len(velocities) if velocities else 0.0
    velocity_stddev = _stddev(velocities)
    completion_rates = [sd["rate"] for sd in sprint_details if sd["planned"] > 0]
    logger.info(
        "Velocity worker: avg=%.1f, stddev=%.1f, %d fully done, %d partial, %d confirmed shadows",
        velocity_avg,
        velocity_stddev,
        fully,
        partially,
        len(confirmed_shadows),
    )
    for sd in sprint_details:
        logger.debug(
            "  Sprint %s: %.1f pts, %d/%d done (%d%%), shadow=%s",
            sd["name"],
            sd["points"],
            sd["completed"],
            sd["planned"],
            sd["rate"],
            sd.get("has_shadow", False),
        )

    return {
        "velocity_avg": round(velocity_avg, 1),
        "velocity_stddev": round(velocity_stddev, 1),
        "sprints_fully_completed": fully,
        "sprints_partially_completed": partially,
        "sprint_completion_rate": round(sum(completion_rates) / len(completion_rates), 1) if completion_rates else 0.0,
        "sprint_details": sprint_details,
        "shadow_spillover": confirmed_shadows,
    }


_ACTION_VERBS = {
    "create": "create/build",
    "build": "create/build",
    "implement": "create/build",
    "add": "create/build",
    "set up": "create/build",
    "setup": "create/build",
    "script": "automate/script",
    "automate": "automate/script",
    "pipeline": "automate/script",
    "update": "update/modify",
    "modify": "update/modify",
    "change": "update/modify",
    "upgrade": "update/modify",
    "migrate": "update/modify",
    "fix": "fix/resolve",
    "resolve": "fix/resolve",
    "bug": "fix/resolve",
    "patch": "fix/resolve",
    "hotfix": "fix/resolve",
    "investigate": "investigate/research",
    "research": "investigate/research",
    "spike": "investigate/research",
    "explore": "investigate/research",
    "review": "review/audit",
    "audit": "review/audit",
    "improve": "improve/optimise",
    "optimise": "improve/optimise",
    "optimize": "improve/optimise",
    "refactor": "improve/optimise",
    "enhance": "improve/optimise",
    "deploy": "deploy/release",
    "release": "deploy/release",
    "configure": "configure/setup",
    "config": "configure/setup",
    "monitor": "monitor/observe",
    "alert": "monitor/observe",
    "dashboard": "monitor/observe",
    "test": "test/verify",
    "e2e": "test/verify",
    "verify": "test/verify",
    "document": "document",
    "doc": "document",
    "runbook": "document",
}


def _extract_point_patterns(stories: list[dict]) -> tuple[str, ...]:
    """Extract common work patterns from story titles.

    Groups stories by action verb category and returns the top patterns
    like ("automate/script", "update/modify", "fix/resolve").
    """
    if not stories:
        return ()

    category_count: dict[str, int] = defaultdict(int)
    for s in stories:
        title = (s.get("summary", "") or "").lower()
        matched = False
        for keyword, category in _ACTION_VERBS.items():
            if keyword in title:
                category_count[category] += 1
                matched = True
                break
        if not matched:
            category_count["other"] += 1

    # Return top 3 categories (excluding "other" unless it's dominant)
    sorted_cats = sorted(category_count.items(), key=lambda x: -x[1])
    result = []
    for cat, count in sorted_cats:
        if cat == "other" and len(result) >= 2:
            continue
        pct = round(count / len(stories) * 100)
        if pct >= 10:
            result.append(f"{cat} ({pct}%)")
        if len(result) >= 3:
            break
    return tuple(result)


def _worker_point_calibration(all_stories: list[dict], sprint_data: list[dict], progress: list[str]) -> dict:
    """Worker 2: Point calibration, spillover stats, and pattern extraction."""
    progress.append("Analysing story point patterns\u2026")

    by_points: dict[int, list[dict]] = defaultdict(list)
    for s in all_stories:
        pts = int(_safe_float(s.get("points", 0)))
        if pts in (1, 2, 3, 5, 8):
            by_points[pts].append(s)

    calibrations = []
    for pts in (1, 2, 3, 5, 8):
        stories_at_pts = by_points.get(pts, [])
        if not stories_at_pts:
            calibrations.append(StoryPointCalibration(point_value=pts))
            continue
        cycle_times = [s["cycle_time_days"] for s in stories_at_pts if s.get("cycle_time_days") is not None]
        avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else 0.0
        task_counts = [s.get("task_count", 0) for s in stories_at_pts]
        avg_tasks = sum(task_counts) / len(task_counts) if task_counts else 0.0
        changed = sum(1 for s in stories_at_pts if s.get("point_changed", False))
        overshoot = (changed / len(stories_at_pts) * 100) if stories_at_pts else 0.0
        patterns = _extract_point_patterns(stories_at_pts)
        logger.debug(
            "  %dpt calibration: %.1fd avg cycle, %d samples, %.0f%% overshoot, ~%.1f tasks, patterns=%s",
            pts,
            avg_ct,
            len(stories_at_pts),
            overshoot,
            avg_tasks,
            patterns,
        )
        calibrations.append(
            StoryPointCalibration(
                point_value=pts,
                avg_cycle_time_days=round(avg_ct, 1),
                sample_count=len(stories_at_pts),
                common_patterns=patterns,
                typical_task_count=round(avg_tasks, 1),
                overshoot_pct=round(overshoot, 1),
            )
        )

    # Spillover: stories that carried over to next sprint
    carried = 0
    total = 0
    spillover_pts = []
    spillover_reasons: dict[str, int] = defaultdict(int)
    for sd in sprint_data:
        for s in sd.get("stories", []):
            total += 1
            if s.get("carried_over", False):
                carried += 1
                pts = _safe_float(s.get("points", 0))
                spillover_pts.append(pts)
                disc = s.get("discipline", "unknown")
                spillover_reasons[f"{disc} stories"] += 1

    carried_pct = (carried / total * 100) if total else 0.0
    avg_spill = sum(spillover_pts) / len(sprint_data) if sprint_data else 0.0
    top_reason = max(spillover_reasons, key=spillover_reasons.get) if spillover_reasons else ""

    spillover = SpilloverStats(
        carried_over_pct=round(carried_pct, 1),
        avg_spillover_pts=round(avg_spill, 1),
        most_common_spillover_reason=top_reason,
    )

    # Estimation accuracy
    stories_with_pts = [s for s in all_stories if _safe_float(s.get("points", 0)) > 0]
    unchanged = sum(1 for s in stories_with_pts if not s.get("point_changed", False))
    estimation_accuracy = (unchanged / len(stories_with_pts) * 100) if stories_with_pts else 0.0

    return {
        "calibrations": tuple(calibrations),
        "spillover": spillover,
        "estimation_accuracy_pct": round(estimation_accuracy, 1),
    }


def _worker_writing_patterns(all_stories: list[dict], progress: list[str]) -> WritingPatterns:
    """Worker 3: Work item shapes and writing pattern analysis."""
    progress.append("Computing velocity & spillover\u2026")

    ac_counts = [s.get("ac_count", 0) for s in all_stories if s.get("ac_count", 0) > 0]
    task_counts = [s.get("task_count", 0) for s in all_stories if s.get("task_count", 0) > 0]
    median_ac = statistics.median(ac_counts) if ac_counts else 0.0
    median_tasks = statistics.median(task_counts) if task_counts else 0.0

    # Sub-task label distribution
    label_counter: dict[str, int] = defaultdict(int)
    subtask_patterns: dict[str, int] = defaultdict(int)
    for s in all_stories:
        for label in s.get("subtask_labels", []):
            label_counter[label.lower().strip()] += 1
        for pattern in s.get("subtask_titles", []):
            subtask_patterns[pattern.strip()] += 1

    total_labels = sum(label_counter.values()) or 1
    label_dist = tuple(
        (lbl, round(cnt / total_labels, 2)) for lbl, cnt in sorted(label_counter.items(), key=lambda x: -x[1])[:10]
    )
    common_subtasks = tuple(p for p, _ in sorted(subtask_patterns.items(), key=lambda x: -x[1])[:5])

    # Naming consistency: check if subtask titles follow a pattern (>60% share prefixes)
    titles = list(subtask_patterns.keys())
    consistent = False
    if len(titles) >= 3:
        prefixes = defaultdict(int)
        for t in titles:
            words = t.split()
            if words:
                prefixes[words[0].lower()] += 1
        top_prefix_pct = max(prefixes.values()) / len(titles) if titles else 0
        consistent = top_prefix_pct > 0.6

    # Persona extraction from story summaries
    persona_counter: dict[str, int] = defaultdict(int)
    _persona_re = re.compile(r"\bas\s+(?:a|an)\s+(\w[\w\s]{0,20}?)(?:,|\s+I\b)", re.IGNORECASE)
    for s in all_stories:
        summary = s.get("summary", "")
        m = _persona_re.search(summary)
        if m:
            persona_counter[m.group(1).strip().lower()] += 1
    common_personas = tuple(p for p, _ in sorted(persona_counter.items(), key=lambda x: -x[1])[:5])

    # Given/When/Then detection
    gwt_count = sum(1 for s in all_stories if s.get("description") and len(_GWT_RE.findall(s["description"])) >= 2)
    uses_gwt = (gwt_count / len(all_stories) > 0.3) if all_stories else False

    # Stories with subtasks
    with_subtasks = sum(1 for s in all_stories if s.get("task_count", 0) > 0)
    subtasks_pct = (with_subtasks / len(all_stories) * 100) if all_stories else 0.0

    # Epic description analysis
    epic_desc_lengths = [len(s.get("epic_description", "")) for s in all_stories if s.get("epic_description")]
    epic_desc_avg = int(sum(epic_desc_lengths) / len(epic_desc_lengths)) if epic_desc_lengths else 0
    epics_with_desc_count = sum(1 for s in all_stories if s.get("epic_has_description", False))
    epics_total = sum(1 for s in all_stories if s.get("epic_key"))
    epics_with_desc_pct = (epics_with_desc_count / epics_total * 100) if epics_total else 0.0

    return WritingPatterns(
        median_ac_count=round(median_ac, 1),
        median_task_count_per_story=round(median_tasks, 1),
        subtask_label_distribution=label_dist,
        common_subtask_patterns=common_subtasks,
        subtasks_use_consistent_naming=consistent,
        common_personas=common_personas,
        uses_given_when_then=uses_gwt,
        epic_description_length_avg=epic_desc_avg,
        stories_with_subtasks_pct=round(subtasks_pct, 1),
        epics_with_description_pct=round(epics_with_desc_pct, 1),
    )


def _worker_dod_signals(all_stories: list[dict], progress: list[str]) -> DoDSignal:
    """Worker 4: Definition of Done signals from comments and descriptions."""
    progress.append("Building team profile\u2026")

    if not all_stories:
        return DoDSignal()

    n = len(all_stories)
    with_comments = 0
    with_pr = 0
    with_review = 0
    with_testing = 0
    with_deploy = 0
    comment_counts: list[int] = []
    checklist_counter: dict[str, int] = defaultdict(int)

    for s in all_stories:
        comments = s.get("comments", [])
        description = s.get("description", "") or ""
        all_text = description + " " + " ".join(comments)

        if comments:
            with_comments += 1
        comment_counts.append(len(comments))

        if _PR_RE.search(all_text):
            with_pr += 1
        if _REVIEW_RE.search(all_text):
            with_review += 1
        if _TEST_RE.search(all_text):
            with_testing += 1
        if _DEPLOY_RE.search(all_text):
            with_deploy += 1

        for _m in _PR_RE.finditer(all_text):
            checklist_counter["PR linked"] += 1
        for _m in _REVIEW_RE.finditer(all_text):
            checklist_counter["code reviewed"] += 1
        for _m in _TEST_RE.finditer(all_text):
            checklist_counter["tests passing"] += 1
        for _m in _DEPLOY_RE.finditer(all_text):
            checklist_counter["deployed"] += 1

        # Supplement with LLM-parsed DoD signals (if available)
        for sig in s.get("parsed_dod_signals", []):
            if isinstance(sig, str) and sig:
                checklist_counter[sig.lower()] += 1

    common_items = tuple(item for item, _ in sorted(checklist_counter.items(), key=lambda x: -x[1])[:6])
    avg_comments = sum(comment_counts) / n if n else 0.0

    return DoDSignal(
        common_checklist_items=common_items,
        stories_with_comments_pct=round(with_comments / n * 100, 1) if n else 0.0,
        stories_with_pr_link_pct=round(with_pr / n * 100, 1) if n else 0.0,
        stories_with_review_mention_pct=round(with_review / n * 100, 1) if n else 0.0,
        stories_with_testing_mention_pct=round(with_testing / n * 100, 1) if n else 0.0,
        stories_with_deploy_mention_pct=round(with_deploy / n * 100, 1) if n else 0.0,
        avg_comments_before_resolution=round(avg_comments, 1),
    )


def _run_parallel_analysis(
    source: str,
    project_key: str,
    sprint_data: list[dict],
    progress: list[str] | None = None,
) -> TeamProfile:
    """Run the 4-worker parallel analysis and merge results into a TeamProfile.

    ``progress`` is a shared list the TUI can read to display live status messages.
    Each worker appends a string when it starts (e.g. "Fetching sprint history\u2026").
    """
    if progress is None:
        progress = []

    all_stories: list[dict] = []
    for sd in sprint_data:
        all_stories.extend(sd.get("stories", []))

    # Tag recurring/ceremony tickets so they're excluded from calibration
    _tag_recurring_batch(all_stories)
    delivery_stories = [s for s in all_stories if not s.get("is_recurring")]
    recurring_stories = [s for s in all_stories if s.get("is_recurring")]
    logger.info(
        "Team analysis: %d total stories, %d delivery, %d recurring across %d sprints",
        len(all_stories),
        len(delivery_stories),
        len(recurring_stories),
        len(sprint_data),
    )

    # LLM-powered ticket structure parsing — enriches story dicts with
    # extracted acceptance criteria, DoD signals, risks, personas.
    # Runs BEFORE workers so they all get enriched data.
    # Falls back silently to regex if LLM is unavailable.
    _parsed_tickets = _parse_tickets_with_llm(delivery_stories, progress)
    _enrich_stories_with_parsed(delivery_stories, _parsed_tickets)

    # Re-filter: LLM may have flagged additional stories as recurring
    if _parsed_tickets:
        _newly_recurring = [s for s in delivery_stories if s.get("is_recurring")]
        if _newly_recurring:
            recurring_stories.extend(_newly_recurring)
            delivery_stories = [s for s in delivery_stories if not s.get("is_recurring")]
            logger.info("LLM flagged %d additional recurring stories", len(_newly_recurring))

    # Story shapes by discipline (computed in main thread — lightweight)
    by_discipline: dict[str, list[dict]] = defaultdict(list)
    for s in delivery_stories:
        disc = s.get("discipline", "fullstack")
        by_discipline[disc].append(s)

    shapes = []
    for disc, stories_in_disc in sorted(by_discipline.items()):
        pts_vals = [_safe_float(s.get("points", 0)) for s in stories_in_disc]
        ac_vals = [_safe_float(s.get("ac_count", 0)) for s in stories_in_disc]
        task_vals = [_safe_float(s.get("task_count", 0)) for s in stories_in_disc]
        shapes.append(
            StoryShapePattern(
                discipline=disc,
                avg_points=round(sum(pts_vals) / len(pts_vals), 1) if pts_vals else 0.0,
                avg_ac_count=round(sum(ac_vals) / len(ac_vals), 1) if ac_vals else 0.0,
                avg_task_count=round(sum(task_vals) / len(task_vals), 1) if task_vals else 0.0,
                sample_count=len(stories_in_disc),
            )
        )

    # Epic patterns (delivery stories only)
    by_epic: dict[str, list[dict]] = defaultdict(list)
    for s in delivery_stories:
        ek = s.get("epic_key", "")
        if ek:
            by_epic[ek].append(s)

    if by_epic:
        epic_story_counts = [len(stories) for stories in by_epic.values()]
        epic_point_totals = [sum(_safe_float(s.get("points", 0)) for s in stories) for stories in by_epic.values()]
        epic_pattern = EpicPattern(
            avg_stories_per_epic=round(sum(epic_story_counts) / len(epic_story_counts), 1),
            avg_points_per_epic=round(sum(epic_point_totals) / len(epic_point_totals), 1),
            typical_story_count_range=(min(epic_story_counts), max(epic_story_counts)),
            sample_count=len(by_epic),
        )
    else:
        epic_pattern = EpicPattern()

    # 4-worker parallel analysis
    results: dict = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_worker_sprint_velocity, sprint_data, progress): "velocity",
            executor.submit(_worker_point_calibration, delivery_stories, sprint_data, progress): "calibration",
            executor.submit(_worker_writing_patterns, delivery_stories, progress): "writing",
            executor.submit(_worker_dod_signals, delivery_stories, progress): "dod",
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception:
                logger.exception("Worker %s failed", key)
                results[key] = None

    vel = results.get("velocity") or {}
    cal = results.get("calibration") or {}
    writing = results.get("writing") or WritingPatterns()
    dod = results.get("dod") or DoDSignal()

    # Collect illustrative examples for each metric
    examples = _collect_examples(delivery_stories, sprint_data)

    # Include sprint-by-sprint details and shadow spillover for the report
    examples["sprint_details"] = vel.get("sprint_details", [])  # type: ignore[assignment]
    examples["shadow_spillover"] = vel.get("shadow_spillover", [])  # type: ignore[assignment]

    # Task decomposition analysis from subtask_details
    examples["task_decomposition"] = _analyse_subtasks(delivery_stories)  # type: ignore[assignment]

    # Proposed Definition of Done — cross-reference DoD signals with subtask patterns
    examples["proposed_dod"] = _analyse_proposed_dod(  # type: ignore[assignment]
        dod,
        examples.get("task_decomposition", {}),
        delivery_stories,
    )

    # Acceptance criteria pattern analysis
    examples["ac_patterns"] = _analyse_acceptance_criteria(delivery_stories)  # type: ignore[assignment]

    # LLM parse stats
    llm_count = sum(1 for s in delivery_stories if s.get("parse_source") == "llm")
    regex_count = sum(1 for s in delivery_stories if s.get("parse_source") == "regex")
    if llm_count > 0 or regex_count > 0:
        examples["parse_stats"] = {  # type: ignore[assignment]
            "llm_parsed": llm_count,
            "regex_fallback": regex_count,
            "total": len(delivery_stories),
        }

    # Repository analysis from PR links in story text
    examples["repositories"] = _analyse_repositories(delivery_stories)  # type: ignore[assignment]

    # Mid-sprint scope change analysis
    examples["scope_changes"] = _analyse_scope_changes(sprint_data)  # type: ignore[assignment]

    # Add recurring work examples so the report can show them
    if recurring_stories:
        # Deduplicate by title (show unique recurring patterns)
        seen_titles: set[str] = set()
        rec_examples = []
        for s in recurring_stories:
            title = (s.get("summary", "") or "").strip().lower()
            if title not in seen_titles and s.get("issue_key"):
                seen_titles.add(title)
                rec_examples.append(
                    {
                        "issue_key": s.get("issue_key", ""),
                        "issue_url": s.get("issue_url", ""),
                        "summary": (s.get("summary", "") or "")[:60],
                        "detail": "",
                    }
                )
            if len(rec_examples) >= 5:
                break
        examples["recurring"] = rec_examples
        examples["recurring_count"] = len(recurring_stories)  # type: ignore[assignment]
        examples["delivery_count"] = len(delivery_stories)  # type: ignore[assignment]

    # ── New metrics: discipline calibration, spillover correlation, velocity trend, confidence ──

    # Discipline-specific point calibration
    disc_cal: dict[str, dict[int, dict]] = defaultdict(lambda: defaultdict(dict))
    for s in delivery_stories:
        disc = s.get("discipline", "fullstack")
        pts = int(_safe_float(s.get("points", 0)))
        if pts not in (1, 2, 3, 5, 8):
            continue
        disc_cal[disc].setdefault(pts, {"cycle_times": [], "count": 0, "spill": 0})
        disc_cal[disc][pts]["count"] += 1
        ct = s.get("cycle_time_days")
        if ct is not None:
            disc_cal[disc][pts]["cycle_times"].append(ct)
        if s.get("carried_over", False):
            disc_cal[disc][pts]["spill"] += 1

    discipline_calibration: dict[str, list[dict]] = {}
    for disc, by_pts in sorted(disc_cal.items()):
        disc_entries = []
        for pts in (1, 2, 3, 5, 8):
            data = by_pts.get(pts)
            if not data or data["count"] == 0:
                continue
            cts = data["cycle_times"]
            avg_ct = round(sum(cts) / len(cts), 1) if cts else 0.0
            variance = round(_stddev(cts), 1) if len(cts) >= 2 else 0.0
            spill_pct = round(data["spill"] / data["count"] * 100, 1)
            disc_entries.append(
                {
                    "points": pts,
                    "avg_cycle_days": avg_ct,
                    "variance": variance,
                    "samples": data["count"],
                    "spill_pct": spill_pct,
                }
            )
        if disc_entries:
            discipline_calibration[disc] = disc_entries
    examples["discipline_calibration"] = discipline_calibration  # type: ignore[assignment]

    # Spillover root-cause correlation (by size, discipline, task count)
    spill_by_size: dict[int, dict[str, int]] = defaultdict(lambda: {"spill": 0, "total": 0})
    spill_by_disc: dict[str, dict[str, int]] = defaultdict(lambda: {"spill": 0, "total": 0})
    spill_by_tasks: dict[str, dict[str, int]] = defaultdict(lambda: {"spill": 0, "total": 0})
    for s in delivery_stories:
        pts = int(_safe_float(s.get("points", 0)))
        disc = s.get("discipline", "fullstack")
        tasks = s.get("task_count", 0)
        is_spill = s.get("carried_over", False)

        if pts in (1, 2, 3, 5, 8):
            spill_by_size[pts]["total"] += 1
            if is_spill:
                spill_by_size[pts]["spill"] += 1

        spill_by_disc[disc]["total"] += 1
        if is_spill:
            spill_by_disc[disc]["spill"] += 1

        bucket = "0-1 tasks" if tasks <= 1 else ("2-3 tasks" if tasks <= 3 else "4+ tasks")
        spill_by_tasks[bucket]["total"] += 1
        if is_spill:
            spill_by_tasks[bucket]["spill"] += 1

    def _spill_pcts(d: dict) -> dict[str, float]:
        return {k: round(v["spill"] / v["total"] * 100, 1) if v["total"] > 0 else 0.0 for k, v in d.items()}

    spillover_correlation = {
        "by_size": {str(k): v for k, v in sorted(_spill_pcts(spill_by_size).items())},
        "by_discipline": dict(sorted(_spill_pcts(spill_by_disc).items())),
        "by_task_count": dict(_spill_pcts(spill_by_tasks)),
    }
    examples["spillover_correlation"] = spillover_correlation  # type: ignore[assignment]

    # Velocity trend — simple linear regression over sprint velocities
    sprint_velocities = [sd.get("completed_points", 0.0) for sd in sprint_data if sd.get("completed_points", 0) > 0]
    velocity_trend: dict[str, object] = {"trend": "insufficient_data", "slope": 0.0}
    if len(sprint_velocities) >= 3:
        n_sv = len(sprint_velocities)
        x_mean = (n_sv - 1) / 2
        y_mean = sum(sprint_velocities) / n_sv
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(sprint_velocities))
        den = sum((i - x_mean) ** 2 for i in range(n_sv))
        slope = num / den if den else 0.0
        if abs(slope) < 0.5:
            trend_label = "stable"
        elif slope > 0:
            trend_label = "improving"
        else:
            trend_label = "degrading"
        velocity_trend = {
            "trend": trend_label,
            "slope": round(slope, 2),
            "first_velocity": round(sprint_velocities[0], 1),
            "last_velocity": round(sprint_velocities[-1], 1),
        }
    examples["velocity_trend"] = velocity_trend  # type: ignore[assignment]

    # Confidence scoring — classify each point calibration
    confidence_levels: dict[int, str] = {}
    for c_cal in cal.get("calibrations", ()):
        if c_cal.sample_count >= 15:
            confidence_levels[c_cal.point_value] = "high"
        elif c_cal.sample_count >= 5:
            confidence_levels[c_cal.point_value] = "medium"
        elif c_cal.sample_count > 0:
            confidence_levels[c_cal.point_value] = "low"
    examples["confidence_levels"] = confidence_levels  # type: ignore[assignment]

    # Team size and per-contributor velocity breakdown
    all_assignees: set[str] = set()
    contributor_delivery_pts: dict[str, float] = defaultdict(float)
    contributor_delivery_count: dict[str, int] = defaultdict(int)
    contributor_recurring_pts: dict[str, float] = defaultdict(float)
    contributor_sprints: dict[str, set[str]] = defaultdict(set)

    for s in delivery_stories:
        a = (s.get("assignee", "") or "").strip()
        if a:
            all_assignees.add(a)
            if not s.get("carried_over"):
                contributor_delivery_pts[a] += _safe_float(s.get("points", 0))
                contributor_delivery_count[a] += 1
            sprint_name = s.get("sprint_name", "")
            if sprint_name:
                contributor_sprints[a].add(sprint_name)

    for s in recurring_stories:
        a = (s.get("assignee", "") or "").strip()
        if a:
            all_assignees.add(a)
            contributor_recurring_pts[a] += _safe_float(s.get("points", 0))

    team_size = len(all_assignees)
    vel_avg = vel.get("velocity_avg", 0.0)
    per_dev_velocity = round(vel_avg / team_size, 1) if team_size > 0 else 0.0

    # Per-contributor breakdown for the TUI
    contributor_stats: list[dict] = []
    for name in sorted(all_assignees):
        d_pts = contributor_delivery_pts.get(name, 0.0)
        r_pts = contributor_recurring_pts.get(name, 0.0)
        d_count = contributor_delivery_count.get(name, 0)
        sprints_active = len(contributor_sprints.get(name, set()))
        per_sprint = round(d_pts / sprints_active, 1) if sprints_active else 0.0
        contributor_stats.append(
            {
                "name": name,
                "delivery_pts": round(d_pts, 1),
                "recurring_pts": round(r_pts, 1),
                "stories_completed": d_count,
                "sprints_active": sprints_active,
                "per_sprint": per_sprint,
            }
        )
    contributor_stats.sort(key=lambda x: -x["delivery_pts"])

    examples["team_members"] = sorted(all_assignees)  # type: ignore[assignment]
    examples["team_size"] = team_size  # type: ignore[assignment]
    examples["per_dev_velocity"] = per_dev_velocity  # type: ignore[assignment]
    examples["contributor_stats"] = contributor_stats  # type: ignore[assignment]

    team_id = f"{source}-{project_key}"
    profile = TeamProfile(
        team_id=team_id,
        source=source,
        project_key=project_key,
        sample_sprints=len(sprint_data),
        sample_stories=len(delivery_stories),
        velocity_avg=vel.get("velocity_avg", 0.0),
        velocity_stddev=vel.get("velocity_stddev", 0.0),
        point_calibrations=cal.get("calibrations", ()),
        story_shapes=tuple(shapes),
        epic_pattern=epic_pattern,
        estimation_accuracy_pct=cal.get("estimation_accuracy_pct", 0.0),
        sprint_completion_rate=vel.get("sprint_completion_rate", 0.0),
        spillover=cal.get("spillover", SpilloverStats()),
        dod_signal=dod,
        writing_patterns=writing,
        sprints_fully_completed=vel.get("sprints_fully_completed", 0),
        sprints_partially_completed=vel.get("sprints_partially_completed", 0),
        sprints_analysed=len(sprint_data),
    )

    logger.info(
        "Profile built: %s — %d sprints, %d stories, "
        "velocity=%.1f±%.1f, completion=%.0f%%, "
        "%d team members, %.1f pts/dev/sprint",
        team_id,
        profile.sample_sprints,
        profile.sample_stories,
        profile.velocity_avg,
        profile.velocity_stddev,
        profile.sprint_completion_rate,
        team_size,
        per_dev_velocity,
    )
    logger.info(
        "  DoD signals: PR=%.0f%% review=%.0f%% testing=%.0f%% deploy=%.0f%%",
        dod.stories_with_pr_link_pct,
        dod.stories_with_review_mention_pct,
        dod.stories_with_testing_mention_pct,
        dod.stories_with_deploy_mention_pct,
    )
    logger.info(
        "  Examples collected: %d categories, %d total items",
        len(examples),
        sum(len(v) for v in examples.values() if isinstance(v, list)),
    )

    return profile, examples


def _analyse_subtasks(delivery_stories: list[dict]) -> dict:
    """Analyse subtask/child work items for decomposition patterns."""
    stories_with_tasks = [s for s in delivery_stories if s.get("subtask_details") and len(s["subtask_details"]) > 0]
    if not stories_with_tasks:
        return {}

    all_tasks: list[dict] = []
    for s in stories_with_tasks:
        all_tasks.extend(s.get("subtask_details", []))

    total_tasks = len(all_tasks)
    done_tasks = sum(1 for t in all_tasks if t.get("done", False))
    completion_rate = round(done_tasks / total_tasks * 100) if total_tasks else 0

    # Task type distribution
    type_counter: dict[str, int] = defaultdict(int)
    for t in all_tasks:
        title = (t.get("title", "") or "").lower()
        if any(k in title for k in ("test", "qa", "verify", "validation")):
            type_counter["Testing"] += 1
        elif any(k in title for k in ("review", "pr", "code review", "approval")):
            type_counter["Review"] += 1
        elif any(k in title for k in ("deploy", "release", "staging", "production")):
            type_counter["Deploy"] += 1
        elif any(k in title for k in ("design", "ux", "ui", "mockup", "wireframe")):
            type_counter["Design"] += 1
        elif any(k in title for k in ("doc", "documentation", "readme", "runbook")):
            type_counter["Documentation"] += 1
        else:
            type_counter["Development"] += 1

    total_typed = sum(type_counter.values()) or 1
    type_dist = {k: round(v / total_typed * 100) for k, v in sorted(type_counter.items(), key=lambda x: -x[1])}

    # Completion by type — which types finish and which lag?
    type_done: dict[str, list[bool]] = defaultdict(list)
    for t in all_tasks:
        title = (t.get("title", "") or "").lower()
        done = t.get("done", False)
        if any(k in title for k in ("test", "qa", "verify", "validation")):
            type_done["Testing"].append(done)
        elif any(k in title for k in ("review", "pr", "code review", "approval")):
            type_done["Review"].append(done)
        elif any(k in title for k in ("deploy", "release", "staging", "production")):
            type_done["Deploy"].append(done)
        else:
            type_done["Development"].append(done)

    bottlenecks = []
    for cat, dones in type_done.items():
        if len(dones) >= 3:
            cat_rate = sum(dones) / len(dones) * 100
            if cat_rate < 60:
                bottlenecks.append((cat, round(cat_rate), len(dones)))

    # Task assignee concentration — is work spread or siloed?
    task_assignees: dict[str, int] = defaultdict(int)
    for t in all_tasks:
        a = (t.get("assignee", "") or "").strip()
        if a:
            task_assignees[a] += 1

    # Common task title patterns
    title_counter: dict[str, int] = defaultdict(int)
    for t in all_tasks:
        title = (t.get("title", "") or "").strip()
        if title:
            title_counter[title] += 1
    common_tasks = [(title, cnt) for title, cnt in sorted(title_counter.items(), key=lambda x: -x[1])[:5] if cnt >= 2]

    return {
        "stories_with_tasks": len(stories_with_tasks),
        "total_stories": len(delivery_stories),
        "total_tasks": total_tasks,
        "avg_tasks_per_story": round(total_tasks / len(stories_with_tasks), 1),
        "task_completion_rate": completion_rate,
        "type_distribution": type_dist,
        "bottlenecks": bottlenecks,
        "task_assignees": dict(sorted(task_assignees.items(), key=lambda x: -x[1])[:8]),
        "common_tasks": common_tasks,
    }


def _analyse_proposed_dod(
    dod_signal: object,
    task_decomp: dict,
    delivery_stories: list[dict],
) -> dict:
    """Generate a proposed Definition of Done by cross-referencing multiple signals.

    Scans stories for 8 quality practices using comment/description text,
    subtask patterns, and subtask completion rates. Each practice gets a
    status: established (strong evidence), emerging (some evidence), or
    missing (no evidence found).
    """
    items: list[dict] = []
    n = len(delivery_stories) or 1

    # ── Scan stories for additional signal percentages ─────────────
    doc_count = 0
    ac_count = 0
    security_count = 0
    perf_count = 0
    monitoring_count = 0
    for s in delivery_stories:
        text = (s.get("description", "") or "") + " " + " ".join(s.get("comments", []))
        if _DOC_RE.search(text):
            doc_count += 1
        if _AC_VERIFY_RE.search(text):
            ac_count += 1
        if _SECURITY_RE.search(text):
            security_count += 1
        if _PERF_RE.search(text):
            perf_count += 1
        if _MONITORING_RE.search(text):
            monitoring_count += 1

    extra_signals = {
        "Documentation": round(doc_count / n * 100, 1),
        "AC verification": round(ac_count / n * 100, 1),
        "Security review": round(security_count / n * 100, 1),
        "Performance testing": round(perf_count / n * 100, 1),
        "Monitoring & observability": round(monitoring_count / n * 100, 1),
    }

    # ── Subtask analysis data ──────────────────────────────────────
    type_dist = task_decomp.get("type_distribution", {}) if task_decomp else {}
    overall_completion = task_decomp.get("task_completion_rate", 100) if task_decomp else 100
    type_completion: dict[str, float] = {}
    bottlenecks = task_decomp.get("bottlenecks", []) if task_decomp else []
    for cat, rate, _count in bottlenecks:
        type_completion[cat] = rate

    # ── All practices to evaluate ──────────────────────────────────
    # Each practice has: name, mention_pct (from regex), subtask_cat (if applicable)
    practices = [
        ("Code review", getattr(dod_signal, "stories_with_review_mention_pct", 0), "Review"),
        ("PR/merge request linked", getattr(dod_signal, "stories_with_pr_link_pct", 0), None),
        ("Testing & QA", getattr(dod_signal, "stories_with_testing_mention_pct", 0), "Testing"),
        ("Deployment verified", getattr(dod_signal, "stories_with_deploy_mention_pct", 0), "Deploy"),
        ("Documentation updated", extra_signals["Documentation"], "Documentation"),
        ("AC verification", extra_signals["AC verification"], None),
        ("Monitoring & observability", extra_signals["Monitoring & observability"], None),
    ]

    # Only include security/perf if there's any evidence (avoid noise for non-applicable teams)
    if extra_signals["Security review"] > 0:
        practices.append(("Security review", extra_signals["Security review"], None))
    if extra_signals["Performance testing"] > 0:
        practices.append(("Performance testing", extra_signals["Performance testing"], None))

    for name, mention_pct, sub_cat in practices:
        task_pct = type_dist.get(sub_cat, 0) if sub_cat else 0
        task_comp = type_completion.get(sub_cat, overall_completion) if sub_cat else 0
        evidence = max(mention_pct, task_pct)

        if evidence >= 40 or (mention_pct >= 20 and task_pct >= 10):
            status = "established"
        elif evidence >= 5:
            status = "emerging"
        else:
            status = "missing"

        signals: list[str] = []
        if mention_pct > 0:
            signals.append(f"{mention_pct:.0f}% mentioned in stories")
        if task_pct > 0:
            comp_note = f", {task_comp:.0f}% completed" if task_comp < 70 else ""
            signals.append(f"{task_pct}% have subtasks{comp_note}")

        if status == "established":
            rec = "Consistently done. Include as required DoD step."
        elif status == "emerging":
            if mention_pct > 0 and task_pct > 0:
                rec = "Done sometimes. Formalise as a required step."
            elif mention_pct > 0:
                rec = "Mentioned but no subtask. Add explicit task."
            else:
                rec = "Subtasks exist but inconsistent. Standardise."
        else:
            rec = "No evidence found. Evaluate if the team should adopt this."

        items.append(
            {
                "practice": name,
                "coverage_pct": round(mention_pct, 1),
                "task_pct": task_pct,
                "task_completion_pct": round(task_comp, 1),
                "status": status,
                "signals": " · ".join(signals) if signals else "no evidence",
                "recommendation": rec,
            }
        )

    # Add subtask-only practices not already covered
    covered_cats = {sub_cat for _, _, sub_cat in practices if sub_cat}
    extra_subtask_map = {"Design": "Design review"}
    for sub_cat, practice_name in extra_subtask_map.items():
        if sub_cat in covered_cats:
            continue
        task_pct = type_dist.get(sub_cat, 0)
        if task_pct >= 5:
            task_comp = type_completion.get(sub_cat, overall_completion)
            status = "established" if task_pct >= 20 and task_comp >= 70 else "emerging"
            items.append(
                {
                    "practice": practice_name,
                    "coverage_pct": 0,
                    "task_pct": task_pct,
                    "task_completion_pct": round(task_comp, 1),
                    "status": status,
                    "signals": f"{task_pct}% of stories have subtasks",
                    "recommendation": f"Team creates {practice_name.lower()} tasks in {task_pct}% of stories.",
                }
            )

    # Sort: established → emerging → missing, then by evidence strength
    status_order = {"established": 0, "emerging": 1, "missing": 2}
    items.sort(key=lambda x: (status_order.get(x["status"], 3), -x["coverage_pct"]))

    established = sum(1 for i in items if i["status"] == "established")
    emerging = sum(1 for i in items if i["status"] == "emerging")
    total = len(items)

    if established >= 3:
        health = "strong"
        summary = (
            f"{established} of {total} practices are well-established. "
            "The team has a clear, consistent definition of done."
        )
    elif established + emerging >= 3:
        health = "moderate"
        parts = []
        if established:
            parts.append(f"{established} established")
        if emerging:
            parts.append(f"{emerging} emerging")
        summary = (
            f"{', '.join(parts)} out of {total} practices. "
            "The team has some quality gates but they're inconsistently applied."
        )
    else:
        health = "weak"
        summary = (
            f"Only {established + emerging} of {total} practices show any evidence. "
            "The team would benefit from defining explicit done criteria."
        )

    # ── DoD ordering: detect typical sequence from timestamped comments ──
    # For each story with timestamped comments, record when each DoD signal
    # FIRST appears. Then aggregate the typical order across all stories.
    signal_regexes = {
        "Code review": _REVIEW_RE,
        "PR linked": _PR_RE,
        "Testing": _TEST_RE,
        "Deployment": _DEPLOY_RE,
    }
    order_counts: dict[tuple[str, str], int] = defaultdict(int)  # (A, B) → A before B count
    for s in delivery_stories:
        timed = s.get("comments_timed", [])
        if not timed:
            continue
        # Find first occurrence timestamp for each signal
        first_seen: dict[str, str] = {}
        for c_date, body in timed:
            if not c_date:
                continue
            for sig_name, sig_re in signal_regexes.items():
                if sig_name not in first_seen and sig_re.search(body):
                    first_seen[sig_name] = c_date
        # Record pairwise ordering
        seen_list = sorted(first_seen.items(), key=lambda x: x[1])
        for i, (a, _) in enumerate(seen_list):
            for b, _ in seen_list[i + 1 :]:
                order_counts[(a, b)] += 1

    # Build ordering: find the most common sequence
    ordering: list[str] = []
    if order_counts:
        # Score each signal by how often it appears first in pairs
        first_score: dict[str, int] = defaultdict(int)
        for (a, _b), cnt in order_counts.items():
            first_score[a] += cnt
        ordering = [
            name
            for name, _ in sorted(first_score.items(), key=lambda x: -x[1])
            if any(i["status"] != "missing" for i in items if i["practice"].startswith(name[:6]))
        ]

    # ── Custom DoD fields: detect team-specific subtask patterns ──────
    # Look for recurring subtask titles that don't match standard categories
    # but appear in 20%+ of stories — these are team-specific DoD steps.
    custom_steps: list[dict] = []
    if task_decomp and task_decomp.get("common_tasks"):
        total_with_tasks = task_decomp.get("stories_with_tasks", 1) or 1
        standard_keywords = {
            "test",
            "qa",
            "verify",
            "review",
            "pr",
            "code review",
            "deploy",
            "release",
            "staging",
            "design",
            "ux",
            "doc",
            "documentation",
            "readme",
            "development",
            "implement",
            "build",
            "create",
            "update",
        }
        for title, count in task_decomp["common_tasks"]:
            title_lower = title.lower()
            # Skip if it matches a standard category
            if any(kw in title_lower for kw in standard_keywords):
                continue
            pct = round(count / total_with_tasks * 100)
            if pct >= 15 or count >= 3:
                custom_steps.append(
                    {
                        "title": title,
                        "count": count,
                        "pct": pct,
                    }
                )

    return {
        "items": items,
        "summary": summary,
        "health": health,
        "ordering": ordering,
        "custom_steps": custom_steps,
    }


def _analyse_acceptance_criteria(delivery_stories: list[dict]) -> dict:
    """Analyse acceptance criteria patterns across stories.

    Scans story descriptions to detect:
    - AC content themes (error handling, validation, edge cases, performance, etc.)
    - AC coverage by discipline (do frontend stories have more ACs?)
    - AC specificity (vague vs precise language)
    - Correlation between AC count and spillover (fewer ACs → more spills?)

    Returns a dict for the examples store and TUI/export rendering.
    """
    if not delivery_stories:
        return {}

    # ── AC content themes ──────────────────────────────────────────
    # Scan description text for common AC topic patterns
    theme_regexes = {
        "Error handling": re.compile(
            r"\b(error|exception|fail|invalid|reject|timeout|retry|fallback)\b",
            re.IGNORECASE,
        ),
        "Validation": re.compile(
            r"\b(validat|required\s*field|input\s*check|format\s*check|constraint|boundary)\b",
            re.IGNORECASE,
        ),
        "Edge cases": re.compile(
            r"\b(edge\s*case|empty|null|zero|negative|overflow|concurrent|race\s*condition)\b",
            re.IGNORECASE,
        ),
        "Performance": re.compile(
            r"\b(performance|latency|throughput|response\s*time|load|scalab|cache|optimi[sz])\b",
            re.IGNORECASE,
        ),
        "Security": re.compile(
            r"\b(auth|permission|role|encrypt|token|secure|RBAC|CORS|XSS|injection)\b",
            re.IGNORECASE,
        ),
        "User experience": re.compile(
            r"\b(user\s*(can|should|sees|is\s*shown)|display|responsive|accessible|usab)\b",
            re.IGNORECASE,
        ),
        "Integration": re.compile(
            r"\b(API|endpoint|webhook|third.party|external\s*service|upstream|downstream)\b",
            re.IGNORECASE,
        ),
        "Data": re.compile(
            r"\b(database|migration|schema|storage|persist|CRUD|query|index)\b",
            re.IGNORECASE,
        ),
    }

    theme_counts: dict[str, int] = defaultdict(int)
    theme_examples: dict[str, dict] = {}  # theme → first example story {issue_key, issue_url, summary}
    stories_with_ac = [s for s in delivery_stories if s.get("ac_count", 0) > 0]
    n_with_ac = len(stories_with_ac) or 1

    for s in stories_with_ac:
        # Prefer LLM-parsed AC items for theme detection (more precise)
        parsed_ac = s.get("parsed_ac", [])
        if parsed_ac:
            ac_text = " ".join(parsed_ac)
        else:
            ac_text = re.sub(r"<[^>]+>", " ", s.get("description", "") or "")
        for theme, regex in theme_regexes.items():
            if regex.search(ac_text):
                theme_counts[theme] += 1
                if theme not in theme_examples and s.get("issue_key"):
                    theme_examples[theme] = {
                        "issue_key": s.get("issue_key", ""),
                        "issue_url": s.get("issue_url", ""),
                        "summary": (s.get("summary", "") or "")[:40],
                    }

    theme_pcts = {t: round(c / n_with_ac * 100) for t, c in sorted(theme_counts.items(), key=lambda x: -x[1]) if c > 0}

    # ── AC by discipline ───────────────────────────────────────────
    disc_ac: dict[str, list[int]] = defaultdict(list)
    for s in delivery_stories:
        ac = s.get("ac_count", 0)
        disc = s.get("discipline", "fullstack")
        disc_ac[disc].append(ac)

    ac_by_discipline = {}
    for disc, counts in sorted(disc_ac.items()):
        if len(counts) >= 3:
            avg = round(sum(counts) / len(counts), 1)
            with_ac = sum(1 for c in counts if c > 0)
            pct = round(with_ac / len(counts) * 100)
            ac_by_discipline[disc] = {
                "avg_ac": avg,
                "stories_with_ac_pct": pct,
                "sample": len(counts),
            }

    # ── AC specificity ─────────────────────────────────────────────
    # Vague: "it should work", "everything is correct", "as expected"
    # Precise: contains numbers, status codes, specific field names, measurable criteria
    _vague_re = re.compile(
        r"\b(should\s*work|as\s*expected|correct(ly)?|proper(ly)?|appropriate(ly)?|no\s*issues)\b",
        re.IGNORECASE,
    )
    _precise_re = re.compile(
        r"(\b\d{3}\b|returns?\s*\d|timeout\s*\d|\d+\s*(ms|seconds?|minutes?|MB|GB)|"
        r"field\s*['\"]?\w+['\"]?|endpoint\s*/\w|status\s*code|JSON|HTTP\s*\d|"
        r"must\s*(not\s*)?exceed|at\s*least\s*\d|no\s*more\s*than\s*\d)",
        re.IGNORECASE,
    )

    vague_count = 0
    precise_count = 0
    for s in stories_with_ac:
        # Prefer LLM specificity classification if available
        llm_spec = s.get("ac_specificity", "")
        if llm_spec in ("precise", "moderate", "vague"):
            if llm_spec == "precise":
                precise_count += 1
            elif llm_spec == "vague":
                vague_count += 1
            continue
        # Fallback to regex
        desc = re.sub(r"<[^>]+>", " ", s.get("description", "") or "")
        has_vague = bool(_vague_re.search(desc))
        has_precise = bool(_precise_re.search(desc))
        if has_precise:
            precise_count += 1
        elif has_vague:
            vague_count += 1

    specificity_pct = round(precise_count / n_with_ac * 100) if n_with_ac else 0
    vague_pct = round(vague_count / n_with_ac * 100) if n_with_ac else 0

    if specificity_pct >= 40:
        specificity_label = "precise"
    elif specificity_pct >= 15:
        specificity_label = "moderate"
    else:
        specificity_label = "vague"

    # ── AC count vs spillover correlation ──────────────────────────
    # Do stories with fewer ACs spill more often?
    low_ac_stories = [s for s in delivery_stories if s.get("ac_count", 0) <= 1]
    high_ac_stories = [s for s in delivery_stories if s.get("ac_count", 0) >= 3]
    low_ac_spill = (
        round(sum(1 for s in low_ac_stories if s.get("carried_over")) / len(low_ac_stories) * 100)
        if low_ac_stories
        else 0
    )
    high_ac_spill = (
        round(sum(1 for s in high_ac_stories if s.get("carried_over")) / len(high_ac_stories) * 100)
        if high_ac_stories
        else 0
    )

    # ── Summary ────────────────────────────────────────────────────
    total = len(delivery_stories)
    with_ac_pct = round(len(stories_with_ac) / total * 100) if total else 0
    median_ac = statistics.median([s.get("ac_count", 0) for s in stories_with_ac]) if stories_with_ac else 0

    # Build a single consolidated recommendation (not multiple separate ones)
    issues: list[str] = []
    if with_ac_pct < 50:
        issues.append(f"only {with_ac_pct}% of stories have ACs")
    if specificity_label == "vague" and vague_pct > 0:
        issues.append(f"{vague_pct}% use vague language instead of measurable criteria")
    if low_ac_spill > high_ac_spill + 10 and len(low_ac_stories) >= 5:
        issues.append(f"stories with fewer ACs spill {low_ac_spill}% vs {high_ac_spill}% for detailed ones")
    missing_themes = [t for t in ("Error handling", "Validation", "Edge cases") if t not in theme_pcts]
    if missing_themes:
        issues.append(f"ACs rarely cover {', '.join(missing_themes).lower()}")

    recommendation = ""
    if issues:
        recommendation = ". ".join(issues).capitalize() + "."

    return {
        "stories_with_ac_pct": with_ac_pct,
        "median_ac": median_ac,
        "themes": theme_pcts,
        "theme_examples": theme_examples,
        "by_discipline": ac_by_discipline,
        "specificity": {
            "label": specificity_label,
            "precise_pct": specificity_pct,
            "vague_pct": vague_pct,
        },
        "spillover_correlation": {
            "low_ac_spill_pct": low_ac_spill,
            "high_ac_spill_pct": high_ac_spill,
            "low_ac_count": len(low_ac_stories),
            "high_ac_count": len(high_ac_stories),
        },
        "recommendation": recommendation,
    }


def _analyse_repositories(delivery_stories: list[dict]) -> dict:
    """Extract and analyse repository patterns from story-linked repos.

    Repo slugs come from ticket text, AzDO work item relations, Jira dev-status,
    and optional Git PR scans. Returns counts, spillover correlation, optional
    avg cycle times, and human-readable ``detection_sources`` for the TUI.
    """
    repo_counter: dict[str, int] = defaultdict(int)
    repo_by_pts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    repo_by_discipline: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    repo_spillover: dict[str, int] = defaultdict(int)
    repo_cycle_times: dict[str, list[float]] = defaultdict(list)

    for s in delivery_stories:
        repos = s.get("repos", [])
        if not repos:
            continue
        pts = int(_safe_float(s.get("points", 0)))
        disc = s.get("discipline", "fullstack")
        ct = s.get("cycle_time_days")
        is_spill = s.get("carried_over", False)

        for repo in repos:
            repo_counter[repo] += 1
            if pts in (1, 2, 3, 5, 8):
                repo_by_pts[pts][repo] += 1
            repo_by_discipline[disc][repo] += 1
            if is_spill:
                repo_spillover[repo] += 1
            if ct is not None:
                repo_cycle_times[repo].append(ct)

    if not repo_counter:
        return {}

    total = sum(repo_counter.values())

    # Top repos overall
    top_repos = [
        {"repo": repo, "stories": cnt, "pct": round(cnt / total * 100)}
        for repo, cnt in sorted(repo_counter.items(), key=lambda x: -x[1])[:10]
    ]

    # Per-point-value: which repos appear most for each story size
    by_pts = {}
    for pts, repos in repo_by_pts.items():
        top = sorted(repos.items(), key=lambda x: -x[1])[:3]
        by_pts[pts] = [r for r, _ in top]

    # Spillover-prone repos (appear in carried-over stories)
    spillover_repos = [
        {"repo": repo, "spills": cnt, "spill_rate": round(cnt / repo_counter[repo] * 100)}
        for repo, cnt in sorted(repo_spillover.items(), key=lambda x: -x[1])[:5]
        if repo_counter[repo] >= 3
    ]

    # Avg cycle time per repo
    repo_avg_ct = {}
    for repo, cts in repo_cycle_times.items():
        if cts and repo_counter[repo] >= 3:
            repo_avg_ct[repo] = round(sum(cts) / len(cts), 1)

    logger.info(
        "Repository analysis: %d unique repos found across %d stories",
        len(repo_counter),
        len(delivery_stories),
    )
    for repo, cnt in sorted(repo_counter.items(), key=lambda x: -x[1])[:5]:
        logger.debug("  %s: %d stories", repo, cnt)

    source_tags: set[str] = set()
    for s in delivery_stories:
        if not s.get("repos"):
            continue
        rs_src = s.get("repo_sources")
        if rs_src:
            for t in rs_src:
                source_tags.add(str(t))
        else:
            source_tags.add("legacy_text")

    source_labels = (
        ("jira_text", "ticket text and comments"),
        ("description", "work item description and AC"),
        ("azdo_work_item_links", "linked PRs/commits on work items"),
        ("jira_development", "Jira Development panel"),
        ("azdo_pr_work_items", "Azure DevOps PR ↔ work item lookup"),
        ("legacy_text", "PR links in ticket text"),
    )
    detection_sources = [lbl for key, lbl in source_labels if key in source_tags]

    return {
        "top_repos": top_repos,
        "by_pts": {str(k): v for k, v in by_pts.items()},
        "spillover_repos": spillover_repos,
        "repo_avg_cycle_time": repo_avg_ct,
        "total_repos": len(repo_counter),
        "stories_with_repos": sum(1 for s in delivery_stories if s.get("repos")),
        "detection_sources": detection_sources,
    }


def _collect_examples(
    all_stories: list[dict],
    sprint_data: list[dict],
) -> dict[str, list[dict]]:
    """Pick illustrative example stories for each metric category.

    Returns a dict keyed by section name, each value a list of example dicts
    with keys: issue_key, issue_url, summary, detail.
    Max 3 examples per category to keep the display concise.
    """

    def _ex(s: dict, detail: str = "") -> dict:
        return {
            "issue_key": s.get("issue_key", ""),
            "issue_url": s.get("issue_url", ""),
            "summary": (s.get("summary", "") or "")[:60],
            "detail": detail,
        }

    examples: dict[str, list[dict]] = {}

    # Per point-value: pick diverse representative stories that show
    # what this point value typically means for the team.
    by_pts: dict[int, list[dict]] = defaultdict(list)
    for s in all_stories:
        pts = int(_safe_float(s.get("points", 0)))
        if pts in (1, 2, 3, 5, 8) and s.get("issue_key"):
            by_pts[pts].append(s)

    for pts in (1, 2, 3, 5, 8):
        stories_at = by_pts.get(pts, [])
        if not stories_at:
            continue

        # Pick up to 4 diverse stories: spread across different action types
        seen_categories: set[str] = set()
        picked: list[dict] = []
        for s in stories_at:
            title_lower = (s.get("summary", "") or "").lower()
            cat = "other"
            for keyword, category in _ACTION_VERBS.items():
                if keyword in title_lower:
                    cat = category
                    break
            if cat not in seen_categories or len(picked) < 2:
                seen_categories.add(cat)
                tc = s.get("task_count", 0)
                detail_parts = []
                if tc:
                    detail_parts.append(f"{tc} tasks")
                ct = s.get("cycle_time_days")
                if ct is not None:
                    detail_parts.append(f"{ct:.0f}d")
                picked.append(_ex(s, ", ".join(detail_parts)))
            if len(picked) >= 4:
                break
        examples[f"calibration_{pts}pt"] = picked

    # Spillover examples
    carried = [s for s in all_stories if s.get("carried_over") and s.get("issue_key")]
    if carried:
        examples["spillover"] = [_ex(s, f"from {s.get('sprint_name', '?')}") for s in carried[:3]]

    # DoD signal examples: stories with PR/review/test mentions
    for label, regex in [
        ("dod_pr", _PR_RE),
        ("dod_review", _REVIEW_RE),
        ("dod_testing", _TEST_RE),
        ("dod_deploy", _DEPLOY_RE),
    ]:
        matches = []
        for s in all_stories:
            if not s.get("issue_key"):
                continue
            text = (s.get("description", "") or "") + " " + " ".join(s.get("comments", []))
            if regex.search(text):
                matches.append(s)
            if len(matches) >= 2:
                break
        if matches:
            examples[label] = [_ex(s) for s in matches[:2]]

    return examples


# ---------------------------------------------------------------------------
# Tool: analyze_team_history
# ---------------------------------------------------------------------------


@tool
def analyze_team_history(
    project_key: str = "",
    source: str = "",
    sprint_count: int = 8,
) -> str:
    """Analyse the team's historical sprint data to build a calibration profile.

    Pulls data from the last 6-10 closed sprints in Jira or Azure DevOps.
    Computes: cycle time per point value, story shape patterns by discipline,
    epic sizing norms, velocity variance, and estimation accuracy.

    The result is a JSON object that can be stored as a TeamProfile for
    future plan calibration. Use this before planning to learn how the team
    actually works — what a 5-point story really means, how often estimates
    overshoot, and typical story shapes by discipline.

    project_key: Jira project key (e.g. "PROJ") or AzDO project name.
    source: "jira" or "azdevops". Auto-detected if only one is configured.
    sprint_count: Number of closed sprints to analyse (default 8, range 3-12).
    """
    # See README: "Tools" — tool types, risk levels
    logger.debug(
        "analyze_team_history called: project_key=%r, source=%r, sprint_count=%d", project_key, source, sprint_count
    )

    sprint_count = max(3, min(12, sprint_count))

    # Auto-detect source if not specified
    if not source:
        source = _detect_source()
        if not source:
            return json.dumps({"error": "Cannot detect tracker. Set source='jira' or source='azdevops'."})

    try:
        if source == "jira":
            sprint_data = _fetch_jira_history(project_key, sprint_count)
        elif source == "azdevops":
            sprint_data = _fetch_azdevops_history(project_key, sprint_count)
        else:
            return json.dumps({"error": f"Unknown source: {source}. Use 'jira' or 'azdevops'."})
    except Exception as e:
        logger.error("Error fetching team history: %s", e)
        return json.dumps({"error": f"Failed to fetch history: {e}"})

    if not sprint_data:
        return json.dumps({"error": "No closed sprints found — cannot build team profile."})

    profile = _build_profile_from_sprint_data(source, project_key or "unknown", sprint_data)

    from dataclasses import asdict

    return json.dumps(asdict(profile), ensure_ascii=False)


def _detect_source() -> str:
    """Auto-detect which tracker is configured."""
    try:
        from scrum_agent.config import get_jira_base_url, get_jira_token

        if get_jira_base_url() and get_jira_token():
            return "jira"
    except Exception:
        pass
    try:
        from scrum_agent.config import get_azure_devops_org_url, get_azure_devops_token

        if get_azure_devops_org_url() and get_azure_devops_token():
            return "azdevops"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Daily sprint scope timeline builders
# ---------------------------------------------------------------------------


def _date_range(start: datetime, end: datetime) -> list[str]:
    """Return list of ISO date strings for each day from start to end (inclusive)."""
    days: list[str] = []
    cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= end_day:
        days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def _normalize_iter_path(path: str) -> str:
    """Strip leading backslash from AzDO iteration path for consistent comparison.

    The SDK's ``TeamSettingsIteration.path`` returns paths like
    ``\\Project\\Sprint 1``, but ``System.IterationPath`` in work item fields
    uses ``Project\\Sprint 1`` (no leading backslash).
    """
    return path.lstrip("\\") if path else ""


def _build_azdo_sprint_scope_timeline(
    wit_client: object,
    project: str,
    stories: list[dict],
    iter_path: str,
    iter_start: str,
    iter_end: str,
    completed_pts: float,
) -> SprintScopeTimeline | None:
    """Walk AzDO work item revisions to build a day-by-day scope timeline.

    For each story currently in the sprint, fetches revisions and records:
    - When it entered this iteration (date + points at that time)
    - When its points changed (re-estimation events)
    - The daily scope total for every day of the sprint
    """
    s_dt = _parse_date(iter_start)
    e_dt = _parse_date(iter_end)
    if not s_dt or not e_dt:
        return None

    days = _date_range(s_dt, e_dt)
    if not days:
        return None

    # Normalize iter_path for comparison with System.IterationPath
    iter_path = _normalize_iter_path(iter_path)

    # Per-day scope map: date → {issue_key: points}
    # Start with empty — we'll reconstruct from revisions
    day_scope: dict[str, dict[str, float]] = {d: {} for d in days}
    change_events: list[ScopeChangeEvent] = []

    for story in stories:
        if story.get("is_recurring"):
            continue  # exclude ceremony/KTLO tickets from scope tracking
        wi_id_str = story.get("issue_key", "")
        if not wi_id_str:
            continue
        try:
            wi_id = int(wi_id_str)
        except (ValueError, TypeError):
            continue

        summary = (story.get("summary", "") or "")[:60]
        issue_url = story.get("issue_url", "")

        try:
            revisions = wit_client.get_revisions(wi_id, project=project)  # type: ignore[union-attr]
            if not revisions:
                continue
        except Exception:
            continue

        # Walk revisions to build a timeline of (date, in_iteration, points)
        # Each entry: (date_str, in_this_iter: bool, points: float)
        wi_timeline: list[tuple[str, bool, float]] = []
        for rev in revisions:
            fields = rev.fields if hasattr(rev, "fields") else {}
            rev_dt = _parse_date(fields.get("System.ChangedDate", ""))
            if not rev_dt:
                continue
            cur_iter = fields.get("System.IterationPath", "") or ""
            cur_pts = _safe_float(fields.get("Microsoft.VSTS.Scheduling.StoryPoints"))
            in_iter = bool(iter_path and iter_path in cur_iter)
            rev_day = rev_dt.strftime("%Y-%m-%d")
            wi_timeline.append((rev_day, in_iter, cur_pts))

        if not wi_timeline:
            continue

        # For each sprint day, determine if the story was in scope and its points.
        # Walk the timeline chronologically, carrying forward the last known state.
        last_in_iter = False
        last_pts = 0.0
        ti = 0  # timeline index

        for day in days:
            # Advance through revisions up to and including this day
            while ti < len(wi_timeline) and wi_timeline[ti][0] <= day:
                _, last_in_iter, last_pts = wi_timeline[ti]
                ti += 1

            if last_in_iter and last_pts > 0:
                day_scope[day][wi_id_str] = last_pts

        # Detect change events by comparing consecutive days
        prev_in = False
        prev_pts_val = 0.0
        for day in days:
            cur_in = wi_id_str in day_scope[day]
            cur_pts_val = day_scope[day].get(wi_id_str, 0.0)

            if cur_in and not prev_in and cur_pts_val > 0:
                # Story entered scope
                if day != days[0]:
                    change_events.append(
                        ScopeChangeEvent(
                            date=day,
                            issue_key=wi_id_str,
                            issue_url=issue_url,
                            summary=summary,
                            change_type="added",
                            from_pts=0.0,
                            to_pts=cur_pts_val,
                            delta_pts=cur_pts_val,
                        )
                    )
            elif not cur_in and prev_in:
                # Story left scope
                change_events.append(
                    ScopeChangeEvent(
                        date=day,
                        issue_key=wi_id_str,
                        issue_url=issue_url,
                        summary=summary,
                        change_type="removed",
                        from_pts=prev_pts_val,
                        to_pts=0.0,
                        delta_pts=-prev_pts_val,
                    )
                )
            elif cur_in and prev_in and cur_pts_val != prev_pts_val:
                # Re-estimation
                delta = cur_pts_val - prev_pts_val
                ctype = "re_estimated_up" if delta > 0 else "re_estimated_down"
                change_events.append(
                    ScopeChangeEvent(
                        date=day,
                        issue_key=wi_id_str,
                        issue_url=issue_url,
                        summary=summary,
                        change_type=ctype,
                        from_pts=prev_pts_val,
                        to_pts=cur_pts_val,
                        delta_pts=delta,
                    )
                )

            prev_in = cur_in
            prev_pts_val = cur_pts_val

    # Build daily snapshots
    snapshots: list[DailyScopeSnapshot] = []
    for day in days:
        items = day_scope[day]
        total = sum(items.values())
        snapshots.append(
            DailyScopeSnapshot(
                date=day,
                total_scope_pts=total,
                stories_in_sprint=tuple(sorted(items.items())),
            )
        )

    committed = snapshots[0].total_scope_pts if snapshots else 0.0
    final = snapshots[-1].total_scope_pts if snapshots else 0.0
    abs_churn = sum(abs(e.delta_pts) for e in change_events)
    churn_rate = round(abs_churn / committed, 2) if committed > 0 else 0.0

    # Sort events by date
    change_events.sort(key=lambda e: e.date)

    return SprintScopeTimeline(
        sprint_name=story.get("sprint_name", "") if stories else "",
        committed_pts=committed,
        final_pts=final,
        delivered_pts=completed_pts,
        scope_change_total=round(final - committed, 1),
        scope_churn=churn_rate,
        daily_snapshots=tuple(snapshots),
        change_events=tuple(change_events),
    )


def _build_jira_sprint_scope_timeline(
    jira_client: object,
    stories: list[dict],
    sprint_name: str,
    sprint_start: str,
    sprint_end: str,
    completed_pts: float,
) -> SprintScopeTimeline | None:
    """Walk Jira changelogs to build a day-by-day scope timeline for a sprint.

    For each story, fetches the changelog and reconstructs when it entered/left
    the sprint and when its points changed.
    """
    s_dt = _parse_date(sprint_start)
    e_dt = _parse_date(sprint_end)
    if not s_dt or not e_dt:
        return None

    days = _date_range(s_dt, e_dt)
    if not days:
        return None

    day_scope: dict[str, dict[str, float]] = {d: {} for d in days}
    change_events: list[ScopeChangeEvent] = []

    for story in stories:
        if story.get("is_recurring"):
            continue  # exclude ceremony/KTLO tickets from scope tracking
        issue_key = story.get("issue_key", "")
        if not issue_key:
            continue
        summary = (story.get("summary", "") or "")[:60]
        issue_url = story.get("issue_url", "")

        try:
            issue = jira_client.issue(issue_key, expand="changelog")  # type: ignore[union-attr]
            changelog = getattr(issue, "changelog", None)
        except Exception:
            # Fallback: use story data as-is, assume in scope for full sprint
            pts = _safe_float(story.get("points", 0))
            if pts > 0:
                for day in days:
                    day_scope[day][issue_key] = pts
            continue

        # Build timeline of (date, in_sprint, points) from changelog
        # Start with the story's current points and assume it was in sprint initially
        cur_pts = _safe_float(story.get("points", 0))
        created_raw = getattr(issue.fields, "created", None) or ""
        created_dt = _parse_date(created_raw)

        # Build revision events: list of (datetime, field, from_val, to_val)
        revs: list[tuple[datetime, str, str, str]] = []
        if changelog:
            for history in getattr(changelog, "histories", []):
                change_dt = _parse_date(getattr(history, "created", ""))
                if not change_dt:
                    continue
                for item in getattr(history, "items", []):
                    field_name = getattr(item, "field", "")
                    from_val = getattr(item, "fromString", "") or ""
                    to_val = getattr(item, "toString", "") or ""
                    if field_name in ("Story Points", "story_points", "Sprint"):
                        revs.append((change_dt, field_name, from_val, to_val))

        revs.sort(key=lambda r: r[0])

        # Walk backwards from current state to reconstruct initial state at sprint start
        pts_at_start = cur_pts
        in_sprint_at_start = True
        for rev_dt, field_name, from_val, to_val in reversed(revs):
            if rev_dt <= s_dt:
                break
            if field_name in ("Story Points", "story_points"):
                pts_at_start = _safe_float(from_val)
            if field_name == "Sprint":
                # If sprint was added during the sprint window, it wasn't there at start
                if sprint_name in to_val and sprint_name not in from_val and rev_dt > s_dt:
                    in_sprint_at_start = False

        # If story was created after sprint start, it wasn't in scope at start
        if created_dt and created_dt > s_dt:
            in_sprint_at_start = False

        # Now walk forward through days, applying changes
        tracking_pts = pts_at_start
        tracking_in = in_sprint_at_start
        rev_idx = 0

        for day in days:
            # Apply revisions for this day
            while rev_idx < len(revs):
                rev_dt, field_name, from_val, to_val = revs[rev_idx]
                rev_day = rev_dt.strftime("%Y-%m-%d")
                if rev_day > day:
                    break
                if field_name in ("Story Points", "story_points"):
                    tracking_pts = _safe_float(to_val)
                if field_name == "Sprint":
                    if sprint_name in to_val and sprint_name not in from_val:
                        tracking_in = True
                    elif sprint_name not in to_val and sprint_name in from_val:
                        tracking_in = False
                rev_idx += 1

            if tracking_in and tracking_pts > 0:
                day_scope[day][issue_key] = tracking_pts

        # Detect change events by comparing consecutive days
        prev_in = False
        prev_pts_val = 0.0
        for day in days:
            cur_in = issue_key in day_scope[day]
            cur_pts_val = day_scope[day].get(issue_key, 0.0)

            if cur_in and not prev_in and cur_pts_val > 0:
                if day != days[0]:
                    change_events.append(
                        ScopeChangeEvent(
                            date=day,
                            issue_key=issue_key,
                            issue_url=issue_url,
                            summary=summary,
                            change_type="added",
                            from_pts=0.0,
                            to_pts=cur_pts_val,
                            delta_pts=cur_pts_val,
                        )
                    )
            elif not cur_in and prev_in:
                change_events.append(
                    ScopeChangeEvent(
                        date=day,
                        issue_key=issue_key,
                        issue_url=issue_url,
                        summary=summary,
                        change_type="removed",
                        from_pts=prev_pts_val,
                        to_pts=0.0,
                        delta_pts=-prev_pts_val,
                    )
                )
            elif cur_in and prev_in and cur_pts_val != prev_pts_val:
                delta = cur_pts_val - prev_pts_val
                ctype = "re_estimated_up" if delta > 0 else "re_estimated_down"
                change_events.append(
                    ScopeChangeEvent(
                        date=day,
                        issue_key=issue_key,
                        issue_url=issue_url,
                        summary=summary,
                        change_type=ctype,
                        from_pts=prev_pts_val,
                        to_pts=cur_pts_val,
                        delta_pts=delta,
                    )
                )

            prev_in = cur_in
            prev_pts_val = cur_pts_val

    # Build daily snapshots
    snapshots: list[DailyScopeSnapshot] = []
    for day in days:
        items = day_scope[day]
        total = sum(items.values())
        snapshots.append(
            DailyScopeSnapshot(
                date=day,
                total_scope_pts=total,
                stories_in_sprint=tuple(sorted(items.items())),
            )
        )

    committed = snapshots[0].total_scope_pts if snapshots else 0.0
    final = snapshots[-1].total_scope_pts if snapshots else 0.0
    abs_churn = sum(abs(e.delta_pts) for e in change_events)
    churn_rate = round(abs_churn / committed, 2) if committed > 0 else 0.0

    change_events.sort(key=lambda e: e.date)

    return SprintScopeTimeline(
        sprint_name=sprint_name,
        committed_pts=committed,
        final_pts=final,
        delivered_pts=completed_pts,
        scope_change_total=round(final - committed, 1),
        scope_churn=churn_rate,
        daily_snapshots=tuple(snapshots),
        change_events=tuple(change_events),
    )


# ---------------------------------------------------------------------------
# Mid-sprint scope change detection helpers (legacy — kept for enrichment)
# ---------------------------------------------------------------------------


def _enrich_jira_scope_changes(
    jira_client: object,
    stories: list[dict],
    sprint_name: str,
    sprint_start: str,
    sprint_end: str,
) -> None:
    """Enrich story dicts with mid-sprint scope change data from Jira changelog.

    For each story, fetches the issue changelog and detects:
    - point_changed: True if story points were modified during the sprint
    - original_points: points value at sprint start (before any mid-sprint change)
    - added_mid_sprint: True if the story was added to this sprint after it started
    - point_changes: list of {date, from_pts, to_pts} dicts
    """
    s_dt = _parse_date(sprint_start)
    e_dt = _parse_date(sprint_end)
    if not s_dt:
        return

    for story in stories:
        issue_key = story.get("issue_key", "")
        if not issue_key:
            continue

        try:
            # Fetch issue with changelog expanded
            issue = jira_client.issue(issue_key, expand="changelog")
            changelog = getattr(issue, "changelog", None)
            if not changelog:
                continue

            point_changes: list[dict] = []
            was_added_mid_sprint = False
            original_pts = story.get("points", 0)

            # Check if issue was created after sprint start
            created_raw = getattr(issue.fields, "created", None) or ""
            created_dt = _parse_date(created_raw)
            if created_dt and created_dt > s_dt:
                was_added_mid_sprint = True

            for history in getattr(changelog, "histories", []):
                change_date = _parse_date(getattr(history, "created", ""))
                if not change_date:
                    continue

                for item in getattr(history, "items", []):
                    field = getattr(item, "field", "")
                    from_val = getattr(item, "fromString", "") or ""
                    to_val = getattr(item, "toString", "") or ""

                    # Detect point changes during sprint — both re-estimation
                    # (from > 0 → different) and initial pointing (from 0 → value)
                    if field in ("Story Points", "story_points"):
                        if s_dt <= change_date <= (e_dt or change_date):
                            from_pts = _safe_float(from_val)
                            to_pts = _safe_float(to_val)
                            if from_pts != to_pts:
                                point_changes.append(
                                    {
                                        "date": change_date.isoformat(),
                                        "from_pts": from_pts,
                                        "to_pts": to_pts,
                                    }
                                )
                                if from_pts > 0:
                                    original_pts = from_pts

                    # Detect sprint membership changes
                    if field == "Sprint":
                        if sprint_name in to_val and sprint_name not in from_val:
                            if s_dt and change_date > s_dt:
                                was_added_mid_sprint = True

            if point_changes:
                story["point_changed"] = True
                story["original_points"] = original_pts
                story["point_changes"] = point_changes
            if was_added_mid_sprint:
                story["added_mid_sprint"] = True

        except Exception:
            # Non-fatal — skip changelog for this issue
            continue


def _enrich_azdo_scope_changes(
    wit_client: object,
    project: str,
    stories: list[dict],
    iter_path: str,
    iter_start: str,
    iter_end: str,
) -> None:
    """Enrich story dicts with mid-sprint scope change data from AzDO revisions.

    For each story, fetches work item revisions and detects:
    - point_changed: True if story points were modified during the iteration
    - original_points: points value at iteration start
    - added_mid_sprint: True if moved into this iteration after it started
    - point_changes: list of {date, from_pts, to_pts} dicts
    """
    s_dt = _parse_date(iter_start)
    e_dt = _parse_date(iter_end)
    if not s_dt:
        return

    # Normalize iter_path for comparison with System.IterationPath
    iter_path = _normalize_iter_path(iter_path)

    for story in stories:
        wi_id_str = story.get("issue_key", "")
        if not wi_id_str:
            continue
        try:
            wi_id = int(wi_id_str)
        except (ValueError, TypeError):
            continue

        try:
            revisions = wit_client.get_revisions(wi_id, project=project)
            if not revisions:
                continue

            point_changes: list[dict] = []
            was_added_mid_sprint = False
            original_pts = story.get("points", 0)
            prev_pts = 0.0
            prev_iter = ""
            created_date = None

            for rev in revisions:
                fields = rev.fields if hasattr(rev, "fields") else {}
                rev_date = _parse_date(fields.get("System.ChangedDate", ""))
                if not rev_date:
                    continue

                cur_pts = _safe_float(fields.get("Microsoft.VSTS.Scheduling.StoryPoints"))
                cur_iter = fields.get("System.IterationPath", "") or ""

                # Track created date from first revision
                if created_date is None:
                    created_date = _parse_date(fields.get("System.CreatedDate", ""))

                # Look at changes within the sprint window
                if s_dt <= rev_date <= (e_dt or rev_date):
                    # Point change: either re-estimation (prev > 0 → different)
                    # OR initial pointing mid-sprint (prev was 0, now has value)
                    if cur_pts != prev_pts and (prev_pts > 0 or cur_pts > 0):
                        point_changes.append(
                            {
                                "date": rev_date.isoformat(),
                                "from_pts": prev_pts,
                                "to_pts": cur_pts,
                            }
                        )
                        if prev_pts > 0 and not story.get("original_points"):
                            original_pts = prev_pts

                    # Iteration path change — moved INTO this iteration mid-sprint
                    if (
                        iter_path
                        and iter_path in cur_iter
                        and prev_iter
                        and iter_path not in prev_iter
                        and rev_date > s_dt
                    ):
                        was_added_mid_sprint = True

                prev_pts = cur_pts
                prev_iter = cur_iter

            # Story created after sprint start directly into this sprint
            if created_date and created_date > s_dt and iter_path and iter_path in (prev_iter or ""):
                was_added_mid_sprint = True

            if point_changes:
                story["point_changed"] = True
                story["original_points"] = original_pts
                story["point_changes"] = point_changes
            if was_added_mid_sprint:
                story["added_mid_sprint"] = True

        except Exception:
            continue


def _analyse_scope_changes(sprint_data: list[dict]) -> dict:
    """Compute aggregate scope change metrics across sprints.

    When sprint_data entries contain a ``scope_timeline`` (SprintScopeTimeline),
    uses the daily snapshot data for accurate committed-vs-delivered metrics.
    Falls back to the legacy story-level flags when no timeline is present.

    Returns a dict with:
    - per_sprint: list of sprint-level metrics including committed/final/delivered pts
    - timelines: list of SprintScopeTimeline objects (for TUI/export rendering)
    - re_estimation_by_size / re_estimation_by_discipline
    - carry_over_chains
    - totals: aggregate counts + committed/delivered velocity averages
    """
    per_sprint: list[dict] = []
    timelines: list[SprintScopeTimeline] = []
    re_est_by_size: dict[int, int] = defaultdict(int)
    re_est_by_disc: dict[str, int] = defaultdict(int)
    issue_sprints: dict[str, list[str]] = defaultdict(list)
    total_added = 0
    total_removed = 0
    total_re_estimated = 0
    total_stories = 0
    committed_velocities: list[float] = []
    delivered_velocities: list[float] = []

    for sd in sprint_data:
        stories = sd.get("stories", [])
        sprint_name = sd.get("sprint_name", "?")
        timeline: SprintScopeTimeline | None = sd.get("scope_timeline")  # type: ignore[assignment]

        added = 0
        removed = 0
        re_estimated = 0

        for s in stories:
            if s.get("is_recurring"):
                continue  # exclude ceremony/KTLO tickets from scope counts
            issue_key = s.get("issue_key", "")
            if issue_key:
                issue_sprints[issue_key].append(sprint_name)

            total_stories += 1
            if s.get("added_mid_sprint"):
                added += 1
                total_added += 1
            if s.get("carried_over"):
                removed += 1
                total_removed += 1
            if s.get("point_changed"):
                re_estimated += 1
                total_re_estimated += 1
                pts = int(_safe_float(s.get("original_points", s.get("points", 0))))
                if pts in (1, 2, 3, 5, 8):
                    re_est_by_size[pts] += 1
                disc = s.get("discipline", "fullstack")
                re_est_by_disc[disc] += 1

        planned = sd.get("planned_count", 0) or len(stories)
        scope_change_rate = (
            round(
                (added + removed) / planned * 100,
                1,
            )
            if planned > 0
            else 0.0
        )

        sprint_entry: dict = {
            "name": sprint_name,
            "added": added,
            "removed": removed,
            "re_estimated": re_estimated,
            "scope_change_rate": scope_change_rate,
        }

        if timeline:
            timelines.append(timeline)
            sprint_entry["committed_pts"] = timeline.committed_pts
            sprint_entry["final_pts"] = timeline.final_pts
            sprint_entry["delivered_pts"] = timeline.delivered_pts
            sprint_entry["scope_change_total"] = timeline.scope_change_total
            sprint_entry["scope_churn"] = timeline.scope_churn
            sprint_entry["change_event_count"] = len(timeline.change_events)
            committed_velocities.append(timeline.committed_pts)
            delivered_velocities.append(timeline.delivered_pts)

        per_sprint.append(sprint_entry)

    # Carry-over chains: issues that appeared in 3+ sprints
    carry_over_chains = [
        {"issue_key": key, "sprint_count": len(sprints), "sprints": sprints}
        for key, sprints in issue_sprints.items()
        if len(sprints) >= 3
    ]
    carry_over_chains.sort(key=lambda x: x["sprint_count"], reverse=True)

    # Aggregate velocity from timelines
    avg_committed = round(statistics.mean(committed_velocities), 1) if committed_velocities else 0.0
    avg_delivered = round(statistics.mean(delivered_velocities), 1) if delivered_velocities else 0.0

    return {
        "per_sprint": per_sprint,
        "timelines": timelines,
        "re_estimation_by_size": dict(sorted(re_est_by_size.items())),
        "re_estimation_by_discipline": dict(sorted(re_est_by_disc.items())),
        "carry_over_chains": carry_over_chains[:10],
        "totals": {
            "added_mid_sprint": total_added,
            "removed_mid_sprint": total_removed,
            "re_estimated": total_re_estimated,
            "total_stories": total_stories,
            "avg_committed_velocity": avg_committed,
            "avg_delivered_velocity": avg_delivered,
        },
    }


def _fetch_jira_history(project_key: str, sprint_count: int) -> list[dict]:
    """Fetch historical sprint data from Jira.

    Returns a list of sprint dicts with story-level detail for profile building.
    """
    from jira import JIRA, JIRAError

    from scrum_agent.config import get_jira_base_url, get_jira_email, get_jira_project_key, get_jira_token

    base_url, email, token = get_jira_base_url(), get_jira_email(), get_jira_token()
    if not all([base_url, email, token]):
        raise ValueError("Jira is not configured. Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN.")

    key = project_key.strip() or (get_jira_project_key() or "")
    if not key:
        raise ValueError("No project key provided and JIRA_PROJECT_KEY is not set.")

    logger.info("Connecting to Jira: %s, project=%s", base_url, key)
    jira = JIRA(server=base_url, basic_auth=(email, token))

    boards = jira.boards(projectKeyOrID=key)
    if not boards:
        raise ValueError(f"No Jira board found for project '{key}'.")

    board = boards[0]
    logger.info("Using board: %s (id=%s)", getattr(board, "name", "?"), board.id)
    closed_sprints = jira.sprints(board.id, state="closed")
    sample = list(closed_sprints)[-sprint_count:] if closed_sprints else []
    logger.info("Found %d closed sprints, analysing last %d", len(list(closed_sprints or [])), len(sample))
    if not sample:
        return []

    sprint_data = []
    for sp in sample:
        # Fetch completed points and sprint dates
        info = jira.sprint_info(board.id, sp.id)
        completed_pts = _safe_float(info.get("completedPoints", 0))
        sprint_start = getattr(sp, "startDate", None) or info.get("startDate", "")
        sprint_end = getattr(sp, "endDate", None) or info.get("endDate", "")

        # Fetch all issues in this sprint (done + not-done for spillover)
        try:
            done_issues = jira.search_issues(
                f'project = "{key}" AND sprint = {sp.id} AND status = Done',
                maxResults=500,
                fields="customfield_10016,story_points,assignee,created,"
                "resolutiondate,issuetype,labels,summary,subtasks,"
                "customfield_10014,description",
            )
        except JIRAError:
            done_issues = []

        try:
            all_in_sprint = jira.search_issues(
                f'project = "{key}" AND sprint = {sp.id}',
                maxResults=500,
                fields="customfield_10016,story_points,status,summary,resolutiondate,issuetype",
            )
            planned_count = len(all_in_sprint)
        except JIRAError:
            all_in_sprint = []
            planned_count = len(done_issues)

        # Spillover detection: issues in this sprint that were NOT done
        done_keys = {i.key for i in done_issues}

        # JQL fallback for velocity
        if completed_pts <= 0:
            jql_total = 0.0
            for issue in done_issues:
                sp_val = getattr(issue.fields, "customfield_10016", None)
                if sp_val is None:
                    sp_val = getattr(issue.fields, "story_points", None)
                if sp_val is not None:
                    jql_total += _safe_float(sp_val)
            completed_pts = jql_total

        stories = []
        for issue in done_issues:
            issue_type = getattr(issue.fields, "issuetype", None)
            type_name = getattr(issue_type, "name", "").lower() if issue_type else ""
            if type_name in ("sub-task", "subtask"):
                continue

            pts = _safe_float(getattr(issue.fields, "customfield_10016", None))
            if pts == 0:
                pts = _safe_float(getattr(issue.fields, "story_points", None))

            resolved = getattr(issue.fields, "resolutiondate", None)
            # Cycle time = max(sprint start, created) → resolved.
            # Sprint start is a better proxy for "work began" than created,
            # which includes backlog sitting time before the sprint.
            created_raw = getattr(issue.fields, "created", None) or ""
            work_started = sprint_start or created_raw
            if created_raw and sprint_start:
                c_dt = _parse_date(created_raw)
                s_dt = _parse_date(sprint_start)
                if c_dt and s_dt:
                    work_started = created_raw if c_dt > s_dt else sprint_start
            ct = _cycle_time_days(work_started, resolved)

            subtasks = getattr(issue.fields, "subtasks", []) or []
            task_count = len(subtasks)
            subtask_titles = []
            subtask_labels = []
            subtask_details = []
            for st in subtasks:
                st_summary = getattr(st.fields, "summary", "") if hasattr(st, "fields") else str(st)
                if st_summary:
                    subtask_titles.append(st_summary)
                st_type = getattr(st.fields, "issuetype", None) if hasattr(st, "fields") else None
                if st_type:
                    subtask_labels.append(getattr(st_type, "name", ""))
                st_status = getattr(st.fields, "status", None) if hasattr(st, "fields") else None
                st_status_name = getattr(st_status, "name", "") if st_status else ""
                st_assignee = getattr(st.fields, "assignee", None) if hasattr(st, "fields") else None
                st_assignee_name = getattr(st_assignee, "displayName", "") if st_assignee else ""
                subtask_details.append(
                    {
                        "title": st_summary or "",
                        "type": getattr(st_type, "name", "") if st_type else "",
                        "status": st_status_name,
                        "assignee": st_assignee_name,
                        "done": st_status_name.lower() in ("done", "closed", "resolved"),
                    }
                )

            epic_key = getattr(issue.fields, "customfield_10014", "") or ""
            summary = getattr(issue.fields, "summary", "") or ""
            description = getattr(issue.fields, "description", "") or ""

            # AC count: approximate by counting bullet/checklist items in description
            # Strip HTML tags first (Jira descriptions can contain rich HTML)
            _desc_text = re.sub(r"<[^>]+>", " ", description).strip()
            ac_count = 0
            if _desc_text:
                ac_count = len(re.findall(r"(?m)^[\s]*[-*●•]\s", _desc_text))
                if ac_count == 0:
                    ac_count = len(re.findall(r"(?m)^\s*\d+[.)]\s", _desc_text))

            # Fetch comments for DoD signal analysis (with timestamps for ordering)
            comments_text: list[str] = []
            comments_timed: list[tuple[str, str]] = []  # (iso_date, body)
            try:
                issue_comments = jira.comments(issue.key)
                for c in issue_comments or []:
                    body = getattr(c, "body", "") or ""
                    if body:
                        comments_text.append(body)
                        c_date = getattr(c, "created", "") or ""
                        comments_timed.append((c_date, body))
            except Exception:
                pass

            labels = [
                lbl.lower() if isinstance(lbl, str) else getattr(lbl, "name", "").lower()
                for lbl in (getattr(issue.fields, "labels", []) or [])
            ]
            discipline = "fullstack"
            for lbl in labels:
                if lbl in ("frontend", "backend", "infrastructure", "design", "testing"):
                    discipline = lbl
                    break

            base_text = description + " " + " ".join(comments_text)
            story_row: dict = {
                "points": pts,
                "cycle_time_days": ct,
                "discipline": discipline,
                "task_count": task_count,
                "ac_count": ac_count,
                "epic_key": epic_key,
                "point_changed": False,
                "summary": summary,
                "description": description,
                "comments": comments_text,
                "comments_timed": comments_timed,
                "subtask_titles": subtask_titles,
                "subtask_labels": subtask_labels,
                "subtask_details": subtask_details,
                "carried_over": False,
                "issue_key": issue.key,
                "issue_url": f"{base_url}/browse/{issue.key}",
                "sprint_name": sp.name,
                "repos": [],
                "repo_sources": [],
                "assignee": getattr(
                    getattr(issue.fields, "assignee", None),
                    "displayName",
                    "",
                )
                or "",
            }
            _story_add_repos(story_row, _extract_repos(base_text), "jira_text")
            _story_add_repos(
                story_row,
                _extract_repos(_jira_development_url_blob(jira, issue)),
                "jira_development",
            )
            stories.append(story_row)

        # Add spillover entries for issues NOT completed in this sprint
        for issue in all_in_sprint:
            if issue.key in done_keys:
                continue
            issue_type = getattr(issue.fields, "issuetype", None)
            type_name = getattr(issue_type, "name", "").lower() if issue_type else ""
            if type_name in ("sub-task", "subtask"):
                continue
            status = getattr(issue.fields, "status", None)
            status_name = getattr(status, "name", "").lower() if status else ""
            if status_name in ("done", "closed"):
                continue
            pts = _safe_float(getattr(issue.fields, "customfield_10016", None))
            if pts == 0:
                pts = _safe_float(getattr(issue.fields, "story_points", None))
            stories.append(
                {
                    "points": pts,
                    "cycle_time_days": None,
                    "discipline": "fullstack",
                    "task_count": 0,
                    "ac_count": 0,
                    "epic_key": "",
                    "point_changed": False,
                    "summary": getattr(issue.fields, "summary", "") or "",
                    "description": "",
                    "comments": [],
                    "subtask_titles": [],
                    "subtask_labels": [],
                    "carried_over": True,
                    "issue_key": issue.key,
                    "issue_url": f"{base_url}/browse/{issue.key}",
                    "sprint_name": sp.name,
                    "repos": [],
                    "repo_sources": [],
                }
            )

        # Enrich stories with mid-sprint scope change data from changelog
        try:
            _enrich_jira_scope_changes(jira, stories, sp.name, sprint_start, sprint_end)
        except Exception as _sc_err:
            logger.debug("Scope change enrichment failed for %s: %s", sp.name, _sc_err)

        # Build daily scope timeline from changelogs
        scope_timeline: SprintScopeTimeline | None = None
        try:
            scope_timeline = _build_jira_sprint_scope_timeline(
                jira,
                stories,
                sp.name,
                sprint_start,
                sprint_end,
                completed_pts,
            )
        except Exception as _tl_err:
            logger.debug("Jira scope timeline failed for %s: %s", sp.name, _tl_err)

        logger.info(
            "  Sprint %s: %.1f pts, %d/%d done, %d stories fetched (%d with comments)",
            sp.name,
            completed_pts,
            len(done_issues),
            planned_count,
            len(stories),
            sum(1 for s in stories if s.get("comments")),
        )
        sd_entry: dict = {
            "sprint_name": sp.name,
            "completed_points": completed_pts,
            "stories": stories,
            "planned_count": planned_count,
            "completed_count": len(done_issues),
        }
        if scope_timeline:
            sd_entry["scope_timeline"] = scope_timeline
        sprint_data.append(sd_entry)

    logger.info(
        "Jira fetch complete: %d sprints, %d total stories",
        len(sprint_data),
        sum(len(sd["stories"]) for sd in sprint_data),
    )
    return sprint_data


def _fetch_azdevops_history(project_key: str, sprint_count: int) -> list[dict]:
    """Fetch historical sprint data from Azure DevOps.

    Returns a list of sprint dicts with story-level detail for profile building.
    """
    from azure.devops.connection import Connection
    from msrest.authentication import BasicAuthentication

    from scrum_agent.config import (
        get_azure_devops_org_url,
        get_azure_devops_project,
        get_azure_devops_team,
        get_azure_devops_token,
    )

    org_url = get_azure_devops_org_url()
    token = get_azure_devops_token()
    project = project_key.strip() or get_azure_devops_project() or ""
    team = get_azure_devops_team() or ""

    if not all([org_url, token, project]):
        raise ValueError(
            "Azure DevOps is not configured. Set AZURE_DEVOPS_ORG_URL, AZURE_DEVOPS_TOKEN, AZURE_DEVOPS_PROJECT."
        )

    credentials = BasicAuthentication("", token)
    connection = Connection(base_url=org_url, creds=credentials)
    work_client = connection.clients.get_work_client()
    wit_client = connection.clients.get_work_item_tracking_client()

    from azure.devops.v7_1.work.models import TeamContext

    _team_name = team or f"{project} Team"
    logger.info("Connecting to AzDO: %s, project=%s, team=%s", org_url, project, _team_name)
    team_context = TeamContext(project=project, team=_team_name)
    all_iterations = work_client.get_team_iterations(team_context) or []
    logger.info("Found %d total iterations", len(all_iterations))
    if not all_iterations:
        return []

    # Filter to past iterations by date (timeframe param not reliable)
    from datetime import UTC
    from datetime import datetime as _dt

    now = _dt.now(UTC)
    past_iterations = []
    for it in all_iterations:
        attrs = getattr(it, "attributes", None)
        if attrs:
            end = getattr(attrs, "finish_date", None)
            if end and end < now:
                past_iterations.append(it)
    if not past_iterations:
        past_iterations = list(all_iterations)

    sample = past_iterations[-sprint_count:]
    sprint_data = []

    for iteration in sample:
        iter_id = iteration.id

        # Capture iteration dates for cycle time calculation and scope change detection
        iter_attrs = getattr(iteration, "attributes", None)
        iter_start_str = ""
        iter_end_str = ""
        iter_path = getattr(iteration, "path", "") or ""
        if iter_attrs:
            _s = getattr(iter_attrs, "start_date", None)
            if _s:
                iter_start_str = _s.isoformat() if hasattr(_s, "isoformat") else str(_s)
            _e = getattr(iter_attrs, "finish_date", None)
            if _e:
                iter_end_str = _e.isoformat() if hasattr(_e, "isoformat") else str(_e)

        # Get work items for this iteration
        try:
            work_items_refs = work_client.get_iteration_work_items(team_context, iter_id)
            wi_relations = work_items_refs.work_item_relations if work_items_refs else []
        except Exception as e:
            logger.warning(
                "AzDO get_iteration_work_items failed for %s (id=%s): %s",
                getattr(iteration, "name", "?"),
                iter_id,
                e,
            )
            wi_relations = []

        if not wi_relations:
            logger.info(
                "AzDO iteration %s: no work_item_relations from API (team=%r project=%r)",
                getattr(iteration, "name", iter_id),
                _team_name,
                project,
            )
            continue

        wi_ids: list[int] = []
        seen_id: set[int] = set()
        for rel in wi_relations:
            tid = _azdo_work_item_link_target_id(rel)
            if tid is not None and tid not in seen_id:
                seen_id.add(tid)
                wi_ids.append(tid)
        if not wi_ids:
            logger.info(
                "AzDO iteration %s: could not resolve work item ids from %d link(s)",
                getattr(iteration, "name", iter_id),
                len(wi_relations),
            )
            continue

        _fields_batch = [
            "System.WorkItemType",
            "Microsoft.VSTS.Scheduling.StoryPoints",
            "System.State",
            "System.CreatedDate",
            "Microsoft.VSTS.Common.ResolvedDate",
            "System.Tags",
            "System.Title",
            "System.Parent",
            "System.Description",
            "Microsoft.VSTS.Common.AcceptanceCriteria",
            "System.CommentCount",
            "System.AssignedTo",
            "Microsoft.VSTS.Common.ActivatedDate",
        ]

        # Fetch work item details in batches — collect both stories and tasks,
        # then attach tasks to their parent stories.
        stories = []
        tasks_by_parent: dict[int, list[dict]] = defaultdict(list)
        all_items_raw: list = []
        completed_pts = 0.0
        completed_count = 0
        planned_count = len(wi_ids)

        batch_size = 200
        for i in range(0, len(wi_ids), batch_size):
            batch_ids = wi_ids[i : i + batch_size]
            items = _wit_get_work_items_batch(
                wit_client,
                project,
                batch_ids,
                _fields_batch,
                want_relations=True,
            )
            all_items_raw.extend(items)

        if not all_items_raw and wi_ids:
            logger.error(
                "AzDO iteration %s: %d work item id(s) from iteration links but "
                "get_work_items returned no rows — check PAT scope (Work Items: Read), "
                "project name %r, team %r",
                getattr(iteration, "name", iter_id),
                len(wi_ids),
                project,
                _team_name,
            )

        # First pass: separate tasks from stories/bugs
        story_items = []
        for item in all_items_raw:
            fields = item.fields or {}
            wi_type = fields.get("System.WorkItemType", "").lower()
            if wi_type in ("task",):
                parent_id = fields.get("System.Parent")
                if parent_id:
                    state = fields.get("System.State", "")
                    tasks_by_parent[int(parent_id)].append(
                        {
                            "title": fields.get("System.Title", "") or "",
                            "type": "Task",
                            "status": state,
                            "assignee": _azdo_assignee_name(fields),
                            "done": state in ("Closed", "Done", "Resolved"),
                        }
                    )
            else:
                story_items.append(item)

        # Second pass: build story dicts with attached child tasks
        for item in story_items:
            fields = item.fields or {}
            state = fields.get("System.State", "")
            pts = _safe_float(fields.get("Microsoft.VSTS.Scheduling.StoryPoints"))
            title = fields.get("System.Title", "") or ""
            desc = fields.get("System.Description", "") or ""
            ac_text_raw = fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""
            # Strip HTML tags for text analysis (AzDO AC fields are often rich HTML)
            ac_text = re.sub(r"<[^>]+>", " ", ac_text_raw).strip()
            ac_text = re.sub(r"\s+", " ", ac_text)
            full_desc = f"{desc}\n{ac_text_raw}"

            ac_count = 0
            if ac_text:
                # Try bullet/numbered list patterns first
                ac_count = len(re.findall(r"(?m)^[\s]*[-*●•]\s", ac_text))
                if ac_count == 0:
                    ac_count = len(re.findall(r"(?m)^\s*\d+[.)]\s", ac_text))
                # If AC field has text but no list structure, count as 1 AC
                if ac_count == 0 and len(ac_text) > 10:
                    ac_count = 1

            is_done = state in ("Closed", "Done", "Resolved")
            ct = None
            if is_done:
                completed_count += 1
                completed_pts += pts

                # Cycle time: use the latest of (iteration start, activated,
                # created) as the "work started" signal. This avoids counting
                # backlog time (created months ago) or re-activation from a
                # previous sprint. The iteration start is the floor — work
                # on a sprint item can't meaningfully start before the sprint.
                activated = fields.get("Microsoft.VSTS.Common.ActivatedDate", "")
                created = fields.get("System.CreatedDate", "")
                resolved = fields.get("Microsoft.VSTS.Common.ResolvedDate", "")

                # Pick the best start signal
                candidates = [
                    _parse_date(iter_start_str),
                    _parse_date(activated),
                    _parse_date(created),
                ]
                valid_starts = [d for d in candidates if d is not None]
                if valid_starts and resolved:
                    # Use the LATEST start date — can't start before sprint
                    best_start = max(valid_starts)
                    r_dt = _parse_date(resolved)
                    if r_dt and best_start:
                        ct = max((r_dt - best_start).total_seconds() / 86400, 0.0)
                    else:
                        ct = None
                else:
                    ct = None

            tags = fields.get("System.Tags", "") or ""
            discipline = "fullstack"
            for tag in tags.lower().split(";"):
                tag = tag.strip()
                if tag in ("frontend", "backend", "infrastructure", "design", "testing"):
                    discipline = tag
                    break

            parent_id = fields.get("System.Parent")
            epic_key = str(parent_id) if parent_id else ""

            child_tasks = tasks_by_parent.get(item.id, [])
            st_titles = [t["title"] for t in child_tasks if t.get("title")]
            st_labels = [t.get("type", "Task") for t in child_tasks]

            story_row = {
                "points": pts,
                "cycle_time_days": ct,
                "discipline": discipline,
                "task_count": len(child_tasks),
                "ac_count": ac_count,
                "epic_key": epic_key,
                "point_changed": False,
                "summary": title,
                "description": full_desc,
                "comments": [],
                "subtask_titles": st_titles,
                "subtask_labels": st_labels,
                "subtask_details": child_tasks,
                "carried_over": not is_done,
                "issue_key": str(item.id),
                "issue_url": f"{org_url}/{project}/_workitems/edit/{item.id}",
                "sprint_name": getattr(iteration, "name", str(iter_id)),
                "repos": [],
                "repo_sources": [],
                "assignee": _azdo_assignee_name(fields),
            }
            _story_add_repos(story_row, _extract_repos(full_desc), "description")
            _story_add_repos(
                story_row,
                _extract_repos_from_azdo_relations(item),
                "azdo_work_item_links",
            )
            stories.append(story_row)

        # Enrich stories with mid-sprint scope change data from revisions
        try:
            _enrich_azdo_scope_changes(
                wit_client,
                project,
                stories,
                iter_path,
                iter_start_str,
                iter_end_str,
            )
        except Exception as _sc_err:
            logger.debug("AzDO scope change enrichment failed for %s: %s", getattr(iteration, "name", "?"), _sc_err)

        # Build daily scope timeline from revisions
        _azdo_timeline: SprintScopeTimeline | None = None
        try:
            _azdo_timeline = _build_azdo_sprint_scope_timeline(
                wit_client,
                project,
                stories,
                iter_path,
                iter_start_str,
                iter_end_str,
                completed_pts,
            )
        except Exception as _tl_err:
            logger.debug("AzDO scope timeline failed for %s: %s", getattr(iteration, "name", "?"), _tl_err)

        _iter_name = getattr(iteration, "name", str(iter_id))
        logger.info(
            "  Iteration %s: %.1f pts, %d/%d done, %d stories, %d tasks attached",
            _iter_name,
            completed_pts,
            completed_count,
            planned_count,
            len(stories),
            sum(len(s.get("subtask_details", [])) for s in stories),
        )
        _azdo_sd_entry: dict = {
            "sprint_name": _iter_name,
            "completed_points": completed_pts,
            "stories": stories,
            "planned_count": planned_count,
            "completed_count": completed_count,
        }
        if _azdo_timeline:
            _azdo_sd_entry["scope_timeline"] = _azdo_timeline
        sprint_data.append(_azdo_sd_entry)

    try:
        _azdo_enrich_repos_from_git_pull_requests(connection, project, sprint_data)
    except Exception as e:
        logger.warning("AzDO PR repo enrichment failed: %s", e)

    return sprint_data


# ---------------------------------------------------------------------------
# Helpers for compare_plan_to_actuals
# ---------------------------------------------------------------------------


def _fetch_jira_actuals(issue_keys: list[str]) -> dict[str, dict]:
    """Fetch actual status/points/cycle time for a set of Jira issue keys."""
    if not issue_keys:
        return {}

    from jira import JIRA

    from scrum_agent.config import (
        get_jira_base_url,
        get_jira_email,
        get_jira_token,
    )

    base_url, email, token = (get_jira_base_url(), get_jira_email(), get_jira_token())
    if not all([base_url, email, token]):
        return {}

    jira = JIRA(server=base_url, basic_auth=(email, token))
    results: dict[str, dict] = {}

    # Fetch in batches of 50 (JQL IN clause limit)
    for i in range(0, len(issue_keys), 50):
        batch = issue_keys[i : i + 50]
        keys_str = ",".join(batch)
        try:
            issues = jira.search_issues(
                f"key in ({keys_str})",
                maxResults=len(batch),
                fields="customfield_10016,story_points,status,created,resolutiondate,summary",
            )
        except Exception:
            continue

        for issue in issues:
            pts = _safe_float(getattr(issue.fields, "customfield_10016", None))
            if pts == 0:
                pts = _safe_float(getattr(issue.fields, "story_points", None))
            status = getattr(issue.fields, "status", None)
            status_name = getattr(status, "name", "") if status else ""
            created = getattr(issue.fields, "created", None)
            resolved = getattr(issue.fields, "resolutiondate", None)
            ct = _cycle_time_days(created, resolved)

            results[issue.key] = {
                "points": pts,
                "status": status_name,
                "cycle_time_days": ct,
                "summary": getattr(issue.fields, "summary", "") or "",
            }

    return results


def _fetch_azdevops_actuals(work_item_ids: list[str], project_key: str) -> dict[str, dict]:
    """Fetch actual status/points/cycle time for AzDO work item IDs."""
    if not work_item_ids:
        return {}

    from azure.devops.connection import Connection
    from msrest.authentication import BasicAuthentication

    from scrum_agent.config import (
        get_azure_devops_org_url,
        get_azure_devops_project,
        get_azure_devops_token,
    )

    org_url = get_azure_devops_org_url()
    token = get_azure_devops_token()
    if not all([org_url, token]):
        return {}

    credentials = BasicAuthentication("", token)
    connection = Connection(base_url=org_url, creds=credentials)
    wit_client = connection.clients.get_work_item_tracking_client()

    proj_scope = (project_key or "").strip() or (get_azure_devops_project() or "")

    int_ids = []
    for wid in work_item_ids:
        try:
            int_ids.append(int(wid))
        except (ValueError, TypeError):
            continue

    results: dict[str, dict] = {}
    for i in range(0, len(int_ids), 200):
        batch = int_ids[i : i + 200]
        try:
            items = wit_client.get_work_items(
                batch,
                project=proj_scope or None,
                fields=[
                    "Microsoft.VSTS.Scheduling.StoryPoints",
                    "System.State",
                    "System.CreatedDate",
                    "Microsoft.VSTS.Common.ResolvedDate",
                    "System.Title",
                ],
            )
        except Exception as e:
            logger.warning("AzDO get_work_items (actuals) failed: %s", e)
            continue

        for item in items or []:
            fields = item.fields or {}
            pts = _safe_float(fields.get("Microsoft.VSTS.Scheduling.StoryPoints"))
            state = fields.get("System.State", "")
            created = fields.get("System.CreatedDate", "")
            resolved = fields.get("Microsoft.VSTS.Common.ResolvedDate", "")
            ct = _cycle_time_days(created, resolved)

            results[str(item.id)] = {
                "points": pts,
                "status": state,
                "cycle_time_days": ct,
                "summary": fields.get("System.Title", "") or "",
            }

    return results


# ---------------------------------------------------------------------------
# Tool: compare_plan_to_actuals
# ---------------------------------------------------------------------------


@tool
def compare_plan_to_actuals(
    session_id: str = "",
    source: str = "",
    project_key: str = "",
) -> str:
    """Compare a previously generated plan to actual sprint outcomes.

    Loads a stored session's plan (stories, points, sprint assignments) and
    matches them to actual Jira/AzDO data. Returns a structured comparison
    showing: estimated vs actual points, planned vs actual sprint, stories
    added/removed, and cycle times.

    Use this after sprints have been completed to measure estimation accuracy
    and feed improvements back into the team profile.

    session_id: The session ID of the plan to compare. Use 'latest' for most recent.
    source: "jira" or "azdevops". Auto-detected if only one is configured.
    project_key: Jira project key or AzDO project name.
    """
    # See README: "Tools" — tool types, risk levels
    logger.debug("compare_plan_to_actuals called: session_id=%r, source=%r", session_id, source)
    from pathlib import Path

    from scrum_agent.sessions import SessionStore

    # Load the session's plan
    db_dir = Path.home() / ".scrum-agent"
    db_path = db_dir / "sessions.db"
    if not db_path.exists():
        return json.dumps({"error": "No sessions database found."})

    with SessionStore(db_path) as store:
        if session_id == "latest" or not session_id:
            sid = store.get_latest_session_id()
            if not sid:
                return json.dumps({"error": "No sessions found."})
        else:
            sid = session_id

        state = store.load_state(sid)
        if not state:
            return json.dumps({"error": f"Could not load state for session '{sid}'."})

    planned_stories = state.get("stories", [])
    planned_sprints = state.get("sprints", [])
    if not planned_stories:
        return json.dumps({"error": "Session has no stories to compare."})

    jira_keys = state.get("jira_story_keys", {})
    azdevops_keys = state.get("azdevops_story_keys", {})

    if not source:
        source = _detect_source()

    def _get_pts(s) -> float:
        if hasattr(s, "story_points"):
            sp = s.story_points
            return float(sp.value if hasattr(sp, "value") else sp)
        if isinstance(s, dict):
            return _safe_float(s.get("story_points", 0))
        return 0.0

    planned_total = sum(_get_pts(s) for s in planned_stories)

    comparison: dict = {
        "session_id": sid,
        "planned_story_count": len(planned_stories),
        "planned_sprint_count": len(planned_sprints),
        "planned_total_points": planned_total,
        "story_comparisons": [],
        "summary": {},
    }

    # Determine which tracker keys we have
    tracker_keys = {}
    if source == "jira" and jira_keys:
        tracker_keys = jira_keys
        comparison["tracker"] = "jira"
    elif source == "azdevops" and azdevops_keys:
        tracker_keys = azdevops_keys
        comparison["tracker"] = "azdevops"
    else:
        comparison["tracker"] = source or "none"
        comparison["matched_stories"] = 0
        comparison["note"] = "No tracker key mappings found \u2014 sync stories to Jira/AzDO first, then re-run retro."
        return json.dumps(comparison, ensure_ascii=False, default=str)

    # Fetch actual data from the tracker for matched stories
    actuals: dict[str, dict] = {}
    try:
        if source == "jira":
            actuals = _fetch_jira_actuals(list(tracker_keys.values()))
        elif source == "azdevops":
            actuals = _fetch_azdevops_actuals(list(tracker_keys.values()), project_key)
    except Exception as exc:
        logger.error("Failed to fetch actuals: %s", exc)
        comparison["note"] = f"Failed to fetch tracker data: {exc}"
        comparison["matched_stories"] = len(tracker_keys)
        return json.dumps(comparison, ensure_ascii=False, default=str)

    # Match planned stories to actuals
    matched = 0
    points_delta_sum = 0.0
    stories_on_target = 0
    stories_over = 0
    stories_under = 0
    actual_total_pts = 0.0

    for ps in planned_stories:
        title = getattr(ps, "title", "") or (ps.get("title", "") if isinstance(ps, dict) else "")
        story_id = getattr(ps, "id", "") or (ps.get("id", "") if isinstance(ps, dict) else "")
        planned_pts = _get_pts(ps)
        tracker_key = tracker_keys.get(story_id, "")

        actual = actuals.get(tracker_key, {}) if tracker_key else {}

        if not actual and tracker_key:
            # Try fuzzy match by title
            for ak, av in actuals.items():
                if av.get("summary", "").lower() == title.lower():
                    actual = av
                    break

        if actual:
            matched += 1
            actual_pts = _safe_float(actual.get("points", 0))
            actual_total_pts += actual_pts
            delta = actual_pts - planned_pts
            points_delta_sum += abs(delta)

            if abs(delta) < 0.5:
                stories_on_target += 1
            elif delta > 0:
                stories_over += 1
            else:
                stories_under += 1

            comparison["story_comparisons"].append(
                {
                    "title": title,
                    "planned_pts": planned_pts,
                    "actual_pts": actual_pts,
                    "delta": round(delta, 1),
                    "status": actual.get("status", "unknown"),
                    "cycle_time_days": actual.get("cycle_time_days"),
                }
            )
        else:
            comparison["story_comparisons"].append(
                {
                    "title": title,
                    "planned_pts": planned_pts,
                    "actual_pts": None,
                    "delta": None,
                    "status": "not found in tracker",
                    "cycle_time_days": None,
                }
            )

    comparison["matched_stories"] = matched
    comparison["summary"] = {
        "matched": matched,
        "not_matched": len(planned_stories) - matched,
        "planned_total_pts": round(planned_total, 1),
        "actual_total_pts": round(actual_total_pts, 1),
        "on_target": stories_on_target,
        "over_estimated": stories_under,
        "under_estimated": stories_over,
        "avg_point_delta": round(points_delta_sum / matched, 1) if matched else 0.0,
    }

    return json.dumps(comparison, ensure_ascii=False, default=str)
