#!/usr/bin/env python3
"""Point releases/production.json at published app images.

    python3 scripts/promote.py hawk-eye              # app's current main
    python3 scripts/promote.py hawk-shop@91efbfe     # a specific commit
    python3 scripts/promote.py hawk-eye hawk-bot     # several at once

Each app's publish workflow pushes `sha-<commit>` only after its smoke tests
pass, so that tag existing is the evidence of a tested image. This resolves the
tag to its immutable digest and edits the release file. It does not commit,
push or deploy. The host re-checks the revision label and configuration
contract of every image before it changes anything.

Needs `gh` with the read:packages scope, because the images are private.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from release import APPS, DIGEST, SHA, validate

ORG = 'FRC2713'
RELEASE = Path(__file__).resolve().parents[1] / 'releases' / 'production.json'
SCOPE_HINT = 'gh auth refresh -h github.com -s read:packages'


class PromoteError(Exception):
    pass


def gh(*args):
    result = subprocess.run(['gh', 'api', *args], capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        if 'read:packages' in message:
            raise PromoteError(f'gh needs the read:packages scope. Run once:\n    {SCOPE_HINT}')
        raise PromoteError(message)
    return result.stdout


def parse_target(text):
    app, _, revision = text.partition('@')
    if app not in APPS:
        raise PromoteError(f'unknown app {app!r}; expected one of {", ".join(APPS)}')
    if revision and not re.fullmatch(r'[0-9a-f]{7,40}', revision):
        raise PromoteError(f'{app}: revision must be a hex commit SHA')
    return app, revision or 'main'


def resolve_revision(app, ref):
    """Return the full SHA for ref, refusing commits that are not on main."""
    repo = f'{ORG}/{app}'
    sha = gh(f'repos/{repo}/commits/{ref}', '--jq', '.sha').strip()
    if not SHA.fullmatch(sha):
        raise PromoteError(f'{app}: could not resolve {ref}')
    status = gh(f'repos/{repo}/compare/{sha}...main', '--jq', '.status').strip()
    if status not in ('identical', 'ahead'):
        raise PromoteError(f'{app}: {sha[:12]} is not on main')
    return sha


def find_digest(app, revision):
    tag = f'sha-{revision}'
    out = gh('--paginate', f'orgs/{ORG}/packages/container/{app}/versions?per_page=100',
             '--jq', f'.[] | select(.metadata.container.tags | index("{tag}")) | .name')
    digests = out.split()
    if not digests:
        raise PromoteError(f'{app}: no published image {tag}. Has its publish workflow finished?\n'
                           f'    gh run list -R {ORG}/{app} --commit {revision}')
    if len(digests) != 1 or not DIGEST.fullmatch(digests[0]):
        raise PromoteError(f'{app}: ambiguous digest for {tag}')
    return digests[0]


def apply(release, app, revision, digest):
    """Update one entry in place. Returns the previous entry, or None if unchanged."""
    item = dict(revision=revision, digest=digest, contract=1)
    previous = release['apps'][app]
    if previous == item:
        return None
    release['apps'][app] = item
    return previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('targets', nargs='+', metavar='APP[@REVISION]')
    parser.add_argument('--release', type=Path, default=RELEASE)
    args = parser.parse_args(argv)
    try:
        targets = [parse_target(t) for t in args.targets]
        if len({app for app, _ in targets}) != len(targets):
            raise PromoteError('each app may appear only once')
        release = validate(json.loads(args.release.read_text()))
        changes = []
        for app, ref in targets:
            revision = resolve_revision(app, ref)
            digest = find_digest(app, revision)
            previous = apply(release, app, revision, digest)
            if previous is None:
                print(f'{app}: already at {revision[:12]}')
            else:
                changes.append((app, previous['revision'], revision))
                print(f'{app}: {previous["revision"][:12]} -> {revision[:12]}  {digest[:19]}')
        if changes:
            validate(release)
            args.release.write_text(json.dumps(release, indent=2) + '\n')
            for app, old, new in changes:
                print(f'  https://github.com/{ORG}/{app}/compare/{old}...{new}')
        return 0
    except PromoteError as error:
        print(f'promote: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
