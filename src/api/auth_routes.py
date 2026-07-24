"""Password account routes (design doc §14.5 / §14.13), Phase 7.f.

A second, **unauthenticated** ``APIRouter`` carrying ``/auth/*``. Passwords
replace the earlier passwordless email code/link flow: sign-up sets a password
and opens a session immediately, sign-in is email + password, and a signed-in
account can change its own password.

Email verification is currently OFF (the ``email_verified`` flag is stamped
True at creation and never gates login). The challenge/mailer machinery in
``auth_store``/``mailer`` is retained but no longer wired here, so verification
can be reintroduced later without rebuilding it.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src import config
from src.api.deps import current_user
from src.models.schemas import (
    ChangePasswordRequest,
    SignInRequest,
    SignUpRequest,
    User,
    UserPublic,
)
from src.utils import auth_store, passwords

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _check_origin(request: Request) -> None:
    """Cheap second gate behind SameSite: a present ``Origin`` must be allowed."""
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in config.AUTH_ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="bad origin")


def _validate_password(password: str) -> None:
    """Reject a password that fails the rule with a ``400`` carrying the reason."""
    reason = passwords.validate_password_rule(password)
    if reason is not None:
        raise HTTPException(status_code=400, detail=reason)


def _issue_session(response: Response, email: str) -> None:
    """Open a session for ``email`` and attach the session cookie to ``response``."""
    cookie = auth_store.create_session(email)
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=cookie,
        max_age=config.SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        secure=config.SESSION_COOKIE_SECURE,
        path="/",
    )


def _public(user: User) -> UserPublic:
    return UserPublic(
        email=user.email, first_name=user.first_name, last_name=user.last_name
    )


@auth_router.post("/signup", status_code=201)
async def signup(body: SignUpRequest, request: Request, response: Response) -> UserPublic:
    """Create an account with a password and sign in immediately.

    The password is validated against the rule first; an already-registered
    address returns ``409`` (with verification off, sign-up is no longer an
    account-existence oracle — the trade-off accepted in Phase 7.f).
    """
    _check_origin(request)
    email = auth_store.normalize(body.email)
    _validate_password(body.password)
    password_hash = passwords.hash_password(body.password)
    try:
        user = auth_store.create_user(
            body.first_name, body.last_name, body.email, password_hash
        )
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409, detail="An account with this email already exists."
        ) from exc
    _issue_session(response, email)
    response.status_code = 201
    logger.info("auth: account created + signed in uid=%s", auth_store.uid(email))
    return _public(user)


@auth_router.post("/signin")
async def signin(body: SignInRequest, request: Request, response: Response) -> UserPublic:
    """Sign in with email + password.

    A missing account and a wrong password both return the same ``401`` so the
    endpoint does not reveal whether an address is registered.
    """
    _check_origin(request)
    email = auth_store.normalize(body.email)
    user = auth_store.load_user(email)
    if user is None or not passwords.verify_password(body.password, user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    _issue_session(response, email)
    logger.info("auth: signed in uid=%s", auth_store.uid(email))
    return _public(user)


@auth_router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
) -> Response:
    """Change the signed-in account's password.

    Requires the current password (proof of possession), validates the new one
    against the rule, then rotates the session so a change re-establishes a fresh
    cookie and any other session for the account is dropped.
    """
    _check_origin(request)
    if not passwords.verify_password(body.current_password, user.password_hash or ""):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    _validate_password(body.new_password)
    auth_store.set_password(user.email, passwords.hash_password(body.new_password))
    # Rotate the session: revoke the cookie in play, issue a fresh one.
    old_cookie = request.cookies.get(config.SESSION_COOKIE_NAME)
    if old_cookie:
        auth_store.delete_session(old_cookie)
    _issue_session(response, user.email)
    response.status_code = 204
    logger.info("auth: password changed uid=%s", auth_store.uid(user.email))
    return response


@auth_router.get("/me")
async def me(user: User = Depends(current_user)) -> UserPublic:
    """Return the signed-in account, or ``401``."""
    return _public(user)


@auth_router.post("/signout", status_code=204)
async def signout(
    request: Request, response: Response, user: User = Depends(current_user)
) -> Response:
    """Revoke the session and clear the cookie."""
    _check_origin(request)
    cookie = request.cookies.get(config.SESSION_COOKIE_NAME)
    if cookie:
        auth_store.delete_session(cookie)
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/")
    response.status_code = 204
    return response
