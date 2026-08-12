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

require() { # require <var> <question>
  while :; do
    prompt "$1" "$2"
    [[ -n ${!1} ]] && break
    echo "  (required)"
  done
}

command -v openssl >/dev/null 2>&1 || {
  echo "openssl is required to generate hawk-mod's secrets. Install it and rerun."
  exit 1
}

echo "hawk_suite setup"
echo "================"
echo
echo "The suite serves three hostnames off one domain:"
echo "  https://<domain>       portal"
echo "  https://shop.<domain>  hawk-shop"
echo "  https://mod.<domain>   hawk-mod"
echo
echo "hawk-mod only works on a real, publicly resolvable domain — Slack posts"
echo "events to it from Slack's servers over HTTPS."
require DOMAIN "Domain (e.g. team2713.org)"

echo
echo "── hawk-shop ────────────────────────────────────────────────────"
echo "Needs an Onshape OAuth app (https://dev-portal.onshape.com/oauthApps)"
echo "with this redirect URL registered:"
echo "  https://shop.${DOMAIN}/auth/onshape/callback"
echo "Leave blank to fill in .env later."
prompt ONSHAPE_CLIENT_ID "Onshape client ID" ""
prompt ONSHAPE_CLIENT_SECRET "Onshape client secret" ""

echo
echo "── hawk-mod ─────────────────────────────────────────────────────"
echo "Needs a Slack app created from hawk-mod's docs/slack-app-manifest.yaml,"
echo "with every URL in it pointed at https://mod.${DOMAIN}."
echo "Credentials are on the app's Basic Information page."
echo "Leave blank to fill in .env later."
prompt SLACK_SIGNING_SECRET "Slack signing secret" ""
prompt SLACK_CLIENT_ID "Slack client ID" ""
prompt SLACK_CLIENT_SECRET "Slack client secret" ""
prompt ALERT_CHANNEL_ID "Findings channel ID (C…, private, screened adults only)" ""
prompt LOG_MODE "Log mode (full = store DM text, metadata = who/when only)" "full"
prompt TZ "Timezone" "America/New_York"

# Generated, never prompted — there is no reason for a human to pick these.
SLACK_STATE_SECRET=$(openssl rand -hex 32)
TOKEN_ENCRYPTION_KEY=$(openssl rand -base64 32)

umask 077
cat > .env <<EOF
# Written by setup.sh. See .env.example for every knob and what it does.
DOMAIN=${DOMAIN}
HAWK_SHOP_TAG=latest
HAWK_MOD_TAG=latest

# ── hawk-shop ────────────────────────────────────────────────────────
ONSHAPE_CLIENT_ID=${ONSHAPE_CLIENT_ID}
ONSHAPE_CLIENT_SECRET=${ONSHAPE_CLIENT_SECRET}
ONSHAPE_REDIRECT_URI=https://shop.${DOMAIN}/auth/onshape/callback
ONSHAPE_SCOPE=OAuth2Read OAuth2ReadPII OAuth2Write
APP_URL=https://shop.${DOMAIN}
ONSHAPE_IFRAME_EMBED=false

# ── hawk-mod ─────────────────────────────────────────────────────────
SLACK_SIGNING_SECRET=${SLACK_SIGNING_SECRET}
SLACK_CLIENT_ID=${SLACK_CLIENT_ID}
SLACK_CLIENT_SECRET=${SLACK_CLIENT_SECRET}
SLACK_STATE_SECRET=${SLACK_STATE_SECRET}
PUBLIC_URL=https://mod.${DOMAIN}
ALERT_CHANNEL_ID=${ALERT_CHANNEL_ID}
TOKEN_ENCRYPTION_KEY=${TOKEN_ENCRYPTION_KEY}
LOG_MODE=${LOG_MODE}
STUDENT_USERGROUP=
ADULT_USERGROUP=
TZ=${TZ}
EOF
chmod 600 .env

echo
echo "Wrote .env (mode 600)."
echo
echo "!! Back up TOKEN_ENCRYPTION_KEY from .env, somewhere off this host."
echo "!! It is not recoverable. Without it every adult must re-authorize"
echo "!! hawk-mod and previously stored tokens are unreadable."

if ! command -v docker >/dev/null 2>&1; then
  echo
  echo "Docker not found — install it, then run: docker compose up -d"
  exit 0
fi

# The GHCR packages are private unless the org has made them public, and the
# host cannot pull without a credential. Existing logins are reused.
if ! docker manifest inspect "ghcr.io/frc2713/hawk-mod:latest" >/dev/null 2>&1; then
  echo
  echo "Can't read ghcr.io/frc2713/hawk-mod — the packages are private to this host."
  echo "Either make them public (GitHub → package → Package settings → Change"
  echo "visibility), or paste a GitHub token with read:packages here."
  echo "The token is passed to 'docker login' and is NOT written to .env."
  prompt GHCR_USER "GitHub username (blank to skip)" ""
  if [[ -n ${GHCR_USER} ]]; then
    read -rsp "GitHub token (read:packages): " GHCR_TOKEN
    echo
    printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
    unset GHCR_TOKEN
  fi
fi

echo
echo "Starting the stack..."
docker compose pull
docker compose up -d

echo
echo "Up. Give Caddy a minute to get certificates, then:"
echo "  Portal:    https://${DOMAIN}"
echo "  hawk-shop: https://shop.${DOMAIN}"
echo "  hawk-mod:  https://mod.${DOMAIN}/health"
echo
echo "Next: a Lead Coach opens https://mod.${DOMAIN}/slack/install to install"
echo "the Slack app, then every adult opens the same URL to authorize."
