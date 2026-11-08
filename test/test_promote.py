import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import promote

ROOT = Path(__file__).resolve().parents[1]
NEW_SHA = 'a' * 40
NEW_DIGEST = 'sha256:' + 'b' * 64


def fake_gh(compare='ahead', digests=(NEW_DIGEST,)):
    def gh(*args):
        path = next(a for a in args if not a.startswith('-') and '/' in a)
        if '/compare/' in path:
            return compare + '\n'
        if '/commits/' in path:
            return NEW_SHA + '\n'
        if '/packages/' in path:
            return ''.join(d + '\n' for d in digests)
        raise AssertionError(path)
    return gh


class PromoteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.release = Path(self.dir.name) / 'production.json'
        self.release.write_text((ROOT / 'releases/production.json').read_text())

    def tearDown(self):
        self.dir.cleanup()

    def run_promote(self, *targets, **gh_kwargs):
        with patch.object(promote, 'gh', fake_gh(**gh_kwargs)):
            return promote.main([*targets, '--release', str(self.release)])

    def test_changes_only_the_named_app(self):
        before = json.loads(self.release.read_text())
        self.assertEqual(self.run_promote('hawk-eye'), 0)
        after = json.loads(self.release.read_text())
        self.assertEqual(after['apps']['hawk-eye'], dict(revision=NEW_SHA, digest=NEW_DIGEST, contract=1))
        for app in ('hawk-shop', 'hawk-mod', 'hawk-bot'):
            self.assertEqual(after['apps'][app], before['apps'][app])

    def test_refuses_commits_not_on_main(self):
        before = self.release.read_text()
        self.assertEqual(self.run_promote('hawk-eye@abcdef1', compare='diverged'), 1)
        self.assertEqual(self.release.read_text(), before)

    def test_refuses_unpublished_or_ambiguous_images(self):
        before = self.release.read_text()
        self.assertEqual(self.run_promote('hawk-bot', digests=()), 1)
        self.assertEqual(self.run_promote('hawk-bot', digests=(NEW_DIGEST, 'sha256:' + 'c' * 64)), 1)
        self.assertEqual(self.release.read_text(), before)

    def test_rejects_unknown_apps_bad_revisions_and_duplicates(self):
        for targets in (['qrscout'], ['hawk-eye@main;rm'], ['hawk-eye', 'hawk-eye@abcdef1']):
            self.assertEqual(self.run_promote(*targets), 1, targets)

    def test_missing_scope_names_the_fix(self):
        failed = type('R', (), dict(returncode=1, stdout='', stderr='You need at least read:packages scope'))()
        with patch.object(promote.subprocess, 'run', return_value=failed):
            with self.assertRaisesRegex(promote.PromoteError, 'gh auth refresh'):
                promote.gh('orgs/FRC2713/packages/container/hawk-eye/versions')


if __name__ == '__main__':
    unittest.main()
