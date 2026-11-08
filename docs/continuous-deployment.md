# Reviewed releases

Production ships the four image digests in `releases/production.json`, together with an exact suite commit. The release PR and the deployment approval are separate reviews. Merging an app change publishes a tested image. Promoting it is a suite PR, and merging that PR queues a Deploy that waits for a production reviewer.

```text
app main → app CI, alongside: build candidate → fresh smoke test
  → upgrade smoke test from the production digest
  → (CI and smoke tests pass) publish sha-<commit> → scripts/promote.py → release PR → review and merge
  → Deploy queued on main → production approval
  → validate and pull → encrypted backup → apply → verify → receipt
```

## Promoting an image

```bash
python3 scripts/promote.py hawk-eye            # the app's current main
python3 scripts/promote.py hawk-shop@91efbfe   # a specific commit on main
```

The script resolves the app's `sha-<commit>` tag to its digest and edits the release file. That tag is pushed only after the publisher's smoke tests pass, and the commit must be on the app's main. It needs `gh` with the `read:packages` scope (`gh auth refresh -h github.com -s read:packages`, once). Commit the change on a branch and open a PR. In Claude Code, `/ship <app>` runs the whole sequence and leaves the production approval to a reviewer.

The script does not repeat the host's checks. Before changing anything, Deploy verifies each image's revision label and configuration contract.

Deploy is queued by pushes to main that change `releases/`, `docker-compose.yml`, `Caddyfile`, `portal/` or the host-side deploy scripts. It can also be started by hand from main.

## One-time migration from the old workflow

1. Merge this suite change first. Its initial release file records the existing published digests with `contract: 0`. Those are a baseline, not approved candidates for the new deployment protocol. Deploy refuses them before touching the running stack.
2. Merge the app workflow changes. Each publisher verifies, builds and tests an image. Promote all four apps with `scripts/promote.py` before the first new Deploy run.
3. Generate `BACKUP_ENCRYPTION_KEY` once with `openssl rand -base64 32`. Save it as a suite production secret and retain a copy in the password manager. It is separate from both apps' encryption keys. Keep old backup keys if rotating it.
4. The host owner installs the new forced-command launcher once. Existing hosts can use the launcher-only command below, keeping their existing SSH key and volumes. Use `scripts/provision-host.sh` for a fresh host. The new transport is JSON; the legacy launcher cannot process it. The launcher remains root-owned at `/usr/local/bin/hawk-deploy` and only authorizes an exact revision of main. Runtime deployment code comes from that reviewed revision's worktree.
5. Confirm the `production` environment has required reviewers, permits only main, and protects its secrets. Enable required CI and narrow merge rights on the suite. Inspect the release shown by the queued Deploy run, then approve it.

For an existing host, run this in its root console to update only the
launcher. The pinned revision contains the new JSON launcher. This does not
regenerate an SSH key or change GitHub SSH secrets:

```bash
launcher=$(mktemp)
curl -fsSL https://raw.githubusercontent.com/FRC2713/hawk_suite/4dd3aa17402c46357641b419a068da7592b3fdaa/scripts/hawk-deploy -o "$launcher" &&
  install -o root -g root -m 755 "$launcher" /usr/local/bin/hawk-deploy
rm -f "$launcher"
```

The new backup encryption key is separate from SSH and Slack credentials. Keep
all existing deployment and token-encryption keys unchanged during migration.

The source does not configure repository protection or create reviewer rules. Configure those in GitHub before rollout. An environment name in YAML alone does not require approval.

## Secrets and variables

Retain existing credentials. The complete production secret names are:

