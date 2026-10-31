# Reviewed releases

Production ships the three image digests in `releases/production.json`, together with an exact suite commit. The release proposal and the deployment approval are separate reviews. Merging an app change publishes a tested image and asks the suite to propose that image. Merging the suite PR does not deploy it.

```text
app main → reusable CI → build candidate → fresh and upgrade smoke tests
  → publish exact candidate → propose suite release PR
  → review and merge → Run Deploy on main → production approval
  → validate and pull → encrypted backup → apply → verify → receipt
```

## One-time migration from the old workflow

1. Merge this suite change first. Its initial release file records the existing published digests with `contract: 0`. Those are a baseline, not approved candidates for the new deployment protocol. Deploy refuses them before touching the running stack.
2. Create a GitHub App installed on `FRC2713/hawk_suite`, with Contents write and Pull requests write. Set `HAWK_SUITE_APP_ID` as an Actions variable and `HAWK_SUITE_APP_PRIVATE_KEY` as a secret in the suite and all three app repositories. The automation requests short-lived installation tokens restricted to the suite. The receiver reads public app workflow runs to verify publication. If these repos become private, grant the receiver Actions read and Contents read on those repos using a separate read-only installation.
3. Merge the three app workflow changes. Each publisher verifies, builds and tests an image, then opens a release PR. Merge all three proposals before the first new Deploy run. If a proposal job fails, retry that job, not the completed publisher.
4. Generate `BACKUP_ENCRYPTION_KEY` once with `openssl rand -base64 32`. Save it as a suite production secret and retain a copy in the password manager. It is separate from both apps' encryption keys. Keep old backup keys if rotating it.
5. The host owner re-runs `scripts/provision-host.sh` once from the new suite revision to replace the old forced-command launcher. Existing SSH keys and volumes are preserved. The new transport is JSON; the legacy launcher cannot process it. The launcher remains root-owned at `/usr/local/bin/hawk-deploy` and only authorizes an exact revision of main. Runtime deployment code comes from that reviewed revision's worktree.
6. Confirm the `production` environment has required reviewers, permits only main, and protects its secrets. Enable required CI and narrow merge rights on the suite. Run Deploy from main, inspect the proposed release, then approve it.

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
| `HAWK_SUITE_APP_PRIVATE_KEY` | Release automation App, repository scope |

Variables are `DOMAIN`, `LOG_MODE`, `TZ`, `MANAGED_USERGROUPS`, `REPORT_TIME`, `LIFECYCLE_SHEET_ID`, `ONSHAPE_IFRAME_EMBED` and `HAWK_SUITE_APP_ID`. Defaults are defined in the workflow. `REPORT_TIME` is an HH:MM seed, not cron; the saved `/hawkmod config` value wins. `MANAGED_USERGROUPS` is the administrator-editable group allowlist. Student and adult roles come from the lifecycle sheet. The old `STUDENT_USERGROUP`, `ADULT_USERGROUP`, `DIGEST_CRON` and `QUARTERLY_CRON` knobs are retired.

Image tags and image overrides are not production variables. Review image changes through the release file. The interactive setup path retains tag aliases for installations without GitHub deployment.

## What deployment does

The plan job records `GITHUB_SHA` and shows the release before the production job is approved. The host rejects the request if main has moved while approval was pending. Start a new run and review the new plan. This also prevents a queued older request from overwriting a newer release.

The root-owned launcher holds a host lock and stages the exact commit in `.deploy/releases/<sha>`. It does not hard-reset or rewrite the active checkout. Staged worktrees are checked for modifications before execution. Deployment writes private staged configuration outside those worktrees, resolves Compose, pulls all images, checks their revision labels, validates each image's configuration contract without production volumes or network, and validates Caddy.

Preflight failure removes staged secret files and leaves active configuration unchanged. `TOKEN_ENCRYPTION_KEY` must match the existing deployment. hawk-bot's encryption key and Google key remain rotatable.

Before applying migrations, deployment takes SQLite backup-API snapshots using the old running containers and copies hawk-shop uploads. It encrypts the archive using AES-256-CBC with PBKDF2 and authenticates the ciphertext with a separate derived HMAC key. Plaintext temporary files are removed on exit. Backups remain host-local under `.deploy/backups`; copy encrypted archives and their `.hmac` files off-site with an appropriate retention policy. This pipeline does not configure an off-site storage account. Uploaded files are copied while the old app is running; use a maintenance window when upload-level point-in-time consistency is required.

Compose waits for readiness. Explicit checks then require all four expected services to be running, app health to be healthy, and each running image ID to match the release digest. Caddy reload and public HTTPS checks must succeed. Successful deployment atomically writes `.deploy/current.json`, retains `.deploy/previous.json`, and returns a secret-free receipt to Actions. There is no automatic image pruning during deployment.

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

A repository-dispatch request is a release suggestion, not a deployment authorization. The receiver allowlists apps, validates revision and digest formats, checks a successful main publisher job, rejects superseded source revisions, verifies the SHA tag and image revision, and opens a PR changing only that app's entry. `repository_dispatch` requires Contents write, not Actions write.

Anyone who can merge suite configuration can cause code execution on the host. Compose can mount host paths, and the deploy account's Docker membership is root-equivalent. Branch protection and narrow merge rights remain essential. Application-image code can access its own app's production state, so app CI and review remain part of the trust model too. Production approval remains in place.
