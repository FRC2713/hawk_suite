# hawk_suite — Plan

hawk_suite packages [hawk-shop](https://github.com/FRC2713/hawk-shop) and
[hawk-mod](https://github.com/FRC2713/hawk-mod) into one Docker Compose stack,
deploys it to a single Linode, and puts both apps behind real HTTPS on
subdomains of one domain.

That is the whole scope. This repo contains no app code — it is the thin
orchestration layer: a compose file, a Caddy config, environment templates, and
setup tooling. Each app lives in its own repo and publishes its own container
image; hawk_suite composes them.

## Goals

- One command on a fresh Linode gets both apps running with automatic HTTPS.
- Every team-specific value (domain, API keys, secrets) is injected via `.env`,
  never baked into an image.
- Upgrades are `./update.sh` — pull, restart, done, with migrations applied by
  the apps themselves on boot.
- Both apps' state lives in named volumes with a documented backup procedure.
  hawk-mod's volume holds minors' data, so this is not a nice-to-have.

## Non-goals

- **Other apps.** QRScout and rhr-mfg were in an earlier version of this repo
  and were removed. QRScout is a static site with no server-side state and no
  reason to live in this stack; rhr-mfg was superseded by hawk-shop.
- **Offline / pit mode.** It was a goal when QRScout was in scope. hawk-mod
  requires public HTTPS reachability by construction, so the suite is now a
  public-internet deployment. `DOMAIN=localhost` still works for poking at
  hawk-shop locally; it is not a supported operating mode.
- **Shared single sign-on.** hawk-shop authenticates through Onshape, hawk-mod
  through Slack. Both are the correct identity provider for their app; a
  forward-auth layer in front would add a third.
- **Kubernetes.** Two containers on one host. If the suite ever gets a hosted
  multi-tenant story, the compose stack is the thing to port — but nothing here
  should be shaped by that possibility today.
- **A shared database.** Both apps use embedded SQLite in their own volume. A
  Postgres container would be a service to run, back up, and upgrade, in
  exchange for nothing either app asked for.

## The apps

| App | Image | Port | State | External dependency |
| --- | --- | --- | --- | --- |
| [hawk-shop](https://github.com/FRC2713/hawk-shop) | `ghcr.io/frc2713/hawk-shop` | 3000 | SQLite + uploaded images in `/data` | Onshape OAuth + API |
| [hawk-mod](https://github.com/FRC2713/hawk-mod) | `ghcr.io/frc2713/hawk-mod` | 3000 | SQLite in `/data` | Slack (events, OAuth) |

Both publish `linux/amd64` images from a `docker.yml` workflow on every push to
`main`, carry their own `HEALTHCHECK`, run as a non-root user, and apply
migrations on boot.

## Architecture

One Linode, one compose stack:

- **Caddy** is the only container binding host ports (80/443). It serves the
  static portal at the root domain and reverse-proxies `shop.` and `mod.` to
  the two app containers by service name. Let's Encrypt certificates are
  automatic.
- **hawk-shop** and **hawk-mod** publish no ports and are reachable only
  through Caddy.
- **State** is two named Docker volumes. Containers are disposable; the volumes
  are not.

`DOMAIN` is the single switch: every URL either app needs is derived from it
(`https://shop.$DOMAIN`, `https://mod.$DOMAIN`), with per-variable overrides in
`.env` for the cases where a registered OAuth URL disagrees.

## Constraints worth keeping

1. Config comes from environment variables and mounted files only — nothing
   team-specific inside an image.
2. Containers are stateless; everything durable is in a volume.
3. Every app exposes a health endpoint and the image declares a `HEALTHCHECK`.
4. Each app owns its own migrations and applies them on boot, so an upgrade is
   never a two-step procedure for the operator.

## Status

Both apps publish images and this repo composes them. Remaining work:

- [ ] Run the whole thing on a real Linode with real DNS and confirm both
      apps' OAuth round trips complete (the redirect URIs are the likely
      failure).
- [ ] Confirm GHCR package visibility — the packages currently reject
      anonymous pulls, so either make them public or the host needs a
      `read:packages` token at `docker login` time.
- [ ] Automate the hawk-mod backup rather than leaving it a documented manual
      command. A nightly `docker compose exec` from cron on the host is
      probably enough; a sidecar is probably too much.
- [ ] Decide whether to pin image tags for competition season, and write down
      the version-bump procedure if so.

## Open decisions

1. **Off-site backups.** The volumes live on the Linode. hawk-mod's data
   should not only exist there. Linode Object Storage with a rotation policy is
   the obvious answer; it needs encryption at rest, given the contents.
2. **Alerting.** Nothing currently notices if a container is unhealthy or the
   host is down. Uptime Kuma on the same host would be self-defeating; an
   external ping service is the honest option.
3. **Tag policy.** `latest` tracks each app's `main` — fine off-season,
   probably wrong during competition.
