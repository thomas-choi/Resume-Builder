"""Root logging configuration driven by LOG_LEVEL / LOG_FILE env vars."""

import contextvars
import logging
from logging.handlers import RotatingFileHandler

from src import config

# Per-run correlation id, set at the top of /ingest so every pipeline node's log
# lines across a run share the same [run:...] tag and are greppable together.
run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")

# Per-user attribution (§14.8). Records the **uid** (the sha256(email) handle),
# never the raw address: the user-id is the email (PII), so the pseudonymous
# hash is what ties a log line back to an account. Codes, link tokens, session
# cookies and raw emails are never logged.
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")

_FORMAT = (
    "%(asctime)s %(levelname)s [run:%(run_id)s user:%(user_id)s] %(name)s: %(message)s"
)
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 3


class _ContextFilter(logging.Filter):
    """Inject the current run_id / user_id contextvars onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        record.user_id = user_id_var.get()
        return True


def set_run_id(run_id: str) -> None:
    """Bind the run correlation id for subsequent log records in this context."""
    run_id_var.set(run_id)


def set_user(uid: str) -> None:
    """Bind the account handle (``sha256(email)``) for subsequent log records.

    The argument is the **uid**, not the email: attribution uses the
    pseudonymous hash so no log line holds the person's address (§14.8).
    """
    user_id_var.set(uid)


def _resolve_level() -> int:
    level = getattr(logging, config.LOG_LEVEL.upper(), None)
    if not isinstance(level, int):
        logging.getLogger(__name__).warning(
            "Unknown LOG_LEVEL %r, falling back to INFO", config.LOG_LEVEL
        )
        return logging.INFO
    return level


def setup_logging() -> None:
    """Configure the root logger: console always, rotating file if LOG_FILE is set.

    Idempotent so `uvicorn --reload` re-imports and repeated test calls don't
    stack duplicate handlers. The file handler rotates at 10 MB, keeping 3
    backups, and its directory is created if missing.
    """
    root = logging.getLogger()
    root.setLevel(_resolve_level())

    has_console = any(
        type(h) is logging.StreamHandler for h in root.handlers
    )
    if not has_console:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FORMAT))
        console.addFilter(_ContextFilter())
        root.addHandler(console)

    if config.LOG_FILE is None:
        return
    log_path = str(config.LOG_FILE.resolve())
    has_file = any(
        isinstance(h, RotatingFileHandler) and h.baseFilename == log_path
        for h in root.handlers
    )
    if not has_file:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            config.LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        file_handler.addFilter(_ContextFilter())
        root.addHandler(file_handler)

    # uvicorn attaches its own console-only handlers with propagate=False,
    # so without this its access/error lines would never reach LOG_FILE.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # At LOG_LEVEL=DEBUG these libraries flood the log (wire chatter, reload
    # polling) and drown out the pipeline's own debug tracing.
    for name in ("httpcore", "httpx", "urllib3", "watchfiles", "openai"):
        logging.getLogger(name).setLevel(logging.INFO)
