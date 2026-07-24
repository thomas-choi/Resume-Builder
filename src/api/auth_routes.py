"""Passwordless account routes (design doc §14.5 / §14.13), Phase 7.b.

A second, **unauthenticated** ``APIRouter`` carrying ``/auth/*``. The flow works
end to end via the ``file`` mail backend; the business routes stay open until
7.d. R6 (no login before confirmation) is enforced here: sign-in on an
unverified account mints a *signup* challenge, never a signin one, and
``/auth/verify`` re-checks ``email_verified`` before honouring a signin proof.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src import config
from src.api.deps import current_user
from src.models.schemas import (
    SignInRequest,
    SignUpRequest,
    User,
    UserPublic,
    VerifyRequest,
)
from src.utils import auth_store
from src.utils.auth_store import ChallengeExpired, ChallengeInvalid
from src.utils.mailer import send

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _check_origin(request: Request) -> None:
    """Cheap second gate behind SameSite: a present ``Origin`` must be allowed."""
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in config.AUTH_ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="bad origin")


def _guard_rate(email: str, request: Request) -> None:
    """Enforce the per-address / per-IP send cap before emailing (§14.9)."""
    ip = request.client.host if request.client else None
    if not auth_store.allow_send(email, ip):
        raise HTTPException(status_code=429, detail="too many requests, try later")


async def _deliver(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send mail, turning any delivery failure into a ``502`` (§14.9)."""
    try:
        await send(to, subject, text, html)
    except Exception as exc:  # noqa: BLE001 — every backend failure maps to 502
        logger.warning("auth: mail send failed for uid=%s: %s", auth_store.uid(to), exc)
        raise HTTPException(
            status_code=502, detail="could not send the email, try again"
        ) from exc


def _minutes(seconds: int) -> int:
    return max(1, seconds // 60)


async def _send_challenge(email: str, purpose: str, method: str) -> None:
    """Mint a challenge and email the code (or link) for it."""
    raw = auth_store.mint(email, purpose, method)
    ttl = config.SIGNUP_TTL_S if purpose == "signup" else config.SIGNIN_TTL_S
    finish = "Finish signing up" if purpose == "signup" else "Sign in"
    if method == "link":
        url = f"{config.PUBLIC_BASE_URL}/#/verify?token={raw}"
        text = (
            f"{finish} to Resume Builder by opening this link "
            f"(expires in {_minutes(ttl)} minutes):\n\n{url}\n"
        )
        html = f"<p>{finish} to Resume Builder:</p><p><a href='{url}'>Verify</a></p>"
        await _deliver(email, f"{finish} — your verification link", text, html)
    else:
        text = (
            f"{finish} to Resume Builder. Your verification code is {raw} "
            f"(expires in {_minutes(ttl)} minutes).\n"
        )
        await _deliver(email, f"{finish} — your verification code", text)


@auth_router.post("/signup", status_code=202)
async def signup(body: SignUpRequest, request: Request) -> dict:
    """Claim an account and email a sign-up challenge.

    Three branches — free / exists-unverified / exists-verified — all return an
    identical ``202`` so the endpoint is not an account oracle.
    """
    _check_origin(request)
    email = auth_store.normalize(body.email)
    method = config.AUTH_VERIFY_METHOD
    _guard_rate(email, request)
    try:
        auth_store.create_user(body.first_name, body.last_name, body.email)
        await _send_challenge(email, "signup", method)
    except FileExistsError:
        user = auth_store.load_user(email)
        if user is not None and user.email_verified:
            text = (
                "You already have a Resume Builder account. "
                f"Sign in here: {config.PUBLIC_BASE_URL}/#/signin\n"
            )
            await _deliver(email, "You already have an account", text)
        else:
            await _send_challenge(email, "signup", method)
    return {"status": "sent", "method": method}


@auth_router.post("/signin", status_code=202)
async def signin(body: SignInRequest, request: Request) -> dict:
    """Email a sign-in challenge — with R6 enforced in the middle branch.

    Unknown address → a "no account" mail; **unverified** account → a
    ``purpose=signup`` challenge (never a signin credential); verified account →
    the normal signin challenge. All three return an identical ``202``.
    """
    _check_origin(request)
    email = auth_store.normalize(body.email)
    method = config.AUTH_VERIFY_METHOD
    _guard_rate(email, request)
    user = auth_store.load_user(email)
    if user is None:
        text = (
            "There is no Resume Builder account for this address. "
            f"Sign up here: {config.PUBLIC_BASE_URL}/#/signup\n"
        )
        await _deliver(email, "No account found", text)
    elif not user.email_verified:
        # R6: an unverified address gets a signup proof, never a signin one.
        await _send_challenge(email, "signup", method)
    else:
        await _send_challenge(email, "signin", method)
    return {"status": "sent", "method": method}


@auth_router.post("/verify")
async def verify(body: VerifyRequest, request: Request, response: Response) -> UserPublic:
    """Consume a challenge, open a session and set the session cookie.

    Body is ``{email, code}`` (code mode) or ``{token}`` (link mode). On a
    ``signup`` proof the account is marked verified; a ``signin`` proof is
    honoured only for an already-verified account (belt for R6).
    """
    _check_origin(request)
    method = "link" if body.token else "code"
    try:
        challenge = auth_store.verify_challenge(
            method=method,
            email=body.email,
            code=body.code,
            token=body.token,
        )
    except ChallengeInvalid as exc:
        raise HTTPException(status_code=400, detail="invalid verification") from exc
    except ChallengeExpired as exc:
        raise HTTPException(status_code=410, detail="verification expired") from exc

    if challenge.purpose == "signup":
        user = auth_store.mark_verified(challenge.email)
    else:  # signin — re-check email_verified before honouring (R6)
        user = auth_store.load_user(challenge.email)
        if user is None or not user.email_verified:
            raise HTTPException(status_code=400, detail="invalid verification")

    if user is None:
        raise HTTPException(status_code=400, detail="invalid verification")

    cookie = auth_store.create_session(user.email)
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=cookie,
        max_age=config.SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        secure=config.SESSION_COOKIE_SECURE,
        path="/",
    )
    return UserPublic(email=user.email, first_name=user.first_name, last_name=user.last_name)


@auth_router.get("/me")
async def me(user: User = Depends(current_user)) -> UserPublic:
    """Return the signed-in account, or ``401``."""
    return UserPublic(email=user.email, first_name=user.first_name, last_name=user.last_name)


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