| Secret | Source |
| --- | --- |
| `DEPLOY_SSH_KEY` | Host provisioning output, PEM or single-line base64 |
| `DEPLOY_KNOWN_HOSTS` | Verified host public key entry |
| `DEPLOY_HOST`, `DEPLOY_USER` | Server address and `deploy` |
| `ONSHAPE_CLIENT_ID`, `ONSHAPE_CLIENT_SECRET` | Onshape OAuth application |
| `SLACK_SIGNING_SECRET`, `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_STATE_SECRET` | hawk-mod's Slack application |
| `TOKEN_ENCRYPTION_KEY` | Existing hawk-mod encryption key; do not regenerate |
| `ALERT_CHANNEL_ID` | hawk-mod findings channel ID |
| `HAWK_MOD_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64` | Optional lifecycle integration, separate Google account |
| `HAWK_BOT_SLACK_SIGNING_SECRET`, `HAWK_BOT_SLACK_CLIENT_ID`, `HAWK_BOT_SLACK_CLIENT_SECRET`, `HAWK_BOT_SLACK_STATE_SECRET` | hawk-bot's separate Slack application |
| `HAWK_BOT_TOKEN_ENCRYPTION_KEY` | hawk-bot's encryption key |
| `HAWK_BOT_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64` | Required hawk-bot Calendar service account key |
| `BACKUP_ENCRYPTION_KEY` | Separate backup key, minimum 32 characters |

Variables are `DOMAIN`, `LOG_MODE`, `TZ`, `MANAGED_USERGROUPS`, `REPORT_TIME`, `LIFECYCLE_SHEET_ID` and `ONSHAPE_IFRAME_EMBED`. Defaults are defined in the workflow. `REPORT_TIME` is an HH:MM seed, not cron; the saved `/hawkmod config` value wins. `MANAGED_USERGROUPS` is the administrator-editable group allowlist. Student and adult roles come from the lifecycle sheet. The old `STUDENT_USERGROUP`, `ADULT_USERGROUP`, `DIGEST_CRON` and `QUARTERLY_CRON` knobs are retired.

Private GHCR images are pulled using the approved workflow job's short-lived
`GITHUB_TOKEN`, limited to Packages read. The transport sends it through SSH
stdin separately from application environment values. The host uses a private
temporary Docker configuration and deletes it on success or failure. The token
is never saved to `.env`, a release receipt, or the host's existing Docker
configuration. Grant the suite repository Read access under each private
package's Manage Actions access settings. No permanent registry token or
additional host login is required for this deployment path.

Image tags and image overrides are not production variables. Review image changes through the release file. The interactive setup path retains tag aliases for installations without GitHub deployment.

## What deployment does

The plan job records `GITHUB_SHA` and shows the release before the production job is approved. The host rejects the request if main has moved while approval was pending. Start a new run and review the new plan. This also prevents a queued older request from overwriting a newer release.

The root-owned launcher holds a host lock and stages the exact commit in `.deploy/releases/<sha>`. It does not hard-reset or rewrite the active checkout. Staged worktrees are checked for modifications before execution. Deployment writes private staged configuration outside those worktrees, resolves Compose, pulls all images, checks their revision labels, validates each image's configuration contract without production volumes or network, and validates Caddy.

Preflight failure removes staged secret files and leaves active configuration unchanged. `TOKEN_ENCRYPTION_KEY` must match the existing deployment. hawk-bot's encryption key and Google key remain rotatable.

Before applying migrations, deployment takes SQLite backup-API snapshots using the old running containers and copies hawk-shop uploads. It encrypts the archive using AES-256-CBC with PBKDF2 and authenticates the ciphertext with a separate derived HMAC key. Plaintext temporary files are removed on exit. Backups remain host-local under `.deploy/backups`; copy encrypted archives and their `.hmac` files off-site with an appropriate retention policy. This pipeline does not configure an off-site storage account. Uploaded files are copied while the old app is running; use a maintenance window when upload-level point-in-time consistency is required.

Compose waits for readiness. Explicit checks then require all five expected services to be running, app health to be healthy, and each running image ID to match the release digest. Caddy reload and public HTTPS checks must succeed. Successful deployment atomically writes `.deploy/current.json`, retains `.deploy/previous.json`, and returns a secret-free receipt to Actions. There is no automatic image pruning during deployment.

## Recovery

A failure after apply records `.deploy/failed.json` with its staged configuration, backup identifier and previous release. Database migrations may already have run. The pipeline deliberately does not restore an old database or start an older image automatically.

For a code regression with a backward-compatible schema, open a suite PR restoring the previous digest entries, merge it, and run Deploy on the new main revision. Do not run an old suite revision directly. The previous images remain available locally.

