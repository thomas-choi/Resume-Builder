"""Versioned JSON profile store."""

import pytest

from src.utils import profile_store


def test_save_and_load_round_trip(data_dir, sample_profile):
    profile_id, version = profile_store.save_profile(sample_profile)
    assert version == 1
    loaded = profile_store.load_profile(profile_id)
    assert loaded == sample_profile


def test_versioning_increments_and_latest_pointer(data_dir, sample_profile):
    profile_id, v1 = profile_store.save_profile(sample_profile)
    edited = sample_profile.model_copy(update={"headline": "Staff Engineer"})
    _, v2 = profile_store.save_profile(edited, profile_id)
    assert (v1, v2) == (1, 2)
    assert profile_store.latest_version(profile_id) == 2
    assert profile_store.list_versions(profile_id) == [1, 2]
    # latest loads v2, explicit version loads v1
    assert profile_store.load_profile(profile_id).headline == "Staff Engineer"
    assert profile_store.load_profile(profile_id, 1).headline == "Senior Engineer"


def test_load_missing_profile_raises(data_dir):
    with pytest.raises(FileNotFoundError):
        profile_store.load_profile("nope")


def test_load_missing_version_raises(data_dir, sample_profile):
    profile_id, _ = profile_store.save_profile(sample_profile)
    with pytest.raises(FileNotFoundError):
        profile_store.load_profile(profile_id, 99)
