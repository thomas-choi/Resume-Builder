"""API tests via TestClient with the graphs mocked out."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.types import Command, Interrupt

from src import config
from src.api import routes
from src.api.main import create_app
from src.models.schemas import (
    CoverLetter,
    JobRequirements,
    ReviewItem,
    ReviewRequest,
    TailoredCV,
    ValidationFlag,
    ValidationResult,
)
from src.utils import document_store, profile_store
from tests.conftest import build_linkedin_export_zip, build_sample_docx


# These functional tests exercise the business routes in legacy single-user
# mode (AUTH_ENABLED=false, §14.11): current_user resolves the synthetic
# SINGLE_USER_EMAIL account with no cookie, so every route is open and the data
# lands under that one account's per-user root. Cross-account isolation and the
# fail-closed enforcement are proved separately in test_isolation.py.
SINGLE_EMAIL = "local@example.com"


def _ur():
    """The single-user per-account root under the (monkeypatched) DATA_DIR."""
    return config.user_root(SINGLE_EMAIL)


@pytest.fixture
def client(data_dir, monkeypatch):
    # Serve the API alone: whether a built frontend happens to be present in the
    # working tree must not change how the API tests behave.
    monkeypatch.setattr(config, "FRONTEND_DIR", data_dir / "no-frontend")
    monkeypatch.setattr(config, "AUTH_ENABLED", False)
    return TestClient(create_app())


class FakeIngestionGraph:
    def __init__(self, final_state, extract_state=None):
        self.final_state = final_state
        # What `extract_source` contributes — where `source_errors` comes from.
        self.extract_state = extract_state or {}
        self.received_state = None

    def stream(self, state, stream_mode=None):
        self.received_state = state
        yield {"ingest_sources": {}}
        yield {"extract_source": self.extract_state}
        yield {"synthesize_profile": {}}
        yield {"store_profile": self.final_state}


class FakeTailoringGraph:
    """Stands in for the compiled tailoring graph, checkpointer included.

    `interrupt_value` makes `invoke` behave like a paused run: LangGraph
    reports a pause via `__interrupt__` on the returned state, and
    `get_state(...).interrupts` until it is resumed with a `Command`.
    """

    def __init__(self, final_state, interrupt_value=None):
        self.final_state = final_state
        self.interrupt_value = interrupt_value
        self.received_state = None
        self.resumed_with = None

    def invoke(self, state, config=None):
        self.config = config
        if isinstance(state, Command):
            self.resumed_with = state.resume
            self.interrupt_value = None
            return {**(self.received_state or {}), **self.final_state}
        self.received_state = state
        if self.interrupt_value is not None:
            return {
                **state,
                **self.final_state,
                "__interrupt__": [Interrupt(value=self.interrupt_value)],
            }
        return {**state, **self.final_state}

    def get_state(self, config):
        return SimpleNamespace(
            interrupts=() if self.interrupt_value is None else (
                Interrupt(value=self.interrupt_value),
            ),
            next=() if self.interrupt_value is None else ("human_review",),
        )


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_root_redirects_to_docs_without_a_built_frontend(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/docs"


def test_root_serves_the_review_ui_when_it_is_built(data_dir, monkeypatch):
    # One container, two roles: with `frontend/dist` present the same app
    # serves the UI at "/" while every API route keeps its path.
    dist = data_dir / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Resume Builder</title>")
    monkeypatch.setattr(config, "FRONTEND_DIR", dist)

    ui_client = TestClient(create_app())
    resp = ui_client.get("/")
    assert resp.status_code == 200
    assert "Resume Builder" in resp.text
    assert ui_client.get("/healthz").json() == {"status": "ok"}


def test_ingest_docx_returns_profile(client, data_dir, monkeypatch, sample_profile):
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    monkeypatch.setattr(
        routes, "build_ingestion_graph", lambda: FakeIngestionGraph(final_state)
    )
    resp = client.post(
        "/ingest",
        files=[
            (
                "cv",
                (
                    "resume.docx",
                    build_sample_docx(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            )
        ],
        data={"job_id": "job-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile_id"] == "abc123"
    assert body["job_id"] == "job-1"
    assert body["run_id"] == "job-1"
    assert body["profile"]["name"] == "Alice Smith"
    # conflicts are surfaced in the response
    assert body["profile"]["conflicts"][0]["field"] == "experience.start_date"

    # the raw upload is archived under sources/{run_id}/cv/ and indexed
    cv_path = _ur() / "sources" / "job-1" / "cv" / "resume.docx"
    assert cv_path.exists()
    manifest = json.loads((_ur() / "sources" / "job-1" / "manifest.json").read_text())
    assert manifest["run_id"] == "job-1"
    categories = {e["category"] for e in manifest["sources"]}
    assert categories == {"cv"}
    assert manifest["sources"][0]["sha256"]


def _fake_fetch_github(calls: list | None = None):
    """Stand-in for `fetch_github_profile`, recording the token it was given."""

    def fake_fetch(username, client=None, token=None):
        from src.models.schemas import SourceDocument

        if calls is not None:
            calls.append({"username": username, "token": token})
        return SourceDocument(
            id=f"github:{username}", source_type="github", raw_text="repos ..."
        )

    return fake_fetch


def test_ingest_archives_github_and_free_text_sources(
    client, data_dir, monkeypatch, sample_profile
):
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    monkeypatch.setattr(
        routes, "build_ingestion_graph", lambda: FakeIngestionGraph(final_state)
    )

    monkeypatch.setattr(routes, "fetch_github_profile", _fake_fetch_github())

    resp = client.post(
        "/ingest",
        data={
            "job_id": "job-2",
            "github_username": "alice",
            "free_text": "LinkedIn summary paragraph",
        },
    )
    assert resp.status_code == 200

    run_dir = _ur() / "sources" / "job-2"
    assert (run_dir / "github" / "github.json").exists()
    assert (run_dir / "linkedin" / "linkedin-summary.txt").read_text() == (
        "LinkedIn summary paragraph"
    )
    manifest = json.loads((run_dir / "manifest.json").read_text())
    categories = {e["category"] for e in manifest["sources"]}
    assert categories == {"github", "linkedin"}


def test_ingest_linkedin_export_zip(client, data_dir, monkeypatch, sample_profile):
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    fake = FakeIngestionGraph(final_state)
    monkeypatch.setattr(routes, "build_ingestion_graph", lambda: fake)

    resp = client.post(
        "/ingest",
        files=[
            (
                "linkedin_export",
                (
                    "Basic_LinkedInDataExport.zip",
                    build_linkedin_export_zip(),
                    "application/zip",
                ),
            )
        ],
        data={"job_id": "job-li"},
    )
    assert resp.status_code == 200

    # The export reached the graph as a parsed linkedin source...
    (source,) = fake.received_state["sources"]
    assert source.source_type == "linkedin"
    assert source.id == "linkedin:Basic_LinkedInDataExport.zip"
    assert source.structured_fields["profile"]["Headline"] == "Senior Engineer"

    # ...and the raw archive is archived under the run and indexed.
    stored = _ur() / "sources" / "job-li" / "linkedin" / "Basic_LinkedInDataExport.zip"
    assert stored.exists()
    assert source.stored_path == str(stored)
    manifest = json.loads((_ur() / "sources" / "job-li" / "manifest.json").read_text())
    entry = manifest["sources"][0]
    assert entry["category"] == "linkedin"
    assert entry["source_id"] == "linkedin:Basic_LinkedInDataExport.zip"


def test_ingest_github_token_reaches_the_client_but_no_disk(
    client, data_dir, monkeypatch, sample_profile
):
    """The per-request token is a secret in transit: used, then gone."""
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    monkeypatch.setattr(
        routes, "build_ingestion_graph", lambda: FakeIngestionGraph(final_state)
    )
    calls: list[dict] = []
    monkeypatch.setattr(routes, "fetch_github_profile", _fake_fetch_github(calls))

    resp = client.post(
        "/ingest",
        data={
            "job_id": "job-tok",
            "github_username": "alice",
            "github_token": "ghp-secret-value",
        },
    )
    assert resp.status_code == 200
    assert calls == [{"username": "alice", "token": "ghp-secret-value"}]

    assert "ghp-secret-value" not in resp.text
    written = [p for p in data_dir.rglob("*") if p.is_file()]
    assert written  # the run really did archive something to search through
    assert not any("ghp-secret-value" in p.read_bytes().decode(errors="replace") for p in written)


def test_ingest_without_github_token_falls_back_to_the_configured_one(
    client, monkeypatch, sample_profile
):
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    monkeypatch.setattr(
        routes, "build_ingestion_graph", lambda: FakeIngestionGraph(final_state)
    )
    calls: list[dict] = []
    monkeypatch.setattr(routes, "fetch_github_profile", _fake_fetch_github(calls))

    # Blank is no token at all — `None` lets fetch_github_profile use the env one.
    resp = client.post(
        "/ingest",
        data={"job_id": "job-tok2", "github_username": "alice", "github_token": "  "},
    )
    assert resp.status_code == 200
    assert calls[0]["token"] is None


def test_ingest_keeps_two_same_named_cvs_apart(
    client, data_dir, monkeypatch, sample_profile
):
    """Same filename twice: two archived files, two source ids, no overwrite."""
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    fake = FakeIngestionGraph(final_state)
    monkeypatch.setattr(routes, "build_ingestion_graph", lambda: fake)

    docx_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    resp = client.post(
        "/ingest",
        files=[
            ("cv", ("CV.docx", build_sample_docx(), docx_type)),
            ("cv", ("CV.docx", build_sample_docx(name="Bob Jones"), docx_type)),
        ],
        data={"job_id": "job-dup"},
    )
    assert resp.status_code == 200

    cv_dir = _ur() / "sources" / "job-dup" / "cv"
    assert sorted(p.name for p in cv_dir.iterdir()) == ["CV-2.docx", "CV.docx"]

    # Distinct source ids end to end, so raw_source_map can tell them apart.
    first, second = fake.received_state["sources"]
    ids = [first.id, second.id]
    assert ids == ["cv_docx:CV.docx", "cv_docx:CV-2.docx"]
    manifest = json.loads((_ur() / "sources" / "job-dup" / "manifest.json").read_text())
    assert [e["source_id"] for e in manifest["sources"]] == ids
    assert [e["filename"] for e in manifest["sources"]] == ["CV.docx", "CV-2.docx"]
    # The second CV really is the second file, not the first one parsed twice.
    assert "Alice Smith" in first.raw_text
    assert "Bob Jones" in second.raw_text


def test_ingest_reports_skipped_repos_in_the_response_and_over_sse(
    client, monkeypatch, sample_profile
):
    """Partial success is still success — but never a silent one."""
    errors = [
        {"source": "github:alice", "repo": "alice/repo-4", "reason": "no tool call"},
        {"source": "github:alice", "repo": "alice/repo-9", "reason": "timed out"},
    ]
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    monkeypatch.setattr(
        routes,
        "build_ingestion_graph",
        lambda: FakeIngestionGraph(final_state, {"source_errors": errors}),
    )
    monkeypatch.setattr(routes, "fetch_github_profile", _fake_fetch_github())

    resp = client.post(
        "/ingest", data={"job_id": "job-warn", "github_username": "alice"}
    )
    assert resp.status_code == 200
    assert resp.json()["source_errors"] == errors

    # Each skipped repo is also announced live on the progress stream.
    queue = routes.jobs.get("job-warn")
    published = []
    while not queue.empty():
        published.append(queue.get_nowait())
    warnings = [e for e in published if e.get("event") == "warning"]
    assert [w["data"] for w in warnings] == [
        "alice/repo-4: no tool call",
        "alice/repo-9: timed out",
    ]


def test_ingest_reports_no_errors_on_a_clean_run(client, monkeypatch, sample_profile):
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    monkeypatch.setattr(
        routes, "build_ingestion_graph", lambda: FakeIngestionGraph(final_state)
    )
    resp, _ = _ingest_docx(client, {"job_id": "job-clean"}, sample_profile, monkeypatch)
    assert resp.json()["source_errors"] == []


def test_ingest_rejects_unknown_linkedin_file_type(client):
    resp = client.post(
        "/ingest",
        files=[("linkedin_export", ("profile.txt", b"hi", "text/plain"))],
    )
    assert resp.status_code == 400
    assert "unsupported LinkedIn export file type" in resp.json()["detail"]


def test_ingest_rejects_unreadable_linkedin_export(client, data_dir):
    resp = client.post(
        "/ingest",
        files=[("linkedin_export", ("export.zip", b"not a zip", "application/zip"))],
        data={"job_id": "job-bad"},
    )
    assert resp.status_code == 400
    assert "unreadable LinkedIn export" in resp.json()["detail"]
    # The rejected upload is still on disk to inspect (archived before parsing).
    assert (_ur() / "sources" / "job-bad" / "linkedin" / "export.zip").exists()


def test_ingest_without_sources_is_400(client):
    resp = client.post("/ingest", data={})
    assert resp.status_code == 400


def test_ingest_rejects_unknown_file_type(client):
    resp = client.post("/ingest", files=[("cv", ("resume.txt", b"hi", "text/plain"))])
    assert resp.status_code == 400


def _ingest_docx(client, data, sample_profile, monkeypatch, final_state=None):
    """POST /ingest with a docx CV and a mocked graph; returns (resp, fake_graph)."""
    final_state = final_state or {
        "profile": sample_profile,
        "profile_id": "abc123",
        "version": 1,
    }
    fake = FakeIngestionGraph(final_state)
    monkeypatch.setattr(routes, "build_ingestion_graph", lambda: fake)
    resp = client.post(
        "/ingest",
        files=[
            (
                "cv",
                (
                    "resume.docx",
                    build_sample_docx(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            )
        ],
        data=data,
    )
    return resp, fake


def test_ingest_threads_caller_profile_id_into_graph(
    client, data_dir, monkeypatch, sample_profile
):
    # A caller-supplied profile_id directs the result into that profile;
    # store_profile receives it (existing id → new version).
    final_state = {"profile": sample_profile, "profile_id": "my-profile", "version": 2}
    resp, fake = _ingest_docx(
        client,
        {"job_id": "job-1", "profile_id": "my-profile"},
        sample_profile,
        monkeypatch,
        final_state,
    )
    assert resp.status_code == 200
    assert resp.json()["profile_id"] == "my-profile"
    assert resp.json()["version"] == 2
    assert fake.received_state["profile_id"] == "my-profile"


def test_ingest_without_profile_id_lets_store_mint_one(
    client, data_dir, monkeypatch, sample_profile
):
    # Omitting profile_id must not put the key in the graph input, so
    # save_profile mints a fresh id (default behavior preserved).
    resp, fake = _ingest_docx(client, {"job_id": "job-1"}, sample_profile, monkeypatch)
    assert resp.status_code == 200
    assert "profile_id" not in fake.received_state


def test_ingest_rejects_unsafe_profile_id(client, data_dir, monkeypatch, sample_profile):
    resp, _ = _ingest_docx(
        client, {"profile_id": "../../etc/passwd"}, sample_profile, monkeypatch
    )
    assert resp.status_code == 400


def test_profile_get_put_roundtrip(client, sample_profile):
    profile_id, _ = profile_store.save_profile(SINGLE_EMAIL, sample_profile)

    resp = client.get(f"/profile/{profile_id}")
    assert resp.status_code == 200
    assert resp.json()["version"] == 1

    edited = sample_profile.model_copy(update={"headline": "Staff Engineer"})
    resp = client.put(f"/profile/{profile_id}", json=edited.model_dump())
    assert resp.status_code == 200
    assert resp.json()["version"] == 2

    resp = client.get(f"/profile/{profile_id}", params={"version": 1})
    assert resp.json()["profile"]["headline"] == "Senior Engineer"
    resp = client.get(f"/profile/{profile_id}")
    assert resp.json()["profile"]["headline"] == "Staff Engineer"


def test_profile_404s(client, sample_profile):
    assert client.get("/profile/nope").status_code == 404
    assert (
        client.put("/profile/nope", json=sample_profile.model_dump()).status_code == 404
    )


def test_tailor_endpoint(client, monkeypatch, sample_profile):
    profile_id, _ = profile_store.save_profile(SINGLE_EMAIL, sample_profile)
    final_state = {
        "job_requirements": JobRequirements(title="Backend Engineer"),
        "tailored_cv": TailoredCV(headline="Senior Engineer", summary="Pitch."),
        "validation": ValidationResult(passed=True),
    }
    monkeypatch.setattr(
        routes, "build_tailoring_graph", lambda: FakeTailoringGraph(final_state)
    )
    resp = client.post(
        "/tailor", json={"profile_id": profile_id, "job_post": "We need a backend engineer"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tailored_cv"]["headline"] == "Senior Engineer"
    assert body["validation"]["passed"] is True
    # Phase 3 defaults: no rendering, no cover letter.
    assert body["documents"] == []
    assert body["cover_letter"] is None
    assert body["tailor_id"]


def _tailor(client, monkeypatch, sample_profile, body, final_state=None):
    """POST /tailor with a mocked graph; returns (response, fake_graph)."""
    profile_id, _ = profile_store.save_profile(SINGLE_EMAIL, sample_profile)
    fake = FakeTailoringGraph(
        final_state
        or {
            "job_requirements": JobRequirements(title="Backend Engineer"),
            "tailored_cv": TailoredCV(headline="Senior Engineer", summary="Pitch."),
            "validation": ValidationResult(passed=True),
        }
    )
    monkeypatch.setattr(routes, "build_tailoring_graph", lambda: fake)
    resp = client.post(
        "/tailor", json={"profile_id": profile_id, "job_post": "job post", **body}
    )
    return resp, fake


def test_tailor_threads_render_flags_into_the_graph(client, monkeypatch, sample_profile):
    resp, fake = _tailor(
        client,
        monkeypatch,
        sample_profile,
        {"render": True, "cover_letter": True, "approve_flagged": True},
    )
    assert resp.status_code == 200
    state = fake.received_state
    assert state["render"] is True
    assert state["want_cover_letter"] is True
    assert state["approved"] is True
    assert state["tailor_id"] == resp.json()["tailor_id"]


def test_tailor_returns_documents_with_download_urls(client, monkeypatch, sample_profile):
    resp, _ = _tailor(
        client,
        monkeypatch,
        sample_profile,
        {"render": True},
        final_state={
            "job_requirements": JobRequirements(title="Backend Engineer"),
            "tailored_cv": TailoredCV(headline="Senior Engineer", summary="Pitch."),
            "validation": ValidationResult(passed=True),
            "documents": [
                {"kind": "cv", "format": "docx", "filename": "cv.docx", "size_bytes": 9}
            ],
            "render_skipped": None,
        },
    )
    body = resp.json()
    tailor_id = body["tailor_id"]
    assert body["documents"] == [
        {
            "kind": "cv",
            "format": "docx",
            "filename": "cv.docx",
            "size_bytes": 9,
            "url": f"/document/{tailor_id}?kind=cv&format=docx",
        }
    ]


def test_tailor_reports_a_skipped_render(client, monkeypatch, sample_profile):
    resp, _ = _tailor(
        client,
        monkeypatch,
        sample_profile,
        {"render": True},
        final_state={
            "job_requirements": JobRequirements(title="Backend Engineer"),
            "tailored_cv": TailoredCV(headline="Senior Engineer", summary="Pitch."),
            "validation": ValidationResult(
                passed=False,
                needs_review=True,
                flags=[
                    ValidationFlag(item="Ran a team of 40", kind="bullet", reason="x")
                ],
            ),
            "documents": [],
            "render_skipped": "1 validation flag(s) need review",
        },
    )
    body = resp.json()
    assert body["documents"] == []
    assert "need review" in body["render_skipped"]


def test_tailor_returns_and_persists_the_cover_letter(
    client, data_dir, monkeypatch, sample_profile
):
    resp, _ = _tailor(
        client,
        monkeypatch,
        sample_profile,
        {"cover_letter": True},
        final_state={
            "job_requirements": JobRequirements(title="Backend Engineer"),
            "tailored_cv": TailoredCV(headline="Senior Engineer", summary="Pitch."),
            "validation": ValidationResult(passed=True),
            "cover_letter": CoverLetter(
                greeting="Dear Hiring Manager,",
                body_paragraphs=["I would like to apply."],
                closing="Sincerely,",
            ),
        },
    )
    body = resp.json()
    assert body["cover_letter"]["greeting"] == "Dear Hiring Manager,"
    # The run's result is saved beside its documents for traceability.
    saved = json.loads(
        (_ur() / "documents" / body["tailor_id"] / "tailor.json").read_text()
    )
    assert saved["tailor_id"] == body["tailor_id"]
    assert saved["cover_letter"]["closing"] == "Sincerely,"


def _review_request(tailor_id: str = "t-1") -> ReviewRequest:
    return ReviewRequest(
        tailor_id=tailor_id,
        items=[
            ReviewItem(
                id="flag-0",
                item="Ran a team of 40",
                kind="bullet",
                reason="unsourced",
                similarity=0.2,
            )
        ],
        brief="One claim could not be traced.",
    )


def _paused_tailor(client, monkeypatch, sample_profile):
    """POST /tailor against a graph that pauses for review; returns (body, fake)."""
    profile_id, _ = profile_store.save_profile(SINGLE_EMAIL, sample_profile)
    fake = FakeTailoringGraph(
        {
            "job_requirements": JobRequirements(title="Backend Engineer"),
            "tailored_cv": TailoredCV(headline="Senior Engineer", summary="Pitch."),
            "validation": ValidationResult(
                passed=False,
                needs_review=True,
                flags=[ValidationFlag(item="Ran a team of 40", kind="bullet", reason="x")],
            ),
        },
        interrupt_value=_review_request().model_dump(),
    )
    monkeypatch.setattr(routes, "build_tailoring_graph", lambda: fake)
    resp = client.post(
        "/tailor",
        json={"profile_id": profile_id, "job_post": "job post", "render": True},
    )
    assert resp.status_code == 200
    return resp.json(), fake


def test_tailor_pauses_for_review_instead_of_rendering(
    client, data_dir, monkeypatch, sample_profile
):
    body, fake = _paused_tailor(client, monkeypatch, sample_profile)
    assert body["review_required"] is True
    assert body["review"]["items"][0]["id"] == "flag-0"
    assert body["review_url"] == f"/tailor/{body['tailor_id']}/review"
    assert body["documents"] == []
    # The run is checkpointed under a thread_id namespaced to the owner (§14.8),
    # so a guessed tailor_id cannot resume another account's paused run.
    from src.utils import auth_store

    thread_id = fake.config["configurable"]["thread_id"]
    assert thread_id == f"{auth_store.uid(SINGLE_EMAIL)}:{body['tailor_id']}"


def test_clean_tailor_run_reports_no_review(client, monkeypatch, sample_profile):
    resp, _ = _tailor(client, monkeypatch, sample_profile, {"render": True})
    body = resp.json()
    assert body["review_required"] is False
    assert body["review"] is None
    assert body["review_url"] is None


def test_get_review_returns_the_pending_items(client, monkeypatch, sample_profile):
    body, _ = _paused_tailor(client, monkeypatch, sample_profile)
    resp = client.get(f"/tailor/{body['tailor_id']}/review")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["pending"] is True
    assert payload["brief"] == "One claim could not be traced."
    assert payload["items"][0]["item"] == "Ran a team of 40"


def test_get_review_falls_back_to_the_archived_record(client, data_dir, monkeypatch):
    # A resumed (or restart-orphaned) run still shows what was reviewed.
    document_store.save_review(SINGLE_EMAIL, "t-old", _review_request("t-old").model_dump())
    monkeypatch.setattr(
        routes, "build_tailoring_graph", lambda: FakeTailoringGraph({}, None)
    )
    payload = client.get("/tailor/t-old/review").json()
    assert payload["pending"] is False
    assert payload["items"][0]["id"] == "flag-0"


def test_get_review_404s_for_a_run_that_never_paused(client, data_dir, monkeypatch):
    monkeypatch.setattr(
        routes, "build_tailoring_graph", lambda: FakeTailoringGraph({}, None)
    )
    assert client.get("/tailor/t-none/review").status_code == 404


def test_resume_applies_the_decision_and_renders(
    client, data_dir, monkeypatch, sample_profile
):
    body, fake = _paused_tailor(client, monkeypatch, sample_profile)
    tailor_id = body["tailor_id"]
    fake.final_state = {
        "job_requirements": JobRequirements(title="Backend Engineer"),
        "tailored_cv": TailoredCV(headline="Senior Engineer", summary="Pitch."),
        "validation": ValidationResult(passed=True, needs_review=False),
        "documents": [
            {"kind": "cv", "format": "docx", "filename": "cv.docx", "size_bytes": 9}
        ],
        "render_skipped": None,
    }

    resp = client.post(
        f"/tailor/{tailor_id}/resume", json={"approvals": {"flag-0": False}}
    )
    assert resp.status_code == 200
    resumed = resp.json()
    assert resumed["review_required"] is False
    assert resumed["documents"][0]["url"] == f"/document/{tailor_id}?kind=cv&format=docx"
    assert fake.resumed_with == {
        "approvals": {"flag-0": False},
        "approve_all": False,
        "notes": "",
    }
    # The response keeps the profile it was tailored from (read back from the
    # run's saved result — the graph state does not carry it).
    assert resumed["profile_id"] == body["profile_id"]


def test_resume_404s_when_nothing_is_pending(client, data_dir, monkeypatch):
    # 7.d unified this with the cross-account case: a tailor_id with no pending
    # review — whether already resumed, never paused, or belonging to another
    # account — is the same 404, so the endpoint is no enumeration oracle (§14.8).
    monkeypatch.setattr(
        routes, "build_tailoring_graph", lambda: FakeTailoringGraph({}, None)
    )
    resp = client.post("/tailor/t-none/resume", json={"approve_all": True})
    assert resp.status_code == 404
    assert "no review pending" in resp.json()["detail"]


def test_review_endpoints_reject_an_unsafe_tailor_id(client, data_dir):
    # The id addresses a directory under data/documents/, so anything outside
    # [A-Za-z0-9_-] is rejected before the graph or the store is touched.
    assert client.get("/tailor/bad.id/review").status_code == 400
    assert client.post("/tailor/bad.id/resume", json={}).status_code == 400
    # A traversal attempt never reaches a handler at all.
    assert client.get("/tailor/..%2F..%2Fetc/review").status_code in (400, 404, 405)


def test_get_document_serves_a_rendered_file(client, data_dir):
    path = document_store.document_path(SINGLE_EMAIL, "tailor-1", "cv", "docx")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"docx bytes")

    resp = client.get("/document/tailor-1")  # kind=cv, format=docx by default
    assert resp.status_code == 200
    assert resp.content == b"docx bytes"
    assert "cv.docx" in resp.headers["content-disposition"]


def test_get_document_404s_when_not_rendered(client, data_dir):
    assert client.get("/document/tailor-1").status_code == 404
    assert client.get("/document/tailor-1", params={"format": "pdf"}).status_code == 404


def test_get_document_rejects_unknown_kind_and_unsafe_id(client, data_dir):
    assert client.get("/document/tailor-1", params={"kind": "portfolio"}).status_code == 400
    assert client.get("/document/..%2F..%2Fetc").status_code in (400, 404)


def test_ingest_events_sse_streams_progress(client):
    from src.utils import auth_store

    # The stream is owner-scoped (§14.8): pre-create the job owned by the same
    # single-user account the auth-off client resolves to, so the subscribe
    # succeeds rather than 404ing as a foreign job.
    queue = routes.jobs.create("sse-job", auth_store.uid(SINGLE_EMAIL))
    queue.put_nowait({"event": "node", "data": "extract_source"})
    queue.put_nowait({"event": "done"})
    with client.stream("GET", "/ingest/sse-job/events") as resp:
        body = "".join(resp.iter_text())
    assert "extract_source" in body
    assert "done" in body
    assert routes.jobs.get("sse-job") is None  # cleaned up after done


def test_tailor_missing_profile_404(client):
    resp = client.post("/tailor", json={"profile_id": "nope", "job_post": "text"})
    assert resp.status_code == 404


def test_tailor_empty_job_post_400(client, sample_profile):
    profile_id, _ = profile_store.save_profile(SINGLE_EMAIL, sample_profile)
    resp = client.post("/tailor", json={"profile_id": profile_id, "job_post": "  "})
    assert resp.status_code == 400
