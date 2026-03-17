"""Confluence Cloud tools — 3 read-only + 2 write (with user-confirmation guard in docstrings).

# See README: "Tools" — tool types, @tool decorator, risk levels
#
# All read tools are low-risk — they fetch page content from the Confluence REST
# API and return it as plain text for the LLM to reason about during project
# analysis. Write tools (create_page, update_page) are high-risk and carry an
# explicit "only call after user confirms" note in their docstrings.
#
# Why atlassian-python-api?
# The atlassian-python-api package provides a Confluence class that wraps the
# REST API with typed methods, handles authentication, and integrates cleanly
# with the Jira auth pattern already used in this project (shared base_url,
# email, and API token). This keeps the auth model consistent and avoids
# writing raw REST calls.
#
# Auth: Confluence Cloud uses the same HTTP Basic Auth as Jira Cloud —
# JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN are reused. The only new
# env var is CONFLUENCE_SPACE_KEY (the short space identifier, e.g. "MYSPACE").
"""

import logging
import re

from atlassian import Confluence
from langchain_core.tools import tool
from requests.exceptions import HTTPError

from scrum_agent.config import get_confluence_space_key, get_jira_base_url, get_jira_email, get_jira_token

logger = logging.getLogger(__name__)

# Shown whenever Confluence env vars are missing — single source of truth for the message.
_MISSING_CONFIG_MSG = (
    "Error: Confluence is not configured. Ensure JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, "
    "and CONFLUENCE_SPACE_KEY are set in your .env file."
)

# Truncate page content at this many characters to avoid flooding the LLM context.
# See README: "Tools" — scoping tool output for LLM relevance
_MAX_CONTENT_CHARS = 8_000


def _make_confluence_client() -> Confluence | None:
    """Return an authenticated Confluence client, or None if any required config is missing.

    Uses HTTP Basic Auth with the Atlassian account email and API token — the same
    credentials as Jira (both services share the Atlassian identity platform).
    cloud=True enables the Confluence Cloud REST API endpoint path (/wiki/rest/api/).
    """
    base_url, email, token = get_jira_base_url(), get_jira_email(), get_jira_token()
    if not all([base_url, email, token]):
        logger.warning("Confluence client not created — missing config")
        return None
    logger.debug("Creating Confluence client for %s", base_url)
    client = Confluence(url=base_url, username=email, password=token, cloud=True)
    logger.debug("Confluence client created successfully")
    return client


def _confluence_error_msg(e: HTTPError) -> str:
    """Return a user-friendly message for common Confluence HTTP error codes."""
    # HTTPError carries the response object; extract status_code from it.
    code = getattr(getattr(e, "response", None), "status_code", 0)
    if code == 401:
        return "Error: Confluence authentication failed. Check JIRA_EMAIL and JIRA_API_TOKEN in .env."
    if code == 403:
        return "Error: Confluence permission denied. Ensure your API token has access to this space."
    if code == 404:
        return f"Error: Confluence resource not found — verify the page ID or space key. ({e})"
    if code == 429:
        return "Error: Confluence rate limit reached. Wait a moment and try again."
    return f"Error: Confluence API error {code}: {e}"


def _strip_html_tags(html: str) -> str:
    """Strip HTML/XML tags from Confluence storage format for LLM-readable plain text.

    Confluence pages are stored as XHTML ('storage format'). This function converts
    them to readable plain text by:
      1. Converting <br> and </p> to newlines for natural paragraph breaks.
      2. Removing all remaining tags.
      3. Expanding common HTML entities.
      4. Collapsing excess whitespace.
    """
    # Preserve paragraph and line breaks as newlines before stripping all tags.
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    # Remove all remaining HTML/XML tags.
    text = re.sub(r"<[^>]+>", " ", text)
    # Expand common HTML entities.
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse multiple whitespace/newlines into a single space or newline.
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _text_to_storage(text: str) -> str:
    """Convert plain text to Confluence storage format (basic XHTML paragraphs).

    Splits the text on double-newlines (paragraph boundaries) and wraps each
    paragraph in <p> tags. This produces valid Confluence storage XHTML without
    requiring an external library or a conversion API call.
    """
    paragraphs = text.strip().split("\n\n")
    return "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


