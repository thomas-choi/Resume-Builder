"""Legacy → per-user migration (scripts/migrate_to_users.py, §14.11)."""

import importlib.util
from pathlib import Path

import pytest

from src import config
from src.utils import auth_store

# Load the script by path — it lives in scripts/, not an importable package.
_SPEC = importlib.util.spec_from_file_location(
    "migrate_to_users",
    Path(__file__).resolve().parents[2] / "scripts" / "migrate_to_users.py",
)
migrate_to_users = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migrate_to_users)

EMAIL = "owner@example.com"


def _seed_legacy_tree(data_dir):
    """Write a populated pre-Phase-7 top-level tree and return its file map."""
    files = {
        "profiles/p1/v1.json": '{"name": "Alice"}',
        "profiles/p1/latest": "1",
        "sources/run-1/manifest.json": '{"run_id": "run-1"}',
        "output/run-1/output.json": '{"run_id": "run-1"}',
        "documents/t-1/tailor.json": '{"tailor_id": "t-1"}',
    }
    for rel, text in files.items():
        path = data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return files


def test_migration_moves_the_tree_and_creates_a_verified_account(data_dir):
    files = _seed_legacy_tree(data_dir)

    uid = migrate_to_users.migrate(EMAIL)

    assert uid == auth_store.uid(EMAIL)
    # The account exists and is verified so the owner can sign in immediately.
    user = auth_store.load_user(EMAIL)
    assert user is not None and user.email_verified

    root = config.user_root(EMAIL)
    for rel, text in files.items():
        assert (root / rel).read_text() == text
    # Nothing left at the legacy top level.
    for legacy in ("profiles", "sources", "output", "documents"):
        assert not (data_dir / legacy).exists()


def test_migration_is_idempotent_when_nothing_is_left_to_move(data_dir):
    _seed_legacy_tree(data_dir)
    migrate_to_users.migrate(EMAIL)
    # A second run finds no legacy trees and no populated target — a no-op.
    uid = migrate_to_users.migrate(EMAIL)
    assert uid == auth_store.uid(EMAIL)
    assert (config.user_root(EMAIL) / "profiles" / "p1" / "v1.json").exists()


def test_migration_refuses_a_non_empty_target(data_dir):
    _seed_legacy_tree(data_dir)
    # A target root that already holds one of the four trees is a red flag.
    (config.user_root(EMAIL) / "profiles").mkdir(parents=True)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        migrate_to_users.migrate(EMAIL)


def test_pre_and_post_reads_match(data_dir):
    files = _seed_legacy_tree(data_dir)
    before = {rel: (data_dir / rel).read_text() for rel in files}
    migrate_to_users.migrate(EMAIL)
    root = config.user_root(EMAIL)
    after = {rel: (root / rel).read_text() for rel in files}
    assert before == after
