"""Prepare private, release-specific auth material without exposing credentials."""
import base64
import os
from pathlib import Path
import re
import subprocess

SECRET_NAMES = ('HAWK_AUTH_SLACK_CLIENT_SECRET', 'HAWK_AUTH_SIGNING_KEY_BASE64')
REQUIRED = ('HAWK_AUTH_SLACK_CLIENT_ID', 'HAWK_AUTH_SLACK_TEAM_ID', *SECRET_NAMES)


def checked(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        raise ValueError('authentication key validation failed')
    return result.stdout


def prepare(target, directory, values):
    for name in REQUIRED:
        if not values.get(name):
            raise ValueError('missing ' + name)
    domain = values['DOMAIN'].lower()
    if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', domain):
        raise ValueError('invalid authentication domain')
    if not re.fullmatch(r'[0-9]+\.[0-9]+', values['HAWK_AUTH_SLACK_CLIENT_ID']):
        raise ValueError('invalid authentication client ID')
    if not re.fullmatch(r'T[A-Z0-9]+', values['HAWK_AUTH_SLACK_TEAM_ID']):
        raise ValueError('invalid authentication workspace ID')
    if not re.fullmatch(r'[a-fA-F0-9]{32,128}', values['HAWK_AUTH_SLACK_CLIENT_SECRET']):
        raise ValueError('invalid authentication client secret format')
    try:
        key = base64.b64decode(values['HAWK_AUTH_SIGNING_KEY_BASE64'], validate=True)
    except (ValueError, TypeError):
        raise ValueError('invalid authentication signing key encoding') from None
    # Require an unencrypted RSA key >=3072 bits. Never print OpenSSL errors.
    public = checked(['openssl', 'rsa', '-pubout', '-passin', 'pass:'], input=key)
    info = checked(['openssl', 'pkey', '-pubin', '-text', '-noout'], input=public)
    bits = re.search(rb'Public-Key: \((\d+) bit\)', info)
    if not bits or int(bits.group(1)) < 3072:
        raise ValueError('authentication RSA key must be at least 3072 bits')
    directory = Path(directory)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError('authentication config directory already exists: ' + str(directory)) from None
    files = {'private.pem': key, 'public.pem': public}
    replacements = {'DOMAIN': domain, 'CLIENT_ID': values['HAWK_AUTH_SLACK_CLIENT_ID'],
                    'TEAM_ID': values['HAWK_AUTH_SLACK_TEAM_ID'],
                    'CLIENT_SECRET': values['HAWK_AUTH_SLACK_CLIENT_SECRET']}
    for source, dest in [('Caddyfile.template', 'Caddyfile'), ('Caddyfile.maintenance', 'Caddyfile.maintenance'),
                         ('portal.template', 'portal.template'), ('login.template', 'login.template')]:
        text = (Path(target) / 'config/authentication' / source).read_text(encoding='utf-8')
        for name, value in replacements.items():
            text = text.replace('__' + name + '__', value)
        files[dest] = text.encode()
    for name, content in files.items():
        path = directory / name
        path.write_bytes(content)
        os.chmod(path, 0o600)
    return {'HAWK_AUTH_CONFIG_DIR': str(directory),
            'HAWK_AUTH_ASSET_DIR': str(Path(target) / 'config/authentication/assets')}
