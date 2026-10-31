# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

hawk_suite is the **orchestration layer** that packages three FRC 2713 (Red Hawk
Robotics) apps for self-hosting on a single Linode. It contains **no
application code** — only a Docker Compose stack, reverse-proxy config, env
templates, cloud-init user-data, and shell tooling.

The apps live in their own repos and publish container images to GHCR:

| App | Repo | Image | Purpose |
| --- | --- | --- | --- |
| hawk-shop | `FRC2713/hawk-shop` | `ghcr.io/frc2713/hawk-shop` | Onshape-driven manufacturing kanban |
| hawk-mod | `FRC2713/hawk-mod` | `ghcr.io/frc2713/hawk-mod` | Youth-protection DM monitoring for Slack |
| hawk-bot | `FRC2713/hawk-bot` | `ghcr.io/frc2713/hawk-bot` | Team assistant for Slack (`/hawkbot`) |

Implication: bugs in an app's *behavior* belong in that app's repo, not here.
This repo owns routing, wiring, env plumbing, and the deploy/update experience.

Scope is deliberately these three apps. QRScout and rhr-mfg were removed — see
PLAN.md's non-goals before adding anything back.

hawk-mod and hawk-bot are **two separate Slack apps** in one workspace, with
separate credentials, separate installs, and separate volumes. hawk-bot's
variables carry a `HAWK_BOT_` prefix in `.env` and arrive unprefixed inside its
container, because both apps read the same names. Crossing them is the
subtlest failure this stack has: hawk-bot would answer as hawk-mod's app and
Slack would appear to be ignoring it. CI asserts they stay distinct.

## Verification

Run `python3 -m unittest discover -s test -v`, shellcheck on the shell scripts,
and the fixture Compose assertions in `.github/workflows/ci.yml`. Run
`python3 test/backup-integration.py` when changing backups or recovery; it uses
only disposable volumes and a unique project label. Validate Caddy and workflow
syntax before publishing changes.

`./setup.sh` and `./update.sh` are the interactive installation path.
Production uses the reviewed Deploy workflow. `python3 scripts/doctor.py
--domain <domain>` performs read-only routing, registry and version checks.

## Architecture

One Docker host runs everything (`docker-compose.yml`):

- **caddy** (`caddy:2-alpine`) — the only container with published ports
  (80/443). Serves the static portal at `apps.` and reverse-proxies by
  subdomain. Automatic Let's Encrypt certificates.
- **hawk-shop** — Node/TanStack Start server on 3000. SQLite + uploaded images
  under `/data` (volume `hawk_shop_data`). Onshape OAuth outbound. Health at
  `/api/health`.
- **hawk-mod** — Node server on 3000. SQLite under `/data` (volume
  `hawk_mod_data`). Slack events + OAuth **inbound from Slack's servers**.
  Health at `/health`.
- **hawk-bot** — Node server on 3000. SQLite under `/data` (volume
  `hawk_bot_data`). Slash commands + events + OAuth **inbound from Slack's
  servers**. Health at `/health`. Holds a workspace bot token and no user
  tokens at all, which is what keeps it uncontroversial next to hawk-mod.

Routing lives in `Caddyfile`: `apps.` → portal, `shop.` → hawk-shop:3000, `mod.` →
hawk-mod:3000, `bot.` → hawk-bot:3000. `$DOMAIN` is interpolated at container
start. Editing the Caddyfile also means bumping `CADDY_CONFIG_REV` in
`docker-compose.yml` — the comment there says why.

No app has a separate database server. All three apply their own migrations on
boot, which is why `update.sh` is a single step.

### DOMAIN is the single switch

Every URL the apps need is derived from `DOMAIN` via nested compose
interpolation (`${APP_URL:-https://shop.${DOMAIN}}`), with per-variable
overrides in `.env` for when a registered OAuth URL disagrees. `setup.sh` also
writes the derived values explicitly so an operator can see and edit them.

**Both Slack apps require a real public domain.** Slack delivers events, slash
commands, and OAuth redirects from its own servers over HTTPS, so
`mod.<domain>` and `bot.<domain>` must be publicly resolvable with real
certificates. `DOMAIN=localhost` still brings the stack up under Caddy's local
certificates — useful for hawk-shop — but neither Slack app can work that way.
Don't reintroduce offline/pit mode as a supported path.

## Invariants

1. All config via environment variables or mounted files — **never bake
   team-specific config into images**.
2. Containers are stateless; state lives in a named volume.
3. Every app exposes a health endpoint and its image declares a `HEALTHCHECK`.
4. `depends_on` stays start-order only, never `condition: service_healthy` —
   one app missing a credential must not keep Caddy and the other app down.

## Deployment and the trust boundary

Read `docs/continuous-deployment.md` before changing release promotion, the SSH
protocol, bootstrap or recovery. It owns the migration sequence and permission
requirements. The initial contract-0 release is a baseline; production requires
all three contract-v1 images before approval.

The root-owned `scripts/hawk-deploy` launcher is installed outside the checkout.
It accepts a bounded JSON request, locks the host, fetches main and authorizes
an exact current revision. Runtime deployment logic executes from that revision's
clean worktree. Launcher updates require one host-owner provisioning run.

`scripts/deploy.py` validates staged configuration and image contracts before
apply. It refuses a change to hawk-mod's existing `TOKEN_ENCRYPTION_KEY`.
hawk-bot's key and Google credential remain rotatable. Preserve those distinct
policies and their tests. Secret files stay mode 600 outside source worktrees.

A post-apply failure may have run migrations. Retain the failure record,
previous images and authenticated encrypted backup. Database restore is an
explicit maintenance operation; image rollback requires schema compatibility.

Production keeps its GitHub Environment approval. Referencing the environment
in YAML alone does not configure reviewers. Merging suite configuration grants
host code execution through Docker; branch protection and narrow merge rights
remain the actual control. Apps retain separate credentials and persistent data.

## Sensitive data

`hawk_mod_data` holds parental-consent records and, at `LOG_MODE=full`, the
message text of students' DMs. `TOKEN_ENCRYPTION_KEY` in `.env` encrypts adults'
Slack tokens at rest and is **not recoverable**. Treat both accordingly:
`.env` is mode 600, backups use SQLite's backup API (not `cp` — WAL), and the
data-handling section of `docs/deploy-linode.md` is not boilerplate.

## Conventions

- `.env` is gitignored and generated by `setup.sh`; `.env.example` documents
  every knob and must stay in sync with what `setup.sh` writes and
  `docker-compose.yml` reads.
- Adding an app = service in `docker-compose.yml`, subdomain block in
  `Caddyfile`, env vars in both `.env.example` and `setup.sh`, a card in
  `portal/index.html`, and a row in the tables here and in README.md.
- `PLAN.md` holds scope, non-goals, remaining work, and open decisions. Consult
  it before adding services or changing the architecture.
