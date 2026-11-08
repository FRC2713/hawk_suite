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

Reviewed authenticated releases gate Eye with Slack sign-in at Caddy and
forward the signed `AUTHP_ACCESS_TOKEN` session cookie; `/health` remains
public. The authentication overlay gives Eye `HAWK_SUITE_ISSUER`
(`https://apps.<domain>/`) and `HAWK_SUITE_PUBLIC_KEY` (the gateway's public
key, base64 PEM). Eye verifies the session itself and gives any workspace
member scouting read access and pick list editing. Uploads, rebuilds, REST, and
MCP still need operator-issued JWTs and stay blocked at the proxy. See the
[authentication gateway release guide](continuous-deployment.md#authentication-gateway-releases).

Gated scouting routes are the event scouting tab, team scouting views, the
event pick list page (`/events/{eventKey}/picklist/`) and its live state
(`picklist.json`), and three exact POST actions with no query string:
`/_actions/editPickList`, `/_actions/sharePickList`, and
`/_actions/postPickListMessage`, each with an optional trailing slash.

Partner sharing bypasses Slack sign-in. GET/HEAD `/picklist` and `/picklist/`
serve the join form or shared board; `/picklist/state.json` serves live updates.
POSTs to `/picklist` or `/picklist/` accept only the exact query
`_action=joinPickList` or `_action=leavePickList`, with no extra or duplicate
parameters. POST `/_actions/joinPickList`, `/_actions/leavePickList`, and
`/_actions/postSharedPickListMessage` accept an optional trailing slash and no
query string. GET/HEAD assets under `/_astro/` and `/brand/`, plus the favicon,
also bypass login so partners can load the board.

Eye validates the revocable share code, keeps it in an HttpOnly cookie, rate
limits joining and chat, and exposes only shared lists, alliances, public
provider figures, and that share's chat. Private lists and scouting observations
remain hidden; partners cannot edit the host board. The proxy clears caller
identity headers and strips Authorization on partner requests. Host pages,
editing actions, and documentation still require Suite membership. Tests in
`scripts/test-auth-routing.py` exercise the production template against
disposable backends, including partner requests and blocked alternatives.

Older Eye images reject the two settings at deploy, so an Eye image from
FRC2713/hawk-eye 5b93df6 or later must be promoted with them. Any further
scouting route needs the same review of Eye's authorization and the Caddy
allowlist.


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
