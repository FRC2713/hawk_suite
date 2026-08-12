# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

hawk_suite is the **orchestration layer** that packages two FRC 2713 (Red Hawk
Robotics) apps for self-hosting on a single Linode. It contains **no
application code** — only a Docker Compose stack, reverse-proxy config, env
templates, cloud-init user-data, and shell tooling.

The two apps live in their own repos and publish container images to GHCR:

| App | Repo | Image | Purpose |
| --- | --- | --- | --- |
| hawk-shop | `FRC2713/hawk-shop` | `ghcr.io/frc2713/hawk-shop` | Onshape-driven manufacturing kanban |
| hawk-mod | `FRC2713/hawk-mod` | `ghcr.io/frc2713/hawk-mod` | Youth-protection DM monitoring for Slack |

Implication: bugs in an app's *behavior* belong in that app's repo, not here.
This repo owns routing, wiring, env plumbing, and the deploy/update experience.

Scope is deliberately these two apps. QRScout and rhr-mfg were removed — see
PLAN.md's non-goals before adding anything back.

## Commands

```sh
./setup.sh          # First run: prompts for domain + credentials, generates hawk-mod's
                    # secrets, writes .env (600), optional ghcr login, brings the stack up.
                    # Refuses to run if .env exists (edit .env directly instead).
./update.sh         # docker compose pull && up -d && image prune -f && ps
docker compose logs -f <service>   # services: caddy, hawk-shop, hawk-mod
docker compose config              # validate compose + .env interpolation
shellcheck setup.sh update.sh scripts/hawk-deploy
```

No build or unit tests — this repo ships config, not compiled code. CI
(`.github/workflows/ci.yml`) is the test suite: shellcheck, `docker compose
config` against a fixture `.env` with assertions on the derived URLs, the
blank-`DOMAIN` guard, cloud-init YAML validity, and a check that every variable
`setup.sh` writes appears in `.env.example`. Run those locally before pushing;
they are all a few seconds.

## Architecture

One Docker host runs everything (`docker-compose.yml`):

- **caddy** (`caddy:2-alpine`) — the only container with published ports
  (80/443). Serves the static portal at the root domain and reverse-proxies by
  subdomain. Automatic Let's Encrypt certificates.
- **hawk-shop** — Node/TanStack Start server on 3000. SQLite + uploaded images
  under `/data` (volume `hawk_shop_data`). Onshape OAuth outbound. Health at
  `/api/health`.
- **hawk-mod** — Node server on 3000. SQLite under `/data` (volume
  `hawk_mod_data`). Slack events + OAuth **inbound from Slack's servers**.
  Health at `/health`.

Routing lives in `Caddyfile`: root → portal, `shop.` → hawk-shop:3000, `mod.` →
hawk-mod:3000. `$DOMAIN` is interpolated at container start.

Neither app has a separate database server. Both apply their own migrations on
boot, which is why `update.sh` is a single step.

### DOMAIN is the single switch

Every URL both apps need is derived from `DOMAIN` via nested compose
interpolation (`${APP_URL:-https://shop.${DOMAIN}}`), with per-variable
overrides in `.env` for when a registered OAuth URL disagrees. `setup.sh` also
writes the derived values explicitly so an operator can see and edit them.

**hawk-mod requires a real public domain.** Slack delivers events and the OAuth
redirect from its own servers over HTTPS, so `mod.<domain>` must be publicly
resolvable with a real certificate. `DOMAIN=localhost` still brings the stack up
under Caddy's local certificates — useful for hawk-shop — but hawk-mod cannot
work that way. Don't reintroduce offline/pit mode as a supported path.

## Invariants

1. All config via environment variables or mounted files — **never bake
   team-specific config into images**.
2. Containers are stateless; state lives in a named volume.
3. Every app exposes a health endpoint and its image declares a `HEALTHCHECK`.
4. `depends_on` stays start-order only, never `condition: service_healthy` —
   one app missing a credential must not keep Caddy and the other app down.

## Deployment and the trust boundary

`.github/workflows/deploy.yml` SSHes to the host as a restricted `deploy` user
whose key carries a forced command pointing at `/usr/local/bin/hawk-deploy` —
installed from `scripts/hawk-deploy`, root-owned, **outside** the git checkout
so the deploy user cannot rewrite it. The `production` GitHub Environment gates
every run behind a reviewer.

Understand what that does and does not protect. Deployment applies whatever
`main` says, and `docker-compose.yml` can mount any host path — so **merging to
`main` here is code execution on the machine holding hawk-mod's data**. The key
restrictions limit a leaked CI secret, not a merge. Branch protection and narrow
merge rights on this repo are the actual control; app-repo merges only produce
images, which is why student and mentor contribution belongs there. Don't
weaken this arrangement (e.g. by moving `hawk-deploy` into the checkout, or
dropping the environment gate) without saying so explicitly.

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
