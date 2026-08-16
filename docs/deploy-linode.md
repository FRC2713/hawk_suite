# Deploying hawk_suite to Linode

One Linode, one Docker Compose stack, four hostnames. About twenty minutes,
most of it waiting on DNS.

Do the prerequisites first — the setup script asks for every app's credentials,
and going back to collect them afterwards means editing `.env` by hand.

## Before you start

- A domain you control DNS for.
- **Onshape OAuth app** — https://dev-portal.onshape.com/oauthApps. Redirect
  URL: `https://shop.<domain>/auth/onshape/callback`. Keep the client ID and
  secret.
- **Slack app for hawk-mod** — created from hawk-mod's
  [`docs/slack-app-manifest.yaml`](https://github.com/FRC2713/hawk-mod/blob/main/docs/slack-app-manifest.yaml),
  with every URL in the manifest pointed at `https://mod.<domain>`. Keep the
  signing secret, client ID, and client secret from Basic Information.
- **A private Slack channel** for hawk-mod's findings, and its `C…` ID.
- **A second Slack app for hawk-bot** — created from hawk-bot's
  [`docs/slack-app-manifest.yaml`](https://github.com/FRC2713/hawk-bot/blob/main/docs/slack-app-manifest.yaml),
  with every URL pointed at `https://bot.<domain>`. Same workspace, its own
  app: two sets of credentials, and they must not be crossed. Keep its signing
  secret, client ID, and client secret too.
- **GHCR access.** The images are published to `ghcr.io/frc2713/hawk-shop`,
  `ghcr.io/frc2713/hawk-mod`, and `ghcr.io/frc2713/hawk-bot` by each repo's
  `docker.yml` workflow. If those packages are private, either make them public
  (GitHub → the package → Package settings → Change visibility) or have a
  GitHub token with `read:packages` ready; `setup.sh` will offer to
  `docker login` with it.

## 1. Create the Linode

**Shared CPU, 2 GB RAM (Linode 2GB, ~$12/mo), Ubuntu 24.04 LTS**, in a region
near the team. The apps are Node processes with SQLite databases and no
separate database server; 1 GB works but leaves nothing for the image pulls.

Every image is built for `linux/amd64` only, so pick a **shared or dedicated
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

Four A records, all to the Linode's public IPv4:

| Host           | Type | Value            |
| -------------- | ---- | ---------------- |
| `<domain>`     | A    | the Linode's IP  |
| `shop`         | A    | the Linode's IP  |
| `mod`          | A    | the Linode's IP  |
| `bot`          | A    | the Linode's IP  |

A wildcard `*.<domain>` instead of the three subdomain records works too. Add
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
— its redirect URI is one field to change later. **Create the Slack apps once,
against the final domain, and don't enroll adults until then.** Neither Slack
app will start without its credentials, so a temporary-hostname run means
leaving them blank and accepting two restarting containers; what you are
deferring is the ~15 minutes *per app* of Slack dashboard edits (events URL
re-verification, OAuth redirect, slash command, interactivity) that a hostname
change costs. Enrolled adults' tokens
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

**Both Slack apps genuinely require this.** Slack delivers events, slash
commands, and OAuth redirects from its own servers to `https://mod.<domain>`
and `https://bot.<domain>`. A private hostname, a VPN-only address, or a
self-signed certificate will not work.

## 3. Run setup

SSH in as the `hawk` user (or `root`, then `su - hawk`):

```bash
ssh hawk@<linode-ip>
```

```bash
cd /opt/hawk_suite && ./setup.sh
```

It asks for the domain and the credentials collected above, generates each
Slack app's two secrets, writes `.env` with mode 600, logs in to GHCR if
needed, and brings the stack up.

**Copy `TOKEN_ENCRYPTION_KEY` out of `.env` into the team's password manager
now.** It encrypts adults' Slack tokens at rest and cannot be recovered from
anywhere else.

## 4. Check it came up

```bash
docker compose ps
```

Every app should report `healthy` within about 30 seconds. Then, from your
laptop:

```bash
curl -s https://mod.<domain>/health
curl -s https://bot.<domain>/health
```

`{"status":"ok","installed":false,…}` from each is the expected state before
the Slack apps are installed — running and waiting.

If a container is restarting, it is almost always a blank credential:

```bash
docker compose logs --tail=50 hawk-mod
docker compose logs --tail=50 hawk-bot
```

Both print exactly which environment variables failed validation. Fix `.env`
and `docker compose up -d`.

## 5. Finish the app setup

**hawk-shop**: open `https://shop.<domain>` and sign in with Onshape. If the
OAuth round trip fails, the registered redirect URL and `ONSHAPE_REDIRECT_URI`
in `.env` disagree — they must match character for character.

**hawk-mod**: a Lead Coach opens `https://mod.<domain>/slack/install`, invites
the bot to the findings channel, and imports the roster and consents (see
hawk-mod's README). Then every adult opens the same install URL to authorize
on their own account. `/hawkmod status` shows coverage.

**hawk-bot**: a workspace admin opens `https://bot.<domain>/slack/install`
once. That is the whole setup — nobody else authorizes anything, because
hawk-bot asks for no user tokens. `/hawkbot help` in any channel confirms it, and
`/hawkbot config` sets the workspace settings.

**hawk-bot's calendars** are a second, separate setup, and the one that
actually catches people out. Holding the service account credential
(`HAWK_BOT_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64`) is not the same as having
access to anything. Three things have to be true on Google's side:

1. The Google Calendar API is **enabled** in the service account's Cloud
   project. Creating the account does not enable it.
2. Each calendar is **shared** with the service account's own email address,
   at "See all event details". A service account is not a member of the
   workspace; nothing reaches it implicitly, and "See only free/busy" is not
   enough.
3. The calendar ids are set from Slack:
   ```
   /hawkbot config set team_meeting_calendar_id  <id>
   /hawkbot config set informational_calendar_id <id>   (optional)
   /hawkbot config set mentor_calendar_id        <id>   (optional)
   ```

Miss any one and the symptom is identical — no events, no Check-in Posts, no
Weekly Summary. Run **`/hawkbot calendar`**: it prints the address to share
with, then fetches each configured calendar live and names which of the three
is wrong. `/hawkbot status` shows the last failure per scheduler step, for a
sync that broke after it was working.

## Changing the domain later

Moving from a temporary hostname to the real one, in the order that avoids
downtime. Budget half an hour, nearly all of it in the Slack dashboard — there
are two Slack apps to edit, and the work is the same for each.

**1. DNS.** Point the new `<domain>`, `shop.`, `mod.`, and `bot.` at the host
and wait for them to resolve. Leave the old hostname pointing there too until
the end — it costs nothing and keeps the stack reachable while you work.

**2. `.env` on the host.** Changing `DOMAIN` is **not sufficient**. The compose
file derives the app URLs from `DOMAIN`, but `setup.sh` writes the derived
values explicitly, and an explicit value in `.env` wins. Update all five:

```
DOMAIN=<new-domain>
APP_URL=https://shop.<new-domain>
ONSHAPE_REDIRECT_URI=https://shop.<new-domain>/auth/onshape/callback
PUBLIC_URL=https://mod.<new-domain>
HAWK_BOT_PUBLIC_URL=https://bot.<new-domain>
```

**3. Onshape.** Update the redirect URL on the OAuth app to match
`ONSHAPE_REDIRECT_URI` exactly.

**4. Slack — hawk-mod's app.** Every URL that carries the hostname, from
`docs/slack-app-manifest.yaml`:

| Where | What |
| --- | --- |
| Event Subscriptions | Request URL — Slack re-verifies on save, so the stack must already be answering at the new hostname |
| OAuth & Permissions | Redirect URL |
| Slash Commands | `/hawkmod` request URL |
| Interactivity & Shortcuts | Request URL (the Resolve/Acknowledge buttons) |

Order matters here: do step 6 before saving the Event Subscriptions URL, or the
verification challenge fails.

**5. Slack — hawk-bot's app.** The same four fields, pointed at
`https://bot.<new-domain>`, with `/hawkbot` as the slash command. Different app,
different dashboard; nothing is shared between them.

**6. Restart.** `docker compose up -d` on the host. Caddy requests certificates
for the new hostnames on its own; the old ones stay in its store harmlessly.

**7. Verify.** `curl -s https://mod.<new-domain>/health` and
`curl -s https://bot.<new-domain>/health`, then `/hawkmod status` in Slack —
coverage should read the same N/N it did before — and `/hawkbot status`.

Adults do **not** re-authorize, and hawk-bot does **not** need reinstalling.
Tokens are keyed to the Slack user and workspace, not the URL, so both survive
the move. Anyone mid-enrollment at the moment of the cutover just reopens the
install link.

Once traffic is confirmed on the new hostname, drop the old DNS record.

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

No app publishes a port of its own; only Caddy binds to the host.

## Updating

```bash
cd /opt/hawk_suite && ./update.sh
```

Pulls the current images, restarts what changed, prunes the old layers. Every
app applies its database migrations on boot, so that is the whole procedure.

To pin versions instead of tracking each repo's `main`, set `HAWK_SHOP_TAG`,
`HAWK_MOD_TAG`, and `HAWK_BOT_TAG` in `.env` to released tags. Worth doing
before competition season, when an unexpected change mid-event is the thing you
least want.

## Backups

Three volumes hold everything that matters:

| Volume            | Contents                                                    |
| ----------------- | ----------------------------------------------------------- |
| `hawk_shop_data`  | hawk-shop's SQLite database and uploaded part images        |
| `hawk_mod_data`   | parental-consent records; at `LOG_MODE=full`, students' DM text |
| `hawk_bot_data`   | hawk-bot's workspace install token and its settings table   |

`hawk_bot_data` is the one you can afford to lose: an admin reinstalling and a
coach re-entering a couple of settings recreates it. Back up the other two.

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