For schema recovery, stop writes during a maintenance window. Copy the authenticated encrypted archive and HMAC sidecar to a trusted machine. Read the backup key without placing it in a command line:

```bash
read -rs BACKUP_ENCRYPTION_KEY
export BACKUP_ENCRYPTION_KEY
python3 scripts/restore-backup.py snapshot.tar.gz.enc recovered-snapshot
unset BACKUP_ENCRYPTION_KEY
```

The restore helper verifies authentication and decrypts into a new review directory. It never writes production volumes. Verify SQLite integrity, match the snapshot to the appropriate image, account for writes after the snapshot, and have the host owner restore the selected volumes while the services are stopped. Retain hawk-mod's original token encryption key with the restored data.

## Setup diagnostics

From a checkout:

```bash
python3 scripts/doctor.py --domain redhawkrobotics.org
python3 scripts/doctor.py --domain example.org --manifests /tmp/slack-manifests
```

Doctor checks DNS, HTTPS, expected app revisions and access to every pinned image. It reports Slack apps waiting for installation and can produce domain-specific Slack manifests from the pinned source revisions. It does not read production secrets. Run `/hawkbot calendar` and `/hawkmod lifecycle plan` as an administrator to check Google access through the actual integration. Health means the runtime and database work, not that third-party authorization is complete.

## Trust and permissions

A release PR is a suggestion, not a deployment authorization. Merging it queues Deploy, but only a production reviewer's approval runs it. Release digests come from `sha-<commit>` tags that only the app's main publisher pushes, after its smoke tests. The host independently checks every image's revision label and configuration contract before applying.

Anyone who can merge suite configuration can cause code execution on the host. Compose can mount host paths, and the deploy account's Docker membership is root-equivalent. Branch protection and narrow merge rights remain essential. Application-image code can access its own app's production state, so app CI and review remain part of the trust model too. Production approval remains in place.

Hawk Eye is included in the same release, configuration validation, encrypted
backup, container checks, and HTTPS checks. See [hawk-eye.md](hawk-eye.md).

Hawk Eye provider secrets are `HAWK_EYE_TBA_API_KEY` and
`HAWK_EYE_MATCH13_API_KEY`. `HAWK_EYE_MATCH13_ACCOUNT` is an optional repository
variable, default `default`, used for stable account-level cache/quota tracking.
These reach only the Hawk Eye container. Private scouting endpoints remain
outside the public proxy allowlist.

## Authentication gateway releases

Caddy gates Mod, Bot, and Eye's read-only pages behind a shared Slack sign-in.
Shop keeps its own Onshape login and is not behind the gateway: the gateway's
SameSite=Lax cookie would break the Onshape iframe, so excluding Shop preserves
its existing direct and embedded access. Its card remains in the authenticated
portal. No suite membership grants any
app's admin rights; each app keeps its own permissions. Signing out of the
gateway does not sign out of individual apps.

Sign-in is at `https://apps.<domain>/auth/`. The session cookie is scoped to
the bare domain, Secure, HttpOnly, SameSite=Lax, with a 15-minute access
token. Client-supplied `X-Hawk-Identity` is stripped before every app proxy;
apps do not receive or trust suite identity today.

| Host | Public exceptions | Everything else |
| --- | --- | --- |
| `apps` | Native sign-in/callback routes, public branding | Portal requires login; unknown paths 404 |
| `mod`, `bot` | POST `/slack/events`; GET `/slack/oauth_redirect`, `/auth/slack/callback`; GET/HEAD `/health` | Gateway membership required, then the existing app login/permissions |
| `shop` | Entire host bypasses the suite login gate | Existing Onshape login and app permissions |
| `scout` | GET/HEAD `/health` | Approved read-only pages require gateway membership; other routes 503 |

The Deploy plan requires an `authentication` entry in the release manifest;
its absence intentionally blocks deployment until a tested gateway image is
published and promoted. Existing app image entries and logins stay unchanged.
An authenticated release adds the generated private configuration and
branding Compose overlay, verifies the gateway source label and running image
ID, and checks anonymous/forged access denial after deployment. An active
authenticated release cannot be replaced with a manifest that removes
authentication.

