#!/usr/bin/env bash
# Runner transport. Credentials travel only through the encrypted stdin channel.
set -euo pipefail
umask 077
scratch=$(mktemp -d)
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT
for required in SSH_KEY DEPLOY_HOST DEPLOY_USER KNOWN_HOSTS DOMAIN TOKEN_ENCRYPTION_KEY HAWK_BOT_TOKEN_ENCRYPTION_KEY HAWK_BOT_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64 BACKUP_ENCRYPTION_KEY SUITE_SHA; do
  [[ -n ${!required:-} ]] || { echo "Missing $required" >&2; exit 1; }
done
if printf '%s' "$SSH_KEY" | grep -q 'BEGIN .*PRIVATE KEY'; then
  printf '%s\n' "$SSH_KEY" > "$scratch/key"
else
  printf '%s' "$SSH_KEY" | tr -d '[:space:]' | base64 -d > "$scratch/key"
fi
chmod 600 "$scratch/key"
ssh-keygen -y -f "$scratch/key" >/dev/null 2>&1 || { echo 'Invalid DEPLOY_SSH_KEY' >&2; exit 1; }
printf '%s\n' "$KNOWN_HOSTS" > "$scratch/known_hosts"
ssh-keygen -F "$DEPLOY_HOST" -f "$scratch/known_hosts" >/dev/null || { echo 'DEPLOY_KNOWN_HOSTS has no entry for the host' >&2; exit 1; }
python3 - "$scratch/request.json" <<'PY'
import json, os, sys
names = '''DOMAIN LOG_MODE TZ MANAGED_USERGROUPS REPORT_TIME LIFECYCLE_SHEET_ID ONSHAPE_IFRAME_EMBED
ONSHAPE_CLIENT_ID ONSHAPE_CLIENT_SECRET SLACK_SIGNING_SECRET SLACK_CLIENT_ID SLACK_CLIENT_SECRET
SLACK_STATE_SECRET ALERT_CHANNEL_ID TOKEN_ENCRYPTION_KEY HAWK_MOD_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64
HAWK_BOT_SLACK_SIGNING_SECRET HAWK_BOT_SLACK_CLIENT_ID HAWK_BOT_SLACK_CLIENT_SECRET HAWK_BOT_SLACK_STATE_SECRET
HAWK_BOT_TOKEN_ENCRYPTION_KEY HAWK_BOT_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64 BACKUP_ENCRYPTION_KEY'''.split()
json.dump(dict(suite_sha=os.environ['SUITE_SHA'], env={k:os.environ.get(k,'') for k in names}), open(sys.argv[1],'w'))
PY
ssh -i "$scratch/key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$scratch/known_hosts" -o ConnectTimeout=20 \
  "${DEPLOY_USER}@${DEPLOY_HOST}" < "$scratch/request.json" | tee receipt.txt
