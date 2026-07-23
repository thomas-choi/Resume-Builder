"""Filesystem-backed account, challenge and session store (design doc §14.3–14.7).

Pure filesystem layer — **no FastAPI imports**. Everything lives under
``config.DATA_DIR / "auth"``:

- ``users/{uid}.json``       — the account record; ``uid = sha256(normalize(email))``.
- ``challenges/{lookup}.json`` — a code/link proof; the filename **is** the lookup
  key. Neither the raw code nor the raw token is ever stored (§14.4).
- ``sessions/{sha256(cookie)}.json`` — a server-side session.

The normalized email is the user-id (R4); ``uid`` is only the on-disk handle, so
the address never appears in a directory name. Every write is atomic (tempfile in
the same dir + ``os.replace``); every read tolerates a missing file as
"not found" (§14.3).

Two challenge lookups:

- **code** — ``lookup = sha256(email:code)``; scoped to the email so a guesser
  must target one address. A companion index ``challenges/idx-{sha256(email)}.json``
  points email → live code lookup, so a **wrong** guess (which resolves to a
  different, non-existent hash) can still find the live challenge to increment
  ``attempts`` and burn it past ``AUTH_MAX_CODE_ATTEMPTS``.
- **link** — ``lookup = sha256(token)``; high-entropy, single-shot.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import config
from src.models.schemas import Challenge, Session, User

logger = logging.getLogger(__name__)


class ChallengeInvalid(Exception):
    """The proof is unknown, wrong, or presented on the wrong flow/method (→ 400)."""


class ChallengeExpired(Exception):
    """The proof was real but is expired, consumed, or attempts-exhausted (→ 410)."""


# --- identity ---------------------------------------------------------------


def normalize(email: str) -> str:
    """The user-id: ``strip().lower()`` only (no gmail dot/plus folding — §14.3)."""
    return email.strip().lower()


def uid(email: str) -> str:
    """The on-disk handle for an account — ``sha256(normalize(email))``, 64 hex."""
    return hashlib.sha256(normalize(email).encode("utf-8")).hexdigest()


# --- roots ------------------------------------------------------------------


def _auth_root() -> Path:
    return config.DATA_DIR / "auth"


def _users_dir() -> Path:
    return _auth_root() / "users"


def _challenges_dir() -> Path:
    return _auth_root() / "challenges"


def _sessions_dir() -> Path:
    return _auth_root() / "sessions"


# --- low-level IO -----------------------------------------------------------


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file in the same dir + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_text(path: Path) -> str | None:
    """Read a file, returning ``None`` if it is missing (expired-and-swept)."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)


# --- users ------------------------------------------------------------------


def _user_path(email: str) -> Path:
    return _users_dir() / f"{uid(email)}.json"


