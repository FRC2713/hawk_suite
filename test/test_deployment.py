import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import deploy
from release import APPS, image, render, validate

ROOT = Path(__file__).resolve().parents[1]


def release():
    data = json.loads((ROOT / 'releases/production.json').read_text())
    for item in data['apps'].values():
        item['contract'] = 1
    return data


class ReleaseTests(unittest.TestCase):
    def test_authentication_digest_and_source_are_validated(self):
        data = release()
        data['authentication'] = dict(digest='sha256:' + 'a' * 64, revision='b' * 40)
        self.assertIn('HAWK_AUTH_IMAGE=ghcr.io/frc2713/hawk-auth@sha256:', render(data))
        for field in ('digest', 'revision'):
            bad = copy.deepcopy(data)
            bad['authentication'][field] = 'latest'
            with self.assertRaises(ValueError):
                validate(bad)

    def test_image_injection_and_incomplete_release_are_rejected(self):
        data = release()
        data['apps']['hawk-bot']['digest'] = 'latest; echo unsafe'
        with self.assertRaises(ValueError):
            validate(data)
        data = release()
        del data['apps']['hawk-shop']
        with self.assertRaises(ValueError):
            validate(data)

    def test_release_references_are_allowlisted(self):
        text = render(release())
        self.assertEqual(len(text.splitlines()), 4)
        self.assertIn('HAWK_BOT_IMAGE=ghcr.io/frc2713/hawk-bot@sha256:', text)
        with self.assertRaises(ValueError):
            image('arbitrary-image', {})

    def test_health_does_not_accept_empty_missing_or_wrong_image(self):
        data = release()
        with patch.object(deploy, 'output', return_value=''):
            with self.assertRaises(ValueError):
                deploy.check_containers(['compose'], data, authenticated=False)
        rows = [{'Service': app, 'State': 'running', 'Health': 'healthy', 'ID': app} for app in ['caddy', *APPS]]
        for rows_bad in [rows[:-1], [dict(row, State='exited') for row in rows]]:
            with patch.object(deploy, 'output', return_value=json.dumps(rows_bad)):
                with self.assertRaises(ValueError):
                    deploy.check_containers(['compose'], data, authenticated=False)
        with patch.object(deploy, 'output', side_effect=[json.dumps(rows), 'expected', 'wrong']):
            with self.assertRaises(ValueError):
                deploy.check_containers(['compose'], data, authenticated=False)

    def test_authenticated_health_checks_gateway_image_as_well_as_apps(self):
        data = release()
        data['authentication'] = dict(digest='sha256:' + 'a' * 64, revision='b' * 40)
        rows = [{'Service': app, 'State': 'running', 'Health': 'healthy', 'ID': app}
                for app in ['caddy', *APPS]]
        with patch.object(deploy, 'output', side_effect=[json.dumps(rows), 'expected', 'wrong']):
            with self.assertRaisesRegex(ValueError, 'wrong image running: caddy'):
                deploy.check_containers(['compose'], data, authenticated=True)
        with patch.object(deploy, 'output', side_effect=[json.dumps(rows)] + ['expected', 'expected'] * len(rows)):
            deploy.check_containers(['compose'], data, authenticated=True)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = Path(self.temp.name)
        self.target = self.stack / 'target'
        (self.target / 'releases').mkdir(parents=True)
        (self.target / 'releases/production.json').write_text(json.dumps(release()))
        (self.stack / '.deploy').mkdir()
        (self.stack / '.env').write_text('TOKEN_ENCRYPTION_KEY=ORIGINAL\n')
        self.values = dict(DOMAIN='example.org', TOKEN_ENCRYPTION_KEY='ORIGINAL', HAWK_BOT_TOKEN_ENCRYPTION_KEY='bot',
                           HAWK_BOT_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64='google', BACKUP_ENCRYPTION_KEY='b' * 44)
        self.request = dict(suite_sha='1' * 40, env=self.values)

    def test_encryption_key_guard_never_mutates_environment(self):
        self.values['TOKEN_ENCRYPTION_KEY'] = 'CHANGED'
        with patch.object(deploy, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'differs'):
                deploy.main(self.stack, self.target, self.request)
            run.assert_not_called()
        self.assertEqual((self.stack / '.env').read_text(), 'TOKEN_ENCRYPTION_KEY=ORIGINAL\n')

    def test_authentication_cannot_be_removed_after_activation(self):
        active_release = release()
        active_release['authentication'] = dict(digest='sha256:' + 'a' * 64, revision='b' * 40)
        (self.stack / '.deploy/current.json').write_text(json.dumps(
            dict(release=active_release, env=str(self.stack / '.env'))))
        with patch.object(deploy, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'cannot remove authentication'):
                deploy.main(self.stack, self.target, self.request)
            run.assert_not_called()

    def test_authentication_preparation_failure_cleans_staged_secrets(self):
        data = release()
        data['authentication'] = dict(digest='sha256:' + 'a' * 64, revision='b' * 40)
        (self.target / 'releases/production.json').write_text(json.dumps(data))
        with patch.object(deploy, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'missing HAWK_AUTH'):
                deploy.main(self.stack, self.target, self.request)
            run.assert_not_called()
        self.assertEqual(list((self.stack / '.deploy/config').iterdir()), [])

    def test_authenticated_release_uses_overlay_exact_candidate_and_public_probe(self):
        data = release()
        data['authentication'] = dict(digest='sha256:' + 'a' * 64, revision='b' * 40)
        (self.target / 'releases/production.json').write_text(json.dumps(data))
        outputs = [json.dumps({'services': {app: {'environment': {}} for app in APPS}})]
        for app in APPS:
            outputs += [data['apps'][app]['revision'], json.dumps(dict(name=app, schema=1, required_env=[], optional_env=[]))]
        outputs.append('b' * 40)
        with patch.object(deploy, 'output', side_effect=outputs), patch.object(deploy, 'run') as run, \
                patch.object(deploy, 'prepare_auth', return_value={'HAWK_AUTH_CONFIG_DIR': '/private/auth', 'HAWK_AUTH_ASSET_DIR': '/assets'}), \
                patch.object(deploy.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as validate_run, \
                patch.object(deploy, 'backup', return_value=None), patch.object(deploy, 'check_containers'):
            deploy.main(self.stack, self.target, self.request)
        validation = validate_run.call_args
        self.assertTrue(validation.kwargs['capture_output'])
        self.assertIn(str(self.target / 'config/authentication/compose.yaml'), validation.args[0])
        self.assertTrue(any('verify-authentication.py' in str(call.args[0]) for call in run.call_args_list))
        record = json.loads((self.stack / '.deploy/current.json').read_text())
        self.assertIn('ghcr.io/frc2713/hawk-auth@sha256:', Path(record['env']).read_text())

    def test_missing_required_value_and_legacy_images_do_not_apply(self):
        for name in ('HAWK_BOT_TOKEN_ENCRYPTION_KEY', 'HAWK_BOT_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64', 'BACKUP_ENCRYPTION_KEY'):
            values = dict(self.values, **{name: ''})
            with self.subTest(name=name), patch.object(deploy, 'run') as run:
                with self.assertRaises(ValueError):
                    deploy.main(self.stack, self.target, dict(self.request, env=values))
                run.assert_not_called()
        data = release()
        data['apps']['hawk-bot']['contract'] = 0
        (self.target / 'releases/production.json').write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'bootstrap'):
            deploy.main(self.stack, self.target, self.request)

    def test_pull_failure_preserves_active_environment_and_removes_staged_secrets(self):
        config = {'services': {app: {'environment': {}} for app in APPS}}
        with patch.object(deploy, 'output', return_value=json.dumps(config)), patch.object(deploy, 'run', side_effect=RuntimeError('pull failed')):
            with self.assertRaises(RuntimeError):
                deploy.main(self.stack, self.target, self.request)
        self.assertEqual((self.stack / '.env').read_text(), 'TOKEN_ENCRYPTION_KEY=ORIGINAL\n')
        self.assertEqual(list((self.stack / '.deploy/config').iterdir()), [])
        self.assertFalse((self.stack / '.deploy/current.json').exists())

    def test_apply_failure_retains_backup_and_failed_record_without_promoting(self):
        data = release()
        config = {'services': {app: {'environment': {}} for app in APPS}}
        outputs = [json.dumps(config)]
        for app in APPS:
            outputs += [data['apps'][app]['revision'], json.dumps(dict(name=app, schema=1, required_env=[], optional_env=[]))]
        events = []
        def run(args, **kwargs):
            events.append(args)
            if 'up' in args:
                raise RuntimeError('migration or startup failed')
        with patch.object(deploy, 'output', side_effect=outputs), patch.object(deploy, 'run', side_effect=run), patch.object(deploy, 'backup', return_value='encrypted-backup') as backup:
            with self.assertRaises(RuntimeError):
                deploy.main(self.stack, self.target, self.request)
            backup.assert_called_once()
        self.assertTrue((self.stack / '.deploy/failed.json').exists())
        self.assertFalse((self.stack / '.deploy/current.json').exists())
        self.assertFalse(any('prune' in arg for event in events for arg in event))

    def test_bot_credential_rotation_promotes_a_secret_free_receipt(self):
        self.values['HAWK_BOT_TOKEN_ENCRYPTION_KEY'] = 'rotated-bot-key'
        self.values['HAWK_BOT_GOOGLE_SERVICE_ACCOUNT_KEY_BASE64'] = 'rotated-google-key'
        data = release()
        config = {'services': {app: {'environment': {}} for app in APPS}}
        outputs = [json.dumps(config)]
        for app in APPS:
            outputs += [data['apps'][app]['revision'], json.dumps(dict(name=app, schema=1, required_env=[], optional_env=[]))]
        with patch.object(deploy, 'output', side_effect=outputs), patch.object(deploy, 'run'), \
                patch.object(deploy, 'backup', return_value='encrypted-backup'), patch.object(deploy, 'check_containers'):
            deploy.main(self.stack, self.target, self.request)
        record = (self.stack / '.deploy/current.json').read_text()
        self.assertNotIn('rotated-bot-key', record)
        self.assertNotIn('rotated-google-key', record)
        config_file = Path(json.loads(record)['env'])
        self.assertIn('rotated-bot-key', config_file.read_text())
        self.assertEqual(config_file.stat().st_mode & 0o777, 0o600)

    def test_environment_rejects_multiline_and_malformed_names(self):
        for values in [{'TOKEN': 'a\nb'}, {'bad-name': 'a'}, {'TOKEN': 4}]:
            with self.assertRaises(ValueError):
                deploy.env_text(values)


