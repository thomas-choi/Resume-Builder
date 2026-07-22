"""GitHub client — owned repos, org/collaborator repos, and external contributions.

Pulls repo descriptions + top languages + README excerpts, not full source,
to keep downstream token cost down (design doc §3).

Coverage (PLAN.md Phase 1.f/1.g) is three-tiered, and the assembled source
document labels each tier explicitly so downstream extraction can never mistake
a contribution to someone else's project for authorship of it:

1. **Owned** — repos under the personal username.
2. **Organization / collaborator** — repos whose owner is not the user,
   attributed to their owning org, and kept only when the user actually
   committed to them (see below).
3. **External contributions** — repos the user neither owns nor is a member of,
   discovered from merged PRs (REST search) and, when ``GITHUB_TOKEN`` is set,
   GraphQL ``repositoriesContributedTo`` + per-repo commit counts. These carry a
   contribution scope and PR titles but **no README excerpt** — a README
   describes the project, not the contribution.

Two properties of the GitHub API shape how tiers 1-2 are collected:

*Membership is private by default.* ``GET /users/{u}/orgs`` lists only **public**
memberships, so a user whose memberships are all private looks org-less. When
the token belongs to the very username being ingested ("self-token"), the
viewer endpoints ``GET /user/orgs`` and ``GET /user/repos?affiliation=...`` are
used instead: they see private memberships and private repos. This never applies
to a third party — a token for someone else falls back to the public endpoints,
so it can never surface another account's private data.

The token is resolved **per call** (PLAN.md Phase 5.a): the caller may pass one
for the username being ingested, and ``config.GITHUB_TOKEN`` is only the
fallback. One process can therefore serve several users' tokens without a
restart, and the self-token determination is made per request rather than once
at import.

*Access is not contribution.* ``affiliation=collaborator`` returns every repo the
user was ever invited to, the overwhelming majority of which they never touched.
Non-owned repos are therefore kept only on evidence of a commit
(``GET /repos/{full}/commits?author={u}``, one probe per candidate, skipped when
the PR/GraphQL evidence already proves it).
"""

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

import httpx

from src import config
from src.models.schemas import SourceDocument

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
README_EXCERPT_CHARS = 1500
MAX_REPOS = 30
# Merged-PR search is paged once (never exhaustively) — Fix 4, call budget.
SEARCH_PER_PAGE = 100
MAX_PR_TITLES = 8
# repositoriesContributedTo is all-time; this caps the single GraphQL page.
GRAPHQL_CONTRIBUTED_REPOS = 50
# Pagination ceiling for the authenticated repo listing (100 repos per page).
PER_PAGE = 100
MAX_PAGES = 5

_CONTRIBUTED_REPOS_QUERY = """
query($login: String!, $first: Int!) {
  user(login: $login) {
    createdAt
    repositoriesContributedTo(
      first: $first
      includeUserRepositories: false
      contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]
      orderBy: {field: STARGAZERS, direction: DESC}
    ) {
      nodes {
        nameWithOwner
        description
        stargazerCount
        isFork
        primaryLanguage { name }
      }
    }
  }
}
"""