@tool
def confluence_search_docs(query: str, space_key: str = "", limit: int = 10) -> str:
    """Search Confluence pages by keyword or phrase.

    Use this before project analysis to discover architecture docs, ADRs, runbooks,
    and product specs that should inform the scrum plan. Falls back to
    CONFLUENCE_SPACE_KEY env var when space_key is not provided.
    Returns title, excerpt, page ID, and URL for each result.
    """
    # See README: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("confluence_search_docs called: query=%r, space=%r", query, space_key)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    key = space_key.strip() or (get_confluence_space_key() or "")
    space_filter = f' AND space = "{key}"' if key else ""

    try:
        # CQL (Confluence Query Language) — SQL-like syntax for searching content.
        # We match both title and full-text to surface relevant pages.
        cql = f'type = page AND (title ~ "{query}" OR text ~ "{query}"){space_filter}'
        results = conf.cql(cql, limit=limit)
        pages = results.get("results", []) if isinstance(results, dict) else []

        if not pages:
            scope = f" in space '{key}'" if key else ""
            return f"No Confluence pages found for '{query}'{scope}."

        base_url = (get_jira_base_url() or "").rstrip("/")
        lines: list[str] = [f"Confluence search results for '{query}':", ""]

        for page in pages:
            title = page.get("title", "Untitled")
            page_id = page.get("id", "")
            excerpt = _strip_html_tags(page.get("excerpt", ""))[:200]
            # _links.webui is the canonical page path; fall back to /wiki/pages/{id}.
            web_ui = page.get("_links", {}).get("webui", f"/wiki/pages/{page_id}")
            url = f"{base_url}{web_ui}"
            lines.append(f"[{title}] (ID: {page_id})")
            if excerpt:
                lines.append(f"  {excerpt}")
            lines.append(f"  URL: {url}")
            lines.append("")

        logger.debug("confluence_search_docs found %d results for %r", len(pages), query)
        lines.append(f"({len(pages)} results shown)")
        return "\n".join(lines)

    except HTTPError as e:
        logger.error("Confluence API error in search_docs: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_search_docs: %s", e)
        return f"Error: {e}"


@tool
def confluence_read_page(page_id: str = "", page_title: str = "", space_key: str = "") -> str:
    """Fetch and read a Confluence page as plain text.

    Provide either page_id or page_title (not both). When using page_title,
    space_key is required (or falls back to CONFLUENCE_SPACE_KEY env var).
    Strips Confluence storage format to plain text for LLM context.
    Truncates at 8 000 characters with a note if the page is larger.
    Use this to read architecture docs, ADRs, runbooks, and product specs.
    """
    logger.debug("confluence_read_page called: page_id=%r, title=%r", page_id, page_title)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    if not page_id and not page_title:
        return "Error: Provide either page_id or page_title."

    try:
        if page_id:
            # get_page_by_id returns a dict with the full page including body,
            # or None if the page doesn't exist.
            page = conf.get_page_by_id(page_id, expand="body.storage")
        else:
            key = space_key.strip() or (get_confluence_space_key() or "")
            if not key:
                return "Error: space_key is required when using page_title. Set CONFLUENCE_SPACE_KEY in .env."
            page = conf.get_page_by_title(space=key, title=page_title, expand="body.storage")

        # atlassian-python-api returns None (or False in older versions) for not-found pages.
        if not page:
            ref = page_id or f"'{page_title}'"
            return f"Error: Confluence page {ref} not found."

        title = page.get("title", "Untitled")
        # body.storage.value is the raw XHTML storage format — strip to plain text.
        body_storage = page.get("body", {}).get("storage", {}).get("value", "")
        content = _strip_html_tags(body_storage)

        truncated = False
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS]
            truncated = True

        base_url = (get_jira_base_url() or "").rstrip("/")
        pid = page.get("id", page_id)
        web_ui = page.get("_links", {}).get("webui", f"/wiki/pages/{pid}")
        url = f"{base_url}{web_ui}"

        logger.debug("confluence_read_page fetched %r (%d chars)", title, len(content))
        header = f"=== {title} ===\nURL: {url}\n\n"
        suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
        return header + content + suffix

    except HTTPError as e:
        logger.error("Confluence API error in read_page: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_read_page: %s", e)
        return f"Error: {e}"


