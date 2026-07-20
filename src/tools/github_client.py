"""GitHub REST client — repos, languages, README excerpts (truncated).

Pulls repo descriptions + top languages + README excerpts, not full source,
to keep downstream token cost down (design doc §3).
"""

import base64
import logging

import httpx

from src import config
from src.models.schemas import SourceDocument

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
README_EXCERPT_CHARS = 1500
MAX_REPOS = 30


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return headers


def fetch_github_profile(username: str, client: httpx.Client | None = None) -> SourceDocument:
    """Fetch a user's public repos, languages, and README excerpts.

    Args:
        username: GitHub username.
        client: Optional httpx client (injected in tests).

    Returns:
        A SourceDocument summarizing the user's public GitHub activity.
    """
    own_client = client is None
    client = client or httpx.Client(base_url=API_BASE, headers=_headers(), timeout=30)
    try:
        repos_resp = client.get(
            f"/users/{username}/repos",
            params={"sort": "updated", "per_page": MAX_REPOS, "type": "owner"},
        )
        repos_resp.raise_for_status()
        repos = [r for r in repos_resp.json() if not r.get("fork")]
        logger.debug(
            "github[%s]: %d non-fork repos: %s",
            username,
            len(repos),
            [r["name"] for r in repos],
        )

        sections: list[str] = [f"GitHub profile: {username}"]
        for repo in repos:
            name = repo["name"]
            lines = [f"\n## Repository: {name}"]
            if repo.get("description"):
                lines.append(f"Description: {repo['description']}")
            if repo.get("language"):
                lines.append(f"Primary language: {repo['language']}")
            lines.append(f"Stars: {repo.get('stargazers_count', 0)}")

            lang_resp = client.get(f"/repos/{username}/{name}/languages")
            if lang_resp.status_code == 200 and lang_resp.json():
                lines.append("Languages: " + ", ".join(lang_resp.json()))

            readme_resp = client.get(f"/repos/{username}/{name}/readme")
            if readme_resp.status_code == 200:
                content = readme_resp.json().get("content", "")
                try:
                    text = base64.b64decode(content).decode("utf-8", errors="replace")
                    lines.append("README excerpt:\n" + text[:README_EXCERPT_CHARS])
                except (ValueError, TypeError):
                    pass
            logger.debug(
                "github[%s]: repo %s: language=%s stars=%s readme=%s",
                username,
                name,
                repo.get("language"),
                repo.get("stargazers_count", 0),
                "yes" if readme_resp.status_code == 200 else "no",
            )
            sections.append("\n".join(lines))

        raw_text = "\n".join(sections)
        logger.debug(
            "github[%s]: source document %d chars:\n%s", username, len(raw_text), raw_text
        )
        return SourceDocument(
            id=f"github:{username}",
            source_type="github",
            raw_text=raw_text,
        )
    finally:
        if own_client:
            client.close()


def free_text_source(text: str) -> SourceDocument:
    """Wrap pasted free text (bio, notes) as a SourceDocument passthrough."""
    return SourceDocument(id="free_text", source_type="free_text", raw_text=text.strip())
