# hawk_suite

Two Red Hawk Robotics apps — [hawk-shop](https://github.com/FRC2713/hawk-shop)
and [hawk-mod](https://github.com/FRC2713/hawk-mod) — packaged as one Docker
Compose stack behind automatic HTTPS, so a team can run them on a single cheap
VPS.

This repo contains no application code. It is the orchestration layer: a
compose file that pulls each app's published container image, a Caddy config
that routes a subdomain to each, environment templates, and setup tooling.

| URL | App |
| --- | --- |
| `https://<domain>` | Portal — links to both |
| `https://shop.<domain>` | **hawk-shop** — manufacturing kanban driven by Onshape |
| `https://mod.<domain>` | **hawk-mod** — youth-protection DM monitoring for Slack |

## Deploying

[**docs/deploy-linode.md**](docs/deploy-linode.md) is the full walkthrough:
Linode sizing, DNS, the credentials to collect first, and the post-install
steps in each app. The short version, on any Ubuntu host with Docker:

```sh
git clone https://github.com/FRC2713/hawk_suite.git
cd hawk_suite
./setup.sh
```

`setup.sh` asks for the domain and each app's credentials, generates hawk-mod's
two secrets, writes `.env`, and starts the stack. Caddy provisions Let's
Encrypt certificates on first boot.

DNS: point A records for `<domain>`, `shop.<domain>`, and `mod.<domain>` at the
host (or `<domain>` plus a wildcard `*.<domain>`).

[`cloud-init.yaml`](cloud-init.yaml) gets a fresh Linode to the point of that
`./setup.sh` — paste it into the User Data field when creating the instance.

### The domain is not optional

hawk-mod receives Slack events and OAuth redirects from Slack's own servers, so
`mod.<domain>` must be publicly resolvable and serve real HTTPS. `DOMAIN=localhost`
still brings the stack up with Caddy's local certificates, which is enough to
click around hawk-shop, but hawk-mod cannot function that way.

## Updating

```sh
./update.sh
```

Pulls current images, restarts what changed, prunes old layers. Both apps apply
their migrations on boot, so that is the whole procedure. Pin `HAWK_SHOP_TAG`
and `HAWK_MOD_TAG` in `.env` to released versions before competition season if
you would rather not track each repo's `main`.

## What's where

```
docker-compose.yml     caddy + hawk-shop + hawk-mod, from ghcr.io/frc2713/*
Caddyfile              subdomain routing + automatic HTTPS
.env.example           every knob, documented
setup.sh               interactive first run → .env → docker compose up
update.sh              pull, restart, prune
cloud-init.yaml        Linode user-data: Docker, a `hawk` user, this repo
portal/                static landing page served at the root domain
docs/deploy-linode.md  the deployment walkthrough
```

Both images come from each app repo's `docker.yml` workflow, which publishes to
`ghcr.io/frc2713/<app>` on every push to `main`. If those packages are private,
`setup.sh` will offer to `docker login ghcr.io` with a `read:packages` token.

## Data

Two named volumes hold all state; the containers are disposable.
`hawk_mod_data` holds parental-consent records and, at `LOG_MODE=full`,
students' DM text. Read the backup and data-handling sections of
[docs/deploy-linode.md](docs/deploy-linode.md) before running it on a rented
machine.
