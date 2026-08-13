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

Deploys run from GitHub Actions; nobody SSHes into the server to ship. The
host needs one bootstrap, run once by whoever owns it:

```sh
curl -fsSL https://raw.githubusercontent.com/FRC2713/hawk_suite/main/scripts/provision-host.sh | sudo bash
```

That installs Docker, a restricted `deploy` user, and a key that can only
trigger a deploy — then prints the two values to paste into GitHub Secrets.
Everything after it is Actions → **Deploy**, behind a required reviewer.
[**docs/continuous-deployment.md**](docs/continuous-deployment.md) has the full
list of secrets and variables, and the trust model behind that gate.

[**docs/deploy-linode.md**](docs/deploy-linode.md) covers the rest: Linode
sizing, DNS, temporary domains, the credentials to collect first, and the
post-install steps in each app.

For a one-off or offline install with no GitHub involved, `./setup.sh` still
does the interactive version — it asks for the domain and each app's
credentials, generates hawk-mod's secrets, writes `.env`, and starts the stack.

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

On the host:

```sh
./update.sh
```

Pulls current images, restarts what changed, prunes old layers. Both apps apply
their migrations on boot, so that is the whole procedure. Pin `HAWK_SHOP_TAG`
and `HAWK_MOD_TAG` in `.env` to released versions before competition season if
you would rather not track each repo's `main`.

Or from GitHub: Actions → **Deploy**, which does the same thing over SSH behind
an approval gate. [docs/continuous-deployment.md](docs/continuous-deployment.md)
sets that up.

## Contributing

Most work belongs in the app repos — [hawk-shop](https://github.com/FRC2713/hawk-shop)
and [hawk-mod](https://github.com/FRC2713/hawk-mod) — where each has its own
compose file for running that app on a laptop. You do not need access to the
server to write code, and merging there produces a container image rather than
touching the host.

This repo is different: merging here means the server runs it. Merge rights
are narrow on purpose. See the trust model in
[docs/continuous-deployment.md](docs/continuous-deployment.md).

## What's where

```
docker-compose.yml     caddy + hawk-shop + hawk-mod, from ghcr.io/frc2713/*
Caddyfile              subdomain routing + automatic HTTPS
.env.example           every knob, documented
setup.sh               interactive first run → .env → docker compose up
update.sh              pull, restart, prune
cloud-init.yaml        Linode user-data: Docker, a `hawk` user, this repo
portal/                static landing page served at the root domain
scripts/provision-host.sh  one-time host bootstrap, run by the server's owner
scripts/hawk-deploy    host-side deploy, run by the Deploy workflow over SSH
docs/                  deploy-linode.md, continuous-deployment.md
.github/workflows/     ci.yml validates PRs; deploy.yml ships main
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
