"""FastAPI auth dependencies (Phase 7).

:func:`current_user` resolves the account a request acts as. Two modes (§14.11):

- ``AUTH_ENABLED=true`` (the shipped default, 7.d fail-closed): read the session
  cookie, load the session + account, slide freshness forward, and raise ``401``
  on a missing/expired session. The user-id comes *only* from the session —
  never from a path, query, form field or header (§14.2).
- ``AUTH_ENABLED=false`` (legacy single-user mode, 7.c): return a synthetic
  verified account for ``SINGLE_USER_EMAIL`` without reading any cookie, so the
  business routes stay open with no login. Its data still lands under
  ``user_root(SINGLE_USER_EMAIL)`` — the *same* store code path as a real user,
  so auth-off can never become the untested branch.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, Request

from src import config
from src.models.schemas import User
from src.utils import auth_store


def _single_user() -> User:
    """The synthetic verified account used when ``AUTH_ENABLED=false`` (§14.11)."""
    email = auth_store.normalize(config.SINGLE_USER_EMAIL)
    return User(
        email=email,
        display_email=config.SINGLE_USER_EMAIL,
        first_name="Local",
        last_name="User",
        email_verified=True,
        created_at=datetime.now(timezone.utc),
    )


def current_user(request: Request) -> User:
    """Resolve the account a request acts as, or ``401`` (§14.8, §14.11)."""
    if not config.AUTH_ENABLED:
        return _single_user()
    cookie = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not cookie:
        raise HTTPException(status_code=401, detail="not authenticated")
    session = auth_store.load_session(cookie)
    if session is None:
        raise HTTPException(status_code=401, detail="session expired")
    user = auth_store.load_user(session.email)
    if user is None:
        # Session outlived its account (e.g. a deleted user) — treat as signed out.
        auth_store.delete_session(cookie)
        raise HTTPException(status_code=401, detail="session expired")
    return user
