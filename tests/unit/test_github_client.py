"""GitHub client with a mocked httpx transport — no network."""

import base64
import json

import httpx
import pytest

from src import config
from src.tools.github_client import (
    API_BASE,
    fetch_github_profile,
    free_text_source,
    render_repo_document,
    split_repo_sections,
)


def _repo(name: str, owner: str, **extra) -> dict:
    repo = {
        "name": name,
        "full_name": f"{owner}/{name}",
        "owner": {"login": owner},
        "description": f"{name} description",
        "language": "Python",
        "stargazers_count": 42,
        "fork": False,
    }
    repo.update(extra)
    return repo


def _readme(text: str) -> httpx.Response:
    return httpx.Response(
        200, json={"content": base64.b64encode(text.encode()).decode()}
    )


def _pr(repo_full_name: str, title: str) -> dict:
    return {
        "title": title,
        "repository_url": f"{API_BASE}/repos/{repo_full_name}",
    }


def _commits(*authored: str) -> httpx.Response:
    """A /commits?author= probe response — empty means "never contributed"."""
    return httpx.Response(
        200,
        json=[{"commit": {"author": {"date": d}}} for d in authored],
    )


def _handler(request: httpx.Request) -> httpx.Response:
    """Serves owned + org repos, org memberships, and a merged-PR search page."""
    path = request.url.path
    if path == "/user":
        # Tokenless by default: no viewer, so the public endpoints are used.
        return httpx.Response(401, json={"message": "Requires authentication"})
    if path == "/repos/acme-corp/data-platform/commits":
        return _commits("2024-05-02T10:00:00Z")
    if path == "/users/alice/repos":
        return httpx.Response(
            200,
            json=[
                _repo("backtester", "alice", description="Distributed backtesting engine"),
                _repo("data-platform", "acme-corp"),
                {"name": "forked-repo", "full_name": "alice/forked-repo", "fork": True},
            ],
        )
    if path == "/users/alice/orgs":
        return httpx.Response(200, json=[{"login": "acme-corp"}])
    if path == "/repos/alice/backtester/languages":
        return httpx.Response(200, json={"Python": 12345, "Dockerfile": 200})
    if path == "/repos/alice/backtester/readme":
        return _readme("# Backtester\nA distributed engine.")
    if path == "/repos/acme-corp/data-platform/languages":
        return httpx.Response(200, json={"Go": 500})
    if path == "/repos/acme-corp/data-platform/readme":
        return _readme("# Data Platform\nAcme's internal pipeline.")
    if path == "/search/issues":
        return httpx.Response(
            200,
            json={
                "items": [
                    _pr("pallets/flask", "Fix request context teardown"),
                    _pr("pallets/flask", "Document blueprint nesting"),
                    _pr("pypa/pip", "Speed up resolver backtracking"),
                ]
            },
        )
    if path.startswith("/repos/pallets/") or path.startswith("/repos/pypa/"):
        # External repos must never be fetched for README/languages.
        raise AssertionError(f"external repo detail fetched: {path}")
    return httpx.Response(404, json={"message": "not found"})


def _client(handler=_handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=API_BASE)