def create_user(first_name: str, last_name: str, email: str) -> User:
    """Atomically claim an account for ``email`` (R4, §14.3).

    Uses ``open(..., "x")`` (``O_CREAT|O_EXCL``): two concurrent sign-ups for one
    address race on the create and exactly one wins.

    Raises:
        FileExistsError: The account already exists — the "account exists" signal.
    """
    normalized = normalize(email)
    user = User(
        email=normalized,
        display_email=email.strip(),
        first_name=first_name,
        last_name=last_name,
        created_at=_now(),
    )
    path = _user_path(normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as handle:  # O_EXCL account claim
        handle.write(user.model_dump_json(indent=2))
    logger.info("auth: created account uid=%s", uid(normalized))
    return user


def load_user(email: str) -> User | None:
    """Load an account by email, or ``None`` if it does not exist."""
    text = _read_text(_user_path(email))
    if text is None:
        return None
    return User.model_validate_json(text)


def _save_user(user: User) -> None:
    _write_atomic(_user_path(user.email), user.model_dump_json(indent=2))


def mark_verified(email: str) -> User | None:
    """Flip ``email_verified`` / ``verified_at`` on an account (idempotent)."""
    user = load_user(email)
    if user is None:
        return None
    if not user.email_verified:
        user.email_verified = True
        user.verified_at = _now()
        _save_user(user)
    return user


# --- challenges -------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _code_lookup(email: str, code: str) -> str:
    return _sha256_hex(f"{normalize(email)}:{code}")


def _token_lookup(token: str) -> str:
    return _sha256_hex(token)


def _challenge_path(lookup: str) -> Path:
    return _challenges_dir() / f"{lookup}.json"


def _idx_path(email: str) -> Path:
    """email → live code-challenge lookup, so a wrong guess can still find it."""
    return _challenges_dir() / f"idx-{uid(email)}.json"


def _read_challenge(lookup: str) -> Challenge | None:
    text = _read_text(_challenge_path(lookup))
    if text is None:
        return None
    return Challenge.model_validate_json(text)


def _write_challenge(lookup: str, record: Challenge) -> None:
    _write_atomic(_challenge_path(lookup), record.model_dump_json(indent=2))


def _clear_code_challenge(email: str) -> None:
    """Drop any live code challenge for ``email`` before minting a fresh one."""
    idx = _idx_path(email)
    text = _read_text(idx)
    if text is not None:
        try:
            live = json.loads(text).get("lookup")
        except json.JSONDecodeError:
            live = None
        if live:
            _remove(_challenge_path(live))
    _remove(idx)


def mint(email: str, purpose: str, method: str) -> str:
    """Mint a challenge, persist only its hash, and return the raw code/token.

    Args:
        email: The address the proof is for (normalized internally).
        purpose: ``"signup"`` or ``"signin"`` — binds the proof to its flow.
        method: ``"code"`` or ``"link"``.

    Returns:
        The raw 6-digit code (code method) or url-safe token (link method), for
        the caller to email. Only the hash-keyed record is written to disk.
    """
    email = normalize(email)
    now = _now()
    ttl = config.SIGNUP_TTL_S if purpose == "signup" else config.SIGNIN_TTL_S
    record = Challenge(
        email=email,
        purpose=purpose,  # type: ignore[arg-type]
        method=method,  # type: ignore[arg-type]
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
    if method == "code":
        _clear_code_challenge(email)
        code = f"{secrets.randbelow(1_000_000):06d}"
        lookup = _code_lookup(email, code)
        _write_challenge(lookup, record)
        _write_atomic(_idx_path(email), json.dumps({"lookup": lookup}))
        logger.info("auth: minted %s code challenge uid=%s", purpose, uid(email))
        return code
    if method == "link":
        token = secrets.token_urlsafe(32)
        _write_challenge(_token_lookup(token), record)
        logger.info("auth: minted %s link challenge uid=%s", purpose, uid(email))
        return token
    raise ValueError(f"unknown challenge method {method!r}")


def verify_challenge(
    *,
    method: str,
    email: str | None = None,
    code: str | None = None,
    token: str | None = None,
    expected_purpose: str | None = None,
) -> Challenge:
    """Consume a challenge, returning it on success.

    Args:
        method: How the proof is presented — ``"code"`` (needs ``email`` +
            ``code``) or ``"link"`` (needs ``token``).
        expected_purpose: If given, reject a challenge whose ``purpose`` differs
            (a sign-up proof presented on the sign-in path, and vice versa).

    Returns:
        The consumed :class:`Challenge` (its ``purpose`` tells the caller what to
        do next).

    Raises:
        ChallengeInvalid: Unknown / wrong code / wrong method / wrong purpose.
        ChallengeExpired: Expired, already consumed, or attempts exhausted.
    """
    now = _now()
    if method == "code":
        if not email or not code:
            raise ChallengeInvalid("code and email required")
        email = normalize(email)
        idx_text = _read_text(_idx_path(email))
        if idx_text is None:
            raise ChallengeInvalid("no live code challenge")
        live_lookup = json.loads(idx_text).get("lookup", "")
        record = _read_challenge(live_lookup)
        if record is None:
            _remove(_idx_path(email))
            raise ChallengeInvalid("challenge swept")
        if record.method != "code":
            raise ChallengeInvalid("method mismatch")
        if record.consumed_at is not None:
            raise ChallengeExpired("already consumed")
        if now >= record.expires_at:
            raise ChallengeExpired("expired")
        submitted = _code_lookup(email, code)
        if not hmac.compare_digest(submitted, live_lookup):
            record.attempts += 1
            if record.attempts >= config.AUTH_MAX_CODE_ATTEMPTS:
                _remove(_challenge_path(live_lookup))
                _remove(_idx_path(email))
                raise ChallengeExpired("attempts exhausted")
            _write_challenge(live_lookup, record)
            raise ChallengeInvalid("wrong code")
        if expected_purpose is not None and record.purpose != expected_purpose:
            raise ChallengeInvalid("purpose mismatch")
        record.consumed_at = now
        _write_challenge(live_lookup, record)
        # The idx is left pointing at the now-consumed challenge, so a replay of
        # the same code resolves to it and reports "consumed" (410) rather than
        # "unknown" (400). A fresh mint clears it via _clear_code_challenge.
        return record

    if method == "link":
        if not token:
            raise ChallengeInvalid("token required")
        lookup = _token_lookup(token)
        record = _read_challenge(lookup)
        if record is None:
            raise ChallengeInvalid("unknown token")
        if record.method != "link":
            raise ChallengeInvalid("method mismatch")
        if record.consumed_at is not None:
            raise ChallengeExpired("already consumed")
        if now >= record.expires_at:
            raise ChallengeExpired("expired")
        if expected_purpose is not None and record.purpose != expected_purpose:
            raise ChallengeInvalid("purpose mismatch")
        record.consumed_at = now
        _write_challenge(lookup, record)
        return record

    raise ChallengeInvalid(f"unknown method {method!r}")


# --- sessions ---------------------------------------------------------------


def _session_path(cookie: str) -> Path:
    return _sessions_dir() / f"{_sha256_hex(cookie)}.json"


def create_session(email: str) -> str:
    """Open a session for ``email`` and return the raw cookie value.

    The record is stored under ``sha256(cookie)``; the raw cookie is never
    written. Also stamps the account's ``last_login_at``.
    """
    email = normalize(email)
    now = _now()
    cookie = secrets.token_urlsafe(32)
    session = Session(
        email=email,
        created_at=now,
        expires_at=now + timedelta(seconds=config.SESSION_TTL_S),
        last_seen_at=now,
    )
    _write_atomic(_session_path(cookie), session.model_dump_json(indent=2))
    user = load_user(email)
    if user is not None:
        user.last_login_at = now
        _save_user(user)
    return cookie


def load_session(cookie: str) -> Session | None:
    """Load a live session by cookie, refreshing it (sliding), or ``None``.

    Expired sessions are swept and reported as not-found. A live session's
    ``last_seen_at`` and ``expires_at`` slide forward on each use (§14.7).
    """
    path = _session_path(cookie)
    text = _read_text(path)
    if text is None:
        return None
    session = Session.model_validate_json(text)
    now = _now()
    if now >= session.expires_at:
        _remove(path)
        return None
    session.last_seen_at = now
    session.expires_at = now + timedelta(seconds=config.SESSION_TTL_S)
    _write_atomic(path, session.model_dump_json(indent=2))
    return session


def delete_session(cookie: str) -> None:
    """Revoke a session — the cookie is instantly worthless (§14.7)."""
    _remove(_session_path(cookie))


# --- rate limiting ----------------------------------------------------------
#
# In-process timestamp log, adequate for the single-container deployment (§10).
# A multi-replica deployment needs shared state; noted here rather than solved.

_send_log: dict[str, list[float]] = {}
_WINDOW_S = 3600.0


def _recent(key: str, now: float) -> list[float]:
    stamps = [t for t in _send_log.get(key, []) if now - t < _WINDOW_S]
    _send_log[key] = stamps
    return stamps


def allow_send(email: str, ip: str | None = None) -> bool:
    """Whether another challenge may be emailed to ``email`` now (§14.9).

    Caps at ``AUTH_MAX_SENDS_PER_HOUR`` per address plus a looser per-IP ceiling
    (so one address cannot be mailbombed and one client cannot farm codes across
    addresses). Records the send when it allows it.
    """
    email = normalize(email)
    now = time.time()
    email_key = f"email:{email}"
    if len(_recent(email_key, now)) >= config.AUTH_MAX_SENDS_PER_HOUR:
        return False
    ip_key = f"ip:{ip}" if ip else None
    if ip_key is not None:
        if len(_recent(ip_key, now)) >= config.AUTH_MAX_SENDS_PER_HOUR * 4:
            return False
    _send_log[email_key].append(now)
    if ip_key is not None:
        _send_log[ip_key].append(now)
    return True


def reset_rate_limits() -> None:
    """Clear the in-process send log (test helper)."""
    _send_log.clear()
