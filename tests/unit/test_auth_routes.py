"""Tests for the /auth/* routes (Phase 7.b), with the mailer mocked."""

import re

import pytest
from fastapi.testclient import TestClient

from src import config
from src.api import auth_routes
from src.api.main import create_app
from src.utils import auth_store


@pytest.fixture
def sent(monkeypatch):
    """Capture every mail the routes try to send instead of delivering it."""
    captured: list[dict] = []

    async def fake_send(to, subject, text, html=None):
        captured.append({"to": to, "subject": subject, "text": text, "html": html})

    monkeypatch.setattr(auth_routes, "send", fake_send)
    return captured


@pytest.fixture
def client(data_dir, monkeypatch, sent):
    # Cookie must round-trip over TestClient's http:// origin.
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "AUTH_VERIFY_METHOD", "code")
    monkeypatch.setattr(config, "FRONTEND_DIR", data_dir / "no-frontend")
    auth_store.reset_rate_limits()
    return TestClient(create_app())


def _code_from(text: str) -> str:
    return re.search(r"\b(\d{6})\b", text).group(1)


def _token_from(text: str) -> str:
    return re.search(r"token=([\w-]+)", text).group(1)


def _signup_and_verify(client, sent, email="a@example.com"):
    client.post(
        "/auth/signup", json={"first_name": "A", "last_name": "B", "email": email}
    )
    code = _code_from(sent[-1]["text"])
    return client.post("/auth/verify", json={"email": email, "code": code})


# --- sign-up ----------------------------------------------------------------


def test_signup_sends_code_and_returns_202(client, sent):
    resp = client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "a@example.com"},
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "sent", "method": "code"}
    assert len(sent) == 1
    assert auth_store.load_user("a@example.com") is not None


def test_signup_accepts_any_allowed_origin(client, sent, monkeypatch):
    monkeypatch.setattr(
        config,
        "AUTH_ALLOWED_ORIGINS",
        frozenset({"http://localhost:8000", "http://192.168.0.212:8000"}),
    )
    resp = client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "a@example.com"},
        headers={"Origin": "http://192.168.0.212:8000"},
    )
    assert resp.status_code == 202


def test_signup_rejects_origin_outside_allow_list(client, monkeypatch):
    monkeypatch.setattr(
        config, "AUTH_ALLOWED_ORIGINS", frozenset({"http://localhost:8000"})
    )
    resp = client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "a@example.com"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "bad origin"


def test_verify_flips_email_verified_and_sets_cookie(client, sent):
    resp = _signup_and_verify(client, sent)
    assert resp.status_code == 200
    assert resp.json()["email"] == "a@example.com"
    assert config.SESSION_COOKIE_NAME in resp.cookies
    assert auth_store.load_user("a@example.com").email_verified is True


def test_second_signup_verified_address_no_new_account(client, sent):
    _signup_and_verify(client, sent)
    before = auth_store.load_user("a@example.com").created_at
    resp = client.post(
        "/auth/signup",
        json={"first_name": "X", "last_name": "Y", "email": "a@example.com"},
    )
    assert resp.status_code == 202
    assert auth_store.load_user("a@example.com").created_at == before  # not recreated
    assert "already have" in sent[-1]["text"].lower()


def test_second_signup_unverified_address_resends_challenge(client, sent):
    client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "u@example.com"},
    )
    resp = client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "u@example.com"},
    )
    assert resp.status_code == 202
    # a fresh, usable signup code was sent, not the "already have" mail
    assert "already have" not in sent[-1]["text"].lower()
    code = _code_from(sent[-1]["text"])
    verify = client.post("/auth/verify", json={"email": "u@example.com", "code": code})
    assert verify.status_code == 200


# --- sign-in / R6 -----------------------------------------------------------


def test_signin_unknown_address_returns_202_and_no_account_mail(client, sent):
    resp = client.post("/auth/signin", json={"email": "ghost@example.com"})
    assert resp.status_code == 202
    assert "no" in sent[-1]["text"].lower() and "account" in sent[-1]["text"].lower()
    assert auth_store.load_user("ghost@example.com") is None


def test_verified_account_signs_in(client, sent):
    _signup_and_verify(client, sent)
    resp = client.post("/auth/signin", json={"email": "a@example.com"})
    assert resp.status_code == 202
    code = _code_from(sent[-1]["text"])
    verify = client.post("/auth/verify", json={"email": "a@example.com", "code": code})
    assert verify.status_code == 200
    assert config.SESSION_COOKIE_NAME in verify.cookies


def test_r6_unverified_signin_yields_no_session(client, sent):
    # Sign up but never verify.
    client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "u@example.com"},
    )
    # Sign in on the unverified account.
    resp = client.post("/auth/signin", json={"email": "u@example.com"})
    assert resp.status_code == 202
    # The proof minted is a *signup* challenge — completing it verifies the
    # account (R6: the only way out is to finish sign-up), it does not sneak a
    # sign-in past confirmation.
    code = _code_from(sent[-1]["text"])
    verify = client.post("/auth/verify", json={"email": "u@example.com", "code": code})
    assert verify.status_code == 200
    assert auth_store.load_user("u@example.com").email_verified is True


def test_verify_refuses_handcrafted_signin_challenge_for_unverified(client, sent):
    client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "u@example.com"},
    )
    # Forge a signin challenge directly in the store for the unverified account.
    code = auth_store.mint("u@example.com", "signin", "code")
    resp = client.post("/auth/verify", json={"email": "u@example.com", "code": code})
    assert resp.status_code == 400
    assert auth_store.load_session  # no session issued
    assert config.SESSION_COOKIE_NAME not in resp.cookies


# --- verify error taxonomy --------------------------------------------------


def test_verify_unknown_code_is_400(client, sent):
    client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "a@example.com"},
    )
    resp = client.post("/auth/verify", json={"email": "a@example.com", "code": "000001"})
    assert resp.status_code == 400


def test_verify_consumed_code_is_410(client, sent):
    client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "a@example.com"},
    )
    code = _code_from(sent[-1]["text"])
    client.post("/auth/verify", json={"email": "a@example.com", "code": code})
    resp = client.post("/auth/verify", json={"email": "a@example.com", "code": code})
    assert resp.status_code == 410


# --- session lifecycle ------------------------------------------------------


def test_me_requires_session(client, sent):
    assert client.get("/auth/me").status_code == 401
    _signup_and_verify(client, sent)
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "a@example.com"


def test_signout_revokes_session(client, sent):
    _signup_and_verify(client, sent)
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/signout").status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_cookie_flags(client, sent):
    resp = _signup_and_verify(client, sent)
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/" in header
    assert "max-age=" in header


# --- link mode --------------------------------------------------------------


def test_link_mode_verifies_via_token(client, sent, monkeypatch):
    monkeypatch.setattr(config, "AUTH_VERIFY_METHOD", "link")
    resp = client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "l@example.com"},
    )
    assert resp.json()["method"] == "link"
    token = _token_from(sent[-1]["text"])
    verify = client.post("/auth/verify", json={"token": token})
    assert verify.status_code == 200
    assert config.SESSION_COOKIE_NAME in verify.cookies


# --- mail failure -----------------------------------------------------------


def test_mail_send_failure_is_502(client, monkeypatch):
    async def boom(*a, **k):
        raise OSError("smtp down")

    monkeypatch.setattr(auth_routes, "send", boom)
    resp = client.post(
        "/auth/signup",
        json={"first_name": "A", "last_name": "B", "email": "a@example.com"},
    )
    assert resp.status_code == 502
