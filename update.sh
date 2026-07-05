#!/usr/bin/env bash
# Pull the latest app images and restart whatever changed.
set -euo pipefail
cd "$(dirname "$0")"

docker compose pull
docker compose up -d
docker image prune -f
