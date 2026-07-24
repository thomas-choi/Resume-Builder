"""Per-run provenance store: archives raw inputs and the final output per run_id.

Mirrors the layout conventions of :mod:`src.utils.profile_store`, but keyed by a
``run_id`` (one ``/ingest`` execution) instead of a ``profile_id`` (an evolving
storage key). Per account (§14.8): every root-touching function takes the
owner's ``email`` first. For every run it captures, under
``data/users/{uid}/``:

- ``sources/{run_id}/`` — the raw inputs exactly as received (uploaded CVs, the
  serialized GitHub source, the free-text / LinkedIn summary).
- ``sources/{run_id}/manifest.json`` — an index of those inputs (category,
  filename, byte size, sha256) linked to the produced ``profile_id`` / ``version``.
- ``output/{run_id}/output.json`` — a copy of the synthesized profile.

Together these let a run be reconstructed or audited after the fact, tying raw
inputs → final output (design doc §13 / PLAN.md Phase 1 run tracking).

The pure helpers that touch no root (:func:`source_entry`,
:func:`prune_source_document`, :func:`_free_path`) keep their old signatures.
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


def _sources_root(email: str) -> Path:
    return config.user_root(email) / "sources"


def _output_root(email: str) -> Path:
    return config.user_root(email) / "output"


def _checked(root: Path, run_id: str) -> Path:
    """Join ``run_id`` onto ``root``, asserting it stays inside (§14.2)."""
    path = root / run_id
    if not config.within(root, path):
        raise ValueError(f"run id {run_id!r} escapes the user root")
    return path


def sources_dir(email: str, run_id: str) -> Path:
    """Directory holding the archived raw inputs for a run."""
    return _checked(_sources_root(email), run_id)


def output_dir(email: str, run_id: str) -> Path:
    """Directory holding the saved output copy for a run."""
    return _checked(_output_root(email), run_id)


def _free_path(dest_dir: Path, safe_name: str) -> Path:
    """First unused path for ``safe_name`` in ``dest_dir`` (``x.pdf``, ``x-2.pdf``…)."""
    dest = dest_dir / safe_name
    if not dest.exists():
        return dest
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    counter = 2
    while (dest := dest_dir / f"{stem}-{counter}{suffix}").exists():
        counter += 1
    return dest


def save_source_file(
    email: str, run_id: str, category: str, filename: str, data: bytes
) -> Path:
    """Archive one raw input under ``sources/{run_id}/{category}/{filename}``.

    A name already taken within the run is suffixed (``CV.docx`` → ``CV-2.docx``
    → ``CV-3.docx``) rather than overwritten: uploading two files that happen to
    share a name is ordinary, and the second silently replacing the first loses
    a source the profile was meant to be built from.

    Args:
        run_id: The run correlation id.
        category: Sub-folder grouping the input, e.g. ``"cv"``, ``"github"``,
            ``"linkedin"``.
        filename: Desired file name; only its final path component is used
            (``Path(filename).name``) so a malicious upload name cannot escape
            the run directory via ``..`` or absolute paths.
        data: Raw bytes to write verbatim.

    Returns:
        The path the bytes were actually written to — which is *not* always
        ``{category}/{filename}``, so callers deriving a source id from the name
        must use this rather than the name they passed in.
    """
    safe_name = Path(filename).name or "unnamed"
    dest_dir = sources_dir(email, run_id) / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _free_path(dest_dir, safe_name)
    dest.write_bytes(data)
    logger.debug(
        "run_store: archived %s/%s (%d bytes) for run %s",
        category,
        dest.name,
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


def prune_source_document(path: Path, pruned_text: str) -> Path:
    """Rewrite an archived ``SourceDocument`` JSON to carry ``pruned_text``.

    The as-fetched copy is preserved alongside it as ``<stem>.raw.json`` before
    the rewrite: the pruned file records what actually reached the profile,
    and the raw one remains the audit trail of what the provider really
    returned. Losing the latter to the pruning would make a dropped item
    impossible to investigate.

    Args:
        path: The archived document, e.g. ``sources/{run_id}/github/github.json``.
        pruned_text: The document text with the failed items removed.

    Returns:
        The path the as-fetched copy was written to.
    """
    original = path.read_bytes()
    raw_path = path.with_suffix(".raw.json")
    raw_path.write_bytes(original)

    document = json.loads(original)
    document["raw_text"] = pruned_text
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    logger.debug(
        "run_store: pruned %s (%d -> %d chars), as-fetched copy at %s",
        path.name,
        len(original),
        len(pruned_text),
        raw_path.name,
    )
    return raw_path


def add_source_entry(email: str, run_id: str, entry: dict) -> None:
    """Append one entry to an existing manifest, preserving its profile link.

    No-op (logs a warning) when the manifest is missing, for the same reason as
    :func:`link_profile`: provenance bookkeeping must never fail a run.
    """
    manifest = load_manifest(email, run_id)
    if manifest is None:
        logger.warning("run_store: no manifest to extend for run %s", run_id)
        return
    write_manifest(
        email,
        run_id,
        [*manifest.get("sources", []), entry],
        profile_id=manifest.get("profile_id"),
        version=manifest.get("version"),
    )


def write_manifest(
    email: str,
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
    run_dir = sources_dir(email, run_id)
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


def load_manifest(email: str, run_id: str) -> dict | None:
    """Read a run's manifest, or ``None`` if it was never written."""
    path = sources_dir(email, run_id) / _MANIFEST_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def link_profile(email: str, run_id: str, profile_id: str, version: int) -> None:
    """Patch an existing manifest with the produced ``profile_id`` / ``version``.

    No-op (logs a warning) if the manifest is missing, so output persistence
    never fails a run purely for lack of a manifest.
    """
    manifest = load_manifest(email, run_id)
    if manifest is None:
        logger.warning("run_store: no manifest to link for run %s", run_id)
        return
    write_manifest(
        email,
        run_id,
        manifest.get("sources", []),
        profile_id=profile_id,
        version=version,
    )


def save_output(
    email: str, run_id: str, profile: CareerProfile, meta: dict | None = None
) -> Path:
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
    out_dir = output_dir(email, run_id)
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
        link_profile(email, run_id, profile_id, version)
    logger.debug("run_store: saved output for run %s -> %s", run_id, path)
    return path
