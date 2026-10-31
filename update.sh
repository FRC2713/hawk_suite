#!/usr/bin/env bash
# Interactive installations only. Production releases go through Actions.
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .deploy/current.json ]]; then
  echo 'This host uses reviewed releases. Run the Deploy workflow from main.' >&2
  exit 1
fi
docker compose pull
docker compose up -d --wait --wait-timeout 180
docker compose ps
