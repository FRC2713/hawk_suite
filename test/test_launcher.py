import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('launcher', str(ROOT / 'scripts/hawk-deploy'))
spec = importlib.util.spec_from_loader('launcher', loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_stale_approval_never_executes_deployment_or_rewrites_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            request = json.dumps(dict(suite_sha='1' * 40, env={'TOKEN_ENCRYPTION_KEY': 'fixture'})).encode()
            stdin = io.TextIOWrapper(io.BytesIO(request))
            with patch.dict(os.environ, STACK_DIR=temp), patch.object(launcher.sys, 'stdin', stdin), \
                    patch.object(launcher.subprocess, 'check_output', return_value='2' * 40), patch.object(launcher.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'stale'):
                    launcher.main()
                self.assertEqual(run.call_count, 1)
                self.assertIn('fetch', run.call_args.args[0])
                self.assertFalse((Path(temp) / '.deploy/releases').exists())

    def test_malformed_revision_cannot_become_a_git_argument(self):
        with tempfile.TemporaryDirectory() as temp:
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps(dict(suite_sha='--upload-pack=unsafe', env={})).encode()))
            with patch.dict(os.environ, STACK_DIR=temp), patch.object(launcher.sys, 'stdin', stdin), patch.object(launcher.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    launcher.main()
                run.assert_not_called()