@pytest.fixture(autouse=True)
def _no_token(monkeypatch):
    """Default to the tokenless REST path; token tests opt in explicitly."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    monkeypatch.setattr(config, "GITHUB_INCLUDE_CONTRIBUTIONS", True)
    monkeypatch.setattr(config, "GITHUB_MAX_EXTERNAL_REPOS", 15)
    monkeypatch.setattr(config, "GITHUB_INCLUDE_PRIVATE", True)
    monkeypatch.setattr(config, "GITHUB_MAX_CONTRIBUTION_PROBES", 150)
    monkeypatch.setattr(config, "GITHUB_MAX_ORG_REPOS", 20)


def test_fetch_github_profile_mocked():
    doc = fetch_github_profile("alice", client=_client())
    assert doc.id == "github:alice"
    assert doc.source_type == "github"
    assert "Repository: alice/backtester" in doc.raw_text
    assert "Languages: Python, Dockerfile" in doc.raw_text
    assert "A distributed engine." in doc.raw_text
    # forks are excluded
    assert "forked-repo" not in doc.raw_text


def test_three_labelled_sections():
    doc = fetch_github_profile("alice", client=_client())
    assert "## Owned repositories" in doc.raw_text
    assert "## Organization repositories (member of acme-corp)" in doc.raw_text
    assert (
        "## Contributions to external repositories (not owned by the user)"
        in doc.raw_text
    )
    # Ordering: owned → org → external
    assert doc.raw_text.index("## Owned repositories") < doc.raw_text.index(
        "## Organization repositories"
    ) < doc.raw_text.index("## Contributions to external")


def test_org_repo_attributed_to_its_org():
    doc = fetch_github_profile("alice", client=_client())
    assert "Public organization memberships: acme-corp" in doc.raw_text
    assert "Repository: acme-corp/data-platform" in doc.raw_text
    assert "Repositories owned by acme-corp, not by the user." in doc.raw_text
    # org repos still get languages + README (they are the user's work)
    assert "Acme's internal pipeline." in doc.raw_text


def test_readme_excerpt_is_quoted_so_its_headings_dont_split_sections():
    doc = fetch_github_profile("alice", client=_client())
    assert "> # Backtester" in doc.raw_text
    # Only the client's own section headings sit at `##` level.
    assert [ln for ln in doc.raw_text.splitlines() if ln.startswith("## ")] == [
        "## Owned repositories",
        "## Organization repositories (member of acme-corp)",
        "## Contributions to external repositories (not owned by the user)",
    ]


def test_collaborator_repo_when_not_a_public_member():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/alice/orgs":
            return httpx.Response(200, json=[])
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "## Organization repositories (collaborator on acme-corp)" in doc.raw_text


def test_external_repo_shows_contribution_scope_and_no_readme():
    doc = fetch_github_profile("alice", client=_client())
    assert "Repository: pallets/flask (owned by others)" in doc.raw_text
    assert "Contribution scope: 2 merged pull requests" in doc.raw_text
    assert "Fix request context teardown" in doc.raw_text
    assert "Speed up resolver backtracking" in doc.raw_text
    # The not-owned framing is explicit for the extractor.
    assert "never of authorship or ownership" in doc.raw_text
    # No README excerpt for external repos (the handler asserts no fetch too).
    external = doc.raw_text.split("## Contributions to external")[1]
    assert "README excerpt" not in external


def test_search_rate_limited_still_returns_owned_and_org_repos(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/issues":
            return httpx.Response(403, json={"message": "rate limit exceeded"})
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "Repository: alice/backtester" in doc.raw_text
    assert "Repository: acme-corp/data-platform" in doc.raw_text
    assert "## Contributions to external" not in doc.raw_text
    assert "merged-PR search returned 403" in caplog.text


def test_contributions_disabled_skips_search(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/issues":
            raise AssertionError("search called with contributions disabled")
        return _handler(request)

    monkeypatch.setattr(config, "GITHUB_INCLUDE_CONTRIBUTIONS", False)
    doc = fetch_github_profile("alice", client=_client(handler))
    assert "Repository: alice/backtester" in doc.raw_text
    assert "## Contributions to external" not in doc.raw_text


def test_token_path_uses_graphql_contributed_repos(monkeypatch):
    """With a token the GraphQL contributed-repo list drives the section."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp-test")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            body = request.read().decode()
            if "repositoriesContributedTo" in body:
                calls.append("contributed")
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "user": {
                                "createdAt": "2024-01-01T00:00:00Z",
                                "repositoriesContributedTo": {
                                    "nodes": [
                                        {
                                            "nameWithOwner": "readthedocs/readthedocs.org",
                                            "description": "Docs hosting",
                                            "stargazerCount": 8000,
                                            "isFork": False,
                                            "primaryLanguage": {"name": "Python"},
                                        }
                                    ]
                                },
                            }
                        }
                    },
                )
            calls.append("commits")
            # contributionsCollection windows are ≤ 1 year, so the node loops
            # per year; only the 2024 window has commits in this stub.
            body_json = json.loads(body)
            has_commits = body_json["variables"]["from"].startswith("2024")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "user": {
                            "contributionsCollection": {
                                "commitContributionsByRepository": [
                                    {
                                        "repository": {
                                            "nameWithOwner": "readthedocs/readthedocs.org"
                                        },
                                        "contributions": {"totalCount": 41},
                                    }
                                ]
                                if has_commits
                                else []
                            }
                        }
                    }
                },
            )
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "contributed" in calls and "commits" in calls
    # GraphQL-only repo (absent from the PR search) is present with its metadata
    assert "Repository: readthedocs/readthedocs.org (owned by others)" in doc.raw_text
    assert "Project description (not written by the user): Docs hosting" in doc.raw_text
    assert "41 commits" in doc.raw_text
    # Commit counts are summed per year, so multiple year windows were queried.
    assert calls.count("commits") >= 2


