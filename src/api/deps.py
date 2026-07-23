"""FastAPI auth dependencies (Phase 7.b).

Minimal for 7.b: :func:`current_user` reads the session cookie, loads the
session and its account, and raises ``401`` on a missing/expired session. Only
``/auth/me`` and ``/auth/signout`` depend on it now; wiring the **business**
routes behind it is 7.d.
"""

from fastapi import HTTPException, Request

from src import config
from src.models.schemas import User
from src.utils import auth_store


def current_user(request: Request) -> User:
    """Resolve the signed-in account from the session cookie, or ``401``.

    The user-id comes *only* from the session — never from a path, query, form
    field or header (§14.2). ``load_session`` slides the session's freshness
    forward as a side effect.
    """
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
