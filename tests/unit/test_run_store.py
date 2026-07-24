"""Unit tests for the per-run provenance store (src/utils/run_store.py)."""

import hashlib
import json

from src import config
from src.utils import run_store
from tests.conftest import TEST_EMAIL


def _root(email: str = TEST_EMAIL):
    return config.user_root(email)


def test_save_source_file_writes_bytes_and_returns_path(data_dir):
    data = b"raw cv bytes"
    path = run_store.save_source_file(TEST_EMAIL, "run-1", "cv", "resume.pdf", data)

    assert path == _root() / "sources" / "run-1" / "cv" / "resume.pdf"
    assert path.read_bytes() == data
    # Nothing at the legacy top level.
    assert not (data_dir / "sources").exists()


def test_save_source_file_keeps_both_files_when_names_collide(data_dir):
    """Two uploads called CV.docx are two sources — neither may overwrite the other."""
    first = run_store.save_source_file(TEST_EMAIL, "run-1", "cv", "CV.docx", b"one")
    second = run_store.save_source_file(TEST_EMAIL, "run-1", "cv", "CV.docx", b"two")
    third = run_store.save_source_file(TEST_EMAIL, "run-1", "cv", "CV.docx", b"three")

    assert [p.name for p in (first, second, third)] == ["CV.docx", "CV-2.docx", "CV-3.docx"]
    assert (first.read_bytes(), second.read_bytes(), third.read_bytes()) == (
        b"one",
        b"two",
        b"three",
    )


def test_save_source_file_sanitizes_filename(data_dir):
    """A traversal-style upload name must not escape the run directory."""
    path = run_store.save_source_file(TEST_EMAIL, "run-1", "cv", "../../etc/passwd", b"x")

    assert path.name == "passwd"
    assert path.parent == _root() / "sources" / "run-1" / "cv"


def test_source_entry_records_size_and_sha256(data_dir):
    data = b"hello world"
    path = run_store.sources_dir(TEST_EMAIL, "run-1") / "cv" / "a.pdf"
    entry = run_store.source_entry("cv", path, data, source_id="cv_pdf:a.pdf")

    assert entry["category"] == "cv"
    assert entry["filename"] == "a.pdf"
    assert entry["size_bytes"] == len(data)
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["source_id"] == "cv_pdf:a.pdf"


def test_write_and_load_manifest_roundtrip(data_dir):
    entries = [{"category": "cv", "filename": "resume.pdf"}]
    run_store.write_manifest(TEST_EMAIL, "run-1", entries)

    manifest = run_store.load_manifest(TEST_EMAIL, "run-1")
    assert manifest["run_id"] == "run-1"
    assert manifest["sources"] == entries
    assert manifest["profile_id"] is None
    assert manifest["version"] is None
    assert "created_at" in manifest


def test_load_manifest_missing_returns_none(data_dir):
    assert run_store.load_manifest(TEST_EMAIL, "nope") is None


def test_save_output_writes_copy_and_links_manifest(data_dir, sample_profile):
    run_store.write_manifest(
        TEST_EMAIL, "run-1", [{"category": "cv", "filename": "resume.pdf"}]
    )

    path = run_store.save_output(
        TEST_EMAIL, "run-1", sample_profile, {"profile_id": "abc123", "version": 2}
    )

    assert path == _root() / "output" / "run-1" / "output.json"
    payload = json.loads(path.read_text())
    assert payload["run_id"] == "run-1"
    assert payload["profile_id"] == "abc123"
    assert payload["version"] == 2
    assert payload["profile"]["name"] == sample_profile.name

    # manifest is patched with the produced profile id/version
    manifest = run_store.load_manifest(TEST_EMAIL, "run-1")
    assert manifest["profile_id"] == "abc123"
    assert manifest["version"] == 2
    assert manifest["sources"] == [{"category": "cv", "filename": "resume.pdf"}]


def test_save_output_without_manifest_still_writes(data_dir, sample_profile):
    """Missing manifest must not fail output persistence."""
    path = run_store.save_output(
        TEST_EMAIL, "orphan", sample_profile, {"profile_id": "p", "version": 1}
    )
    assert path.exists()
    assert run_store.load_manifest(TEST_EMAIL, "orphan") is None
