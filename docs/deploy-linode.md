# Deploying hawk_suite to Linode

One Linode, one Docker Compose stack, three hostnames. About twenty minutes,
most of it waiting on DNS.

Do the prerequisites first — the setup script asks for credentials from all
three, and going back to collect them afterwards means editing `.env` by hand.

## Before you start

- A domain you control DNS for.
- **Onshape OAuth app** — https://dev-portal.onshape.com/oauthApps. Redirect
  URL: `https://shop.<domain>/auth/onshape/callback`. Keep the client ID and
  secret.
- **Slack app** — created from hawk-mod's
  [`docs/slack-app-manifest.yaml`](https://github.com/FRC2713/hawk-mod/blob/main/docs/slack-app-manifest.yaml),
  with every URL in the manifest pointed at `https://mod.<domain>`. Keep the
  signing secret, client ID, and client secret from Basic Information.
- **A private Slack channel** for hawk-mod's findings, and its `C…` ID.
- **GHCR access.** The images are published to `ghcr.io/frc2713/hawk-shop` and
  `ghcr.io/frc2713/hawk-mod` by each repo's `docker.yml` workflow. If those
  packages are private, either make them public (GitHub → the package →
  Package settings → Change visibility) or have a GitHub token with
  `read:packages` ready; `setup.sh` will offer to `docker login` with it.

## 1. Create the Linode

**Shared CPU, 2 GB RAM (Linode 2GB, ~$12/mo), Ubuntu 24.04 LTS**, in a region
near the team. Both apps are Node processes with SQLite databases and no
separate database server; 1 GB works but leaves nothing for the image pulls.

Both images are built for `linux/amd64` only, so pick a **shared or dedicated
x86 plan** — not one of the ARM offerings.

Under *Advanced Options*, paste [`cloud-init.yaml`](../cloud-init.yaml) into
the user-data field. That installs Docker, creates a non-root `hawk` user,
opens 22/80/443, and clones this repo to `/opt/hawk_suite` while the machine
boots. Skip it if you prefer to do those by hand.

Paste your public SSH key into the file's `ssh_authorized_keys:` list first if
you want to SSH straight in as `hawk`; otherwise you reach the box as `root`
(with the key you attach in the Linode UI) and `su - hawk` from there.

Add your SSH key, set a root password, and boot it.

## 2. Point DNS at it

Three A records, all to the Linode's public IPv4:

| Host           | Type | Value            |
| -------------- | ---- | ---------------- |
| `<domain>`     | A    | the Linode's IP  |
| `shop`         | A    | the Linode's IP  |
| `mod`          | A    | the Linode's IP  |

A wildcard `*.<domain>` instead of the two subdomain records works too. Add
AAAA records for the IPv6 address if you want it reachable over v6.

### Before the real domain is ready

A throwaway hostname is enough to stand the stack up, and worth doing — it
exercises Caddy, certificate issuance, the GHCR pulls, and both containers
before any credential is at stake.

Use [DuckDNS](https://duckdns.org): claim a name, point it at the host's IP,
and set `DOMAIN=<name>.duckdns.org`. Confirm arbitrary subdomains resolve
before relying on it:

```bash
dig +short shop.<name>.duckdns.org
```

**Not `nip.io` or `sslip.io`.** They resolve fine, but neither is on the
[Public Suffix List](https://publicsuffix.org/list/), so Let's Encrypt counts
every certificate under `nip.io` against one shared rate limit for the entire
internet. Issuance fails unpredictably and the error looks nothing like the
cause. `duckdns.org` is on the list, so each name gets its own quota.

Take hawk-shop all the way through an Onshape login on the temporary hostname
— its redirect URI is one field to change later. **Create the Slack app once,
against the final domain, and don't enroll adults until then.** hawk-mod will
run and answer `/health` regardless; what you are deferring is the ~15 minutes
of Slack dashboard edits (events URL re-verification, OAuth redirect, slash
command, interactivity) that a hostname change costs. Enrolled adults' tokens
survive a move — they are keyed to the Slack user, not the URL — but there is
no reason to do the work twice.

While iterating, point Caddy at Let's Encrypt's staging CA so failed attempts
don't consume the real quota — add `acme_ca https://acme-staging-v02.api.letsencrypt.org/directory`
to a global options block in the `Caddyfile`, and remove it before going live.
Staging certificates are untrusted, so browsers will warn; that is expected.

Wait for propagation before the next step — Caddy asks Let's Encrypt for
certificates on first boot, and a failed challenge means a retry backoff:

```bash
dig +short shop.<domain>
```

**hawk-mod genuinely requires this.** Slack delivers events and the OAuth
redirect from its own servers to `https://mod.<domain>`. A private hostname, a
VPN-only address, or a self-signed certificate will not work.

## 3. Run setup

SSH in as the `hawk` user (or `root`, then `su - hawk`):

```bash
ssh hawk@<linode-ip>
```

```bash
cd /opt/hawk_suite && ./setup.sh
```

It asks for the domain and the credentials collected above, generates
hawk-mod's two secrets, writes `.env` with mode 600, logs in to GHCR if needed,
and brings the stack up.

**Copy `TOKEN_ENCRYPTION_KEY` out of `.env` into the team's password manager
now.** It encrypts adults' Slack tokens at rest and cannot be recovered from
anywhere else.

## 4. Check it came up

```bash
docker compose ps
```

Both apps should report `healthy` within about 30 seconds. Then, from your
laptop:

```bash
curl -s https://mod.<domain>/health
```

`{"status":"ok","installed":false,"enrolledAdults":0}` is the expected state
before the Slack app is installed — running and waiting.

If a container is restarting, it is almost always a blank credential:

```bash
docker compose logs --tail=50 hawk-mod
```

hawk-mod prints exactly which environment variables failed validation. Fix
`.env` and `docker compose up -d`.

## 5. Finish the app setup

**hawk-shop**: open `https://shop.<domain>` and sign in with Onshape. If the
OAuth round trip fails, the registered redirect URL and `ONSHAPE_REDIRECT_URI`
in `.env` disagree — they must match character for character.

**hawk-mod**: a Lead Coach opens `https://mod.<domain>/slack/install`, invites
the bot to the findings channel, and imports the roster and consents (see
hawk-mod's README). Then every adult opens the same install URL to authorize
on their own account. `/hawkmod status` shows coverage.

## Firewall

**Check Linode's Cloud Firewall first.** A Linode created with the *Default*
firewall policy permits inbound SSH only, which silently breaks Let's Encrypt:
the HTTP-01 challenge never reaches Caddy, and the failure looks like a DNS
problem. Open 80 and 443 on the firewall attached to the instance (Linode
dashboard → the firewall → Rules), or detach it. Host-level `ufw` is a separate
layer and will not help.

`cloud-init.yaml` already sets `ufw` to inbound 22/80/443 only. If you skipped
it, do the same by hand:

```bash
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
```

Neither app publishes a port of its own; only Caddy binds to the host.

## Updating

```bash
cd /opt/hawk_suite && ./update.sh
```

Pulls the current images, restarts what changed, prunes the old layers. Both
apps apply their database migrations on boot, so that is the whole procedure.

To pin versions instead of tracking each repo's `main`, set `HAWK_SHOP_TAG` and
`HAWK_MOD_TAG` in `.env` to released tags. Worth doing before competition
season, when an unexpected change mid-event is the thing you least want.

## Backups

Two volumes hold everything that matters:

| Volume            | Contents                                                    |
| ----------------- | ----------------------------------------------------------- |
| `hawk_shop_data`  | hawk-shop's SQLite database and uploaded part images        |
| `hawk_mod_data`   | parental-consent records; at `LOG_MODE=full`, students' DM text |

Use SQLite's backup API rather than copying the files — a `cp` of a WAL
database mid-write produces a corrupt copy:

```bash
docker compose exec hawk-mod node -e "const D=require('better-sqlite3');new D(process.env.DATA_DIR+'/hawk-mod.db').backup('/data/backup.db').then(()=>process.exit(0))"
```

Then pull it off the host (`docker compose cp hawk-mod:/data/backup.db .`) and
encrypt it at rest. Quarterly is the minimum for hawk-mod — the point of that
system is being able to produce a conversation months later.

## Data handling

`hawk_mod_data` holds minors' message content on a rented machine. That is a
deliberate tradeoff, and it comes with obligations:

- SSH by key only, and keep the host patched. Anyone with root on the Linode
  can read the volume.
- Leave the data in the named Docker volume rather than bind-mounting it into
  a world-readable host path. The container runs as a non-root user that owns
  it.
- `LOG_MODE=metadata` records who/when/how-many without message text and still
  detects every policy violation. If the team is uneasy about storing minors'
  message text on a rented host, that is the setting to change; the cost is
  that an investigation falls back to a Slack Corporate Export.
- If the host is ever suspected of compromise, rotate `TOKEN_ENCRYPTION_KEY`
  and have every adult re-enroll.
