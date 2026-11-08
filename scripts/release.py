#!/usr/bin/env python3
"""Validate release identities and render Compose image overrides. No secrets."""
import argparse
import json
import re
from pathlib import Path

APPS = ('hawk-shop', 'hawk-mod', 'hawk-bot', 'hawk-eye')
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


def validate(data):
    if data.get('schema') != 1 or set(data.get('apps', {})) != set(APPS):
        raise ValueError('release must contain schema 1 and exactly the four suite apps')
    for app, item in data['apps'].items():
        if not SHA.fullmatch(item.get('revision', '')):
            raise ValueError(f'{app}: invalid source revision')
        if not DIGEST.fullmatch(item.get('digest', '')):
            raise ValueError(f'{app}: invalid image digest')
        if item.get('contract') not in (0, 1):
            raise ValueError(f'{app}: unsupported image contract')
    if 'authentication' in data:
        auth = data['authentication']
        if not isinstance(auth, dict) or set(auth) != {'digest', 'revision'}:
            raise ValueError('authentication requires digest and candidate source revision')
        if not DIGEST.fullmatch(auth.get('digest', '')) or not SHA.fullmatch(auth.get('revision', '')):
            raise ValueError('invalid authentication image identity')
    return data


def image(app, item):
    if app not in APPS:
        raise ValueError('unknown application')
    return f'ghcr.io/frc2713/{app}@{item["digest"]}'


def auth_image(data):
    return 'ghcr.io/frc2713/hawk-auth@' + data['authentication']['digest']


def render(data):
    validate(data)
    return '\n'.join(f'{app.upper().replace("-", "_")}_IMAGE={image(app, data["apps"][app])}' for app in APPS) + '\n' + (('HAWK_AUTH_IMAGE=' + auth_image(data) + '\n') if 'authentication' in data else '')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', nargs='?', default='releases/production.json')
    parser.add_argument('--env', action='store_true')
    parser.add_argument('--require-ready', action='store_true', help='refuse legacy baseline images before approval')
    parser.add_argument('--require-authentication', action='store_true')
    args = parser.parse_args()
    data = validate(json.loads(Path(args.release).read_text()))
    if args.require_authentication and 'authentication' not in data:
        parser.error('publish and pin the authentication candidate before deployment')
    if args.require_ready and any(item['contract'] != 1 for item in data['apps'].values()):
        parser.error('promote contract-v1 images for all four apps before requesting production approval')
    print(render(data) if args.env else json.dumps(data, indent=2), end='\n' if not args.env else '')
