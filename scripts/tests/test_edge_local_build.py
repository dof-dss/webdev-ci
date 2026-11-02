#!/usr/bin/env python3
"""Offline local Edge build checks using fake tools, with no Git repository."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class LocalBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls'
        self.log.touch()
        self.env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                    'TEST_LOG': str(self.log), 'EDGE_SKIP_PUBLISH': 'true',
                    'EDGE_PACKAGES': 'dof-dss/fixture:dev-main other/package:^2',
                    'THEME_BUILD_ENABLED': 'false'}
        self.tool(self.bin / 'git', 'echo git >> "$TEST_LOG"\nexit 99\n')
        self.tool(self.bin / 'composer',
                  'printf "composer <%s>\\n" "$@" >> "$TEST_LOG"\n'
                  'exit "${COMPOSER_EXIT:-0}"\n')
        self.tool(self.checkout / 'theme.sh',
                  'printf "theme <%s>\\n" "$@" >> "$TEST_LOG"\n'
                  'exit "${THEME_EXIT:-0}"\n')

    def tool(self, path, body):
        path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body)
        path.chmod(0o755)

    def build(self, **values):
        return subprocess.run(['bash', str(ROOT / 'scripts/edge/build.sh')],
                              cwd=self.checkout, env={**self.env, **values},
                              text=True, capture_output=True)

    def test_build_without_git_metadata_or_branches(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('publication skipped', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), [
            'composer <require>', 'composer <--no-interaction>',
            'composer <dof-dss/fixture:dev-main>', 'composer <other/package:^2>'])

    def test_theme_explicitly_enabled_and_sites_argument(self):
        result = self.build(THEME_BUILD_ENABLED='true', THEME_BUILD_SCRIPT='theme.sh',
                            THEME_SITES_DIR='web/sites with spaces')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text()
        self.assertIn('theme <--sites-dir>', calls)
        self.assertIn(f'theme <{self.checkout.resolve()}/web/sites with spaces>', calls)
        self.assertNotIn('git', calls)

    def test_false_ignores_configured_theme(self):
        result = self.build(THEME_BUILD_SCRIPT='theme.sh', THEME_EXIT='7')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('theme', self.log.read_text())

    def test_legacy_unset_flag_uses_configured_script(self):
        del self.env['THEME_BUILD_ENABLED']
        result = self.build(THEME_BUILD_SCRIPT='theme.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('theme <--sites-dir>', self.log.read_text())

    def test_composer_failure_stops_before_theme_and_success(self):
        result = self.build(COMPOSER_EXIT='23', THEME_BUILD_ENABLED='true', THEME_BUILD_SCRIPT='theme.sh')
        self.assertEqual(result.returncode, 23)
        self.assertNotIn('theme', self.log.read_text())
        self.assertNotIn('complete', result.stdout)

    def test_theme_failure_propagates(self):
        result = self.build(THEME_EXIT='7', THEME_BUILD_ENABLED='true', THEME_BUILD_SCRIPT='theme.sh')
        self.assertEqual(result.returncode, 7)
        self.assertNotIn('git', self.log.read_text())
        self.assertNotIn('complete', result.stdout)

    def test_invalid_flags_fail_before_any_work(self):
        for flag in ('EDGE_SKIP_PUBLISH', 'THEME_BUILD_ENABLED'):
            for value in ('', 'TRUE', 'yes', 'true; git push', '$(git push)'):
                with self.subTest(flag=flag, value=value):
                    result = self.build(**{flag: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.log.read_text(), '')

    def test_unsafe_or_missing_theme_fails_before_composer(self):
        for script in ('', '/tmp/theme.sh', '../theme.sh', 'a/../theme.sh', 'missing.sh'):
            with self.subTest(script=script):
                result = self.build(THEME_BUILD_ENABLED='true', THEME_BUILD_SCRIPT=script)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.log.read_text(), '')

    def test_theme_symlink_escape_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.tool(outside / 'theme.sh', 'echo escaped >> "$TEST_LOG"\n')
        (self.checkout / 'escape').symlink_to(outside, target_is_directory=True)
        (self.checkout / 'escape.sh').symlink_to(outside / 'theme.sh')
        for script in ('escape/theme.sh', 'escape.sh'):
            with self.subTest(script=script):
                result = self.build(THEME_BUILD_ENABLED='true', THEME_BUILD_SCRIPT=script)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.log.read_text(), '')

    def test_hosted_publication_remains_default_and_uses_shared_build(self):
        self.tool(self.bin / 'git',
                  'printf "git %s\\n" "$*" >> "$TEST_LOG"\n'
                  'if [[ "$1" == rev-parse ]]; then echo fixture-sha; fi\n')
        del self.env['EDGE_SKIP_PUBLISH']
        result = self.build(EDGE_BRANCH='edge', SOURCE_BRANCH='development',
                            THEME_BUILD_ENABLED='true', THEME_BUILD_SCRIPT='theme.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text()
        self.assertIn('git fetch origin development', calls)
        self.assertIn('composer <require>', calls)
        self.assertIn('theme <--sites-dir>', calls)
        self.assertIn('git push --force-with-lease=refs/heads/edge: origin HEAD:refs/heads/edge', calls)
        self.assertIn('Published Edge commit fixture-sha', result.stdout)


if __name__ == '__main__':
    unittest.main()
