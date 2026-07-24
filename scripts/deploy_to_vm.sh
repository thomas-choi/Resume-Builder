#!/usr/bin/env bash
#
# Copy the two files the VM needs into ~/projects/ResumeBuilder on the cloud VM.
# Runs LOCALLY. It does NOT restart anything — pulling and starting is a
# deliberate, separate step performed on the VM (OPERATIONS.md, "Cloud
# deployment").
#
# What gets copied, and nothing else:
#   docker-compose.prod.yml  ->  <remote>/docker-compose.yml
#   .env.prod                ->  <remote>/.env            (chmod 600)
#
# The source tree is NOT copied: it is already baked into the image on Docker
# Hub. Neither is the local data/ — that is other people's PII.
#
# Usage:
#   scripts/deploy_to_vm.sh
#   VM_HOST=1.2.3.4 VM_USER=ops ENV_FILE=.env.prod scripts/deploy_to_vm.sh
#
# Auth is password-based, so ssh will prompt. The connection is multiplexed
# (ControlMaster), so the password is typed ONCE for all three operations.
set -euo pipefail

cd "$(dirname "$0")/.."

VM_HOST="${VM_HOST:-143.198.153.31}"
VM_USER="${VM_USER:-ops}"
REMOTE_DIR="${REMOTE_DIR:-projects/ResumeBuilder}"   # relative to the ops user's home
ENV_FILE="${ENV_FILE:-.env.prod}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found." >&2
  echo "       cp .env.prod.example $ENV_FILE, then fill in ANTHROPIC_API_KEY," >&2
  echo "       SMTP_PASSWORD, and check PUBLIC_HOST/SESSION_COOKIE_SECURE." >&2
  exit 1
fi

# The local .env points at localhost/LAN and would 403 every /auth/* POST from a
# browser on the public IP. Refuse the easy mistake.
if [[ "$ENV_FILE" == ".env" ]]; then
  echo "error: refusing to deploy the local .env — use .env.prod." >&2
  exit 1
fi

CTL="/tmp/rb-deploy-ssh-$$"
SSH_OPTS=(-o ControlMaster=auto -o ControlPath="$CTL" -o ControlPersist=120)
cleanup() { ssh "${SSH_OPTS[@]}" -O exit "$VM_USER@$VM_HOST" 2>/dev/null || true; }
trap cleanup EXIT

echo "==> $VM_USER@$VM_HOST:~/$REMOTE_DIR  (password prompt follows)"
ssh "${SSH_OPTS[@]}" "$VM_USER@$VM_HOST" "mkdir -p ~/$REMOTE_DIR/data ~/$REMOTE_DIR/logs"

echo "==> copying docker-compose.prod.yml -> docker-compose.yml"
scp "${SSH_OPTS[@]}" -q docker-compose.prod.yml "$VM_USER@$VM_HOST:$REMOTE_DIR/docker-compose.yml"

echo "==> copying $ENV_FILE -> .env"
scp "${SSH_OPTS[@]}" -q "$ENV_FILE" "$VM_USER@$VM_HOST:$REMOTE_DIR/.env"
ssh "${SSH_OPTS[@]}" "$VM_USER@$VM_HOST" "chmod 600 ~/$REMOTE_DIR/.env"

echo
echo "done. Now on the VM:"
echo "  ssh $VM_USER@$VM_HOST"
echo "  cd ~/$REMOTE_DIR"
echo "  docker compose pull && docker compose up -d"
echo "  curl -fsS http://localhost:8000/healthz"