@tool
def confluence_read_space(space_key: str = "", limit: int = 25) -> str:
    """List pages in a Confluence space to discover available documentation.

    Returns page titles and IDs for up to limit pages. Use this to discover what
    docs exist (architecture pages, ADRs, runbooks, product specs) before calling
    confluence_read_page on specific ones. Falls back to CONFLUENCE_SPACE_KEY env
    var when space_key is not provided.
    """
    logger.debug("confluence_read_space called: space=%r, limit=%d", space_key, limit)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    key = space_key.strip() or (get_confluence_space_key() or "")
    if not key:
        return "Error: No space key provided and CONFLUENCE_SPACE_KEY is not set in .env."

    try:
        # get_all_pages_from_space returns a list of page dicts (id, title, type, status).
        pages = conf.get_all_pages_from_space(space=key, limit=limit)

        if not pages:
            return f"No pages found in Confluence space '{key}'."

        base_url = (get_jira_base_url() or "").rstrip("/")
        lines: list[str] = [f"Pages in Confluence space '{key}':", ""]

        for page in pages:
            title = page.get("title", "Untitled")
            page_id = page.get("id", "")
            web_ui = page.get("_links", {}).get("webui", f"/wiki/pages/{page_id}")
            url = f"{base_url}{web_ui}"
            lines.append(f"- {title} (ID: {page_id})")
            lines.append(f"  URL: {url}")

        logger.debug("confluence_read_space listed %d pages in space %s", len(pages), key)
        note = "; increase limit to see more" if len(pages) >= limit else ""
        lines.append("")
        lines.append(f"({len(pages)} pages shown{note})")
        return "\n".join(lines)

    except HTTPError as e:
        logger.error("Confluence API error in read_space: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_read_space: %s", e)
        return f"Error: {e}"


@tool
def confluence_create_page(
    title: str,
    body: str,
    space_key: str = "",
    parent_id: str = "",
) -> str:
    """Create a new Confluence page with the generated sprint plan or project brief.

    Only call this after the user has explicitly confirmed they want to publish to Confluence.
    Falls back to CONFLUENCE_SPACE_KEY env var when space_key is not provided.
    body accepts plain text (auto-converted to Confluence storage XHTML) or raw
    storage format XHTML (detected by a leading '<' character).
    parent_id optionally nests the new page under an existing page.
    Returns the new page's title, ID, and URL on success.
    """
    logger.debug("confluence_create_page called: title=%r, space=%r", title, space_key)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    key = space_key.strip() or (get_confluence_space_key() or "")
    if not key:
        return "Error: No space key provided and CONFLUENCE_SPACE_KEY is not set in .env."

    try:
        # Detect whether body is already storage XHTML (starts with '<') or plain text.
        # _text_to_storage wraps plain text paragraphs in <p> tags for Confluence.
        storage_body = body if body.strip().startswith("<") else _text_to_storage(body)

        page = conf.create_page(
            space=key,
            title=title,
            body=storage_body,
            parent_id=parent_id or None,
        )

        page_id = page.get("id", "")
        logger.debug("Created Confluence page %s (ID: %s)", title, page_id)
        base_url = (get_jira_base_url() or "").rstrip("/")
        web_ui = page.get("_links", {}).get("webui", f"/wiki/pages/{page_id}")
        url = f"{base_url}{web_ui}"
        return f"Created Confluence page: '{title}'\nID: {page_id}\nURL: {url}"

    except HTTPError as e:
        logger.error("Confluence API error in create_page: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_create_page: %s", e)
        return f"Error: {e}"


@tool
def confluence_update_page(
    page_id: str,
    body: str,
    title: str = "",
    version_comment: str = "",
) -> str:
    """Update an existing Confluence page (e.g. append a new sprint plan to a running log).

    Only call this after the user has explicitly confirmed they want to update the page.
    body accepts plain text (auto-converted to Confluence storage XHTML) or raw
    storage format XHTML (detected by a leading '<' character).
    If title is omitted, the existing page title is preserved.
    version_comment appears in the page's version history — use it to describe the change.
    Returns the updated page's title, ID, and URL on success.
    """
    logger.debug("confluence_update_page called: page_id=%r, title=%r", page_id, title)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    try:
        # Fetch the existing page to (a) confirm it exists and (b) get its current
        # title when the caller hasn't provided a new one.
        existing = conf.get_page_by_id(page_id)
        if not existing:
            return f"Error: Confluence page '{page_id}' not found."

        effective_title = title.strip() or existing.get("title", "")
        storage_body = body if body.strip().startswith("<") else _text_to_storage(body)

        # atlassian-python-api's update_page handles version incrementing internally.
        conf.update_page(
            page_id=page_id,
            title=effective_title,
            body=storage_body,
            version_comment=version_comment or None,
        )

        logger.debug("Updated Confluence page %s (ID: %s)", effective_title, page_id)
        base_url = (get_jira_base_url() or "").rstrip("/")
        web_ui = existing.get("_links", {}).get("webui", f"/wiki/pages/{page_id}")
        url = f"{base_url}{web_ui}"
        return f"Updated Confluence page: '{effective_title}'\nID: {page_id}\nURL: {url}"

    except HTTPError as e:
        logger.error("Confluence API error in update_page: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_update_page: %s", e)
        return f"Error: {e}"
