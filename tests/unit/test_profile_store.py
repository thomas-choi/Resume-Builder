"""Versioned JSON profile store (per-account root, Phase 7.c)."""

import pytest

from src import config
from src.utils import profile_store
from tests.conftest import TEST_EMAIL


def test_save_and_load_round_trip(data_dir, sample_profile):
    profile_id, version = profile_store.save_profile(TEST_EMAIL, sample_profile)
    assert version == 1
    loaded = profile_store.load_profile(TEST_EMAIL, profile_id)
    assert loaded == sample_profile
    # The bytes land under the per-user root, nothing at the legacy top level.
    assert (config.user_root(TEST_EMAIL) / "profiles" / profile_id).is_dir()
    assert not (data_dir / "profiles").exists()


def test_versioning_increments_and_latest_pointer(data_dir, sample_profile):
    profile_id, v1 = profile_store.save_profile(TEST_EMAIL, sample_profile)
    edited = sample_profile.model_copy(update={"headline": "Staff Engineer"})
    _, v2 = profile_store.save_profile(TEST_EMAIL, edited, profile_id)
    assert (v1, v2) == (1, 2)
    assert profile_store.latest_version(TEST_EMAIL, profile_id) == 2
    assert profile_store.list_versions(TEST_EMAIL, profile_id) == [1, 2]
    # latest loads v2, explicit version loads v1
    assert profile_store.load_profile(TEST_EMAIL, profile_id).headline == "Staff Engineer"
    assert profile_store.load_profile(TEST_EMAIL, profile_id, 1).headline == "Senior Engineer"


def test_load_missing_profile_raises(data_dir):
    with pytest.raises(FileNotFoundError):
        profile_store.load_profile(TEST_EMAIL, "nope")


def test_load_missing_version_raises(data_dir, sample_profile):
    profile_id, _ = profile_store.save_profile(TEST_EMAIL, sample_profile)
    with pytest.raises(FileNotFoundError):
        profile_store.load_profile(TEST_EMAIL, profile_id, 99)


def test_list_profiles_empty_for_new_account(data_dir):
    assert profile_store.list_profiles(TEST_EMAIL) == []


def test_list_profiles_labels_and_isolation(data_dir, sample_profile):
    profile_store.save_profile(TEST_EMAIL, sample_profile, "alice")
    # A second profile with a blank name falls back to its id as the label.
    nameless = sample_profile.model_copy(update={"name": "", "headline": None})
    profile_store.save_profile(TEST_EMAIL, nameless, "blankname")
    # Another account's profile must not leak into this listing.
    profile_store.save_profile("other@example.com", sample_profile, "eve")

    listed = profile_store.list_profiles(TEST_EMAIL)
    by_id = {p["profile_id"]: p for p in listed}
    assert set(by_id) == {"alice", "blankname"}
    assert by_id["alice"]["label"] == "Alice Smith"
    assert by_id["alice"]["latest_version"] == 1
    assert by_id["blankname"]["label"] == "blankname"