### Publishing and promoting the gateway image

1. Run the **Authentication candidate** workflow with `publish=true` on the
   reviewed revision. It builds and tests native amd64/arm64 images and
   publishes only the tested archives. Record the suite source SHA and the
   published **index** digest from its summary.
2. Add this entry to `releases/production.json`, keeping all four app entries
   unchanged:

   ```json
   "authentication": {
     "revision": "<40-character suite SHA from the candidate workflow>",
     "digest": "sha256:<64-character published index digest>"
   }
   ```

   The image is allowlisted to `ghcr.io/frc2713/hawk-auth`. The host validates
   its `io.hawk.suite-source-revision` label and exact running image ID.
3. Add the production Slack redirect URL to the dedicated sign-in app:
   `https://apps.<domain>/auth/oauth2/slack/authorization-code-callback`.
   Sign-in scopes are `openid profile email`.
4. Set Actions variables `HAWK_AUTH_SLACK_CLIENT_ID` and
   `HAWK_AUTH_SLACK_TEAM_ID`; set secrets `HAWK_AUTH_SLACK_CLIENT_SECRET` and
   `HAWK_AUTH_SIGNING_KEY_BASE64` (a fresh unencrypted RSA key, at least 3072
   bits, base64-encoded PEM on one line). Never reuse Mod's or Bot's keys for
   this, and never paste any of these into an issue, chat, or command line.
5. Merge the pinned release and run **Deploy** from main through the existing
   production approval gate.
6. In a fresh browser session, confirm Mod, Bot, and Eye redirect anonymous
   users to the portal; sign in with Slack and confirm each app's own login
   still works; sign out of the gateway and confirm Mod/Bot/Eye are gated
   again. Confirm Shop's direct and embedded Onshape flows are unaffected.

### Private runtime configuration and recovery

Each release stores mode-600 auth files (signing keys, rendered Caddyfile,
portal templates) under `.deploy/config/<release-id>/auth`, outside the clean
source worktree, mounted read-only into the gateway. Gateway logs are
discarded (`logging: driver: none`) because the pinned upstream can log token
contents; use deployment checks and container state for diagnostics instead
of turning that logging on against real sessions.

Do not recover by removing the `authentication` manifest entry or reverting
to an unprotected Caddyfile. For an emergency, the host owner:

1. Identifies the running `hawk-suite` Caddy container and inspects its mounts
   to find the **host source** mounted at `/etc/caddy/Caddyfile`. This is
   `<stack>/.deploy/config/<release-id>/auth/Caddyfile`. Use the actual running
   mount, since a failed deployment may differ from `current.json`.
2. Validates the rendered maintenance file inside that container with
   `caddy validate --config /etc/hawk-auth/Caddyfile.maintenance --adapter caddyfile`.
   Stop if validation fails.
3. On the **host**, saves the current private Caddyfile in a root-only backup,
   then copies the sibling `Caddyfile.maintenance` contents into that host
   Caddyfile **in place**, retaining mode 600. Do not rename or replace the file:
   the running bind mount must continue referencing the same inode. Neither
   `/etc/caddy/Caddyfile` nor `/etc/hawk-auth` is writable inside the container.
4. Runs `caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile` inside
   the same container, then verifies all five hosts return 503. Keep the
   maintenance content at the host startup path until a reviewed authenticated
   release is restored, so restarting the container retains maintenance mode.

All five hosts return 503, including integrations, until an authenticated
release is restored. Existing encrypted backup/schema recovery rules still
apply; do not delete volumes or restore databases for a routing failure.

### Local verification

- `python3 scripts/build-authentication.py`
- `python3 scripts/test-authentication.py`
- Install Python `cryptography==46.0.4`, then run
  `python3 scripts/test-auth-routing.py` (Docker required).

The routing probe substitutes HTTP termination and mock provider metadata,
uses synthetic keys/JWTs and disposable echo backends, and removes its
containers and network afterward. It verifies routing and app-cookie
preservation, not real app permissions, Slack delivery, browser cookie
storage, or Onshape iframe compatibility.
