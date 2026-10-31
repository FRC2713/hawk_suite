#!/usr/bin/env python3
"""Apply a staged release. Called only by the restricted launcher."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from release import APPS, image, validate
from backup_auth import backup_tag


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(args):
    return subprocess.check_output(args, text=True).strip()


def env_text(values):
    lines = []
    for name, value in values.items():
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name) or not isinstance(value, str) or any(ord(c) < 32 for c in value):
            raise ValueError('invalid environment entry')
        lines.append(name + "=" + json.dumps(value, ensure_ascii=False).replace("$", "$$"))
    return '\n'.join(lines) + '\n'


def old_token(path):
    if not path.exists():
        return ''
    for line in path.read_text().splitlines():
        if line.startswith('TOKEN_ENCRYPTION_KEY='):
            value = line.split('=', 1)[1]
            if value.startswith(chr(34)):
                return json.loads(value).replace('$$', '$')
            if value.startswith("'") and value.endswith("'"):
                value = value[1:-1].replace("\\'", "'").replace('\\\\', '\\')
            return value
    return ''


def check_containers(compose, release):
    raw = output(compose + ['ps', '--all', '--format', 'json'])
    # Compose versions emit either a JSON array or one JSON object per line.
    rows = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines() if line]
    actual = {row['Service']: row for row in rows}
    if set(actual) != {'caddy', *APPS}:
        raise ValueError('missing or unexpected containers')
    for app, row in actual.items():
        if row['State'] != 'running' or (app != 'caddy' and row.get('Health') != 'healthy'):
            raise ValueError('container not ready: ' + app)
        if app in APPS:
            expected = output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image(app, release['apps'][app])])
            running = output(['docker', 'inspect', '--format', '{{.Image}}', row['ID']])
            if running != expected:
                raise ValueError('wrong image running: ' + app)


def backup(state, backup_id, key, project="hawk-suite"):
    """Use the currently running images to back up the pre-migration schema."""
    dest = state / 'backups'
    dest.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=state) as scratch:
        scratch = Path(scratch)
        found = []
        for app in APPS:
            ids = output(['docker', 'ps', '--all', '--filter', 'label=com.docker.compose.project=' + project,
                          '--filter', 'label=com.docker.compose.service=' + app, '--format', '{{.ID}}']).splitlines()
            if not ids:
                volume = app.replace('-', '_') + '_data'
                orphaned = output(['docker', 'volume', 'ls', '--filter', 'label=com.docker.compose.project=' + project,
                                   '--filter', 'label=com.docker.compose.volume=' + volume, '--format', '{{.Name}}'])
                if orphaned:
                    raise ValueError('existing volume without its prior container; arrange a snapshot before deployment: ' + app)
                continue  # A fresh install has no prior state.
            if len(ids) != 1:
                raise ValueError('ambiguous existing service: ' + app)
            cid = ids[0]
            target = f'/data/.hawk-backup-{backup_id}.db'
            module = './.output/server/node_modules/better-sqlite3' if app == 'hawk-shop' else 'better-sqlite3'
            # Open read-only: never initialize migrations in the old application.
            script = "const D=require(process.argv[1]);const d=new D('/data/'+process.argv[2]+'.db',{readonly:true,fileMustExist:true});d.backup(process.argv[3]).then(()=>d.close()).catch(()=>process.exit(1));"
            run(['docker', 'exec', cid, 'node', '-e', script, module, app, target], stdout=subprocess.DEVNULL)
            try:
                run(['docker', 'cp', cid + ':' + target, str(scratch / (app + '.db'))], stdout=subprocess.DEVNULL)
                if app == 'hawk-shop':
                    run(['docker', 'cp', cid + ':/data/uploads', str(scratch / 'uploads')], stdout=subprocess.DEVNULL)
            finally:
                run(['docker', 'exec', cid, 'node', '-e', 'require("node:fs").unlinkSync(process.argv[1])', target])
            found.append(app)
        if not found:
            return None
        # Plaintext exists only in this private temporary directory and is removed
        # on every exit. Only the encrypted archive is retained on the host.
        archive = scratch / 'state.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for path in scratch.iterdir():
                if path != archive:
                    tar.add(path, arcname=path.name)
        encrypted = dest / (backup_id + '.tar.gz.enc')
        env = dict(os.environ, HAWK_BACKUP_KEY=key)
        run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-in', str(archive), '-out', str(encrypted),
             '-pass', 'env:HAWK_BACKUP_KEY'], env=env)
        tag = backup_tag(encrypted, key)
        encrypted.with_suffix(encrypted.suffix + '.hmac').write_text(tag + '\n')
        return str(encrypted)


def main(stack, target, request):
    state = stack / '.deploy'
    values = request['env']
    # Validate before staging, pulling or touching the active environment.
    env_text(values)
    for name in ('DOMAIN', 'TOKEN_ENCRYPTION_KEY', 'HAWK_BOT_TOKEN_ENCRYPTION_KEY',
                 'HAWK_BOT_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64', 'BACKUP_ENCRYPTION_KEY'):
        if not values.get(name):
            raise ValueError('missing ' + name)
    if len(values['BACKUP_ENCRYPTION_KEY']) < 32:
        raise ValueError('BACKUP_ENCRYPTION_KEY must be at least 32 characters')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', values['DOMAIN']):
        raise ValueError('invalid DOMAIN')
    active = json.loads((state / 'current.json').read_text()) if (state / 'current.json').exists() else None
    previous_env = Path(active['env']) if active else stack / '.env'
    old = old_token(previous_env)
    if old and old != values['TOKEN_ENCRYPTION_KEY']:
        raise ValueError('TOKEN_ENCRYPTION_KEY differs from the host')
    release = validate(json.loads((target / 'releases/production.json').read_text()))
    if any(item['contract'] != 1 for item in release['apps'].values()):
        raise ValueError('bootstrap release: promote contract-v1 images for all apps first')
    if any(name.endswith(('_IMAGE', '_TAG')) for name in values):
        raise ValueError('image overrides belong in the release file')
    sha = request['suite_sha']
    release_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + sha[:12]
    config_dir = state / 'config' / release_id
    config_dir.mkdir(parents=True, mode=0o700)
    envfile = config_dir / 'stack.env'
    resolved = dict(values)
    for app in APPS:
        resolved[app.upper().replace('-', '_') + '_IMAGE'] = image(app, release['apps'][app])
    envfile.write_text(env_text(resolved))
    os.chmod(envfile, 0o600)
    compose = ['docker', 'compose', '--project-name', 'hawk-suite', '--project-directory', str(target),
               '--env-file', str(envfile), '-f', str(target / 'docker-compose.yml')]
    applied = False
    saved = None
    try:
        config = json.loads(output(compose + ['config', '--format', 'json']))
        run(compose + ['pull', '--quiet'])
        for app in APPS:
            ref = image(app, release['apps'][app])
            revision = output(['docker', 'image', 'inspect', '--format', '{{index .Config.Labels "org.opencontainers.image.revision"}}', ref])
            if revision != release['apps'][app]['revision']:
                raise ValueError('source revision mismatch: ' + app)
            # docker --env-file uses literal values, unlike Compose dotenv files.
            appenv = config['services'][app]['environment']
            raw_env = config_dir / (app + '.env')
            raw_env.write_text(''.join(f'{k}={str(v).replace(chr(36) * 2, chr(36))}\n' for k, v in appenv.items()))
            os.chmod(raw_env, 0o600)
            contract = json.loads(output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'node', ref,
                                          'scripts/deploy-check.mjs', '--contract']))
            if contract['name'] != app or contract['schema'] != 1:
                raise ValueError('invalid image contract: ' + app)
            unknown = set(appenv) - set(contract['required_env']) - set(contract['optional_env'])
            if unknown:
                raise ValueError('unrecognized app environment: ' + app + ' ' + ','.join(sorted(unknown)))
            run(['docker', 'run', '--rm', '--network', 'none', '--env-file', str(raw_env), '--entrypoint', 'node', ref,
                 'scripts/deploy-check.mjs'])
            raw_env.unlink()
        # Validate proposed routing without touching the running Caddy process.
        run(['docker', 'run', '--rm', '-e', 'DOMAIN=' + values['DOMAIN'], '-v', str(target / 'Caddyfile') + ':/etc/caddy/Caddyfile:ro',
             'caddy:2-alpine', 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
        saved = backup(state, release_id, values['BACKUP_ENCRYPTION_KEY'])
        # Applying migrations is the point after which image-only recovery may
        # be unsafe. Do not silently restore a database or restart an older image.
        applied = True
        run(compose + ['up', '-d', '--remove-orphans', '--wait', '--wait-timeout', '180'])
        run(compose + ['exec', '-T', 'caddy', 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile'])
        check_containers(compose, release)
        run([str(target / 'scripts/verify-https.sh')], env=dict(os.environ, DOMAIN=values['DOMAIN']))
        record = dict(suite_sha=sha, release=release, env=str(envfile), directory=str(target), backup=saved, id=release_id)
        (config_dir / 'record.json').write_text(json.dumps(record, indent=2) + '\n')
        if active:
            shutil.copyfile(state / 'current.json', state / 'previous.json')
        temp = state / 'current.tmp'
        temp.write_text(json.dumps(record, indent=2) + '\n')
        os.replace(temp, state / 'current.json')
        (state / 'failed.json').unlink(missing_ok=True)
        # Secret-free receipt is forwarded to the Actions summary over SSH stdout.
        print('HAWK_RECEIPT=' + json.dumps({k: v for k, v in record.items() if k not in ('env', 'directory')}))
    except Exception:
        if applied:
            (state / 'failed.json').write_text(json.dumps(dict(suite_sha=sha, env=str(envfile), directory=str(target), id=release_id, backup=saved, previous=active)) + '\n')
            print('Release failed after apply. Previous images and encrypted backups retained; review schema compatibility before recovery.', file=sys.stderr)
        else:
            shutil.rmtree(config_dir)
            print('Release failed preflight. Active configuration was not changed.', file=sys.stderr)
        raise


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main(Path(sys.argv[1]), Path(sys.argv[2]), json.load(sys.stdin))
    except Exception as error:
        # Do not render subprocess args/configuration or credential parse errors.
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        print('Deployment failed: ' + detail, file=sys.stderr)
        sys.exit(1)
