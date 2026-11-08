---
name: ship
description: Ship one or more hawk apps (hawk-shop, hawk-mod, hawk-bot, hawk-eye) to production — wait for the published image, promote its digest in releases/production.json, open and merge the release PR, then follow the Deploy run. Use when the user says "ship", "deploy", "release" or "promote" an app or commit.
---

# Ship an app release

Arguments: one or more `app` or `app@commit`. No commit means the app's
current `main`. Repo: `FRC2713/hawk_suite`. Apps live in `FRC2713/<app>`.

## 1. Make sure the image is published

The image tag `sha-<commit>` exists only after the app's publish workflow
passed its smoke tests.

- hawk-shop, hawk-mod, hawk-bot publish on every push to main (workflow
  `docker.yml`). Find the run with
  `gh run list -R FRC2713/<app> -w docker.yml --commit <sha> -L 1` and
  `gh run watch <id> -R FRC2713/<app> --exit-status` if it is still going.
  The `publish` job is the one that matters. The `propose` job fails until the
  app repos drop it (it needs a GitHub App that was never set up), so judge the
  run by `gh run view <id> -R FRC2713/<app> --json jobs`, not its overall result.
- hawk-eye publishes only when started by hand:
  `gh workflow run image.yml -R FRC2713/hawk-eye --ref main`, then find and watch
  that run. It takes about 8 minutes.

Run long waits in the background rather than polling.

## 2. Promote

From an up-to-date `origin/main`, on a new branch `release/<app>-<sha12>`:

```bash
python3 scripts/promote.py <app>[@sha] [...]
```

It prints each change and a compare link. If it says gh needs `read:packages`,
give the user the command it prints. It opens a browser, so the user runs it.
If every app is already at that commit, stop: there is nothing to ship.

Commit only `releases/production.json` with the message `Release <app> <sha12>`.
Push, then open the PR with `gh pr create`. Put the compare links in the body
so a reviewer can see what code is going out, and end the body with the
attribution line.

## 3. Merge

Wait for CI with `gh pr checks <n> --watch`. Then `gh pr merge <n> --merge`.
Main requires an approving review. If the merge is blocked, tell the user and
ask. Never add `--admin` unless the user says to in this conversation.

## 4. Deploy

Merging a change to `releases/` starts the Deploy workflow on main, with no
approval step. Find it with `gh run list -w deploy.yml -L 1`, watch it with
`gh run watch <id> --exit-status`, and report the receipt from
`gh run view <id> --log`, or the failure.

If Deploy says the revision is stale, another merge landed while it ran. That
merge started its own Deploy, which carries this release too.

A failure after apply may have run migrations. Report it and stop. Recovery is
in `docs/continuous-deployment.md` and is never automatic.
