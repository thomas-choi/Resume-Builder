"""Versioned JSON store: data/profiles/{profile_id}/v{n}.json + latest pointer."""

import json
import uuid
from pathlib import Path

from src import config
from src.models.schemas import CareerProfile


def _profiles_dir() -> Path:
    return config.DATA_DIR / "profiles"


def _profile_dir(profile_id: str) -> Path:
    return _profiles_dir() / profile_id


def save_profile(profile: CareerProfile, profile_id: str | None = None) -> tuple[str, int]:
    """Save a profile as a new version; returns (profile_id, version)."""
    profile_id = profile_id or uuid.uuid4().hex[:12]
    pdir = _profile_dir(profile_id)
    pdir.mkdir(parents=True, exist_ok=True)
    version = latest_version(profile_id) + 1
    (pdir / f"v{version}.json").write_text(
        profile.model_dump_json(indent=2), encoding="utf-8"
    )
    (pdir / "latest").write_text(str(version), encoding="utf-8")
    return profile_id, version


def latest_version(profile_id: str) -> int:
    """Current latest version number, 0 if the profile does not exist."""
    pointer = _profile_dir(profile_id) / "latest"
    if not pointer.exists():
        return 0
    return int(pointer.read_text(encoding="utf-8").strip())


def load_profile(profile_id: str, version: int | None = None) -> CareerProfile:
    """Load a profile version (latest by default).

    Raises:
        FileNotFoundError: If the profile or version does not exist.
    """
    version = version or latest_version(profile_id)
    path = _profile_dir(profile_id) / f"v{version}.json"
    if version == 0 or not path.exists():
        raise FileNotFoundError(f"profile {profile_id} v{version} not found")
    return CareerProfile.model_validate_json(path.read_text(encoding="utf-8"))


def list_versions(profile_id: str) -> list[int]:
    """All stored version numbers for a profile, ascending."""
    pdir = _profile_dir(profile_id)
    if not pdir.exists():
        return []
    return sorted(int(p.stem[1:]) for p in pdir.glob("v*.json"))
