#!/bin/sh
# Run the API as the host user instead of root, so files written into the
# ./data and ./logs bind mounts are owned by that user (not root, which is what
# made logs/app.log unwritable to the developer after a container run).
#
# The container itself has no way to know which host user typed
# `docker compose up`. But that user owns the bind-mounted ./data directory, and
# a bind mount preserves the host's uid/gid inside the container — so the owner
# of DATA_DIR *is* the host user. We read it, hand the writable mounts to that
# uid, and drop from root to it via gosu before exec'ing the real command.
#
# Precedence: explicit HOST_UID/HOST_GID env > owner of DATA_DIR > root (no drop).
set -e

DATA_DIR="${DATA_DIR:-/app/data}"

if [ -n "${HOST_UID}" ]; then
    uid="${HOST_UID}"
    gid="${HOST_GID:-$HOST_UID}"
elif [ -d "${DATA_DIR}" ]; then
    uid="$(stat -c '%u' "${DATA_DIR}")"
    gid="$(stat -c '%g' "${DATA_DIR}")"
else
    uid=0
    gid=0
fi

# uid 0 means the mount is root-owned (e.g. a named volume, or ./data was
# auto-created by Docker) or root was requested explicitly: run as-is.
if [ "${uid}" = "0" ]; then
    exec "$@"
fi

# Make sure the two writable trees belong to the target user before we drop
# privileges. Recursive on purpose: a fresh bind mount comes up root-owned, and
# files left behind by an earlier root-run container (e.g. a root-owned
# logs/app.log) would otherwise be unwritable to the non-root process and
# crash-loop it. This walks data/ and logs/ on every start — fine for a modest
# store; for a very large data/ set HOST_UID and pre-own the tree to skip it.
mkdir -p /app/data /app/logs
chown -R "${uid}:${gid}" /app/data /app/logs 2>/dev/null || true

exec gosu "${uid}:${gid}" "$@"
