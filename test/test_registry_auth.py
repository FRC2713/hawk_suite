import base64
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deploy import registry_credentials


class RegistryCredentialsTests(unittest.TestCase):
    def test_ephemeral_credentials_are_private_and_cleaned_on_failure(self):
        with patch.dict(os.environ, {'DOCKER_CONFIG': '/existing/config'}):
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with registry_credentials({'username': 'runner', 'token': 'fixture-token'}):
                    directory = Path(os.environ['DOCKER_CONFIG'])
                    config = directory / 'config.json'
                    self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                    data = json.loads(config.read_text())
                    self.assertEqual(base64.b64decode(data['auths']['ghcr.io']['auth']).decode(), 'runner:fixture-token')
                    raise RuntimeError('fixture')
            self.assertFalse(directory.exists())
            self.assertEqual(os.environ['DOCKER_CONFIG'], '/existing/config')

    def test_absent_credentials_keep_existing_host_authentication(self):
        with patch.dict(os.environ, {'DOCKER_CONFIG': '/existing/config'}):
            with registry_credentials(None):
                self.assertEqual(os.environ['DOCKER_CONFIG'], '/existing/config')

    def test_environment_override_is_removed_after_success(self):
        with patch.dict(os.environ):
            os.environ.pop('DOCKER_CONFIG', None)
            with registry_credentials({'username': 'runner', 'token': 'fixture-token'}):
                directory = Path(os.environ['DOCKER_CONFIG'])
            self.assertNotIn('DOCKER_CONFIG', os.environ)
            self.assertFalse(directory.exists())

    def test_invalid_auth_is_refused_without_exposing_values(self):
        for auth in [{}, {'username': 'runner'}, {'username': 'bad:user', 'token': 'fixture-token'},
                     {'username': 'runner', 'token': 'token\nvalue'}, {'username': 'runner', 'token': 1}]:
            with self.subTest(auth=auth), self.assertRaisesRegex(ValueError, '^invalid registry credentials$'):
                with registry_credentials(auth):
                    self.fail('invalid credentials were accepted')