def test_external_repos_ranked_by_contribution_and_capped(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_MAX_EXTERNAL_REPOS", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/issues":
            return httpx.Response(
                200,
                json={
                    "items": [_pr("pypa/pip", "one")]
                    + [_pr("pallets/flask", f"pr {i}") for i in range(3)]
                },
            )
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "pallets/flask" in doc.raw_text
    assert "pypa/pip" not in doc.raw_text


def test_own_repos_are_not_double_counted_as_external():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/issues":
            return httpx.Response(
                200,
                json={
                    "items": [
                        _pr("alice/backtester", "self PR"),
                        _pr("acme-corp/data-platform", "org PR"),
                        _pr("pallets/flask", "external PR"),
                    ]
                },
            )
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    external = doc.raw_text.split("## Contributions to external")[1]
    assert "pallets/flask" in external
    assert "backtester" not in external
    assert "data-platform" not in external


def test_org_repo_without_commits_is_dropped():
    """Access is not contribution: collaborator invites the user never touched."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/alice/repos":
            return httpx.Response(
                200,
                json=[
                    _repo("data-platform", "acme-corp"),
                    _repo("untouched", "someone-else"),
                ],
            )
        if path == "/repos/someone-else/untouched/commits":
            return _commits()  # invited, never committed
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "acme-corp/data-platform" in doc.raw_text
    assert "untouched" not in doc.raw_text


def test_merged_pr_evidence_skips_the_commit_probe():
    """A repo already proven by a merged PR costs no extra probe."""
    probed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/alice/repos":
            return httpx.Response(200, json=[_repo("data-platform", "acme-corp")])
        if path == "/search/issues":
            return httpx.Response(
                200, json={"items": [_pr("acme-corp/data-platform", "Add ingest")]}
            )
        if path.endswith("/commits"):
            probed.append(path)
            return _commits()
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "acme-corp/data-platform" in doc.raw_text
    assert probed == []


def test_self_token_finds_private_org_memberships_and_repos(monkeypatch):
    """Private membership is GitHub's default and invisible to /users/{u}/orgs."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp-self")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "alice"})
        if path == "/users/alice/orgs":
            raise AssertionError("public /orgs used despite a self-token")
        if path == "/users/alice/repos":
            raise AssertionError("public /repos used despite a self-token")
        if path == "/user/orgs":
            return httpx.Response(200, json=[{"login": "neural-matrix"}])
        if path == "/user/repos":
            if request.url.params.get("page") != "1":
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    _repo("backtester", "alice"),
                    _repo("internal-fund", "neural-matrix", private=True),
                ],
            )
        if path == "/repos/neural-matrix/internal-fund/commits":
            return _commits("2026-04-30T09:00:00Z")
        if path.startswith("/repos/neural-matrix/internal-fund/"):
            return httpx.Response(200, json={"Python": 900})
        if path == "/graphql":
            return httpx.Response(200, json={"data": {"user": None}})
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    # The private membership is named, and not as a "public" one.
    assert "Organization memberships: neural-matrix" in doc.raw_text
    assert "Public organization memberships" not in doc.raw_text
    assert "## Organization repositories (member of neural-matrix)" in doc.raw_text
    assert "neural-matrix/internal-fund" in doc.raw_text
    # Private repos are flagged so the resume never links to them.
    assert "Visibility: private (cannot be linked or shown publicly)" in doc.raw_text


