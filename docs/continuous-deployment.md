# Continuous deployment

Every deploy runs from GitHub Actions. Nobody SSHes into the host — not to
ship a change, not to rotate a credential, not to update the stack.

That requires one bootstrap on the host, done once by whoever owns the server.
Everything after that is a button in the Actions tab.

## What the host owner does (once)

SSH into the server as root and run:

```bash
curl -fsSL https://raw.githubusercontent.com/FRC2713/hawk_suite/main/scripts/provision-host.sh | sudo bash
```

[`provision-host.sh`](../scripts/provision-host.sh) installs Docker, creates a
`deploy` user with no sudo, clones this repo to `/opt/hawk_suite`, installs the
deploy script to `/usr/local/bin/hawk-deploy`, generates an SSH key restricted
to running only that script, and sets `ufw` to 22/80/443. It is safe to re-run;
an existing key is left alone.

It finishes by printing two values. **Those are the only things that leave the
machine**, and they go straight into GitHub:

| Printed | Goes into |
| --- | --- |
| `DEPLOY_SSH_KEY` (the private key) | repository secret |
| `DEPLOY_KNOWN_HOSTS` | repository secret |

Then delete the private key from the host, as the script instructs — GitHub is
where it lives now. No credentials for either app are ever typed on the server.

**Also check the Linode Cloud Firewall.** An instance created with the
*Default* policy permits inbound SSH only, which silently breaks Let's
Encrypt: the HTTP-01 challenge never arrives and the symptom reads as a DNS
problem. `ufw` on the host is a separate layer and will not help.

That is the entire host-side job.

## What goes into GitHub

Settings → Secrets and variables → Actions.

### Secrets

| Secret | Where it comes from |
| --- | --- |
| `DEPLOY_SSH_KEY` | printed by `provision-host.sh` |
| `DEPLOY_KNOWN_HOSTS` | printed by `provision-host.sh` |
| `DEPLOY_HOST` | the server's IP or hostname |
| `DEPLOY_USER` | `deploy` |
| `ONSHAPE_CLIENT_ID` | Onshape OAuth app |
| `ONSHAPE_CLIENT_SECRET` | Onshape OAuth app |
| `SLACK_SIGNING_SECRET` | Slack app → Basic Information |
| `SLACK_CLIENT_ID` | Slack app → Basic Information |
| `SLACK_CLIENT_SECRET` | Slack app → Basic Information |
| `SLACK_STATE_SECRET` | `openssl rand -hex 32` |
| `ALERT_CHANNEL_ID` | the private findings channel's `C…` ID |
| `TOKEN_ENCRYPTION_KEY` | `openssl rand -base64 32` — **see below** |
| `HAWK_BOT_SLACK_SIGNING_SECRET` | hawk-bot's **own** Slack app → Basic Information |
| `HAWK_BOT_SLACK_CLIENT_ID` | hawk-bot's Slack app → Basic Information |
| `HAWK_BOT_SLACK_CLIENT_SECRET` | hawk-bot's Slack app → Basic Information |
| `HAWK_BOT_SLACK_STATE_SECRET` | `openssl rand -hex 32` |
| `HAWK_BOT_TOKEN_ENCRYPTION_KEY` | `openssl rand -base64 32` |

The `HAWK_BOT_*` secrets come from a **second Slack app**, not from hawk-mod's.
Two apps in one workspace, and crossing their credentials makes hawk-bot answer
as the wrong app — which presents as Slack silently ignoring `/hawkbot`.

### Variables

Non-secret settings are repository *variables*, so they stay readable and their
changes are visible:

| Variable | Default if unset |
| --- | --- |
| `DOMAIN` | none — the deploy fails without it |
| `HAWK_SHOP_TAG`, `HAWK_MOD_TAG`, `HAWK_BOT_TAG` | `latest` |
| `LOG_MODE` | `full` |
| `TZ` | `America/New_York` |
| `STUDENT_USERGROUP`, `ADULT_USERGROUP` | empty (roster by CSV) |
| `ONSHAPE_IFRAME_EMBED` | `false` |

