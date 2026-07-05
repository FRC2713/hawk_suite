# hawk_suite

The Red Hawk Robotics suite of software — [QRScout](https://github.com/FRC2713/QRScout),
[rhr-mfg](https://github.com/FRC2713/rhr-mfg), and more — bundled for easy self-hosting
on a cheap VPS, a home server, or a laptop in the pit.

See [PLAN.md](PLAN.md) for the roadmap and architecture.

## Quick start

Any machine with [Docker](https://docs.docker.com/engine/install/) works: a $6/mo
DigitalOcean droplet, a Raspberry Pi, a shop computer.

```sh
git clone https://github.com/tytremblay/hawk_suite.git
cd hawk_suite
./setup.sh
```

The script asks for your domain and API keys, writes `.env`, and starts everything.
You get:

| URL | App |
| --- | --- |
| `https://<domain>` | Portal — links to everything |
| `https://scout.<domain>` | QRScout match scouting |
| `https://mfg.<domain>` | Manufacturing / Onshape part tracking |

HTTPS certificates are provisioned automatically (Let's Encrypt). DNS setup: point
`A` records for `<domain>` and `*.<domain>` at your server.

### Offline / pit mode

FRC venues have no internet. Keep the default `DOMAIN=localhost` and the whole suite
runs self-contained at `https://localhost`, `https://scout.localhost`, etc. — Caddy
issues local certificates, no connectivity required.

### Your team's scouting config

Put your QRScout config at [config/qrscout/config.json](config/qrscout/config.json)
(this repo ships FRC 2713's 2026 config as a starting point). It's served at
`https://scout.<domain>/team-config.json` — load that URL once in QRScout's config
menu on each scouting device.

### Updating

```sh
./update.sh
```

## Status

Phase 1 (see [PLAN.md](PLAN.md)): the container images are built from
[FRC2713/QRScout#124](https://github.com/FRC2713/QRScout/pull/124) and
[FRC2713/rhr-mfg#22](https://github.com/FRC2713/rhr-mfg/pull/22) — until those merge,
`docker compose pull` has nothing to pull.