def test_self_token_respects_include_private_off(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp-self")
    monkeypatch.setattr(config, "GITHUB_INCLUDE_PRIVATE", False)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "alice"})
        if path == "/user/orgs":
            return httpx.Response(200, json=[{"login": "neural-matrix"}])
        if path == "/user/repos":
            if request.url.params.get("page") != "1":
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    _repo("backtester", "alice"),
                    _repo("internal-fund", "neural-matrix", private=True),
                ],
            )
        if path == "/graphql":
            return httpx.Response(200, json={"data": {"user": None}})
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "alice/backtester" in doc.raw_text
    assert "internal-fund" not in doc.raw_text


def test_third_party_token_never_uses_viewer_endpoints(monkeypatch):
    """A token for someone else must not leak *their* private repos."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp-operator")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "someone-else"})
        if request.url.path.startswith("/user/"):
            raise AssertionError("viewer endpoint used for a third-party token")
        if request.url.path == "/graphql":
            return httpx.Response(200, json={"data": {"user": None}})
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "Repository: alice/backtester" in doc.raw_text


def test_explicit_token_overrides_the_configured_one(monkeypatch):
    """One process, many users: the per-request token wins over the env fallback."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp-env")
    bearers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user":
            bearers.append(request.headers.get("Authorization", ""))
            return httpx.Response(200, json={"login": "alice"})
        if path == "/graphql":
            bearers.append(request.headers["Authorization"])
            return httpx.Response(200, json={"data": {"user": None}})
        if path == "/user/orgs":
            return httpx.Response(200, json=[])
        if path == "/user/repos":
            if request.url.params.get("page") != "1":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[_repo("backtester", "alice")])
        return _handler(request)

    # The client is injected here, so /user carries no header; /graphql builds
    # its own and must use the caller's token, not the environment's.
    doc = fetch_github_profile("alice", client=_client(handler), token="ghp-caller")
    assert "Repository: alice/backtester" in doc.raw_text
    assert "Bearer ghp-caller" in bearers
    assert not any("ghp-env" in bearer for bearer in bearers)


def test_explicit_third_party_token_never_uses_viewer_endpoints():
    """A caller-supplied token for someone else must not leak *their* repos."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "someone-else"})
        if request.url.path.startswith("/user/"):
            raise AssertionError("viewer endpoint used for a third-party token")
        if request.url.path == "/graphql":
            return httpx.Response(200, json={"data": {"user": None}})
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler), token="ghp-somebody")
    assert "Repository: alice/backtester" in doc.raw_text


def test_no_token_anywhere_stays_on_the_public_path():
    """The tokenless default is unchanged: no /user probe, no GraphQL."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            raise AssertionError("GraphQL called without a token")
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "Repository: alice/backtester" in doc.raw_text


