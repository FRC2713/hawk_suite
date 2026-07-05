# hawk_suite — Plan

hawk_suite bundles, orchestrates, and installs the FRC 2713 web apps so any team can
self-host them on a personal cloud (DigitalOcean, Hetzner, a home server) or a laptop
in the pit — with setup measured in minutes, not evenings.

This repo contains **no app code**. It is the thin orchestration layer: a Docker Compose
file, reverse proxy config, environment templates, and setup tooling. Each app lives in
its own repo and publishes a container image; hawk_suite composes them into a suite.

## Goals

- One-command setup on any machine that runs Docker.
- A single domain with automatic HTTPS, one subdomain per app, and a portal landing
  page that makes the collection feel like a suite.
- Fully offline-capable: the same bundle runs on a pit laptop or Raspberry Pi at
  competitions with no internet (FRC venues famously have none).
- Adoptable by other teams: team-specific config (QRScout `config.json`, branding,
  API keys) is injected via env vars and mounted files, never baked into images.
- A clean growth path to a hosted, multi-tenant Kubernetes platform if demand appears —
  without rework.

## Non-goals (for now)

- Building the Kubernetes platform itself (see [Phase 4](#phase-4-hosted-platform-only-if-demand-appears)).
- Shared single sign-on across apps. Each app keeps its own auth initially; Authelia or
  Authentik can be added behind Caddy as forward-auth later without touching app code.
- Supporting PaaS providers (Heroku, Render, Railway) as first-class targets. They
  deploy one container per app, not a compose stack, so the suite fragments there.
  "Any VM with Docker" is the primary target; per-app PaaS deploys are a possible
  secondary path if teams ask.

## The apps

| App | Repo | Stack | Bundling notes |
| --- | --- | --- | --- |
| QRScout | [FRC2713/QRScout](https://github.com/FRC2713/QRScout) | Vite/TypeScript, fully static | Needs a small Dockerfile serving the build via nginx or Caddy. Team `config.json` mounted as a volume. |
| rhr-mfg | [FRC2713/rhr-mfg](https://github.com/FRC2713/rhr-mfg) | React Router v7 + Node server | Dockerfile already exists. Needs Onshape OAuth keys via env vars; likely a Postgres database. |
| hawk-shop | [FRC2713/hawk-shop](https://github.com/FRC2713/hawk-shop) | Docs/markdown only today | **Open decision.** Simplest path: render as a static docs site (MkDocs or Astro) in the suite. Revisit if it becomes an app. |
| Project tracker | TBD | TBD | **Open decision.** Options: bundle our own TracBoard, or an off-the-shelf self-hosted tool (Plane, Vikunja, Focalboard). Off-the-shelf is zero maintenance but won't share auth or branding. |
| Portal | this repo | Static page | Tiny landing page at the root domain linking to each app. What makes it feel like a suite. |

## Architecture (v1: Docker Compose)

One Docker host runs the whole stack:

- **Caddy** as the reverse proxy. Automatic Let's Encrypt HTTPS and subdomain routing
  (`scout.team2713.org`, `mfg.team2713.org`, …) in a few lines of config. Serves the
  portal at the root domain.
- **App containers** pulled from `ghcr.io/frc2713/<app>`, published by GitHub Actions
  in each app repo on release.
- **One Postgres container** shared by every app that needs a database, with one
  database per app. Apps connect via a single `DATABASE_URL`.
- **Backups**: a nightly `pg_dump` sidecar writing to a mounted volume (later:
  optional off-site upload).
- **Updates**: a `./update.sh` (or `hawk update`) that pulls new image tags and
  restarts. Watchtower is an option for auto-updates but explicit updates are safer
  for teams mid-season.

External dependencies stay external: rhr-mfg calls the Onshape API outbound and only
needs keys in `.env`. Everything else works with zero connectivity.

### Planned repo layout

```
hawk_suite/
  docker-compose.yml       # references ghcr.io/frc2713/* images
  Caddyfile                # routing + automatic HTTPS
  .env.example             # every knob a team can turn, documented
  setup.sh                 # interactive first-run: domain, keys, passwords → .env
  update.sh                # pull latest images, restart
  portal/                  # static landing page
  config/
    qrscout/config.json    # team scouting config, mounted into QRScout
  cloud-init/              # user-data for one-click droplet provisioning
  docs/                    # setup guides per provider (DO, Hetzner, pit laptop)
```

### Setup experience, in tiers

1. **Clone and run** (the foundation): `git clone` + `./setup.sh`. The script asks for
   a domain, Onshape keys, and an admin password, writes `.env`, runs
   `docker compose up -d`. Works on a $6/mo droplet or a shop machine.
2. **One-click cloud**: a cloud-init user-data file / "Deploy to DigitalOcean" button
   that provisions a droplet and runs tier 1 automatically.
3. **Panel templates** (optional): publish Coolify and/or CapRover templates for teams
   that already run those panels. Evaluate before investing heavily in our own
   scripts' polish — those tools already handle HTTPS, updates, and dashboards.

## Rules that keep us Kubernetes-ready

These cost nothing in the compose version and are the entire migration checklist later:

1. All configuration via environment variables; no config baked into images.
2. Containers are stateless — state lives in Postgres or a mounted volume, never the
   container filesystem.
3. Database access through a single `DATABASE_URL`, so a local container and a managed
   cluster are interchangeable.
4. Every app exposes a `/health` endpoint (required for k8s probes later).

## Scaling path (v2: hosted multi-tenant Kubernetes)

Only if the project takes off. Kubernetes for a single team is overkill; where it earns
its complexity is a hosted platform where teams click "create instance" instead of
self-hosting:

- **One Helm chart** is the pivotal artifact. On the hosted cluster each team is a
  release in its own namespace; teams with their own clusters can `helm install`
  directly. Same GHCR images as compose — packaging, not a rewrite.
- **Provisioning via GitOps**: a repo of per-team values files plus an ArgoCD
  ApplicationSet. Signup commits `teams/2713.yaml`; ArgoCD deploys it. Rollback is
  `git revert`.
- **Routing**: wildcard DNS (`*.hawksuite.org`) + one cert-manager wildcard cert.
  Adding a team requires zero DNS/TLS work.
- **Data**: one Postgres via the CloudNativePG operator, one database per team — not a
  Postgres pod per namespace.
- **Seasonality is our friend**: FRC traffic is tiny per team and brutally seasonal.
  Idle instances scale to zero replicas off-season (KEDA or a CronJob) and wake on
  demand. A ~3-node cluster (≈$100/mo on DOKS) can plausibly host hundreds of teams.
- **Static-app optimization**: QRScout doesn't need a pod per team — one shared
  deployment can serve every team, selecting the right `config.json` by hostname.
  Pod-per-team only for apps with real backends.

The honest caveat: v2 is mostly a platform-operations commitment (monitoring, upgrades,
support), not a technical one. The compose path never goes away — it stays first-class
forever as the offline/pit and self-host story.

## Milestones

### Phase 1 — prove the pattern
- [ ] Dockerfile + GHCR publish workflow for QRScout (static build behind nginx/Caddy).
- [ ] GHCR publish workflow for rhr-mfg (Dockerfile exists).
- [ ] `docker-compose.yml` + `Caddyfile` + `.env.example` running QRScout + rhr-mfg +
      Postgres + portal on one droplet with real HTTPS.
- [ ] `setup.sh` and `update.sh`.
- [ ] Verify the full stack offline on a laptop (pit simulation).

### Phase 2 — round out the suite
- [ ] Decide and integrate the project tracker (TracBoard vs off-the-shelf).
- [ ] Decide hawk-shop's form (static docs site vs future app) and integrate.
- [ ] Nightly backup sidecar + restore doc.
- [ ] Setup guides: DigitalOcean, Hetzner, home server, pit laptop.

### Phase 3 — adoption by other teams
- [ ] Cloud-init one-click provisioning for DigitalOcean.
- [ ] Team-config story polished (drop-in QRScout config, branding).
- [ ] Evaluate Coolify/CapRover templates.
- [ ] Announce to the FRC community (Chief Delphi).

### Phase 4 — hosted platform (only if demand appears)
- [ ] Helm chart mirroring the compose stack.
- [ ] ArgoCD ApplicationSet + per-team values repo.
- [ ] Wildcard DNS/TLS, CloudNativePG, scale-to-zero for idle teams.
- [ ] Signup portal.

## Open decisions

1. **hawk-shop**: static docs render, or is an app planned?
2. **Project tracker**: TracBoard (ours, integrated) vs Plane/Vikunja/Focalboard
   (off-the-shelf, zero maintenance)?
3. **Shared auth**: stay per-app, or add Authelia/Authentik forward-auth once the suite
   stabilizes?
4. **Subdomains vs paths**: subdomains are cleaner (apps assume root base paths);
   confirm no team blockers on wildcard DNS.
5. **Update policy**: explicit `update.sh` only, or opt-in auto-updates via Watchtower?
