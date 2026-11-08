# Hawk Eye deployment

Event pages allow the public, rate-limited Refresh TBA form through an exact
POST allowlist: `/events/{eventKey}/?_action=refreshTba`. The app validates event
keys, enforces the shared cooldown, and honors provider backoff. Other action
queries, duplicate query parameters, scouting mutations, and API routes remain
blocked. This recovery action refreshes public provider metadata only.

Hawk Eye is the fourth suite application, hosted at
`https://scout.redhawkrobotics.org`. Its exposed surface is currently the read-only
Events workspace, public event/team provider views, design documentation,
static assets, and `/health`.
Private scouting access and ingestion are outside this public rollout. The proxy accepts
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
- Merge to main, then run the Deploy workflow from main.

The current deployment publishes read-only provider views and design docs. It does
not provide a working scouting service. Reviewed authenticated releases gate
these views and docs with Slack sign-in at Caddy and forward the signed
`AUTHP_ACCESS_TOKEN` session cookie to Eye; `/health` remains public. Eye
ignores that cookie until a Hawk Eye release that verifies it is promoted and
given `HAWK_SUITE_ISSUER` and `HAWK_SUITE_PUBLIC_KEY`. Adding those settings
before that image is promoted fails the deploy's environment-contract check. See the
[authentication gateway release guide](continuous-deployment.md#authentication-gateway-releases).
Future scouting routes require an explicit review of authorization and the
Caddy allowlist before they are made public.


## Live event providers

Set `HAWK_EYE_TBA_API_KEY` and `HAWK_EYE_MATCH13_API_KEY` as suite Actions
secrets. The Deploy workflow transports them through the existing encrypted
configuration channel and passes them only to Hawk Eye as `TBA_API_KEY` and
`MATCH13_API_KEY`. The optional `HAWK_EYE_MATCH13_ACCOUNT` repository variable
sets a stable match13 cache/quota namespace and defaults to `default`.
Interactive installations use the same prefixed settings in `.env`.

Only GET/HEAD requests to valid public event views, team views, and sanitized
event update endpoints are added to the proxy allowlist. REST, MCP, and all
mutation requests remain blocked. JWT configuration and private scouting access
are not activated by this release. Live data requires valid provider keys;
missing or rejected credentials have distinct recovery states in the app.
