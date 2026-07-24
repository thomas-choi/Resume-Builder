#!/usr/bin/env python3
"""Move a legacy single-user ``data/`` tree under a per-account root (§14.11).

Before Phase 7 every run wrote directly to ``data/{profiles,sources,output,
documents}/``. Phase 7.c roots each account at ``data/users/{uid}/`` where
``uid = sha256(normalize(email))``. This one-shot script claims the account for
a given email and **renames** the four legacy top-level directories into that
account's root:

    python scripts/migrate_to_users.py --email you@example.com

Because the per-user tree is byte-for-byte the pre-Phase-7 §13 schema, nothing
is copied or rewritten — each move is an ``os.replace`` (a rename within one
filesystem: instant, atomic and reversible). The script is **idempotent**,
**refuses to run if the target root already holds any of the four dirs**, and
prints the ``email → uid`` mapping so the operator can confirm where the data
went.

Run it once per existing account, with ``AUTH_ENABLED`` set however you like —
migration only touches the filesystem, not the running API.
"""

import argparse
import os
import sys
from pathlib import Path

# Allow `python scripts/migrate_to_users.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.utils import auth_store  # noqa: E402

# The legacy top-level directories, each of which becomes `users/{uid}/<name>`.
_TREES = ("profiles", "sources", "output", "documents")


def migrate(email: str) -> str:
    """Create the account (verified) and move its legacy data under its root.

    Args:
        email: The address that owns the existing single-user data.

    Returns:
        The ``uid`` the data was moved under.

    Raises:
        SystemExit: If the target root already contains any of the four trees
            (refuse rather than merge/overwrite).
    """
    normalized = auth_store.normalize(email)
    uid = auth_store.uid(normalized)
    root = config.user_root(normalized)

    # The account record is the uniqueness claim; create it verified so the
    # migrated owner can sign in immediately. Idempotent: an existing record is
    # fine (re-running the migration must not fail on the account).
    try:
        auth_store.create_user("Local", "User", normalized)
    except FileExistsError:
        pass
    auth_store.mark_verified(normalized)

    # Which legacy trees still exist at the top level and so need moving. A
    # second run finds none (they were moved) and is a clean no-op — that is the
    # idempotency: re-running never moves or rewrites anything twice.
    pending = [name for name in _TREES if (config.DATA_DIR / name).exists()]

    # Refuse to clobber: a legacy tree that needs moving whose target already
    # exists means a prior partial run or a real per-user tree is there, and a
    # rename would fail or merge unpredictably. Stop rather than lose data.
    collisions = [name for name in pending if (root / name).exists()]
    if collisions:
        raise SystemExit(
            f"target root {root} already has {', '.join(collisions)} — "
            "refusing to overwrite (already migrated?)"
        )

    root.mkdir(parents=True, exist_ok=True)
    moved = []
    for name in pending:
        os.replace(config.DATA_DIR / name, root / name)
        moved.append(name)

    print(f"email {normalized} -> uid {uid}")
    print(f"root: {root}")
    print("moved: " + (", ".join(moved) if moved else "(nothing to move)"))
    return uid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--email",
        required=True,
        help="the address that owns the existing single-user data",
    )
    args = parser.parse_args()
    migrate(args.email)


if __name__ == "__main__":
    main()
