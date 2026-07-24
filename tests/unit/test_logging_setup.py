"""Unit tests for src.utils.logging_setup."""

import logging
from logging.handlers import RotatingFileHandler

import pytest

from src import config
from src.utils import logging_setup


@pytest.fixture(autouse=True)
def restore_root_logger():
    """Snapshot and restore root logger handlers/level around each test."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    for handler in root.handlers[:]:
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(saved_level)


def test_level_and_file_handler(tmp_path, monkeypatch):
    log_file = tmp_path / "logs" / "app.log"
    monkeypatch.setattr(config, "LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(config, "LOG_FILE", log_file)

    logging_setup.setup_logging()

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    logging.getLogger("test").debug("hello file")
    assert log_file.exists()
    assert "hello file" in log_file.read_text()


def test_console_only_when_no_log_file(monkeypatch):
    monkeypatch.setattr(config, "LOG_LEVEL", "WARNING")
    monkeypatch.setattr(config, "LOG_FILE", None)
    root = logging.getLogger()
    file_handlers_before = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]

    logging_setup.setup_logging()

    file_handlers_after = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert root.level == logging.WARNING
    assert file_handlers_after == file_handlers_before


def test_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_LEVEL", "INFO")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "app.log")

    logging_setup.setup_logging()
    handlers_after_first = logging.getLogger().handlers[:]
    logging_setup.setup_logging()

    assert logging.getLogger().handlers == handlers_after_first


def test_run_id_tag_in_log_output(tmp_path, monkeypatch):
    log_file = tmp_path / "app.log"
    monkeypatch.setattr(config, "LOG_LEVEL", "INFO")
    monkeypatch.setattr(config, "LOG_FILE", log_file)

    logging_setup.setup_logging()
    logging_setup.set_run_id("run-abc")
    logging_setup.set_user("uid-xyz")
    logging.getLogger("test").info("tagged line")

    # Both the run id and the pseudonymous user handle tag the line (§14.8).
    assert "[run:run-abc user:uid-xyz]" in log_file.read_text()


def test_unknown_level_falls_back_to_info(monkeypatch):
    monkeypatch.setattr(config, "LOG_LEVEL", "VERBOSE")
    monkeypatch.setattr(config, "LOG_FILE", None)

    logging_setup.setup_logging()

    assert logging.getLogger().level == logging.INFO
