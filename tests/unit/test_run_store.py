"""Unit tests for the per-run provenance store (src/utils/run_store.py)."""

import hashlib
import json

from src.utils import run_store


def test_save_source_file_writes_bytes_and_returns_path(data_dir):
    data = b"raw cv bytes"
    path = run_store.save_source_file("run-1", "cv", "resume.pdf", data)

    assert path == data_dir / "sources" / "run-1" / "cv" / "resume.pdf"
    assert path.read_bytes() == data


def test_save_source_file_sanitizes_filename(data_dir):
    """A traversal-style upload name must not escape the run directory."""
    path = run_store.save_source_file("run-1", "cv", "../../etc/passwd", b"x")

    assert path.name == "passwd"
    assert path.parent == data_dir / "sources" / "run-1" / "cv"


def test_source_entry_records_size_and_sha256():
    data = b"hello world"
    path = run_store.sources_dir("run-1") / "cv" / "a.pdf"
    entry = run_store.source_entry("cv", path, data, source_id="cv_pdf:a.pdf")

    assert entry["category"] == "cv"
    assert entry["filename"] == "a.pdf"
    assert entry["size_bytes"] == len(data)
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["source_id"] == "cv_pdf:a.pdf"


def test_write_and_load_manifest_roundtrip(data_dir):
    entries = [{"category": "cv", "filename": "resume.pdf"}]
    run_store.write_manifest("run-1", entries)

    manifest = run_store.load_manifest("run-1")
    assert manifest["run_id"] == "run-1"
    assert manifest["sources"] == entries
    assert manifest["profile_id"] is None
    assert manifest["version"] is None
    assert "created_at" in manifest


def test_load_manifest_missing_returns_none(data_dir):
    assert run_store.load_manifest("nope") is None


def test_save_output_writes_copy_and_links_manifest(data_dir, sample_profile):
    run_store.write_manifest("run-1", [{"category": "cv", "filename": "resume.pdf"}])

    path = run_store.save_output(
        "run-1", sample_profile, {"profile_id": "abc123", "version": 2}
    )

    assert path == data_dir / "output" / "run-1" / "output.json"
    payload = json.loads(path.read_text())
    assert payload["run_id"] == "run-1"
    assert payload["profile_id"] == "abc123"
    assert payload["version"] == 2
    assert payload["profile"]["name"] == sample_profile.name

    # manifest is patched with the produced profile id/version
    manifest = run_store.load_manifest("run-1")
    assert manifest["profile_id"] == "abc123"
    assert manifest["version"] == 2
    assert manifest["sources"] == [{"category": "cv", "filename": "resume.pdf"}]


def test_save_output_without_manifest_still_writes(data_dir, sample_profile):
    """Missing manifest must not fail output persistence."""
    path = run_store.save_output("orphan", sample_profile, {"profile_id": "p", "version": 1})
    assert path.exists()
    assert run_store.load_manifest("orphan") is None
