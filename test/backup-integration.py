#!/usr/bin/env python3
"""Exercise the real SQLite backup, encryption and review-only recovery path.

All containers and volumes are disposable and use a unique Compose project label.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deploy import backup
from release import APPS, image, validate

root = Path(__file__).resolve().parents[1]
release = validate(json.loads((root / 'releases/production.json').read_text()))
project = 'hawk-backup-test-' + uuid.uuid4().hex[:12]
key = 'integration-only-key-with-no-production-data'
containers = []
fixture = dict(SLACK_SIGNING_SECRET='fixture', SLACK_CLIENT_ID='fixture', SLACK_CLIENT_SECRET='fixture',
               SLACK_STATE_SECRET='fixture-state-secret', PUBLIC_URL='https://fixture.example.org',
               TOKEN_ENCRYPTION_KEY='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
               GOOGLE_SERVICE_ACCOUNT_KEY_BASE64='fixture', ONSHAPE_CLIENT_ID='fixture', ONSHAPE_CLIENT_SECRET='fixture',
               APP_URL='https://fixture.example.org', ONSHAPE_REDIRECT_URI='https://fixture.example.org/auth/onshape/callback')
try:
    for app in APPS:
        ref = os.environ.get(app.upper().replace('-', '_') + '_TEST_IMAGE') or image(app, release['apps'][app])
        if not os.environ.get(app.upper().replace('-', '_') + '_TEST_IMAGE'):
            subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', ref], check=True, stdout=subprocess.DEVNULL)
        args = ['docker', 'run', '-d', '--network', 'none', '--health-interval', '1s', '--health-start-period', '1s',
                '--label', 'com.docker.compose.project=' + project, '--label', 'com.docker.compose.service=' + app]
        for name, value in fixture.items():
            args += ['-e', name + '=' + value]
        cid = subprocess.check_output(args + [ref], text=True).strip()
        containers.append(cid)
        for _ in range(90):
            health = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Health.Status}}', cid], text=True).strip()
            if health == 'healthy':
                break
            time.sleep(1)
        else:
            raise RuntimeError('fixture container did not become healthy: ' + app)
    with tempfile.TemporaryDirectory() as scratch:
        state = Path(scratch)
        archive = backup(state, 'integration', key, project=project)
        restore = [sys.executable, str(root / 'scripts/restore-backup.py'), archive, str(state / 'recovered')]
        subprocess.run(restore, check=True, env=dict(os.environ, BACKUP_ENCRYPTION_KEY=key))
        import sqlite3
        for app in APPS:
            conn = sqlite3.connect(state / 'recovered' / (app + '.db'))
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            conn.close()
        assert (state / 'recovered/uploads').is_dir()
        assert not list(state.glob('tmp*'))
        # A changed archive must fail authentication before creating plaintext.
        with open(archive, 'ab') as file:
            file.write(b'tampered')
        rejected = state / 'rejected'
        result = subprocess.run([*restore[:-1], str(rejected)], env=dict(os.environ, BACKUP_ENCRYPTION_KEY=key),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert result.returncode != 0 and not rejected.exists()
    print('SQLite snapshots, uploads, encryption, integrity and tamper rejection passed.')
finally:
    for cid in containers:
        subprocess.run(['docker', 'rm', '-fv', cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
