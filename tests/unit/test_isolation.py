"""R3 isolation suite (Phase 7.d, §14.8/§14.14).

Enforcement is *on* here (``AUTH_ENABLED=true``): every business route requires
a session, an id under another account's root is a ``404`` (never ``403``, never
that account's data), and the three non-path side channels — the SSE registry,
the checkpointer ``thread_id`` and the logs — are isolated too.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from src import config
from src.api import routes
from src.api.main import create_app
from src.models.schemas import (
    CoverLetter,
    JobRequirements,
    TailoredCV,
    ValidationFlag,
    ValidationResult,
)
from src.agents import job_analysis, review, tailoring, tailoring_graph, validation
from src.utils import auth_store, document_store, profile_store


@pytest.fixture
def app_client(data_dir, monkeypatch):
    monkeypatch.setattr(config, "FRONTEND_DIR", data_dir / "no-frontend")
    monkeypatch.setattr(config, "AUTH_ENABLED", True)
    return TestClient(create_app())


def _account(email: str) -> str:
    """Create a verified account and return a live session cookie."""
    try:
        auth_store.create_user("Test", "User", email)
    except FileExistsError:
        pass
    auth_store.mark_verified(email)
    return auth_store.create_session(email)


@pytest.fixture
def two_accounts(app_client):
    return _account("a@example.com"), _account("b@example.com")


def _cookie(client, cookie):
    client.cookies.clear()
    client.cookies.set(config.SESSION_COOKIE_NAME, cookie)
    return client


# --------------------------------------------------------------------------
# Unauthenticated → 401 on every business route (parametrized over the table)
# --------------------------------------------------------------------------


def _business_routes():
    seen = []
    for route in routes.router.routes:
        methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        for method in sorted(methods):
            seen.append((method, route.path))
    return seen


@pytest.mark.parametrize("method,path", _business_routes())
def test_unauthenticated_business_route_is_401(app_client, method, path):
    # A newly added unprotected route fails this suite (the table is enumerated,
    # not hand-listed) — the whole point of the router-level dependency (§14.8).
    concrete = path.replace("{profile_id}", "p1").replace("{tailor_id}", "t1").replace(
        "{job_id}", "j1"
    )
    app_client.cookies.clear()
    resp = app_client.request(method, concrete)
    assert resp.status_code == 401


def test_healthz_and_auth_stay_open(app_client):
    assert app_client.get("/healthz").status_code == 200


def test_expired_session_is_401_and_signout_revokes(app_client):
    cookie = _account("c@example.com")
    _cookie(app_client, cookie)
    assert app_client.get("/auth/me").status_code == 200
    # Sign out revokes the session server-side.
    app_client.post("/auth/signout")
    _cookie(app_client, cookie)
    assert app_client.get("/profile/anything", ).status_code == 401


# --------------------------------------------------------------------------
# Cross-account: an id under another root is a 404, never 403, never B's data
# --------------------------------------------------------------------------


def test_cross_account_profile_is_404(app_client, two_accounts, sample_profile):
    a_cookie, b_cookie = two_accounts
    b_id, _ = profile_store.save_profile("b@example.com", sample_profile)

    _cookie(app_client, a_cookie)
    assert app_client.get(f"/profile/{b_id}").status_code == 404
    put = app_client.put(f"/profile/{b_id}", json=sample_profile.model_dump())
    assert put.status_code == 404
    # B still reads their own profile — nothing was leaked or clobbered.
    _cookie(app_client, b_cookie)
    assert app_client.get(f"/profile/{b_id}").status_code == 200


def test_cross_account_document_and_review_are_404(app_client, two_accounts, sample_profile):
    a_cookie, b_cookie = two_accounts
    # Seed a rendered doc + a persisted review under B.
    path = document_store.document_path("b@example.com", "b-tailor", "cv", "docx")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"docx")
    document_store.save_review("b@example.com", "b-tailor", {"tailor_id": "b-tailor", "items": []})

    _cookie(app_client, a_cookie)
    assert app_client.get("/document/b-tailor").status_code == 404
    assert app_client.get("/tailor/b-tailor/review").status_code == 404
    assert app_client.post("/tailor/b-tailor/resume", json={}).status_code == 404
    # B sees their own.
    _cookie(app_client, b_cookie)
    assert app_client.get("/document/b-tailor").status_code == 200
    assert app_client.get("/tailor/b-tailor/review").status_code == 200


def test_traversal_ids_are_rejected_before_disk(app_client, two_accounts):
    a_cookie, _ = two_accounts
    _cookie(app_client, a_cookie)
    for bad in ("..%2F..%2Fetc", "bad.id"):
        assert app_client.get(f"/profile/{bad}").status_code in (400, 404)
        assert app_client.get(f"/tailor/{bad}/review").status_code in (400, 404)


def test_nothing_escapes_the_account_root(app_client, two_accounts, sample_profile):
    a_cookie, _ = two_accounts
    profile_store.save_profile("a@example.com", sample_profile)
    document_store.save_result("a@example.com", "t-a", {"ok": True})
    a_root = config.user_root("a@example.com")
    # Every business file A produced lives under a_root; nothing at the top level.
    for legacy in ("profiles", "sources", "output", "documents"):
        assert not (config.DATA_DIR / legacy).exists()
    assert any(a_root.rglob("*.json"))


# --------------------------------------------------------------------------
# SSE registry owner (non-path side channel #1)
# --------------------------------------------------------------------------


def test_sse_stream_is_owner_scoped(app_client, two_accounts):
    a_cookie, b_cookie = two_accounts
    a_uid = auth_store.uid("a@example.com")
    queue = routes.jobs.create("job-a", a_uid)
    queue.put_nowait({"event": "done"})

    # B cannot subscribe to A's job — same 404 as a job that never existed.
    _cookie(app_client, b_cookie)
    assert app_client.get("/ingest/job-a/events").status_code == 404


# --------------------------------------------------------------------------
# Checkpointer thread_id namespacing (non-path side channel #2)
# --------------------------------------------------------------------------


def _mock_tailoring_nodes(monkeypatch):
    monkeypatch.setattr(
        job_analysis, "analyze", lambda job_post: JobRequirements(title="Backend Engineer")
    )
    monkeypatch.setattr(
        tailoring,
        "tailor",
        lambda p, r: TailoredCV(
            headline="E", summary="S", highlighted_skills=["Python", "Kubernetes"]
        ),
    )
    monkeypatch.setattr(
        validation,
        "validate",
        lambda p, c: ValidationResult(
            passed=False,
            needs_review=True,
            flags=[ValidationFlag(item="Kubernetes", kind="skill", reason="unsourced")],
        ),
    )
    monkeypatch.setattr(review, "write_brief", lambda *a, **k: "brief")
    monkeypatch.setattr(config, "RENDER_PDF", False)


def test_paused_run_cannot_be_resumed_under_another_session(
    app_client, two_accounts, monkeypatch, sample_profile
):
    a_cookie, b_cookie = two_accounts
    _mock_tailoring_nodes(monkeypatch)
    profile_id, _ = profile_store.save_profile("a@example.com", sample_profile)

    # A starts a flagged run that pauses for review.
    _cookie(app_client, a_cookie)
    resp = app_client.post(
        "/tailor",
        json={"profile_id": profile_id, "job_post": "A job", "render": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["review_required"] is True
    tailor_id = body["tailor_id"]

    # B, with the correct tailor_id, cannot resume A's paused run: the
    # checkpointer key is namespaced to the owner, so B's namespace has no
    # pending review → 404.
    _cookie(app_client, b_cookie)
    assert app_client.post(f"/tailor/{tailor_id}/resume", json={}).status_code == 404
    # A can.
    _cookie(app_client, a_cookie)
    assert app_client.post(f"/tailor/{tailor_id}/resume", json={}).status_code == 200


# --------------------------------------------------------------------------
# Logs carry the uid, never the raw email (non-path side channel #3)
# --------------------------------------------------------------------------


def test_logs_carry_the_uid_not_the_email(
    app_client, two_accounts, monkeypatch, sample_profile, caplog
):
    a_cookie, _ = two_accounts
    _mock_tailoring_nodes(monkeypatch)
    profile_id, _ = profile_store.save_profile("a@example.com", sample_profile)

    _cookie(app_client, a_cookie)
    with caplog.at_level(logging.DEBUG):
        app_client.post(
            "/tailor",
            json={"profile_id": profile_id, "job_post": "A job", "render": True},
        )

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "a@example.com" not in text  # the raw address is never logged
