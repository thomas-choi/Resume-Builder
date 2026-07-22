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


@pytest.fixture
def client(data_dir, monkeypatch):
    # Serve the API alone: whether a built frontend happens to be present in the
    # working tree must not change how the API tests behave.
    monkeypatch.setattr(config, "FRONTEND_DIR", data_dir / "no-frontend")
    return TestClient(create_app())


class FakeIngestionGraph:
    def __init__(self, final_state):
        self.final_state = final_state
        self.received_state = None

    def stream(self, state, stream_mode=None):
        self.received_state = state
        yield {"ingest_sources": {}}
        yield {"extract_source": {}}
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
    cv_path = data_dir / "sources" / "job-1" / "cv" / "resume.docx"
    assert cv_path.exists()
    manifest = json.loads((data_dir / "sources" / "job-1" / "manifest.json").read_text())
    assert manifest["run_id"] == "job-1"
    categories = {e["category"] for e in manifest["sources"]}
    assert categories == {"cv"}
    assert manifest["sources"][0]["sha256"]


def test_ingest_archives_github_and_free_text_sources(
    client, data_dir, monkeypatch, sample_profile
):
    final_state = {"profile": sample_profile, "profile_id": "abc123", "version": 1}
    monkeypatch.setattr(
        routes, "build_ingestion_graph", lambda: FakeIngestionGraph(final_state)
    )

    def fake_fetch(username):
        from src.models.schemas import SourceDocument

        return SourceDocument(
            id=f"github:{username}", source_type="github", raw_text="repos ..."
        )

    monkeypatch.setattr(routes, "fetch_github_profile", fake_fetch)

    resp = client.post(
        "/ingest",
        data={
            "job_id": "job-2",
            "github_username": "alice",
            "free_text": "LinkedIn summary paragraph",
        },
    )
    assert resp.status_code == 200

    run_dir = data_dir / "sources" / "job-2"
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
    stored = data_dir / "sources" / "job-li" / "linkedin" / "Basic_LinkedInDataExport.zip"
    assert stored.exists()
    assert source.stored_path == str(stored)
    manifest = json.loads((data_dir / "sources" / "job-li" / "manifest.json").read_text())
    entry = manifest["sources"][0]
    assert entry["category"] == "linkedin"
    assert entry["source_id"] == "linkedin:Basic_LinkedInDataExport.zip"


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
    assert (data_dir / "sources" / "job-bad" / "linkedin" / "export.zip").exists()


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
    profile_id, _ = profile_store.save_profile(sample_profile)

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
    profile_id, _ = profile_store.save_profile(sample_profile)
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
    profile_id, _ = profile_store.save_profile(sample_profile)
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
        (data_dir / "documents" / body["tailor_id"] / "tailor.json").read_text()
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
    profile_id, _ = profile_store.save_profile(sample_profile)
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
    # The run is checkpointed under its own tailor_id.
    assert fake.config == {"configurable": {"thread_id": body["tailor_id"]}}


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
    document_store.save_review("t-old", _review_request("t-old").model_dump())
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


def test_resume_409s_when_nothing_is_pending(client, data_dir, monkeypatch):
    monkeypatch.setattr(
        routes, "build_tailoring_graph", lambda: FakeTailoringGraph({}, None)
    )
    resp = client.post("/tailor/t-none/resume", json={"approve_all": True})
    assert resp.status_code == 409
    assert "no review pending" in resp.json()["detail"]


def test_review_endpoints_reject_an_unsafe_tailor_id(client, data_dir):
    # The id addresses a directory under data/documents/, so anything outside
    # [A-Za-z0-9_-] is rejected before the graph or the store is touched.
    assert client.get("/tailor/bad.id/review").status_code == 400
    assert client.post("/tailor/bad.id/resume", json={}).status_code == 400
    # A traversal attempt never reaches a handler at all.
    assert client.get("/tailor/..%2F..%2Fetc/review").status_code in (400, 404, 405)


def test_get_document_serves_a_rendered_file(client, data_dir):
    path = document_store.document_path("tailor-1", "cv", "docx")
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
    queue = routes.jobs.create("sse-job")
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
    profile_id, _ = profile_store.save_profile(sample_profile)
    resp = client.post("/tailor", json={"profile_id": profile_id, "job_post": "  "})
    assert resp.status_code == 400
