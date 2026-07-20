"""Per-run provenance store: archives raw inputs and the final output per run_id.

Mirrors the layout conventions of :mod:`src.utils.profile_store`, but keyed by a
``run_id`` (one ``/ingest`` execution) instead of a ``profile_id`` (an evolving
storage key). For every run it captures:

- ``data/sources/{run_id}/`` — the raw inputs exactly as received (uploaded CVs,
  the serialized GitHub source, the free-text / LinkedIn summary).
- ``data/sources/{run_id}/manifest.json`` — an index of those inputs (category,
  filename, byte size, sha256) linked to the produced ``profile_id`` / ``version``.
- ``data/output/{run_id}/output.json`` — a copy of the synthesized profile.

Together these let a run be reconstructed or audited after the fact, tying raw
inputs → final output (design doc §13 / PLAN.md Phase 1 run tracking).
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src import config
from src.models.schemas import CareerProfile

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"
_OUTPUT_NAME = "output.json"


def _sources_root() -> Path:
    return config.DATA_DIR / "sources"


def _output_root() -> Path:
    return config.DATA_DIR / "output"


def sources_dir(run_id: str) -> Path:
    """Directory holding the archived raw inputs for a run."""
    return _sources_root() / run_id


def output_dir(run_id: str) -> Path:
    """Directory holding the saved output copy for a run."""
    return _output_root() / run_id


def save_source_file(run_id: str, category: str, filename: str, data: bytes) -> Path:
    """Archive one raw input under ``sources/{run_id}/{category}/{filename}``.

    Args:
        run_id: The run correlation id.
        category: Sub-folder grouping the input, e.g. ``"cv"``, ``"github"``,
            ``"linkedin"``.
        filename: Desired file name; only its final path component is used
            (``Path(filename).name``) so a malicious upload name cannot escape
            the run directory via ``..`` or absolute paths.
        data: Raw bytes to write verbatim.

    Returns:
        The path the bytes were written to.
    """
    safe_name = Path(filename).name or "unnamed"
    dest_dir = sources_dir(run_id) / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    dest.write_bytes(data)
    logger.debug(
        "run_store: archived %s/%s (%d bytes) for run %s",
        category,
        safe_name,
        len(data),
        run_id,
    )
    return dest


def source_entry(category: str, path: Path, data: bytes, source_id: str | None = None) -> dict:
    """Build a manifest entry describing one archived input."""
    entry = {
        "category": category,
        "filename": path.name,
        "stored_path": str(path),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if source_id is not None:
        entry["source_id"] = source_id
    return entry


def write_manifest(
    run_id: str,
    entries: list[dict],
    profile_id: str | None = None,
    version: int | None = None,
) -> Path:
    """Write (or overwrite) ``sources/{run_id}/manifest.json``.

    Args:
        run_id: The run correlation id.
        entries: Manifest entries, one per archived input (see :func:`source_entry`).
        profile_id: The produced profile id, once known (``None`` before synthesis).
        version: The produced profile version, once known.

    Returns:
        The manifest path.
    """
    run_dir = sources_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_id": profile_id,
        "version": version,
        "sources": entries,
    }
    path = run_dir / _MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def load_manifest(run_id: str) -> dict | None:
    """Read a run's manifest, or ``None`` if it was never written."""
    path = sources_dir(run_id) / _MANIFEST_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def link_profile(run_id: str, profile_id: str, version: int) -> None:
    """Patch an existing manifest with the produced ``profile_id`` / ``version``.

    No-op (logs a warning) if the manifest is missing, so output persistence
    never fails a run purely for lack of a manifest.
    """
    manifest = load_manifest(run_id)
    if manifest is None:
        logger.warning("run_store: no manifest to link for run %s", run_id)
        return
    write_manifest(
        run_id,
        manifest.get("sources", []),
        profile_id=profile_id,
        version=version,
    )


def save_output(run_id: str, profile: CareerProfile, meta: dict | None = None) -> Path:
    """Save a copy of the synthesized profile under ``output/{run_id}/output.json``.

    Also links the run's manifest to the produced ``profile_id`` / ``version``
    when those are present in ``meta``.

    Args:
        run_id: The run correlation id.
        profile: The synthesized career profile.
        meta: Optional metadata (e.g. ``profile_id``, ``version``) stored
            alongside the profile and used to link the manifest.

    Returns:
        The output.json path.
    """
    meta = meta or {}
    out_dir = output_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **meta,
        "profile": profile.model_dump(),
    }
    path = out_dir / _OUTPUT_NAME
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    profile_id = meta.get("profile_id")
    version = meta.get("version")
    if profile_id is not None and version is not None:
        link_profile(run_id, profile_id, version)
    logger.debug("run_store: saved output for run %s -> %s", run_id, path)
    return path
