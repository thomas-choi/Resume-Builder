"""Tests for the filesystem auth store (Phase 7.b): challenges and sessions."""

from datetime import timedelta

import pytest

from src import config
from src.utils import auth_store
from src.utils.auth_store import ChallengeExpired, ChallengeInvalid


@pytest.fixture(autouse=True)
def _reset_rate(data_dir):
    auth_store.reset_rate_limits()
    yield
    auth_store.reset_rate_limits()


# --- identity ---------------------------------------------------------------


def test_normalize_and_uid_are_case_insensitive(data_dir):
    assert auth_store.normalize("  Alice@Example.COM ") == "alice@example.com"
    assert auth_store.uid("Alice@example.com") == auth_store.uid("alice@example.com")
    assert len(auth_store.uid("a@b.com")) == 64


# --- users ------------------------------------------------------------------


def test_create_user_is_exclusive(data_dir):
    auth_store.create_user("Alice", "Smith", "Alice@Example.com", "hash123")
    user = auth_store.load_user("alice@example.com")
    assert user is not None
    assert user.email == "alice@example.com"
    assert user.display_email == "Alice@Example.com"
    assert user.password_hash == "hash123"
    # Verification is off (Phase 7.f): accounts are created already-verified.
    assert user.email_verified is True
    assert user.password_updated_at is not None
    with pytest.raises(FileExistsError):
        auth_store.create_user("Alice", "Again", "alice@example.com", "hash456")


def test_set_password_replaces_hash(data_dir):
    auth_store.create_user("A", "B", "a@example.com", "old-hash")
    user = auth_store.set_password("a@example.com", "new-hash")
    assert user.password_hash == "new-hash"
    assert auth_store.load_user("a@example.com").password_hash == "new-hash"


def test_set_password_unknown_account_is_none(data_dir):
    assert auth_store.set_password("nobody@example.com", "x") is None


# --- code challenges --------------------------------------------------------


def test_code_challenge_roundtrip(data_dir):
    code = auth_store.mint("a@example.com", "signup", "code")
    assert len(code) == 6 and code.isdigit()
    challenge = auth_store.verify_challenge(method="code", email="a@example.com", code=code)
    assert challenge.purpose == "signup"
    assert challenge.email == "a@example.com"


def test_code_consumed_rejected_on_second_use(data_dir):
    code = auth_store.mint("a@example.com", "signin", "code")
    auth_store.verify_challenge(method="code", email="a@example.com", code=code)
    with pytest.raises((ChallengeExpired, ChallengeInvalid)):
        auth_store.verify_challenge(method="code", email="a@example.com", code=code)


