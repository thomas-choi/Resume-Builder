"""API tests via TestClient with the graphs mocked out."""

import pytest
from fastapi.testclient import TestClient

from src.api import routes
from src.api.main import create_app
from src.models.schemas import JobRequirements, TailoredCV, ValidationResult
from src.utils import profile_store
from tests.conftest import build_sample_docx


@pytest.fixture
def client(data_dir):
    return TestClient(create_app())


class FakeIngestionGraph:
    def __init__(self, final_state):
        self.final_state = final_state

    def stream(self, state, stream_mode=None):
        yield {"ingest_sources": {}}
        yield {"extract_source": {}}
        yield {"synthesize_profile": {}}
        yield {"store_profile": self.final_state}


class FakeTailoringGraph:
    def __init__(self, final_state):
        self.final_state = final_state

    def invoke(self, state):
        return {**state, **self.final_state}


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ingest_docx_returns_profile(client, monkeypatch, sample_profile):
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
    assert body["profile"]["name"] == "Alice Smith"
    # conflicts are surfaced in the response
    assert body["profile"]["conflicts"][0]["field"] == "experience.start_date"


def test_ingest_without_sources_is_400(client):
    resp = client.post("/ingest", data={})
    assert resp.status_code == 400


def test_ingest_rejects_unknown_file_type(client):
    resp = client.post("/ingest", files=[("cv", ("resume.txt", b"hi", "text/plain"))])
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
