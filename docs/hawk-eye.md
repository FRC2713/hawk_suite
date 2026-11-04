# Hawk Eye deployment

Hawk Eye is the fourth suite application, hosted at
`https://scout.redhawkrobotics.org`. Its public surface is currently a branded
construction page, design documentation, static assets, and `/health`.
Scouting ingestion and authentication remain unimplemented. The proxy accepts
only GET and HEAD requests to those routes; demo, API, and mutation paths
remain blocked until authorization is implemented.

## Release contract

The image is built and verified in FRC2713/hawk-eye, published with a
`sha-<revision>` tag, and promoted by immutable digest in
`releases/production.json`. It carries the source revision label and the same
configuration-check contract as the other suite apps. Production never selects
a mutable tag or accepts an image override from workflow variables.

The container listens on internal port 3000, runs as a non-root user, applies
migrations before startup, and stores `/data/app.db` in `hawk_eye_data`.
`APP_URL` derives from `DOMAIN` as `https://scout.${DOMAIN}`. No Slack,
Onshape, or Google credentials are passed to this container.

The encrypted suite backup includes Hawk Eye using SQLite's online backup
API. Its database filename is `app.db`, unlike the service-named database
files used by the other apps. Recovery archives name its snapshot
`hawk-eye.db`. Keep the encrypted archive and its HMAC sidecar together, and
retain the backup encryption key in the team's password manager.

## Activation requirements

- Replace the `scout` CNAME pointing to GitHub Pages with a CNAME pointing to
  `apps.redhawkrobotics.org`, or an A record pointing to the suite host.
- Complete the one-time new-launcher migration documented in
  [continuous-deployment.md](continuous-deployment.md).
- Set `BACKUP_ENCRYPTION_KEY` and promote verified contract-v1 images for all
  suite apps before running Deploy. These requirements also protect the
  existing three apps during the first deployment with the new pipeline.
- Run the protected main Deploy workflow and use its production approval gate.

The current deployment publishes a construction page and design docs. It does
not provide a working scouting service or the Suite authentication contract.
Future scouting routes require an explicit review of authorization and the
Caddy allowlist before they are made public.
