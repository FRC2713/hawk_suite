import base64
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('auth_config', ROOT / 'scripts/auth_config.py')
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class AuthConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('openssl'):
            raise unittest.SkipTest('OpenSSL is required')
        cls.key = subprocess.check_output(['openssl', 'genpkey', '-algorithm', 'RSA',
                                          '-pkeyopt', 'rsa_keygen_bits:3072'], stderr=subprocess.DEVNULL)

    def values(self):
        return dict(DOMAIN='ci.example.org', HAWK_AUTH_SLACK_CLIENT_ID='123.456',
                    HAWK_AUTH_SLACK_TEAM_ID='TAPPROVED', HAWK_AUTH_SLACK_CLIENT_SECRET='a' * 32,
                    HAWK_AUTH_SIGNING_KEY_BASE64=base64.b64encode(self.key).decode())

    def test_private_material_and_rendered_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'auth'
            result = auth.prepare(ROOT, directory, self.values())
            self.assertEqual(result['HAWK_AUTH_CONFIG_DIR'], str(directory))
            self.assertEqual((directory / 'private.pem').read_bytes(), self.key)
            self.assertIn(b'PUBLIC KEY', (directory / 'public.pem').read_bytes())
            self.assertEqual(base64.b64decode(result['HAWK_AUTH_PUBLIC_KEY_BASE64']),
                             (directory / 'public.pem').read_bytes())
            config = (directory / 'Caddyfile').read_text()
            self.assertNotIn('__DOMAIN__', config)
            self.assertIn('slack/workspace/TAPPROVED', config)
            self.assertNotIn('PRIVATE KEY', config)
            self.assertIn('cookie domain ci.example.org', config)
            self.assertNotIn('cookie strip domain', config)
            self.assertIn('set auth url https://apps.ci.example.org/auth/', config)
            self.assertEqual(config.count('import auth-portal'), 1)
            if __import__('os').name != 'nt':
                for path in directory.iterdir():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_bad_credentials_and_config_injection_fail_before_files_exist(self):
        cases = [('HAWK_AUTH_SLACK_CLIENT_SECRET', ''), ('HAWK_AUTH_SLACK_CLIENT_ID', '123\n}'),
                 ('HAWK_AUTH_SLACK_TEAM_ID', 'TONE role admin'), ('DOMAIN', 'example.org {'),
                 ('HAWK_AUTH_SIGNING_KEY_BASE64', 'not-base64'),
                 ('HAWK_AUTH_SIGNING_KEY_BASE64', base64.b64encode(b'not a key').decode())]
        for name, value in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'auth'
                values = dict(self.values(), **{name: value})
                with self.assertRaises(ValueError):
                    auth.prepare(ROOT, directory, values)
                self.assertFalse(directory.exists())
