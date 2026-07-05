#!/usr/bin/env bash
# First-run setup for hawk_suite: writes .env and starts the stack.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f .env ]]; then
  echo ".env already exists — edit it directly, or delete it and rerun this script."
  exit 1
fi

prompt() { # prompt <var> <question> <default>
  local var=$1 question=$2 default=${3:-}
  local answer
  read -rp "$question${default:+ [$default]}: " answer
  printf -v "$var" '%s' "${answer:-$default}"
}

echo "hawk_suite setup"
echo "----------------"
echo "Serving on a real domain? Enter it (e.g. team2713.org)."
echo "Just testing, or running offline in the pit? Keep 'localhost'."
prompt DOMAIN "Domain" "localhost"

echo
echo "rhr-mfg needs an Onshape OAuth app (https://dev-portal.onshape.com)"
echo "and a Supabase project. Leave blank to fill in .env later."
prompt ONSHAPE_CLIENT_ID "Onshape client ID" ""
prompt ONSHAPE_CLIENT_SECRET "Onshape client secret" ""
prompt ONSHAPE_REDIRECT_URI "Onshape redirect URI" "https://mfg.${DOMAIN}/api/auth/callback"
prompt ONSHAPE_SCOPE "Onshape scope" "OAuth2Read"
prompt SUPABASE_URL "Supabase URL" ""
prompt SUPABASE_SERVICE_ROLE_KEY "Supabase service role key" ""

cat > .env <<EOF
DOMAIN=${DOMAIN}
ONSHAPE_CLIENT_ID=${ONSHAPE_CLIENT_ID}
ONSHAPE_CLIENT_SECRET=${ONSHAPE_CLIENT_SECRET}
ONSHAPE_REDIRECT_URI=${ONSHAPE_REDIRECT_URI}
ONSHAPE_SCOPE=${ONSHAPE_SCOPE}
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}
EOF
echo
echo "Wrote .env"

if command -v docker >/dev/null 2>&1; then
  echo "Starting the stack..."
  docker compose up -d
  echo
  echo "Done. Portal:  https://${DOMAIN}"
  echo "      Scout:   https://scout.${DOMAIN}"
  echo "      Mfg:     https://mfg.${DOMAIN}"
else
  echo "Docker not found — install it, then run: docker compose up -d"
fi