def test_wrong_code_increments_and_burns(data_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MAX_CODE_ATTEMPTS", 3)
    code = auth_store.mint("a@example.com", "signup", "code")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(3):
        with pytest.raises((ChallengeInvalid, ChallengeExpired)):
            auth_store.verify_challenge(method="code", email="a@example.com", code=wrong)
    # burned: even the correct code no longer works
    with pytest.raises((ChallengeExpired, ChallengeInvalid)):
        auth_store.verify_challenge(method="code", email="a@example.com", code=code)


def test_one_address_code_cannot_verify_another(data_dir):
    code_a = auth_store.mint("a@example.com", "signup", "code")
    auth_store.mint("b@example.com", "signup", "code")
    # a's code presented for b's live challenge → rejected
    with pytest.raises((ChallengeInvalid, ChallengeExpired)):
        auth_store.verify_challenge(method="code", email="b@example.com", code=code_a)


def test_expired_code_not_honoured(data_dir, monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_TTL_S", -1)  # already expired
    code = auth_store.mint("a@example.com", "signup", "code")
    with pytest.raises(ChallengeExpired):
        auth_store.verify_challenge(method="code", email="a@example.com", code=code)


def test_signup_proof_rejected_on_signin_path(data_dir):
    code = auth_store.mint("a@example.com", "signup", "code")
    with pytest.raises(ChallengeInvalid):
        auth_store.verify_challenge(
            method="code", email="a@example.com", code=code, expected_purpose="signin"
        )


def test_signin_proof_rejected_on_signup_path(data_dir):
    code = auth_store.mint("a@example.com", "signin", "code")
    with pytest.raises(ChallengeInvalid):
        auth_store.verify_challenge(
            method="code", email="a@example.com", code=code, expected_purpose="signup"
        )


def test_unknown_code_rejected(data_dir):
    auth_store.mint("a@example.com", "signup", "code")
    with pytest.raises((ChallengeInvalid, ChallengeExpired)):
        auth_store.verify_challenge(method="code", email="a@example.com", code="999999")


# --- link challenges --------------------------------------------------------


def test_link_challenge_roundtrip(data_dir):
    token = auth_store.mint("a@example.com", "signup", "link")
    assert len(token) > 20
    challenge = auth_store.verify_challenge(method="link", token=token)
    assert challenge.purpose == "signup"


def test_link_consumed_rejected_on_second_use(data_dir):
    token = auth_store.mint("a@example.com", "signin", "link")
    auth_store.verify_challenge(method="link", token=token)
    with pytest.raises(ChallengeExpired):
        auth_store.verify_challenge(method="link", token=token)


def test_expired_link_not_honoured(data_dir, monkeypatch):
    monkeypatch.setattr(config, "SIGNIN_TTL_S", -1)
    token = auth_store.mint("a@example.com", "signin", "link")
    with pytest.raises(ChallengeExpired):
        auth_store.verify_challenge(method="link", token=token)


def test_code_presented_through_link_path_rejected(data_dir):
    code = auth_store.mint("a@example.com", "signup", "code")
    with pytest.raises(ChallengeInvalid):
        auth_store.verify_challenge(method="link", token=code)


def test_link_presented_through_code_path_rejected(data_dir):
    token = auth_store.mint("a@example.com", "signup", "link")
    # no live code challenge for this email → invalid
    with pytest.raises((ChallengeInvalid, ChallengeExpired)):
        auth_store.verify_challenge(method="code", email="a@example.com", code=token)


def test_unknown_token_rejected(data_dir):
    with pytest.raises(ChallengeInvalid):
        auth_store.verify_challenge(method="link", token="not-a-real-token")


# --- no raw secret ever hits disk ------------------------------------------


def test_raw_code_and_token_never_written(data_dir):
    code = auth_store.mint("a@example.com", "signup", "code")
    token = auth_store.mint("b@example.com", "signup", "link")
    for path in (data_dir / "auth").rglob("*"):
        if path.is_file():
            blob = path.read_text(encoding="utf-8", errors="ignore")
            assert code not in blob
            assert token not in blob


# --- sessions ---------------------------------------------------------------


def test_session_create_load_delete(data_dir):
    auth_store.create_user("A", "B", "a@example.com")
    cookie = auth_store.create_session("a@example.com")
    session = auth_store.load_session(cookie)
    assert session is not None and session.email == "a@example.com"
    auth_store.delete_session(cookie)
    assert auth_store.load_session(cookie) is None


def test_session_create_stamps_last_login(data_dir):
    auth_store.create_user("A", "B", "a@example.com")
    auth_store.create_session("a@example.com")
    user = auth_store.load_user("a@example.com")
    assert user.last_login_at is not None


def test_expired_session_swept(data_dir, monkeypatch):
    monkeypatch.setattr(config, "SESSION_TTL_S", -1)
    cookie = auth_store.create_session("a@example.com")
    assert auth_store.load_session(cookie) is None


def test_session_sliding_refresh(data_dir):
    cookie = auth_store.create_session("a@example.com")
    first = auth_store.load_session(cookie)
    second = auth_store.load_session(cookie)
    assert second.last_seen_at >= first.last_seen_at
    assert second.expires_at >= first.expires_at


def test_unknown_cookie_is_none(data_dir):
    assert auth_store.load_session("nope") is None


# --- rate limiting ----------------------------------------------------------


def test_send_cap_holds(data_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MAX_SENDS_PER_HOUR", 3)
    email = "a@example.com"
    assert [auth_store.allow_send(email) for _ in range(3)] == [True, True, True]
    assert auth_store.allow_send(email) is False
    # a different address is unaffected
    assert auth_store.allow_send("b@example.com") is True
