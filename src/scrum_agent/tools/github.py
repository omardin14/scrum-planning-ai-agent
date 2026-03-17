"""GitHub read-only tools for fetching repo context.

# See README: "Tools" — tool types, @tool decorator, risk levels
#
# All four tools are read-only (low risk) — they fetch public data from the
# GitHub API and return it as a string for the LLM to reason about. The LLM
# uses these tools in the ReAct loop (Thought → Action → Observation) to
# ground its scrum planning in the actual codebase.
#
# Why PyGithub instead of raw requests?
# PyGithub wraps the REST API with typed objects, handles pagination, and
# raises structured exceptions (GithubException, RateLimitExceededException).
# This makes error handling predictable across all four tools.
"""

import logging

import github
from langchain_core.tools import tool

from scrum_agent.config import get_github_token

logger = logging.getLogger(__name__)

# Truncate file/README content at this many characters to avoid flooding the LLM context.
_MAX_CONTENT_CHARS = 8_000

# Key config/manifest files to highlight in the repo tree summary.
_KEY_FILES = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".github",
    "README.md",
    "README.rst",
    "CONTRIBUTING.md",
    "Makefile",
    "requirements.txt",
    ".env.example",
    "tsconfig.json",
    "webpack.config.js",
    "vite.config.ts",
    "vite.config.js",
}


