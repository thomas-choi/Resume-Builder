#!/usr/bin/env bash
#
# Build the Resume-Builder image on this workstation and push it to Docker Hub.
# Runs LOCALLY, never on the VM — the VM only ever pulls (OPERATIONS.md,
# "Cloud deployment").
#
# Usage:
#   scripts/build_and_push.sh              # tag = short git SHA, plus `latest`
#   scripts/build_and_push.sh v1.2.0       # tag = v1.2.0, plus `latest`
#   IMAGE_REPO=someone/other ./scripts/build_and_push.sh
#   ALLOW_DIRTY=1 scripts/build_and_push.sh    # build with uncommitted changes
#
# Prerequisite: `docker login` (Docker Hub username `thomaschoi`, and an access
# token from hub.docker.com -> Account Settings -> Personal access tokens as the
# password).
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE_REPO="${IMAGE_REPO:-thomaschoi/resume-builder}"
# linux/amd64 is what the DigitalOcean droplet runs. Stating it explicitly means
# an Apple-Silicon/ARM workstation cannot silently push an image the VM refuses
# to start ("exec format error").
PLATFORM="${PLATFORM:-linux/amd64}"

TAG="${1:-$(git rev-parse --short HEAD)}"

# A tag pointing at a dirty tree is a tag you cannot reproduce or roll back to.
if [[ -z "${ALLOW_DIRTY:-}" ]] && ! git diff-index --quiet HEAD -- 2>/dev/null; then
  echo "error: uncommitted changes — the image would not match tag '$TAG'." >&2
  echo "       commit them, or re-run with ALLOW_DIRTY=1 to build anyway." >&2
  exit 1
fi

echo "==> building $IMAGE_REPO:$TAG ($PLATFORM)"
docker build --platform "$PLATFORM" \
  -t "$IMAGE_REPO:$TAG" \
  -t "$IMAGE_REPO:latest" \
  .

echo "==> pushing $IMAGE_REPO:$TAG and :latest"
if ! docker push "$IMAGE_REPO:$TAG"; then
  echo "error: push failed — run 'docker login' first (username thomaschoi," >&2
  echo "       password = a Docker Hub personal access token)." >&2
  exit 1
fi
docker push "$IMAGE_REPO:latest"

echo
echo "pushed:"
echo "  $IMAGE_REPO:$TAG"
echo "  $IMAGE_REPO:latest"
echo
echo "next: scripts/deploy_to_vm.sh   (copies compose + .env to the VM)"
echo "      then on the VM: docker compose pull && docker compose up -d"
