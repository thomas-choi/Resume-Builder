"""Rendered-document store: ``data/documents/{tailor_id}/``.

Third store alongside :mod:`src.utils.profile_store` (an evolving profile) and
:mod:`src.utils.run_store` (one ingest run), keyed by ``tailor_id`` — one
`POST /tailor` execution. It holds what that execution produced:

- ``cv.docx`` / ``cv.pdf`` and ``cover-letter.docx`` / ``cover-letter.pdf`` —
  the rendered documents served by ``GET /document/{tailor_id}``.
- ``tailor.json`` — the tailored CV, validation result and cover letter, so a
  downloaded document can be traced back to the claims that were checked.

Filenames are fixed per (kind, format) rather than caller-supplied, so a
document request can never address a path outside the tailor's directory.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)

_RESULT_NAME = "tailor.json"

# kind -> filename stem. A closed set: callers pick a kind, never a filename.
_STEMS = {"cv": "cv", "cover_letter": "cover-letter"}
_FORMATS = ("docx", "pdf")

# A tailor_id becomes a directory name under data/documents/, so restrict it to
# safe filename characters (same rule as profile ids).
_TAILOR_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def validate_tailor_id(tailor_id: str) -> str:
    """Return the id if it is a safe directory name.

    Raises:
        ValueError: If the id contains anything but letters, digits, ``-``, ``_``.
    """
    tailor_id = tailor_id.strip()
    if not _TAILOR_ID_RE.fullmatch(tailor_id):
        raise ValueError(
            "tailor_id must be 1-64 characters of letters, digits, '-' or '_'"
        )
    return tailor_id


def document_dir(tailor_id: str) -> Path:
    """Directory holding one tailoring run's rendered documents."""
    return config.DATA_DIR / "documents" / validate_tailor_id(tailor_id)


def document_path(tailor_id: str, kind: str, fmt: str) -> Path:
    """Path of one rendered document (existing or not).

    Args:
        tailor_id: The tailoring run id.
        kind: ``"cv"`` or ``"cover_letter"``.
        fmt: ``"docx"`` or ``"pdf"``.

    Raises:
        ValueError: On an unknown kind/format or an unsafe ``tailor_id``.
    """
    if kind not in _STEMS:
        raise ValueError(f"unknown document kind {kind!r} (expected: {', '.join(_STEMS)})")
    if fmt not in _FORMATS:
        raise ValueError(f"unknown document format {fmt!r} (expected: {', '.join(_FORMATS)})")
    return document_dir(tailor_id) / f"{_STEMS[kind]}.{fmt}"


def find_document(tailor_id: str, kind: str, fmt: str) -> Path:
    """Path of a rendered document that exists.

    Raises:
        FileNotFoundError: If it was never rendered (e.g. PDF conversion was
            unavailable, or the run was skipped by the validation gate).
        ValueError: On an unknown kind/format or an unsafe ``tailor_id``.
    """
    path = document_path(tailor_id, kind, fmt)
    if not path.exists():
        raise FileNotFoundError(f"no {kind}.{fmt} rendered for tailor {tailor_id}")
    return path


def list_documents(tailor_id: str) -> list[dict]:
    """Describe every rendered document for a tailoring run, CV first."""
    documents = []
    for kind in _STEMS:
        for fmt in _FORMATS:
            path = document_path(tailor_id, kind, fmt)
            if path.exists():
                documents.append(
                    {
                        "kind": kind,
                        "format": fmt,
                        "filename": path.name,
                        "size_bytes": path.stat().st_size,
                    }
                )
    return documents


def save_result(tailor_id: str, payload: dict) -> Path:
    """Save the tailoring run's JSON result next to its documents."""
    out_dir = document_dir(tailor_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _RESULT_NAME
    path.write_text(
        json.dumps(
            {
                "tailor_id": tailor_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                **payload,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    logger.debug("document_store: saved result for tailor %s -> %s", tailor_id, path)
    return path


def load_result(tailor_id: str) -> dict | None:
    """Read a tailoring run's saved result, or ``None`` if it was never written."""
    path = document_dir(tailor_id) / _RESULT_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