def _parse_repo(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL or pass through if already a slug.

    Handles:
    - https://github.com/owner/repo
    - http://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo/
    - owner/repo  (already a slug — returned unchanged)
    """
    url = url.strip().rstrip("/")
    if url.startswith(("https://github.com/", "http://github.com/")):
        url = url.split("github.com/", 1)[1]
    if url.endswith(".git"):
        url = url[:-4]
    # Strip any trailing path segments (e.g. /tree/main, /issues)
    parts = url.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return url


def _get_github_client() -> github.Github:
    """Create a PyGithub client, using GITHUB_TOKEN if available."""
    token = get_github_token()
    # PyGithub accepts token=None for unauthenticated access (lower rate limits).
    # See README: "Tools" — authentication pattern
    if not token:
        logger.warning("No GITHUB_TOKEN set — using unauthenticated access (60 req/hr)")
    logger.debug("Creating GitHub client (authenticated=%s)", bool(token))
    return github.Github(auth=github.Auth.Token(token) if token else None)


@tool
def github_read_repo(repo_url: str, max_depth: int = 2) -> str:
    """Read the repository file tree and return a structured summary.

    Returns the top-level directory structure (up to max_depth), detected tech
    stack files (package.json, pyproject.toml, Dockerfile, etc.), and language
    breakdown. Use this first to understand a project's structure before reading
    individual files.
    """
    # See README: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("github_read_repo called: repo_url=%r, max_depth=%d", repo_url, max_depth)
    try:
        slug = _parse_repo(repo_url)
        g = _get_github_client()
        repo = g.get_repo(slug)

        # get_git_tree with recursive=True fetches the full tree in one API call.
        # We filter to max_depth to avoid overwhelming the LLM with deep paths.
        tree = repo.get_git_tree(sha="HEAD", recursive=True)

        lines: list[str] = [f"Repository: {slug}", f"Default branch: {repo.default_branch}", ""]

        # Separate directories and files, filtered to max_depth
        dirs: set[str] = set()
        files_at_depth: list[str] = []
        key_files_found: list[str] = []

        for item in tree.tree:
            parts = item.path.split("/")
            depth = len(parts)
            name = parts[-1]

            if depth <= max_depth:
                if item.type == "tree":
                    dirs.add(item.path)
                else:
                    files_at_depth.append(item.path)

            # Collect key files regardless of depth
            if name in _KEY_FILES or item.path in _KEY_FILES:
                key_files_found.append(item.path)

        # Build a simple indented tree from top-level items
        lines.append("File tree (top level):")
        top_level = sorted({p.split("/")[0] for p in [i.path for i in tree.tree]})
        for entry in top_level[:50]:  # cap at 50 top-level entries
            lines.append(f"  {entry}/")

        if key_files_found:
            lines.append("")
            lines.append("Key files detected:")
            for kf in sorted(key_files_found):
                lines.append(f"  {kf}")

        # Language breakdown from GitHub's language API
        try:
            languages = repo.get_languages()
            if languages:
                total = sum(languages.values())
                lines.append("")
                lines.append("Languages:")
                for lang, bytes_count in sorted(languages.items(), key=lambda x: -x[1])[:5]:
                    pct = bytes_count / total * 100
                    lines.append(f"  {lang}: {pct:.1f}%")
        except github.GithubException:
            pass  # Language data not critical — skip silently

        lines.append("")
        lines.append(
            f"Stars: {repo.stargazers_count}  Forks: {repo.forks_count}  Open issues: {repo.open_issues_count}"
        )
        if repo.description:
            lines.append(f"Description: {repo.description}")

        logger.debug("github_read_repo completed for %s", slug)
        return "\n".join(lines)

    except github.RateLimitExceededException:
        logger.warning("GitHub rate limit exceeded in github_read_repo")
        return "GitHub rate limit reached. Add a GITHUB_TOKEN to .env to raise limits from 60 to 5 000 req/hr."
    except github.GithubException as e:
        logger.error("GitHub API error in github_read_repo: %s", e)
        return f"Error: {e.data.get('message', str(e)) if isinstance(e.data, dict) else str(e)}"
    except Exception as e:
        logger.error("Unexpected error in github_read_repo: %s", e)
        return f"Error: {e}"


@tool
def github_read_file(repo_url: str, file_path: str) -> str:
    """Fetch the raw contents of a specific file from a GitHub repository.

    Use this after github_read_repo identifies an important file (e.g. README,
    package.json, Dockerfile, main source file). Truncates at 8 000 characters
    with a note if the file is larger.
    """
    logger.debug("github_read_file called: repo=%r, path=%r", repo_url, file_path)
    try:
        slug = _parse_repo(repo_url)
        g = _get_github_client()
        repo = g.get_repo(slug)

        # get_contents() returns a ContentFile with base64-encoded content.
        # It raises UnknownObjectException (404) if the file does not exist.
        content_file = repo.get_contents(file_path)

        # content_file may be a list if file_path is a directory — guard against it.
        if isinstance(content_file, list):
            entries = [f.path for f in content_file]
            return "Path is a directory. Contents:\n" + "\n".join(f"  {e}" for e in entries)

        decoded = content_file.decoded_content.decode("utf-8", errors="replace")

        truncated = False
        if len(decoded) > _MAX_CONTENT_CHARS:
            decoded = decoded[:_MAX_CONTENT_CHARS]
            truncated = True

        logger.debug("github_read_file fetched %s (%d bytes)", file_path, content_file.size)
        header = f"File: {file_path} ({content_file.size} bytes)\n\n"
        suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
        return header + decoded + suffix

    except github.RateLimitExceededException:
        logger.warning("GitHub rate limit exceeded in github_read_file")
        return "GitHub rate limit reached. Add a GITHUB_TOKEN to .env to raise limits from 60 to 5 000 req/hr."
    except github.GithubException as e:
        logger.error("GitHub API error in github_read_file: %s", e)
        return f"Error: {e.data.get('message', str(e)) if isinstance(e.data, dict) else str(e)}"
    except Exception as e:
        logger.error("Unexpected error in github_read_file: %s", e)
        return f"Error: {e}"


@tool
def github_list_issues(repo_url: str, state: str = "open", max_issues: int = 20) -> str:
    """List issues and pull requests from a GitHub repository.

    Returns issue number, title, labels, and first 200 characters of the body
    for up to max_issues results. Use this to understand current work in progress,
    known bugs, and planned features that should inform the scrum plan.
    state: 'open' (default), 'closed', or 'all'.
    """
    logger.debug("github_list_issues called: repo=%r, state=%s, max=%d", repo_url, state, max_issues)
    try:
        slug = _parse_repo(repo_url)
        g = _get_github_client()
        repo = g.get_repo(slug)

        # get_issues() returns a PaginatedList — slicing triggers lazy pagination.
        # state must be "open", "closed", or "all" (validated by PyGithub).
        issues = repo.get_issues(state=state)

        lines: list[str] = [f"Issues ({state}) for {slug}:", ""]

        count = 0
        for issue in issues:
            if count >= max_issues:
                break
            labels = ", ".join(label.name for label in issue.labels)
            label_str = f" [{labels}]" if labels else ""
            pr_tag = " [PR]" if issue.pull_request else ""
            body_preview = ""
            if issue.body:
                body_preview = issue.body[:200].replace("\n", " ").strip()
                if len(issue.body) > 200:
                    body_preview += "..."

            lines.append(f"#{issue.number}{pr_tag}: {issue.title}{label_str}")
            if body_preview:
                lines.append(f"  {body_preview}")
            count += 1

        if count == 0:
            lines.append(f"No {state} issues found.")
        else:
            lines.append("")
            note = "; increase max_issues to see more" if count >= max_issues else ""
            lines.append(f"({count} issues shown{note})")

        logger.debug("github_list_issues returned %d issues for %s", count, slug)
        return "\n".join(lines)

    except github.RateLimitExceededException:
        logger.warning("GitHub rate limit exceeded in github_list_issues")
        return "GitHub rate limit reached. Add a GITHUB_TOKEN to .env to raise limits from 60 to 5 000 req/hr."
    except github.GithubException as e:
        logger.error("GitHub API error in github_list_issues: %s", e)
        return f"Error: {e.data.get('message', str(e)) if isinstance(e.data, dict) else str(e)}"
    except Exception as e:
        logger.error("Unexpected error in github_list_issues: %s", e)
        return f"Error: {e}"


@tool
def github_read_readme(repo_url: str) -> str:
    """Fetch the README and CONTRIBUTING docs from a GitHub repository.

    Returns the decoded README content (truncated at 8 000 chars) and
    CONTRIBUTING.md if present. Use this to understand the project's purpose,
    architecture, and contribution guidelines.
    """
    logger.debug("github_read_readme called: repo=%r", repo_url)
    try:
        slug = _parse_repo(repo_url)
        g = _get_github_client()
        repo = g.get_repo(slug)

        sections: list[str] = []

        # get_readme() finds README.md, README.rst, README.txt, etc. automatically.
        # Raises UnknownObjectException if no README exists.
        try:
            readme = repo.get_readme()
            content = readme.decoded_content.decode("utf-8", errors="replace")
            truncated = False
            if len(content) > _MAX_CONTENT_CHARS:
                content = content[:_MAX_CONTENT_CHARS]
                truncated = True
            header = f"=== README ({readme.path}) ===\n\n"
            suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
            sections.append(header + content + suffix)
        except github.GithubException:
            sections.append("=== README ===\n\nNo README found in this repository.")

        # Try to fetch CONTRIBUTING.md — not always present, so handle 404 gracefully.
        try:
            contributing = repo.get_contents("CONTRIBUTING.md")
            if not isinstance(contributing, list):
                contrib_content = contributing.decoded_content.decode("utf-8", errors="replace")
                if len(contrib_content) > _MAX_CONTENT_CHARS:
                    contrib_content = contrib_content[:_MAX_CONTENT_CHARS]
                    contrib_content += f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]"
                sections.append(f"\n=== CONTRIBUTING.md ===\n\n{contrib_content}")
        except github.GithubException:
            pass  # CONTRIBUTING.md is optional — skip silently

        logger.debug("github_read_readme completed for %s", slug)
        return "\n".join(sections)

    except github.RateLimitExceededException:
        logger.warning("GitHub rate limit exceeded in github_read_readme")
        return "GitHub rate limit reached. Add a GITHUB_TOKEN to .env to raise limits from 60 to 5 000 req/hr."
    except github.GithubException as e:
        logger.error("GitHub API error in github_read_readme: %s", e)
        return f"Error: {e.data.get('message', str(e)) if isinstance(e.data, dict) else str(e)}"
    except Exception as e:
        logger.error("Unexpected error in github_read_readme: %s", e)
        return f"Error: {e}"
