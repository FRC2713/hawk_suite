#!/usr/bin/env python3
"""Validate an app publisher and open a PR changing only that app's release."""
import json
import os
from pathlib import Path
import re
import subprocess
from release import APPS, DIGEST, SHA, image, validate


def gh(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def main():
    payload = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())['client_payload']
    app, revision, digest, run_id = (payload[k] for k in ('app', 'revision', 'digest', 'run_id'))
    if app not in APPS or not SHA.fullmatch(revision) or not DIGEST.fullmatch(digest) or not re.fullmatch(r'[0-9]+', str(run_id)):
        raise ValueError('invalid release request')
    repo = 'FRC2713/' + app
    run = gh(f'repos/{repo}/actions/runs/{run_id}')
    jobs = gh(f'repos/{repo}/actions/runs/{run_id}/jobs?per_page=100')['jobs']
    if (run['head_sha'] != revision or run['event'] != 'push' or run['head_branch'] != 'main'
            or run['path'] != '.github/workflows/docker.yml'
            or not any(job['name'] == 'publish' and job['conclusion'] == 'success' for job in jobs)):
        raise ValueError('request is not backed by a successful main publisher')
    if gh(f'repos/{repo}/commits/main')['sha'] != revision:
        raise ValueError('publisher is superseded by a newer app revision')
    release = validate(json.loads(Path('releases/production.json').read_text()))
    item = dict(revision=revision, digest=digest, contract=1)
    ref = image(app, item)
    actual = subprocess.check_output(['docker', 'buildx', 'imagetools', 'inspect', f'ghcr.io/frc2713/{app}:sha-{revision}',
                                      '--format', '{{.Manifest.Digest}}'], text=True).strip()
    if actual != digest:
        raise ValueError('digest does not match the published SHA tag')
    subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', ref], check=True)
    actual_revision = subprocess.check_output(['docker', 'image', 'inspect', '--format',
                                               '{{index .Config.Labels "org.opencontainers.image.revision"}}', ref], text=True).strip()
    if actual_revision != revision:
        raise ValueError('image source revision mismatch')
    contract = json.loads(subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'node', ref,
                                                   'scripts/deploy-check.mjs', '--contract'], text=True))
    if contract['name'] != app or contract['schema'] != 1:
        raise ValueError('image does not support deployment contract v1')
    if release['apps'][app] == item:
        print('Release already contains this image')
        return
    branch = f'release/{app}-{revision[:12]}-{digest[7:19]}'
    existing = gh(f'repos/FRC2713/hawk_suite/pulls?state=open&head=FRC2713:{branch}')
    if existing:
        print('Release proposal already exists: ' + existing[0]['html_url'])
        return
    subprocess.run(['git', 'switch', '-c', branch], check=True)
    previous = release['apps'][app]
    release['apps'][app] = item
    Path('releases/production.json').write_text(json.dumps(release, indent=2) + '\n')
    subprocess.run(['git', 'add', 'releases/production.json'], check=True)
    subprocess.run(['git', '-c', 'user.name=hawk-release[bot]', '-c', 'user.email=hawk-release@users.noreply.github.com',
                    'commit', '-m', f'Promote {app} {revision[:12]}'], check=True)
    subprocess.run(['git', 'push', 'origin', branch], check=True)
    body = (f'Update only {app} from `{previous["revision"]}` to `{revision}`.\n\n'
            f'Image: `{ref}`\n\nVerified publisher: https://github.com/{repo}/actions/runs/{run_id}\n\n'
            'Review configuration and migration compatibility before merging. Production still requires an approved Deploy run.')
    subprocess.run(['gh', 'pr', 'create', '--repo', 'FRC2713/hawk_suite', '--base', 'main', '--head', branch,
                    '--title', f'Release {app} {revision[:12]}', '--body', body], check=True)


if __name__ == '__main__':
    main()
