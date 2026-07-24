"""Per-user storage rooting mechanics (Phase 7.c, §14.8)."""

import re
from pathlib import Path

import pytest

from src import config
from src.utils import auth_store, document_store, profile_store, run_store
from tests.conftest import TEST_EMAIL


def test_user_root_is_data_dir_users_sha256(data_dir):
    root = config.user_root(TEST_EMAIL)
    assert root == data_dir / "users" / auth_store.uid(TEST_EMAIL)
    # The handle is a 64-char hex sha256 — structurally incapable of a separator.
    assert re.fullmatch(r"[0-9a-f]{64}", root.name)


def test_normalization_folds_case_to_one_root(data_dir):
    assert config.user_root("A@X.com") == config.user_root("a@x.com")
    # ...but two different addresses never share a root.
    assert config.user_root("a@x.com") != config.user_root("b@x.com")


def test_within_rejects_a_dotdot_escape(data_dir):
    root = config.user_root(TEST_EMAIL)
    assert config.within(root, root / "profiles" / "p1")
    assert not config.within(root, root / ".." / ".." / "etc" / "passwd")
    assert not config.within(root, Path("/etc/passwd"))


def test_profile_bytes_land_under_the_user_root(data_dir, sample_profile):
    profile_id, _ = profile_store.save_profile(TEST_EMAIL, sample_profile)
    under = config.user_root(TEST_EMAIL) / "profiles" / profile_id
    assert under.is_dir()
    # Nothing at the legacy top level.
    assert not (data_dir / "profiles").exists()


def test_run_and_document_bytes_land_under_the_user_root(data_dir, sample_profile):
    run_store.save_source_file(TEST_EMAIL, "run-1", "cv", "cv.txt", b"hi")
    run_store.write_manifest(TEST_EMAIL, "run-1", [])
    run_store.save_output(TEST_EMAIL, "run-1", sample_profile)
    document_store.save_result(TEST_EMAIL, "t-1", {"ok": True})

    root = config.user_root(TEST_EMAIL)
    assert (root / "sources" / "run-1" / "cv" / "cv.txt").exists()
    assert (root / "output" / "run-1" / "output.json").exists()
    assert (root / "documents" / "t-1" / "tailor.json").exists()
    for legacy in ("sources", "output", "documents"):
        assert not (data_dir / legacy).exists()


def test_two_accounts_do_not_share_storage(data_dir, sample_profile):
    a_id, _ = profile_store.save_profile("a@x.com", sample_profile)
    b_id, _ = profile_store.save_profile("b@x.com", sample_profile)
    # A's id is unknown under B's root, and vice versa — separate trees.
    with pytest.raises(FileNotFoundError):
        profile_store.load_profile("b@x.com", a_id)
    with pytest.raises(FileNotFoundError):
        profile_store.load_profile("a@x.com", b_id)


def test_auth_off_uses_the_single_user_root(data_dir, monkeypatch, sample_profile):
    # §14.11: auth-off must not be an untested branch — a full save/load goes
    # through the *same* store code path, rooted at user_root(SINGLE_USER_EMAIL).
    monkeypatch.setattr(config, "AUTH_ENABLED", False)
    from fastapi import Request

    from src.api.deps import current_user

    scope = {"type": "http", "headers": [], "method": "GET", "path": "/"}
    user = current_user(Request(scope))
    assert user.email == auth_store.normalize(config.SINGLE_USER_EMAIL)
    assert user.email_verified is True

    profile_id, _ = profile_store.save_profile(user.email, sample_profile)
    loaded = profile_store.load_profile(user.email, profile_id)
    assert loaded == sample_profile
    assert (config.user_root(config.SINGLE_USER_EMAIL) / "profiles" / profile_id).is_dir()
