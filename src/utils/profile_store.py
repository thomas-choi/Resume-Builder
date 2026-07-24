"""Versioned JSON store: data/users/{uid}/profiles/{profile_id}/v{n}.json + latest.

Keyed per account (§14.8): every function takes the owner's ``email`` (the
user-id) as its first positional argument — required and never defaulted, so a
missed call site is a type error rather than a silent write into a shared tree.
The email is turned into the on-disk root by :func:`config.user_root`; the
address itself never appears in a path.
"""

import uuid
from pathlib import Path

from src import config
from src.models.schemas import CareerProfile


def _profiles_dir(email: str) -> Path:
    return config.user_root(email) / "profiles"


def _profile_dir(email: str, profile_id: str) -> Path:
    root = _profiles_dir(email)
    pdir = root / profile_id
    if not config.within(root, pdir):
        # Defense in depth: the id is separator-validated at the route, but a
        # path that escapes the user's own tree must never be opened (§14.2).
        raise ValueError(f"profile id {profile_id!r} escapes the user root")
    return pdir


def save_profile(
    email: str, profile: CareerProfile, profile_id: str | None = None
) -> tuple[str, int]:
    """Save a profile as a new version under ``email``'s root; returns (id, version)."""
    profile_id = profile_id or uuid.uuid4().hex[:12]
    pdir = _profile_dir(email, profile_id)
    pdir.mkdir(parents=True, exist_ok=True)
    version = latest_version(email, profile_id) + 1
    (pdir / f"v{version}.json").write_text(
        profile.model_dump_json(indent=2), encoding="utf-8"
    )
    (pdir / "latest").write_text(str(version), encoding="utf-8")
    return profile_id, version


def latest_version(email: str, profile_id: str) -> int:
    """Current latest version number, 0 if the profile does not exist."""
    pointer = _profile_dir(email, profile_id) / "latest"
    if not pointer.exists():
        return 0
    return int(pointer.read_text(encoding="utf-8").strip())


def load_profile(
    email: str, profile_id: str, version: int | None = None
) -> CareerProfile:
    """Load a profile version (latest by default) from ``email``'s root.

    Raises:
        FileNotFoundError: If the profile or version does not exist.
    """
    version = version or latest_version(email, profile_id)
    path = _profile_dir(email, profile_id) / f"v{version}.json"
    if version == 0 or not path.exists():
        raise FileNotFoundError(f"profile {profile_id} v{version} not found")
    return CareerProfile.model_validate_json(path.read_text(encoding="utf-8"))


def list_versions(email: str, profile_id: str) -> list[int]:
    """All stored version numbers for a profile, ascending."""
    pdir = _profile_dir(email, profile_id)
    if not pdir.exists():
        return []
    return sorted(int(p.stem[1:]) for p in pdir.glob("v*.json"))
