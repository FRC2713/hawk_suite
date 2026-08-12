# Continuous deployment

Deploying is one GitHub Actions run. Setting it up is a handful of one-time
steps on the host.

**Read the trust model first.** It determines who you can safely give access
to, which is usually the actual question behind "can we automate this?".

## The trust model

Deployment means *the host runs what `main` says*. `docker-compose.yml` can
mount any host path into a container; `Caddyfile` can route anywhere. So:

> **Anyone who can merge to `hawk_suite`'s `main` can run code on the machine
> that stores students' DM content.**

No amount of SSH key hardening changes that, because the key's whole job is to
apply whatever `main` contains. The restricted deploy user and forced command
below are still worth having — they shrink a *leaked GitHub secret* from "shell
on the box" to "triggers a deploy" — but they do not contain a merge.

The practical consequence for opening development to students and mentors:

| Repo | Who can contribute | Who can merge | What merging gets you |
| --- | --- | --- | --- |
| `hawk-shop`, `hawk-mod` | anyone — students, mentors | reviewers on that repo | a new **container image** |
| `hawk_suite` | anyone, via PR | a small set of leads | **code execution on the host** |

That asymmetry is the useful part. Students can do essentially all the
interesting work — features, fixes, UI — in the app repos, review each other's
PRs, and see it ship, without anyone handing them root on a machine holding
minors' data. Keep `hawk_suite` merge rights narrow and it stays that way.

So, before wiring any of this up:

- Protect `main` on `hawk_suite`: require a PR, require review, and restrict
  who can approve. This is the real access control.
- Do the same on the app repos for `main` — merges there publish images that
  this host will pull.
- Development happens locally, not on the Linode. Both app repos have their own
  `docker-compose.yml` for running that app on a laptop; nobody needs the
  server to write code.

## Host setup

One time, as root on the Linode.

### 1. A deploy user that can only deploy

```bash
sudo adduser --system --group --shell /bin/bash --home /home/deploy deploy
sudo usermod -aG docker deploy
```

No sudo. Membership in `docker` is already equivalent to root on this host —
that is unavoidable for something that runs `docker compose` — but it keeps the
account's reach explicit.

The stack directory has to be writable by `deploy`, since the deploy does a
`git reset`:

```bash
sudo chown -R deploy:deploy /opt/hawk_suite
```

### 2. Install the deploy script outside the checkout

```bash
sudo install -m 755 -o root -g root /opt/hawk_suite/scripts/hawk-deploy /usr/local/bin/hawk-deploy
```

Root-owned and outside `/opt/hawk_suite` on purpose: the forced command points
here, and `deploy` cannot rewrite it. Re-run this line if
[`scripts/hawk-deploy`](../scripts/hawk-deploy) changes.

### 3. A key that can only run that script

On your laptop:

```bash
ssh-keygen -t ed25519 -f ~/hawk-deploy-key -N '' -C 'github-actions deploy'
```

On the host, add the **public** half with a forced command:

```bash
sudo -u deploy mkdir -p /home/deploy/.ssh
sudo -u deploy tee -a /home/deploy/.ssh/authorized_keys <<'EOF'
command="/usr/local/bin/hawk-deploy",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding ssh-ed25519 AAAA...paste-the-public-key... github-actions deploy
EOF
sudo -u deploy chmod 700 /home/deploy/.ssh
sudo -u deploy chmod 600 /home/deploy/.ssh/authorized_keys
```

`command=` makes this key run `hawk-deploy` and nothing else, no matter what
the client asks for.

### 4. GHCR credentials on the host

The deploy pulls images. If the packages are private, log in **once** as
`deploy` so the credential persists in `/home/deploy/.docker/config.json`:

```bash
sudo -u deploy docker login ghcr.io -u <github-user>
```

Making the two packages public avoids this entirely and is one toggle per
package (GitHub → the package → Package settings → Change visibility). The
images contain no secrets.

## GitHub setup

### Secrets

Repo → Settings → Secrets and variables → Actions:

| Secret | Value |
| --- | --- |
| `DEPLOY_SSH_KEY` | contents of `~/hawk-deploy-key` (the **private** half) |
| `DEPLOY_HOST` | the Linode's IP or hostname |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_KNOWN_HOSTS` | output of `ssh-keyscan <host>` |

`DEPLOY_KNOWN_HOSTS` is not optional. Without it the workflow would have to
disable host-key checking, which hands the deploy key to anything that can
answer on port 22.

Then delete the private key from your laptop — GitHub is where it lives now:

```bash
shred -u ~/hawk-deploy-key
```

### The approval gate

Repo → Settings → Environments → **New environment** → `production`, then check
**Required reviewers** and add the leads.

This is what keeps a merge from being a deploy. With it, every deployment is a
deliberate act by a named person, recorded in the Actions log — which is what
you want for a host holding this data, and what makes it safe to let more
people work in the repo.

## Deploying

Actions → **Deploy** → *Run workflow*. A reviewer approves; the job SSHes in,
fast-forwards `/opt/hawk_suite` to `origin/main`, pulls images, restarts, and
waits for both containers to report healthy — failing the run if they don't.

To deploy automatically on every merge to `main` instead, uncomment the `push`
trigger in [`deploy.yml`](../.github/workflows/deploy.yml). Reviewers still
gate it. Reasonable off-season; think hard before competition season, when the
last thing you want is an unattended restart during a match.

### From the app repos

A merge in `hawk-shop` or `hawk-mod` publishes an image but does not deploy it.
To have it ask for one, add a step to that repo's `docker.yml` after the push:

```yaml
      - name: Ask hawk_suite to deploy
        run: gh api repos/FRC2713/hawk_suite/dispatches -f event_type=deploy
        env:
          GH_TOKEN: ${{ secrets.HAWK_SUITE_DISPATCH_TOKEN }}
```

The token needs only `actions: write` on `hawk_suite` — a fine-grained PAT, not
a classic one. The `production` environment still requires approval, so this
requests a deploy rather than performing one.

## When it fails

The workflow fails loudly rather than leaving a half-deployed stack:

- **`Permission denied (publickey)`** — the forced-command line is malformed, or
  `authorized_keys` has the wrong permissions. `sudo tail -f /var/log/auth.log`
  on the host while re-running says which.
- **`Host key verification failed`** — the Linode was rebuilt and its host key
  changed. Re-run `ssh-keyscan` and update `DEPLOY_KNOWN_HOSTS`.
- **`still not healthy after 120s`** — the images pulled but an app won't start.
  The run prints the last 40 log lines of the unhealthy service; a blank
  credential in `.env` is the usual cause. `.env` is not in git, so CI cannot
  fix it — SSH in.
- **`denied` / `unauthorized` on pull** — the host's GHCR login expired or the
  packages went private. Redo step 4.

Rolling back is a deploy of an older state: set `HAWK_SHOP_TAG` /
`HAWK_MOD_TAG` in `.env` on the host to the previous version and re-run, or
revert the offending commit on `main` and deploy that.