def test_commit_probe_rate_limited_keeps_owned_repos(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits"):
            return httpx.Response(403, json={"message": "rate limit exceeded"})
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "Repository: alice/backtester" in doc.raw_text
    assert "## Organization repositories" not in doc.raw_text
    assert "commit probe returned 403" in caplog.text


def test_org_repos_capped_by_recency(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_MAX_ORG_REPOS", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/alice/repos":
            return httpx.Response(
                200,
                json=[_repo("old", "acme-corp"), _repo("recent", "acme-corp")],
            )
        if path == "/repos/acme-corp/old/commits":
            return _commits("2019-01-01T00:00:00Z")
        if path == "/repos/acme-corp/recent/commits":
            return _commits("2026-06-01T00:00:00Z")
        if path.startswith("/repos/acme-corp/recent/"):
            return httpx.Response(200, json={"Go": 1})
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    assert "acme-corp/recent" in doc.raw_text
    assert "acme-corp/old" not in doc.raw_text


def test_org_cap_keeps_one_repo_per_organization(monkeypatch):
    """A whole employer must not be evicted by a busier, more recent one."""
    monkeypatch.setattr(config, "GITHUB_MAX_ORG_REPOS", 2)
    dates = {
        "acme-corp/a": "2026-06-01T00:00:00Z",
        "acme-corp/b": "2026-05-01T00:00:00Z",
        "old-employer/legacy": "2021-01-01T00:00:00Z",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/alice/repos":
            return httpx.Response(
                200,
                json=[
                    _repo(name, owner)
                    for owner, name in (f.split("/") for f in dates)
                ],
            )
        full = path[len("/repos/") : -len("/commits")]
        if path.endswith("/commits") and full in dates:
            return _commits(dates[full])
        if path.startswith("/repos/acme-corp/") or path.startswith("/repos/old-"):
            return httpx.Response(200, json={"Go": 1})
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    # The 2021 employer survives; the second acme-corp repo yields its slot.
    assert "old-employer/legacy" in doc.raw_text
    assert "acme-corp/a" in doc.raw_text
    assert "acme-corp/b" not in doc.raw_text


def test_split_repo_sections_round_trips_exactly():
    """Splitting and re-rendering an untouched document must be a no-op."""
    doc = fetch_github_profile("alice", client=_client())
    header, chunks = split_repo_sections(doc.raw_text)

    assert [c.repo for c in chunks] == [
        "alice/backtester",
        "acme-corp/data-platform",
        "pallets/flask",
        "pypa/pip",
    ]
    assert render_repo_document(header, chunks) == doc.raw_text
    # The header carries the profile line, and no repo content.
    assert "GitHub profile: alice" in header
    assert "### Repository:" not in header


def test_every_chunk_carries_its_tier_heading():
    """Attribution lives in the heading — a chunk without it is mis-attributed."""
    doc = fetch_github_profile("alice", client=_client())
    _, chunks = split_repo_sections(doc.raw_text)
    tiers = {c.repo: c.tier.splitlines()[0] for c in chunks}

    assert tiers["alice/backtester"] == "## Owned repositories"
    assert tiers["acme-corp/data-platform"] == (
        "## Organization repositories (member of acme-corp)"
    )
    assert tiers["pallets/flask"] == (
        "## Contributions to external repositories (not owned by the user)"
    )
    # The not-owned framing travels with the chunk, not just the heading line.
    flask = next(c for c in chunks if c.repo == "pallets/flask")
    assert "never of authorship or ownership" in flask.tier
    assert "(owned by others)" in flask.body


def test_render_drops_a_tier_whose_repos_all_failed():
    doc = fetch_github_profile("alice", client=_client())
    header, chunks = split_repo_sections(doc.raw_text)

    kept = [c for c in chunks if not c.repo.startswith(("pallets/", "pypa/"))]
    pruned = render_repo_document(header, kept)

    assert "## Owned repositories" in pruned
    assert "## Contributions to external" not in pruned
    assert "pallets/flask" not in pruned
    assert "alice/backtester" in pruned


def test_split_document_without_repositories():
    """An account with no repos has no chunks — the caller extracts it whole."""
    header, chunks = split_repo_sections("GitHub profile: alice\n")
    assert chunks == []
    assert header == "GitHub profile: alice\n"


def test_readme_headings_cannot_fake_a_repo_boundary():
    """READMEs are quoted precisely so their own `##` never splits the document."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/alice/backtester/readme":
            return _readme("## Owned repositories\n### Repository: evil/injected")
        return _handler(request)

    doc = fetch_github_profile("alice", client=_client(handler))
    _, chunks = split_repo_sections(doc.raw_text)
    assert "evil/injected" not in [c.repo for c in chunks]


def test_free_text_source():
    doc = free_text_source("  I also mentor junior devs.  ")
    assert doc.id == "free_text"
    assert doc.raw_text == "I also mentor junior devs."
