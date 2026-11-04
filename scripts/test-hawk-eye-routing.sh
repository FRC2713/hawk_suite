#!/usr/bin/env bash
# Exercise the actual Caddyfile against a disposable upstream, not the demo app.
set -euo pipefail
cd "$(dirname "$0")/.."

network="hawk-eye-routing-$$"
proxy="${network}-proxy"
upstream="${network}-upstream"
cleanup() {
  docker rm -f "$proxy" "$upstream" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$network" >/dev/null
docker run -d --name "$upstream" --network "$network" --network-alias hawk-eye \
  caddy:2-alpine caddy respond --listen :3000 --body upstream >/dev/null
docker run -d --name "$proxy" --network "$network" \
  -p 127.0.0.1::443 -e DOMAIN=localhost \
  -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2-alpine >/dev/null
port=$(docker port "$proxy" 443/tcp | sed 's/.*://')

request() {
  curl -ksS --noproxy '*' --resolve "scout.localhost:${port}:127.0.0.1" \
    --max-time 3 -o /dev/null -w '%{http_code}' "$@"
}
base="https://scout.localhost:${port}"
ready=0
for _ in {1..30}; do
  if [[ $(request "$base/health" 2>/dev/null || true) == 200 ]]; then
    ready=1
    break
  fi
  sleep 1
done
if (( ! ready )); then
  docker logs "$proxy"
  echo 'Caddy never became ready' >&2
  exit 1
fi

check() {
  local expected=$1 method=$2 path=$3 actual
  actual=$(request -X "$method" "$base$path")
  [[ $actual == "$expected" ]] || {
    echo "$method $path: expected $expected, got $actual" >&2
    exit 1
  }
}
check 200 GET /
for path in /docs/ /docs/architecture /docs/contracts/report.schema.json \
            /_astro/docs.js /pagefind/pagefind.js /brand/2713-red.svg /favicon.svg /health; do
  check 200 GET "$path"
done
for path in / /projects /projects/new /api/reports /_actions /docs/ /health; do
  check 503 POST "$path"
done
check 503 GET /projects
check 503 GET /projects/1/edit
check 503 GET /_actions
# A missing upstream must give a controlled unavailable response.
docker rm -f "$upstream" >/dev/null
check 503 GET /docs/
check 503 GET /health
echo 'Hawk Eye routing checks passed'
