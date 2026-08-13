#!/usr/bin/env bash
# One-time host provisioning for hawk_suite. Run once, as root, on a fresh
# Ubuntu server. After this, every deploy happens from GitHub Actions and
# nobody needs to SSH in again.
#
#   curl -fsSL https://raw.githubusercontent.com/FRC2713/hawk_suite/main/scripts/provision-host.sh | sudo bash
#
# It is safe to re-run: every step checks before acting, and an existing
# deploy key is left alone rather than replaced.
#
# It prints, at the end, the two values that go into GitHub Secrets. Those are
# the only things that leave this machine.
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/FRC2713/hawk_suite.git}
STACK_DIR=${STACK_DIR:-/opt/hawk_suite}
DEPLOY_USER=${DEPLOY_USER:-deploy}
KEY_PATH="/home/${DEPLOY_USER}/.ssh/id_ed25519"

[[ $EUID -eq 0 ]] || { echo "run this as root (sudo)" >&2; exit 1; }

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

step "Packages"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl git openssl ufw
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091  # /etc/os-release exists on the target, not here
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  echo "installed Docker $(docker --version)"
else
  apt-get install -y -qq git openssl ufw >/dev/null
  echo "Docker already present: $(docker --version)"
fi

step "Deploy user"
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  # No sudo. Membership in `docker` is already root-equivalent on this host —
  # unavoidable for anything that runs compose — but it keeps the account's
  # reach explicit rather than granting a second path to the same place.
  adduser --system --group --shell /bin/bash --home "/home/${DEPLOY_USER}" "$DEPLOY_USER"
  echo "created ${DEPLOY_USER}"
else
  echo "${DEPLOY_USER} already exists"
fi
usermod -aG docker "$DEPLOY_USER"

step "Stack directory"
if [[ ! -d ${STACK_DIR}/.git ]]; then
  git clone --quiet "$REPO_URL" "$STACK_DIR"
  echo "cloned into ${STACK_DIR}"
else
  git -C "$STACK_DIR" fetch --quiet origin main
  git -C "$STACK_DIR" reset --hard --quiet origin/main
  echo "updated ${STACK_DIR}"
fi
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "$STACK_DIR"

step "Deploy script"
# A root-owned stub the deploy user cannot rewrite, which execs the real script
# from the checkout.
#
# The first version of this copied the script itself, so that a merge could not
# change what the forced command runs. That protection was illusory: a merge can
# already alter docker-compose.yml to mount anything, so anyone who can merge
# can run anything on this host regardless. Branch protection is the real
# control, as docs/continuous-deployment.md says.
#
# What copying did buy was a trap — changes to scripts/hawk-deploy silently did
# not deploy, and the host quietly ran an old version. Stubbing keeps the file
# root-owned while letting the logic ship through git like everything else.
cat > /usr/local/bin/hawk-deploy <<STUB
#!/usr/bin/env bash
exec ${STACK_DIR}/scripts/hawk-deploy "\$@"
STUB
chown root:root /usr/local/bin/hawk-deploy
chmod 755 /usr/local/bin/hawk-deploy
echo "installed /usr/local/bin/hawk-deploy (stub -> ${STACK_DIR}/scripts/hawk-deploy)"

step "Deploy key"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh"
NEW_KEY=false
if [[ ! -f $KEY_PATH ]]; then
  sudo -u "$DEPLOY_USER" ssh-keygen -t ed25519 -N '' -C 'github-actions deploy' -f "$KEY_PATH" -q
  NEW_KEY=true
  echo "generated a new deploy key"
else
  echo "a deploy key already exists — leaving it alone"
fi

# command= is what makes this key a deploy trigger instead of a shell.
AUTH="/home/${DEPLOY_USER}/.ssh/authorized_keys"
FORCED='command="/usr/local/bin/hawk-deploy",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding'
if ! grep -qF "$(cut -d' ' -f2 "${KEY_PATH}.pub")" "$AUTH" 2>/dev/null; then
  printf '%s %s\n' "$FORCED" "$(cat "${KEY_PATH}.pub")" >> "$AUTH"
  echo "authorized the deploy key with a forced command"
else
  echo "deploy key already authorized"
fi
chown "${DEPLOY_USER}:${DEPLOY_USER}" "$AUTH"
chmod 600 "$AUTH"

step "Firewall"
# Only Caddy binds to the host; the apps are reachable through it. This is the
# host layer — a Linode Cloud Firewall in front is separate and must also
# allow 80/443, or Let's Encrypt's challenge never arrives.
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | head -8

cat <<'BANNER'

────────────────────────────────────────────────────────────────────────
 Provisioning done. Two values go into GitHub:
   Settings -> Secrets and variables -> Actions
────────────────────────────────────────────────────────────────────────
BANNER

# `hostname -I` returns every address in no guaranteed order, so it can hand
# back IPv6 or a private address on a dual-stack host. The source address for
# outbound traffic is the one the world actually reaches this box on.
PUBLIC_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')

echo
echo "DEPLOY_KNOWN_HOSTS ────────────────────────────────────────────────"
ssh-keyscan -t ed25519,rsa "$PUBLIC_IP" 2>/dev/null | grep -v '^#'
echo
echo "  (if that IP looks wrong, run 'ssh-keyscan <host>' from your laptop"
echo "   instead — host keys are public, so re-scanning is always safe)"

if [[ $NEW_KEY == true ]]; then
  echo
  echo "DEPLOY_SSH_KEY ────────────────────────────────────────────────────"
  # Single-line base64 rather than PEM. A wrapped or truncated private key
  # fails much later, as "error in libcrypto" at connect time, and nothing
  # about that message points back at the copy-paste that caused it. One line
  # has nothing to wrap. The deploy workflow accepts either form.
  base64 -w0 < "$KEY_PATH"
  echo
  echo
  echo "Copy that single line into the DEPLOY_SSH_KEY secret — all of it,"
  echo "and nothing else. Then remove the key from this host:"
  echo "    sudo shred -u ${KEY_PATH}"
  echo "(the .pub stays; authorized_keys is what matters here)"
  echo
  echo "Verify it survived the trip before shredding anything:"
  echo "    ssh-keygen -y -f ${KEY_PATH} | head -c 40"
  echo "should print the same prefix as the key in ${AUTH}."
else
  echo
  echo "The deploy key was not regenerated, so DEPLOY_SSH_KEY is unchanged."
  echo "To print it again as one line:"
  echo "    sudo base64 -w0 ${KEY_PATH}"
  echo "To start over with a fresh key:"
  echo "    sudo rm ${KEY_PATH}* && sudo bash $0"
  echo "and remove the old line from ${AUTH}."
fi

echo
echo "Nothing else on this host needs a human. Deploys run from Actions."
