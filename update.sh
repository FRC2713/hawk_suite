#!/usr/bin/env bash
# Pull the latest app images and restart whatever changed.
# Both apps apply their database migrations on boot, so this is the whole
# upgrade procedure.
set -euo pipefail
cd "$(dirname "$0")"

docker compose pull
docker compose up -d
docker image prune -f
docker compose ps
