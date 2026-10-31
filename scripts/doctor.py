#!/usr/bin/env python3
"""Read-only checks for DNS, HTTPS, image access and initial integration setup."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import urllib.request
from release import APPS, image, validate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--domain', required=True)
parser.add_argument('--release', default='releases/production.json')
parser.add_argument('--manifests', type=Path, help='write domain-specific Slack manifests into this directory')
args = parser.parse_args()
release = validate(json.loads(Path(args.release).read_text()))
failed = False
for host, path in [('apps', '/'), ('shop', '/api/health'), ('mod', '/health'), ('bot', '/health')]:
    name = host + '.' + args.domain
    try:
        socket.getaddrinfo(name, 443)
        with urllib.request.urlopen('https://' + name + path, timeout=15) as response:
            if host != 'apps':
                body = json.load(response)
                expected = release['apps']['hawk-' + host]['revision']
                if body.get('revision') != expected:
                    raise ValueError('revision mismatch')
                if body.get('installed') is False:
                    print(f'SETUP {name}: open https://{name}/slack/install')
            print('OK DNS and HTTPS: ' + name)
    except Exception:
        failed = True
        print('FAIL DNS, HTTPS or revision: ' + name)
for app in APPS:
    result = subprocess.run(['docker', 'manifest', 'inspect', image(app, release['apps'][app])],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(('OK' if result.returncode == 0 else 'FAIL') + ' registry access: ' + app)
    failed |= result.returncode != 0
if args.manifests:
    args.manifests.mkdir(parents=True, exist_ok=True)
    for app, host in [('hawk-mod', 'mod'), ('hawk-bot', 'bot')]:
        revision = release['apps'][app]['revision']
        url = f'https://raw.githubusercontent.com/FRC2713/{app}/{revision}/docs/slack-app-manifest.yaml'
        text = urllib.request.urlopen(url, timeout=15).read().decode()
        # Manifests use placeholder or production hostnames. Replace URL origins,
        # retaining their endpoint paths and leaving scopes unchanged.
        import re
        text = re.sub(r'https://(?:mod|bot)\.[^/\s"\']+', f'https://{host}.{args.domain}', text)
        (args.manifests / (app + '.yaml')).write_text(text)
print('Google access: run /hawkbot calendar and /hawkmod lifecycle plan as an administrator.')
print('These checks require Slack authorization; HTTP health does not prove integration access.')
raise SystemExit(1 if failed else 0)
