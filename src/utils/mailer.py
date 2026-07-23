"""Email delivery for the account system (design doc §14.9).

One entry point, :func:`send`, dispatching on ``config.EMAIL_BACKEND``:

- ``file`` (**default**) — writes a complete ``.eml`` into
  ``config.EMAIL_OUTBOX_DIR``. The whole sign-up/sign-in flow is then
  exercisable with no SMTP server: OPERATIONS.md tells the operator to open the
  newest file in the outbox and read the code (or click the link).
- ``console`` — logs the subject and body at INFO (container demos).
- ``smtp`` — stdlib ``smtplib`` over STARTTLS, run in a worker thread via
  ``anyio.to_thread.run_sync`` (the same pattern the blocking graph runs use, so
  ``aiosmtplib`` is deliberately not added).

Send failures **propagate** — never swallowed; the auth route turns them into a
``502`` (§14.9). There is no retry/queue in this phase.
"""

import logging
import secrets
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import anyio

from src import config

logger = logging.getLogger(__name__)


async def send(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send one email via the configured backend.

    Args:
        to: Recipient address.
        subject: Message subject.
        text: Plain-text body (always set).
        html: Optional HTML alternative body.

    Raises:
        Exception: Whatever the backend raises on failure — the caller (an auth
            route) maps it to a ``502``. Nothing is swallowed.
    """
    backend = config.EMAIL_BACKEND
    if backend == "file":
        _send_file(to, subject, text, html)
    elif backend == "console":
        logger.info("EMAIL to=%s subject=%r\n%s", to, subject, text)
    elif backend == "smtp":
        message = _build_message(to, subject, text, html)
        await anyio.to_thread.run_sync(_smtp_send, message)
    else:
        raise ValueError(f"unknown EMAIL_BACKEND {backend!r}")


def _build_message(to: str, subject: str, text: str, html: str | None) -> EmailMessage:
    """Build a MIME message with a plain-text body and optional HTML alternative."""
    message = EmailMessage()
    message["From"] = config.EMAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html is not None:
        message.add_alternative(html, subtype="html")
    return message


def _send_file(to: str, subject: str, text: str, html: str | None) -> None:
    """Write the message as a complete ``.eml`` into the outbox directory.

    The filename is timestamped down to microseconds plus a random suffix, so
    the newest message sorts **last** (the operator reads the last file).
    """
    outbox = config.EMAIL_OUTBOX_DIR
    outbox.mkdir(parents=True, exist_ok=True)
    message = _build_message(to, subject, text, html)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = outbox / f"{stamp}-{secrets.token_hex(4)}.eml"
    path.write_bytes(message.as_bytes())
    logger.info("EMAIL written to %s (to=%s subject=%r)", path, to, subject)


def _smtp_send(message: EmailMessage) -> None:
    """Blocking SMTP delivery over STARTTLS — run in a worker thread."""
    if not config.SMTP_HOST:
        raise RuntimeError("EMAIL_BACKEND=smtp but SMTP_HOST is unset")
    with smtplib.SMTP(
        config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT_S
    ) as smtp:
        if config.SMTP_STARTTLS:
            smtp.starttls()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD or "")
        smtp.send_message(message)
