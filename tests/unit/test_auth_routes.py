"""Tests for the /auth/* routes (Phase 7.f): password sign-up / sign-in / change."""

import pytest
from fastapi.testclient import TestClient

from src import config
from src.api.main import create_app
from src.utils import auth_store

GOOD_PW = "s3cret_pw"  # >8 chars and contains '_'
OTHER_PW = "an0ther-pw"  # >8 chars and contains '-'


@pytest.fixture
def client(data_dir, monkeypatch):
    # Cookie must round-trip over TestClient's http:// origin.
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "FRONTEND_DIR", data_dir / "no-frontend")
    auth_store.reset_rate_limits()
    return TestClient(create_app())


def _signup(client, email="a@example.com", password=GOOD_PW, first="A", last="B"):
    return client.post(
        "/auth/signup",
        json={"first_name": first, "last_name": last, "email": email, "password": password},
    )


# --- sign-up ----------------------------------------------------------------


def test_signup_creates_account_and_signs_in(client):
    resp = _signup(client)
    assert resp.status_code == 201
    assert resp.json()["email"] == "a@example.com"
    assert config.SESSION_COOKIE_NAME in resp.cookies
    user = auth_store.load_user("a@example.com")
    assert user is not None and user.password_hash
    assert user.password_hash != GOOD_PW  # only the hash is stored


def test_signup_rejects_weak_password(client):
    resp = _signup(client, password="short_1")  # only 7 chars
    assert resp.status_code == 400
    assert auth_store.load_user("a@example.com") is None


def test_signup_rejects_password_without_special(client):
    resp = _signup(client, password="abcdefghij")  # long enough, no special char
    assert resp.status_code == 400


def test_duplicate_signup_is_409(client):
    _signup(client)
    resp = _signup(client, first="X", last="Y")
    assert resp.status_code == 409


def test_signup_rejects_origin_outside_allow_list(client, monkeypatch):
    monkeypatch.setattr(
        config, "AUTH_ALLOWED_ORIGINS", frozenset({"http://localhost:8000"})
    )
    resp = client.post(
        "/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": "a@example.com",
            "password": GOOD_PW,
        },
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


# --- sign-in ----------------------------------------------------------------


def test_signin_with_correct_password(client):
    _signup(client)
    client.cookies.clear()
    resp = client.post("/auth/signin", json={"email": "a@example.com", "password": GOOD_PW})
    assert resp.status_code == 200
    assert config.SESSION_COOKIE_NAME in resp.cookies


def test_signin_wrong_password_is_401(client):
    _signup(client)
    resp = client.post(
        "/auth/signin", json={"email": "a@example.com", "password": "wrong_pass1"}
    )
    assert resp.status_code == 401


def test_signin_unknown_account_is_401(client):
    resp = client.post(
        "/auth/signin", json={"email": "ghost@example.com", "password": GOOD_PW}
    )
    assert resp.status_code == 401


def test_signin_unknown_and_wrong_password_give_same_error(client):
    _signup(client)
    a = client.post(
        "/auth/signin", json={"email": "a@example.com", "password": "wrong_pass1"}
    )
    b = client.post(
        "/auth/signin", json={"email": "ghost@example.com", "password": GOOD_PW}
    )
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]  # no account oracle


# --- change password --------------------------------------------------------


def test_change_password_then_signin_with_new(client):
    _signup(client)  # leaves a session cookie on the client
    resp = client.post(
        "/auth/change-password",
        json={"current_password": GOOD_PW, "new_password": OTHER_PW},
    )
    assert resp.status_code == 204
    client.cookies.clear()
    # old password no longer works
    old = client.post("/auth/signin", json={"email": "a@example.com", "password": GOOD_PW})
    assert old.status_code == 401
    # new password does
    new = client.post("/auth/signin", json={"email": "a@example.com", "password": OTHER_PW})
    assert new.status_code == 200


def test_change_password_wrong_current_is_400(client):
    _signup(client)
    resp = client.post(
        "/auth/change-password",
        json={"current_password": "not-it-99", "new_password": OTHER_PW},
    )
    assert resp.status_code == 400


def test_change_password_rejects_weak_new(client):
    _signup(client)
    resp = client.post(
        "/auth/change-password",
        json={"current_password": GOOD_PW, "new_password": "weak"},
    )
    assert resp.status_code == 400


def test_change_password_requires_session(client):
    resp = client.post(
        "/auth/change-password",
        json={"current_password": GOOD_PW, "new_password": OTHER_PW},
    )
    assert resp.status_code == 401


# --- session lifecycle ------------------------------------------------------


def test_me_requires_session(client):
    assert client.get("/auth/me").status_code == 401
    _signup(client)
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "a@example.com"


def test_signout_revokes_session(client):
    _signup(client)
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/signout").status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_cookie_flags(client):
    resp = _signup(client)
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/" in header
    assert "max-age=" in header