_COMMIT_COUNTS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      commitContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
        contributions { totalCount }
      }
    }
  }
}
"""


def _headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _repo_owner(repo: dict, username: str) -> str:
    """Owner login of a repo payload, defaulting to the queried username."""
    return (repo.get("owner") or {}).get("login") or username


def _repo_full_name(repo: dict, username: str) -> str:
    return repo.get("full_name") or f"{_repo_owner(repo, username)}/{repo['name']}"


def _viewer_login(client: httpx.Client, token: str | None) -> str | None:
    """Login of the account owning ``token``; ``None`` when tokenless."""
    if not token:
        return None
    try:
        resp = client.get("/user")
    except httpx.HTTPError as exc:  # pragma: no cover - network-only path
        logger.warning("github: /user failed (%s) — public endpoints only", exc)
        return None
    if resp.status_code != 200:
        logger.warning(
            "github: /user returned %s — public endpoints only", resp.status_code
        )
        return None
    return resp.json().get("login")


def _paged(client: httpx.Client, path: str, params: dict) -> list[dict]:
    """Follow up to ``MAX_PAGES`` pages of a list endpoint (best effort)."""
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        resp = client.get(path, params={**params, "per_page": PER_PAGE, "page": page})
        if resp.status_code != 200:
            logger.warning("github: %s page %d returned %s", path, page, resp.status_code)
            break
        batch = resp.json()
        if not batch:
            break
        items.extend(batch)
        if len(batch) < PER_PAGE:
            break
    return items


def _fetch_orgs(client: httpx.Client, username: str, is_self: bool) -> list[str]:
    """Org memberships (best effort — never fatal).

    The public ``/users/{u}/orgs`` lists *public* memberships only; with a
    self-token ``/user/orgs`` also lists the private ones, which are GitHub's
    default and are otherwise invisible.
    """
    path = "/user/orgs" if is_self else f"/users/{username}/orgs"
    try:
        resp = client.get(path, params={"per_page": PER_PAGE})
        if resp.status_code != 200:
            logger.warning(
                "github[%s]: %s returned %s — org attribution degraded",
                username,
                path,
                resp.status_code,
            )
            return []
        return [o["login"] for o in resp.json() if o.get("login")]
    except httpx.HTTPError as exc:  # pragma: no cover - network-only path
        logger.warning("github[%s]: %s failed (%s)", username, path, exc)
        return []


def _list_repos(client: httpx.Client, username: str, is_self: bool) -> list[dict]:
    """Candidate repos (forks dropped), from the widest endpoint available.

    With a self-token this is ``/user/repos`` across all three affiliations,
    which alone can see private repos and private org membership; otherwise the
    public ``/users/{u}/repos?type=all``.
    """
    if is_self:
        repos = _paged(
            client,
            "/user/repos",
            {
                "affiliation": "owner,organization_member,collaborator",
                "sort": "updated",
            },
        )
        if not config.GITHUB_INCLUDE_PRIVATE:
            repos = [r for r in repos if not r.get("private")]
            logger.debug("github[%s]: private repos excluded by config", username)
    else:
        resp = client.get(
            f"/users/{username}/repos",
            params={"sort": "updated", "per_page": MAX_REPOS, "type": "all"},
        )
        resp.raise_for_status()
        repos = resp.json()
    return [r for r in repos if not r.get("fork")]


def _keep_contributed(
    client: httpx.Client, username: str, repos: list[dict], proven: set[str]
) -> list[dict]:
    """Filter non-owned repos down to those the user actually committed to.

    Being listed under ``organization_member`` or ``collaborator`` only means
    *access* — most such repos were never touched by the user, and rendering
    them would invite the extractor to claim someone else's work. Repos already
    proven by PR/GraphQL evidence skip the probe; the rest cost one
    ``/commits?author=`` request each, bounded by
    ``GITHUB_MAX_CONTRIBUTION_PROBES``. A rate-limited probe stops the sweep
    with a WARNING rather than failing the ingest.

    Args:
        client: httpx client bound to the GitHub API.
        username: GitHub username whose commits count as contributions.
        repos: candidate repo payloads not owned by the user.
        proven: lowercased ``owner/name`` already known to have contributions.

    Returns:
        The surviving repo payloads, each annotated with ``_contributed_at``.
    """
    kept: list[dict] = []
    probes = 0
    degraded = False
    for repo in repos:
        full_name = _repo_full_name(repo, username)
        if full_name.lower() in proven:
            repo["_contributed_at"] = repo.get("pushed_at") or ""
            kept.append(repo)
            continue
        if degraded or probes >= config.GITHUB_MAX_CONTRIBUTION_PROBES:
            continue
        probes += 1
        try:
            resp = client.get(
                f"/repos/{full_name}/commits",
                params={"author": username, "per_page": 1},
            )
        except httpx.HTTPError as exc:  # pragma: no cover - network-only path
            logger.warning("github[%s]: commit probe failed (%s)", full_name, exc)
            continue
        if resp.status_code in (403, 429):
            logger.warning(
                "github[%s]: commit probe returned %s after %d probes — remaining "
                "organization repos dropped",
                username,
                resp.status_code,
                probes,
            )
            degraded = True
            continue
        # 409 = empty repo; 404 = gone. Anything non-200 is not evidence.
        if resp.status_code != 200:
            continue
        commits = resp.json()
        if not commits:
            continue
        repo["_contributed_at"] = (
            ((commits[0].get("commit") or {}).get("author") or {}).get("date") or ""
        )
        kept.append(repo)
    logger.debug(
        "github[%s]: %d/%d non-owned repos have commits by the user (%d probes)",
        username,
        len(kept),
        len(repos),
        probes,
    )
    return kept


def _search_merged_prs(client: httpx.Client, username: str) -> dict[str, dict]:
    """Merged PRs authored by the user, aggregated per repository.

    A single search page (Fix 4). A 403/429 (rate limit) degrades to an empty
    result with a WARNING rather than failing the ingest.

    Returns:
        ``{full_name: {"pr_count": int, "pr_titles": [str, ...]}}``.
    """
    try:
        resp = client.get(
            "/search/issues",
            params={
                "q": f"author:{username} type:pr is:merged",
                "per_page": SEARCH_PER_PAGE,
                "sort": "updated",
            },
        )
    except httpx.HTTPError as exc:  # pragma: no cover - network-only path
        logger.warning("github[%s]: merged-PR search failed (%s)", username, exc)
        return {}
    if resp.status_code != 200:
        logger.warning(
            "github[%s]: merged-PR search returned %s — external contributions "
            "degraded to owned + org repos",
            username,
            resp.status_code,
        )
        return {}

    per_repo: dict[str, dict] = {}
    for item in resp.json().get("items", []):
        repo_url = item.get("repository_url", "")
        if "/repos/" not in repo_url:
            continue
        full_name = repo_url.split("/repos/", 1)[1]
        entry = per_repo.setdefault(full_name, {"pr_count": 0, "pr_titles": []})
        entry["pr_count"] += 1
        title = (item.get("title") or "").strip()
        if title and len(entry["pr_titles"]) < MAX_PR_TITLES:
            entry["pr_titles"].append(title)
    logger.debug(
        "github[%s]: merged-PR search matched %d repos", username, len(per_repo)
    )
    return per_repo


def _graphql(
    client: httpx.Client, query: str, variables: dict, token: str | None
) -> dict | None:
    """POST a GraphQL query; ``None`` on any failure (caller degrades)."""
    try:
        resp = client.post(
            "/graphql",
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:  # pragma: no cover - network-only path
        logger.warning("github: GraphQL request failed (%s)", exc)
        return None
    if resp.status_code != 200:
        logger.warning("github: GraphQL returned %s", resp.status_code)
        return None
    payload = resp.json()
    if payload.get("errors"):
        logger.warning("github: GraphQL errors: %s", payload["errors"])
        return None
    return payload.get("data") or None


def _graphql_contributions(
    client: httpx.Client, username: str, token: str | None
) -> tuple[dict[str, dict], dict[str, int]]:
    """Contributed repos + per-repo commit counts via GraphQL (token required).

    ``contributionsCollection`` defaults to the **last 12 months**, so commit
    counts are collected by looping ``from``/``to`` over the user's active years.

    Returns:
        ``({full_name: {description, language, stars}}, {full_name: commits})``.
    """
    data = _graphql(
        client,
        _CONTRIBUTED_REPOS_QUERY,
        {"login": username, "first": GRAPHQL_CONTRIBUTED_REPOS},
        token,
    )
    user = (data or {}).get("user") or {}
    repos: dict[str, dict] = {}
    for node in (user.get("repositoriesContributedTo") or {}).get("nodes") or []:
        if not node or node.get("isFork"):
            continue
        repos[node["nameWithOwner"]] = {
            "description": node.get("description") or "",
            "language": (node.get("primaryLanguage") or {}).get("name") or "",
            "stars": node.get("stargazerCount") or 0,
        }

    commits: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    created = user.get("createdAt") or ""
    try:
        first_year = datetime.fromisoformat(created.replace("Z", "+00:00")).year
    except ValueError:
        first_year = now.year
    for year in range(first_year, now.year + 1):
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc), now)
        if end <= start:
            continue
        year_data = _graphql(
            client,
            _COMMIT_COUNTS_QUERY,
            {
                "login": username,
                "from": start.isoformat().replace("+00:00", "Z"),
                "to": end.isoformat().replace("+00:00", "Z"),
            },
            token,
        )
        collection = ((year_data or {}).get("user") or {}).get(
            "contributionsCollection"
        ) or {}
        for item in collection.get("commitContributionsByRepository") or []:
            full_name = (item.get("repository") or {}).get("nameWithOwner")
            if not full_name:
                continue
            commits[full_name] = commits.get(full_name, 0) + (
                (item.get("contributions") or {}).get("totalCount") or 0
            )
    logger.debug(
        "github[%s]: GraphQL contributed repos=%d, commit-counted repos=%d",
        username,
        len(repos),
        len(commits),
    )
    return repos, commits


def _gather_evidence(
    client: httpx.Client, username: str, token: str | None
) -> tuple[dict[str, dict], dict[str, dict], dict[str, int]]:
    """Contribution evidence, gathered once and shared by both consumers.

    Returns:
        ``(merged PRs per repo, GraphQL repo metadata, commit counts per repo)``.
    """
    prs = _search_merged_prs(client, username)
    graph_repos: dict[str, dict] = {}
    commits: dict[str, int] = {}
    if token:
        graph_repos, commits = _graphql_contributions(client, username, token)
    return prs, graph_repos, commits


def _external_contributions(
    username: str,
    known: set[str],
    prs: dict[str, dict],
    graph_repos: dict[str, dict],
    commits: dict[str, int],
) -> list[dict]:
    """Repos the user neither owns nor is a member of, ranked by contribution.

    Args:
        username: GitHub username.
        known: lowercased ``owner/name`` of repos already covered by the owned
            and organization sections (excluded here to avoid double-counting).
        prs: merged-PR aggregates from :func:`_search_merged_prs`.
        graph_repos: repo metadata from ``repositoriesContributedTo``.
        commits: per-repo commit counts from ``contributionsCollection``.

    Returns:
        External repo dicts with ``full_name``, ``description``, ``language``,
        ``stars``, ``pr_count``, ``commit_count`` and ``pr_titles``, sorted by
        contribution volume and capped at ``GITHUB_MAX_EXTERNAL_REPOS``.
    """
    externals: list[dict] = []
    for full_name in set(prs) | set(graph_repos) | set(commits):
        owner = full_name.split("/", 1)[0]
        if owner.lower() == username.lower() or full_name.lower() in known:
            continue
        meta = graph_repos.get(full_name, {})
        pr_entry = prs.get(full_name, {})
        externals.append(
            {
                "full_name": full_name,
                "description": meta.get("description", ""),
                "language": meta.get("language", ""),
                "stars": meta.get("stars", 0),
                "pr_count": pr_entry.get("pr_count", 0),
                "pr_titles": pr_entry.get("pr_titles", []),
                "commit_count": commits.get(full_name, 0),
            }
        )
    # Ranked by contribution volume, not `updated` — the most-contributed-to
    # external repos are the ones worth the truncation budget.
    externals.sort(
        key=lambda r: (r["pr_count"] + r["commit_count"], r["stars"]), reverse=True
    )
    capped = externals[: config.GITHUB_MAX_EXTERNAL_REPOS]
    logger.debug(
        "github[%s]: %d external repos (capped to %d): %s",
        username,
        len(externals),
        len(capped),
        [r["full_name"] for r in capped],
    )
    return capped


def _rank_org_repos(repos: list[dict], username: str, limit: int) -> list[dict]:
    """Cap organization repos by recency while keeping every organization.

    A straight recency sort silently drops whole employers: an org the user last
    committed to in 2021 loses to five repos from a current one. A resume needs
    the breadth of organizations more than an extra repo from the same one, so
    each org's most recent repo is seeded first and the remaining budget is
    filled by recency.

    Args:
        repos: contributed, non-owned repo payloads (``_contributed_at`` set).
        username: GitHub username, used to resolve the owning org.
        limit: maximum repos to keep.

    Returns:
        At most ``limit`` repos, newest contribution first.
    """
    by_recency = sorted(repos, key=lambda r: r.get("_contributed_at") or "", reverse=True)
    by_owner: dict[str, list[dict]] = {}
    for repo in by_recency:
        by_owner.setdefault(_repo_owner(repo, username), []).append(repo)

    seeded = [repos[0] for repos in by_owner.values()]
    seeded.sort(key=lambda r: r.get("_contributed_at") or "", reverse=True)
    selected = seeded[:limit]
    if len(seeded) > limit:
        logger.warning(
            "github[%s]: %d organizations exceed GITHUB_MAX_ORG_REPOS=%d — the "
            "oldest are dropped entirely",
            username,
            len(seeded),
            limit,
        )
    remaining = [r for r in by_recency if r not in selected]
    selected.extend(remaining[: max(0, limit - len(selected))])
    selected.sort(key=lambda r: r.get("_contributed_at") or "", reverse=True)
    return selected


def _render_repo(client: httpx.Client, repo: dict, full_name: str) -> str:
    """Render one owned/org repo: metadata + languages + README excerpt."""
    lines = [f"\n### Repository: {full_name}"]
    if repo.get("description"):
        lines.append(f"Description: {repo['description']}")
    if repo.get("language"):
        lines.append(f"Primary language: {repo['language']}")
    if repo.get("private"):
        # Flagged so the resume never offers a private repo as a public link.
        lines.append("Visibility: private (cannot be linked or shown publicly)")
    lines.append(f"Stars: {repo.get('stargazers_count', 0)}")

    lang_resp = client.get(f"/repos/{full_name}/languages")
    if lang_resp.status_code == 200 and lang_resp.json():
        lines.append("Languages: " + ", ".join(lang_resp.json()))

    readme_resp = client.get(f"/repos/{full_name}/readme")
    if readme_resp.status_code == 200:
        content = readme_resp.json().get("content", "")
        try:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
            # Quoted: READMEs carry their own `##` headings, which would
            # otherwise be read as section boundaries of this document and blur
            # the owned/org/external labelling.
            excerpt = "\n".join(
                f"> {line}" for line in text[:README_EXCERPT_CHARS].splitlines()
            )
            lines.append("README excerpt:\n" + excerpt)
        except (ValueError, TypeError):
            pass
    logger.debug(
        "github: repo %s: language=%s stars=%s readme=%s",
        full_name,
        repo.get("language"),
        repo.get("stargazers_count", 0),
        "yes" if readme_resp.status_code == 200 else "no",
    )
    return "\n".join(lines)


def _render_external(repo: dict) -> str:
    """Render one external repo: attribution + contribution scope, no README."""
    lines = [f"\n### Repository: {repo['full_name']} (owned by others)"]
    if repo.get("description"):
        lines.append(f"Project description (not written by the user): {repo['description']}")
    if repo.get("language"):
        lines.append(f"Primary language: {repo['language']}")
    scope = []
    if repo.get("pr_count"):
        n = repo["pr_count"]
        scope.append(f"{n} merged pull request{'s' if n != 1 else ''}")
    if repo.get("commit_count"):
        n = repo["commit_count"]
        scope.append(f"{n} commit{'s' if n != 1 else ''}")
    lines.append("Contribution scope: " + ("; ".join(scope) or "contributor"))
    if repo.get("pr_titles"):
        lines.append(
            "Merged pull requests by the user:\n"
            + "\n".join(f"- {t}" for t in repo["pr_titles"])
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class RepoChunk:
    """One repository's slice of a rendered GitHub source document.

    Attributes:
        repo: ``owner/name`` of the repository this chunk describes.
        tier: The verbatim span of the ``## …`` tier heading and its prose
            preamble. Carried on every chunk because
            ``skills/source-extraction/SKILL.md`` decides ownership-vs-contribution
            attribution from exactly that labelling — a repo extracted without
            its heading is liable to be mis-attributed as authorship.
        body: The verbatim ``### Repository: …`` span.
    """

    repo: str
    tier: str
    body: str


_TIER_RE = re.compile(r"^## .*$", re.MULTILINE)
_REPO_RE = re.compile(r"^### Repository: (\S+)", re.MULTILINE)


def split_repo_sections(raw_text: str) -> tuple[str, list[RepoChunk]]:
    """Split a rendered GitHub document into per-repository chunks.

    The spans tile the document exactly, so
    ``render_repo_document(*split_repo_sections(text)) == text``: splitting and
    re-rendering an untouched document is a no-op, and a *pruned* re-render
    differs from the original only by the repos that were dropped.

    Args:
        raw_text: The ``raw_text`` of a ``source_type="github"`` document, as
            assembled by :func:`fetch_github_profile`.

    Returns:
        ``(header, chunks)`` — the leading profile/membership lines, and one
        :class:`RepoChunk` per repository in document order. A document with no
        ``### Repository:`` sections yields ``(raw_text, [])``.
    """
    boundaries = sorted(
        [(m.start(), "tier", None) for m in _TIER_RE.finditer(raw_text)]
        + [(m.start(), "repo", m.group(1)) for m in _REPO_RE.finditer(raw_text)]
    )
    if not boundaries:
        return raw_text, []

    header = raw_text[: boundaries[0][0]]
    chunks: list[RepoChunk] = []
    tier = ""
    for index, (start, kind, repo) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(raw_text)
        span = raw_text[start:end]
        if kind == "tier":
            tier = span
        else:
            chunks.append(RepoChunk(repo=repo, tier=tier, body=span))
    return header, chunks


def render_repo_document(header: str, chunks: Sequence[RepoChunk]) -> str:
    """Reassemble a GitHub document from a subset of its repository chunks.

    Each tier heading is emitted once, before the first surviving repo under it;
    a tier whose repos were all dropped disappears with them.
    """
    parts = [header]
    last_tier: str | None = None
    for chunk in chunks:
        if chunk.tier != last_tier:
            parts.append(chunk.tier)
            last_tier = chunk.tier
        parts.append(chunk.body)
    return "".join(parts)


def fetch_github_profile(
    username: str,
    client: httpx.Client | None = None,
    token: str | None = None,
) -> SourceDocument:
    """Fetch a user's public GitHub footprint as one labelled SourceDocument.

    Covers repos the user owns, repos owned by organizations they belong to or
    collaborate on, and — unless ``GITHUB_INCLUDE_CONTRIBUTIONS`` is off —
    external repos they contributed to. Each tier is rendered under its own
    heading so ownership is never implied for contributions.

    Args:
        username: GitHub username.
        client: Optional httpx client (injected in tests).
        token: GitHub token for this request, falling back to
            ``config.GITHUB_TOKEN``. A token belonging to ``username`` also
            unlocks their private repos and private org memberships; anyone
            else's only raises rate limits.

    Returns:
        A SourceDocument summarizing the user's public GitHub activity.
    """
    token = token or config.GITHUB_TOKEN
    own_client = client is None
    client = client or httpx.Client(
        base_url=API_BASE, headers=_headers(token), timeout=30
    )
    try:
        viewer = _viewer_login(client, token)
        is_self = bool(viewer) and viewer.lower() == username.lower()
        logger.debug(
            "github[%s]: token viewer=%s self=%s", username, viewer or "-", is_self
        )
        repos = _list_repos(client, username, is_self)

        owned: list[dict] = []
        candidates: list[dict] = []
        for repo in repos:
            if _repo_owner(repo, username).lower() == username.lower():
                owned.append(repo)
            else:
                candidates.append(repo)
        owned = owned[:MAX_REPOS]

        # Gathered before the org filter: a repo already proven by a merged PR
        # or a GraphQL commit count needs no probe.
        prs: dict[str, dict] = {}
        graph_repos: dict[str, dict] = {}
        commits: dict[str, int] = {}
        if config.GITHUB_INCLUDE_CONTRIBUTIONS:
            prs, graph_repos, commits = _gather_evidence(client, username, token)
        else:
            logger.debug(
                "github[%s]: GITHUB_INCLUDE_CONTRIBUTIONS off — owned + org only",
                username,
            )
        proven = {name.lower() for name in set(prs) | set(commits)}

        contributed = _keep_contributed(client, username, candidates, proven)
        # Capped by recency: access to an org can span far more repos than a
        # resume can carry.
        contributed = _rank_org_repos(
            contributed, username, config.GITHUB_MAX_ORG_REPOS
        )
        by_org: dict[str, list[dict]] = {}
        for repo in contributed:
            by_org.setdefault(_repo_owner(repo, username), []).append(repo)
        logger.debug(
            "** github[%s]: %d non-fork repos (%d owned, %d of %d org/collaborator "
            "kept): %s",
            username,
            len(repos),
            len(owned),
            len(contributed),
            len(candidates),
            [_repo_full_name(r, username) for r in contributed],
        )

        orgs = _fetch_orgs(client, username, is_self) if candidates else []
        # Every candidate is excluded from the external tier, contributed or
        # not: a repo the user was merely invited to is not a contribution.
        known = {_repo_full_name(r, username).lower() for r in repos}
        externals: list[dict] = []
        if config.GITHUB_INCLUDE_CONTRIBUTIONS:
            externals = _external_contributions(
                username, known, prs, graph_repos, commits
            )

        sections: list[str] = [f"GitHub profile: {username}"]
        if orgs:
            label = "Organization memberships" if is_self else (
                "Public organization memberships"
            )
            sections.append(f"{label}: {', '.join(orgs)}")

        if owned:
            sections.append("\n## Owned repositories")
            sections.append("Repositories owned by the user's personal account.")
            for repo in owned:
                sections.append(_render_repo(client, repo, _repo_full_name(repo, username)))

        for owner, org_repos in by_org.items():
            relation = "member of" if owner in orgs else "collaborator on"
            sections.append(f"\n## Organization repositories ({relation} {owner})")
            sections.append(
                f"Repositories owned by {owner}, not by the user. Every "
                "repository listed here has commits authored by the user, so it "
                "is work they did; the projects themselves belong to "
                f"{owner}, not to them."
            )
            for repo in org_repos:
                sections.append(_render_repo(client, repo, _repo_full_name(repo, username)))

        if externals:
            sections.append(
                "\n## Contributions to external repositories (not owned by the user)"
            )
            sections.append(
                "These projects belong to other people or organizations. The "
                "listed pull requests and commits are the user's contributions "
                "to them — evidence of that contribution only, never of "
                "authorship or ownership of the project."
            )
            for repo in externals:
                sections.append(_render_external(repo))

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