class ComposeCredentialTests(unittest.TestCase):
    def test_auth_overlay_changes_only_gateway_and_keeps_keys_out_of_apps(self):
        import shutil
        if not shutil.which('docker'):
            self.skipTest('Docker CLI required')
        values = dict(DOMAIN='ci.example.org', HAWK_AUTH_IMAGE='ghcr.io/frc2713/hawk-auth@sha256:'+'a'*64,
                      HAWK_AUTH_CONFIG_DIR='/private/auth', HAWK_AUTH_ASSET_DIR='/reviewed/assets')
        with tempfile.NamedTemporaryFile(mode='w') as file:
            file.write(deploy.env_text(values))
            file.flush()
            command = ['docker','compose','--project-directory',str(ROOT),'--env-file',file.name,
                       '-f',str(ROOT/'docker-compose.yml')]
            base = json.loads(subprocess.check_output(command+['config','--format','json']))
            overlay = json.loads(subprocess.check_output(command+['-f',str(ROOT/'config/authentication/compose.yaml'),
                                                                 'config','--format','json']))
        for app in APPS:
            self.assertEqual(base['services'][app], overlay['services'][app])
        gateway = overlay['services']['caddy']
        self.assertEqual(gateway['image'], values['HAWK_AUTH_IMAGE'])
        mounts = {v['target']: v for v in gateway['volumes']}
        self.assertEqual(mounts['/etc/caddy/Caddyfile']['source'], '/private/auth/Caddyfile')
        self.assertTrue(mounts['/etc/hawk-auth']['read_only'])
        self.assertEqual(gateway['logging']['driver'], 'none')

    def test_credentials_survive_compose_and_literal_docker_environment(self):
        import shutil
        if not shutil.which('docker'):
            self.skipTest('Docker CLI required for Compose interpolation')
        for value in ["a$B \\ hi'yes", 'trailing\\', 'double"quotes', '$$cash', 'unicode:雪']:
            with self.subTest(value=value), tempfile.NamedTemporaryFile(mode='w') as file:
                file.write(deploy.env_text(dict(DOMAIN='ci.example.org', ONSHAPE_CLIENT_SECRET=value, TOKEN_ENCRYPTION_KEY=value)))
                file.flush()
                resolved = json.loads(subprocess.check_output(['docker', 'compose', '--project-directory', str(ROOT), '--env-file', file.name,
                                                              '-f', str(ROOT / 'docker-compose.yml'), 'config', '--format', 'json'],
                                                             env={'PATH': os.environ['PATH']}))
                actual = resolved['services']['hawk-shop']['environment']['ONSHAPE_CLIENT_SECRET'].replace('$$', '$')
                self.assertEqual(value, actual)
                self.assertEqual(value, deploy.old_token(Path(file.name)))


if __name__ == '__main__':
    unittest.main()
