"""Tests for the account-system mailer (Phase 7.a)."""

import asyncio
from email.parser import BytesParser
from email.policy import default as default_policy

import pytest

from src import config
from src.utils import mailer


def _run(coro):
    return asyncio.run(coro)


def test_file_backend_writes_parseable_eml(data_dir, monkeypatch):
    outbox = data_dir / "auth" / "outbox"
    monkeypatch.setattr(config, "EMAIL_BACKEND", "file")
    monkeypatch.setattr(config, "EMAIL_OUTBOX_DIR", outbox)
    monkeypatch.setattr(config, "EMAIL_FROM", "no-reply@localhost")

    _run(mailer.send("alice@example.com", "Your code", "Your code is 481920"))

    files = list(outbox.glob("*.eml"))
    assert len(files) == 1
    message = BytesParser(policy=default_policy).parsebytes(files[0].read_bytes())
    assert message["To"] == "alice@example.com"
    assert message["From"] == "no-reply@localhost"
    assert message["Subject"] == "Your code"
    assert "481920" in message.get_content()


def test_file_backend_two_sends_newest_sorts_last(data_dir, monkeypatch):
    outbox = data_dir / "auth" / "outbox"
    monkeypatch.setattr(config, "EMAIL_BACKEND", "file")
    monkeypatch.setattr(config, "EMAIL_OUTBOX_DIR", outbox)

    _run(mailer.send("a@example.com", "First", "first body"))
    _run(mailer.send("b@example.com", "Second", "second body"))

    files = sorted(p.name for p in outbox.glob("*.eml"))
    assert len(files) == 2
    newest = max(outbox.glob("*.eml"), key=lambda p: p.name)
    message = BytesParser(policy=default_policy).parsebytes(newest.read_bytes())
    assert "second body" in message.get_content()


def test_file_backend_includes_html_alternative(data_dir, monkeypatch):
    outbox = data_dir / "auth" / "outbox"
    monkeypatch.setattr(config, "EMAIL_BACKEND", "file")
    monkeypatch.setattr(config, "EMAIL_OUTBOX_DIR", outbox)

    _run(
        mailer.send(
            "a@example.com",
            "Link",
            "click http://x/verify",
            html="<a href='http://x/verify'>verify</a>",
        )
    )

    message = BytesParser(policy=default_policy).parsebytes(
        next(outbox.glob("*.eml")).read_bytes()
    )
    assert message.is_multipart()
    subtypes = {part.get_content_subtype() for part in message.iter_parts()}
    assert {"plain", "html"} <= subtypes


def test_console_backend_logs_body(monkeypatch, caplog):
    monkeypatch.setattr(config, "EMAIL_BACKEND", "console")
    with caplog.at_level("INFO"):
        _run(mailer.send("a@example.com", "Subj", "code 000111"))
    assert any("000111" in rec.getMessage() for rec in caplog.records)


def test_smtp_backend_drives_mocked_smtp(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USER", "user")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "pw")
    monkeypatch.setattr(config, "SMTP_STARTTLS", True)

    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(f"init:{host}:{port}")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls.append("starttls")

        def login(self, user, password):
            calls.append(f"login:{user}")

        def send_message(self, message):
            calls.append(f"send:{message['To']}")

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    _run(mailer.send("bob@example.com", "Hi", "body"))

    assert calls == [
        "init:smtp.example.com:587",
        "starttls",
        "login:user",
        "send:bob@example.com",
    ]


def test_smtp_backend_no_login_without_user(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_USER", None)
    monkeypatch.setattr(config, "SMTP_STARTTLS", False)

    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls.append("starttls")

        def login(self, *a):
            calls.append("login")

        def send_message(self, message):
            calls.append("send")

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    _run(mailer.send("a@example.com", "s", "b"))

    assert calls == ["send"]  # no starttls (disabled), no login (no user)


def test_send_failure_propagates(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")

    class BoomSMTP:
        def __init__(self, *a, **k):
            raise OSError("connection refused")

    monkeypatch.setattr(mailer.smtplib, "SMTP", BoomSMTP)

    with pytest.raises(OSError):
        _run(mailer.send("a@example.com", "s", "b"))
