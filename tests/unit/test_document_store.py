"""Rendered-document store: fixed filenames, id safety, result roundtrip."""

import pytest

from src import config
from src.utils import document_store
from tests.conftest import TEST_EMAIL


def test_document_path_is_fixed_per_kind_and_format(data_dir):
    assert document_store.document_path(TEST_EMAIL, "t1", "cv", "docx").name == "cv.docx"
    assert (
        document_store.document_path(TEST_EMAIL, "t1", "cover_letter", "pdf").name
        == "cover-letter.pdf"
    )
    # The directory sits under the per-user root, not the legacy top level.
    assert document_store.document_dir(TEST_EMAIL, "t1").parent.parent == config.user_root(
        TEST_EMAIL
    )


def test_unknown_kind_or_format_raises(data_dir):
    with pytest.raises(ValueError, match="unknown document kind"):
        document_store.document_path(TEST_EMAIL, "t1", "portfolio", "docx")
    with pytest.raises(ValueError, match="unknown document format"):
        document_store.document_path(TEST_EMAIL, "t1", "cv", "rtf")


def test_unsafe_tailor_id_raises(data_dir):
    # The id becomes a directory name — traversal must not reach the filesystem.
    with pytest.raises(ValueError, match="tailor_id must be"):
        document_store.document_dir(TEST_EMAIL, "../../etc/passwd")


def test_find_document_raises_when_not_rendered(data_dir):
    with pytest.raises(FileNotFoundError, match="no cv.pdf rendered"):
        document_store.find_document(TEST_EMAIL, "t1", "cv", "pdf")


def test_list_documents_reports_only_what_exists(data_dir):
    assert document_store.list_documents(TEST_EMAIL, "t1") == []
    path = document_store.document_path(TEST_EMAIL, "t1", "cv", "docx")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"docx bytes")

    (entry,) = document_store.list_documents(TEST_EMAIL, "t1")
    assert entry == {
        "kind": "cv",
        "format": "docx",
        "filename": "cv.docx",
        "size_bytes": 10,
    }
    assert document_store.find_document(TEST_EMAIL, "t1", "cv", "docx") == path


def test_result_roundtrip(data_dir):
    assert document_store.load_result(TEST_EMAIL, "t1") is None
    document_store.save_result(TEST_EMAIL, "t1", {"profile_id": "alice", "render_skipped": None})
    result = document_store.load_result(TEST_EMAIL, "t1")
    assert result["tailor_id"] == "t1"
    assert result["profile_id"] == "alice"
    assert result["created_at"]