Changing the domain is now editing the `DOMAIN` variable and re-running the
workflow — plus the dashboard edits in
[deploy-linode.md](deploy-linode.md#changing-the-domain-later).

### TOKEN_ENCRYPTION_KEY is generated once, ever

Generate it with `openssl rand -base64 32`, store it as a secret, and keep a
copy in the team's password manager. It encrypts adults' Slack tokens at rest.

Changing it makes every stored token undecryptable and forces the whole team to
re-authorize. Because the workflow now writes `.env` on every deploy, a wrong
value here would do that silently — so `hawk-deploy` refuses any deploy whose
incoming key differs from the one already on the host, and leaves `.env`
untouched. CI tests that refusal. If you ever genuinely need to rotate it, do
it on the host by hand and accept the re-enrollment.

`HAWK_BOT_TOKEN_ENCRYPTION_KEY` is a different animal despite the similar name.
It encrypts one workspace installation, so losing or changing it costs a single
admin reinstall of hawk-bot. `hawk-deploy` requires it to be present — a blank
one crash-loops the container, which reads as an unrelated outage — but
deliberately does not compare it against the host's. CI tests that it stays
rotatable, so the two do not get conflated later.

### The approval gate

Settings → Environments → **New environment** → `production`, then check
**Required reviewers** and add the leads.

This is what keeps a merge from being a deploy. Every deployment becomes a
deliberate act by a named person, recorded in the Actions log.

## How a deploy works

Actions → **Deploy** → *Run workflow*. After a reviewer approves:

1. The job renders `.env` from the secrets and variables above.
2. It connects as `deploy` and pipes that `.env` over the SSH session. The
   key's forced command ignores arguments, so stdin is the only channel, and
   the credentials never touch a command line or a disk outside the runner.
3. On the host, `hawk-deploy` validates the incoming environment, writes
   `.env` at mode 600, fast-forwards the checkout to `origin/main`, pulls
   images, restarts, and polls until every container reports healthy — failing
   the run with logs if any doesn't.

To deploy automatically on every merge to `main`, uncomment the `push` trigger
in [`deploy.yml`](../.github/workflows/deploy.yml). Reviewers still gate it.
Reasonable off-season; think hard before competition season.

### From the app repos

A merge in `hawk-shop`, `hawk-mod`, or `hawk-bot` publishes an image but does
not deploy it. To have it ask for one, add a step to that repo's `docker.yml`:

```yaml
      - name: Ask hawk_suite to deploy
        run: gh api repos/FRC2713/hawk_suite/dispatches -f event_type=deploy
        env:
          GH_TOKEN: ${{ secrets.HAWK_SUITE_DISPATCH_TOKEN }}
```

A fine-grained PAT with `actions: write` on `hawk_suite` is enough. The
`production` environment still requires approval.

## The trust model

Deployment means *the host runs what `main` says*. `docker-compose.yml` can
mount any host path; `Caddyfile` can route anywhere. So:

> **Anyone who can merge to `hawk_suite`'s `main` can run code on the machine
> that stores students' DM content.**

The restricted deploy user and forced command do not change that — they shrink
a *leaked GitHub secret* from "shell on the box" to "triggers a deploy". They
do not contain a merge. Branch protection and narrow merge rights are the
actual control.

That asymmetry is what makes it safe to open development up:

| Repo | Who contributes | What merging gets you |
| --- | --- | --- |
| `hawk-shop`, `hawk-mod`, `hawk-bot` | anyone — students, mentors | a container **image** |
| `hawk_suite` | anyone, via PR | **code execution on the host** |

Students can do essentially all the interesting work in the app repos, review
each other's PRs, and watch it ship, without anyone handing them root on a
machine holding minors' data. Both app repos have their own compose file for
laptop development; nobody needs the server to write code.

## When it fails

- **`Permission denied (publickey)`** — re-run `provision-host.sh`; it is
  idempotent and will repair `authorized_keys`.
- **`Host key verification failed`** — the server was rebuilt and its host key
  changed. Re-run `ssh-keyscan <host>` and update `DEPLOY_KNOWN_HOSTS`.
- **`refusing: incoming environment has no …`** — that secret is unset or empty
  in GitHub. The host's `.env` was left untouched.
- **`refusing: TOKEN_ENCRYPTION_KEY differs …`** — the secret does not match
  what the host already has. Fix the secret; do not "fix" the host.
- **`still not healthy after 120s`** — images pulled but an app won't start.
  The run prints the last 40 log lines of the unhealthy service. hawk-mod and
  hawk-bot each name the exact environment variables that failed validation.
- **`denied` / `unauthorized` on pull** — the GHCR packages are private and the
  host has no credential. Make them public, or `sudo -u deploy docker login
  ghcr.io` once.

Rolling back is a deploy of an older state: set the `HAWK_SHOP_TAG` /
`HAWK_MOD_TAG` / `HAWK_BOT_TAG` variables to the previous versions and re-run,
or revert the commit on `main` and deploy that.
