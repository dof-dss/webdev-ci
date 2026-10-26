#!/usr/bin/env python3
"""Offline regression tests. Git repositories are temporary; platform/Composer
and Drush are fake executables. No credentials or network are used.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/bash\nset -euo pipefail\n' + text)
    path.chmod(0o755)


class ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = yaml.safe_load(subprocess.check_output(
            ['circleci', 'config', 'pack', str(ROOT / 'config/application')], text=True))

    def test_native_pack_layout(self):
        doc = self.document
        self.assertEqual(set(doc['workflows']), {'build-test', 'build-edge', 'build-edge-finalise'})
        for section in ('jobs', 'commands', 'workflows'):
            for path in (ROOT / 'config/application' / section).glob('*.yml'):
                self.assertEqual(doc[section][path.stem], yaml.safe_load(path.read_text()))
        for definition in doc['workflows'].values():
            invoked = {name for job in definition['jobs'] for name in job}
            for job in definition['jobs']:
                name, arguments = next(iter(job.items()))
                self.assertIn(name, doc['jobs'])
                for requirement in arguments.get('requires', []):
                    self.assertIn(requirement, invoked)
                declared = doc['jobs'][name].get('parameters', {})
                self.assertFalse(set(arguments) - set(declared) - {'requires', 'filters'})
                for key, declaration in declared.items():
                    if 'default' not in declaration:
                        self.assertIn(key, arguments)
        self.assertNotIn('requires', doc['workflows']['build-edge-finalise']['jobs'][0]['edge_refresh_shared'])

    def test_legacy_and_application_entry_points_are_separate(self):
        legacy = yaml.safe_load((ROOT / 'shared-config.yml').read_text())
        fixture = yaml.safe_load((ROOT / '.circleci/fixtures/legacy-workflows.yml').read_text())
        for definition in fixture['workflows'].values():
            for entry in definition['jobs']:
                name = next(iter(entry))
                self.assertIn(name, legacy['jobs'])
                self.assertNotIn(name, {'edge_build_shared', 'edge_refresh_shared'})
        self.assertFalse({'build_edge', 'sync_data', 'unity_edge_build', 'unity_sync_data'}
                         & set(self.document['jobs']))
        self.assertNotIn('composer_tasks__edge_packages', self.document['commands'])
        repository_ci = yaml.safe_load((ROOT / '.circleci/config.yml').read_text())
        jobs = repository_ci['workflows']['validate-configurations']['jobs']
        self.assertEqual(set(jobs), {'validate-legacy-config', 'validate-application-config'})
        for name in jobs:
            self.assertIn(name, repository_ci['jobs'])

    def test_all_parameter_files_match_native_declarations(self):
        declarations = self.document['parameters']
        for path in (ROOT / 'examples/application').glob('*.json'):
            with self.subTest(project=path.stem):
                values = json.loads(path.read_text())
                self.assertFalse(set(values) - set(declarations))
                for key, value in values.items():
                    declaration = declarations[key]
                    if declaration['type'] == 'enum':
                        self.assertIn(value, declaration['enum'])
                    else:
                        self.assertIsInstance(value, bool if declaration['type'] == 'boolean' else str)
                self.assertTrue(values['edge_branch'])
                self.assertTrue(values['edge_build_dofdss_packages'])

    def test_workflow_selection(self):
        def selected(action, branch, trigger, schedule):
            substitutions = {
                '<< pipeline.parameters.ci_action >>': action,
                '<< pipeline.parameters.source_branch >>': 'development',
                '<< pipeline.git.branch >>': branch,
                '<< pipeline.trigger_source >>': trigger,
                '<< pipeline.schedule.name >>': schedule,
            }
            def evaluate(condition):
                if isinstance(condition, str):
                    return substitutions.get(condition, condition)
                operator, value = next(iter(condition.items()))
                if operator == 'equal':
                    return evaluate(value[0]) == evaluate(value[1])
                if operator == 'not':
                    return not evaluate(value)
                if operator == 'and':
                    return all(evaluate(item) for item in value)
                if operator == 'or':
                    return any(evaluate(item) for item in value)
                raise AssertionError(operator)
            return {name for name, wf in self.document['workflows'].items() if evaluate(wf['when'])}
        self.assertEqual(selected('auto', 'development', 'webhook', ''), set())
        self.assertEqual(selected('auto', 'feature', 'webhook', ''), {'build-test'})
        self.assertEqual(selected('auto', 'development', 'scheduled_pipeline', 'edge_build'), {'build-edge'})
        self.assertEqual(selected('auto', 'development', 'scheduled_pipeline', 'edge_build_finalise'), {'build-edge-finalise'})
        self.assertEqual(selected('auto', 'edge', 'api', ''), set())
        for action, name in [('checks', 'build-test'), ('edge-build', 'build-edge'), ('edge-refresh', 'build-edge-finalise')]:
            self.assertEqual(selected(action, 'development', 'api', ''), {name})

    def test_latest_main_loader_with_local_archive(self):
        import shutil
        import tarfile
        command = self.document['commands']['prepare_shared_tools']['steps'][0]['run']['command']
        subprocess.run(['bash', '-n'], input=command, text=True, check=True)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = root / 'fixture'
            (fixture / 'scripts/edge').mkdir(parents=True)
            (fixture / 'scripts/edge/build.sh').write_text('fixture build')
            (fixture / 'phpcs.sh').write_text('fixture phpcs')
            archive = root / 'main.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                tar.add(fixture, arcname='webdev-ci-main')
            executable(root / 'bin/curl',
                '[[ "$*" == "-fsSL https://github.com/dof-dss/webdev-ci/archive/refs/heads/main.tar.gz" ]]\n'
                'cat "$FIXTURE_ARCHIVE"\n')
            env_file = root / 'bash-env'
            env_file.touch()
            result = subprocess.run(['bash', '-c', command + '\nprintf "%s" "$shared_dir"'],
                env={**os.environ, 'PATH': f'{root}/bin:{os.environ["PATH"]}',
                     'BASH_ENV': str(env_file), 'FIXTURE_ARCHIVE': str(archive)},
                capture_output=True, text=True, check=True)
            downloaded = Path(result.stdout)
            try:
                self.assertEqual((downloaded / 'scripts/edge/build.sh').read_text(), 'fixture build')
                self.assertEqual((downloaded / 'phpcs.sh').read_text(), 'fixture phpcs')
                self.assertIn('CI_SHARED_TOOLS=', env_file.read_text())
                self.assertIn('CI_SHARED_ROOT=', env_file.read_text())
            finally:
                shutil.rmtree(downloaded)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'calls'
        self.env = {**os.environ, 'PATH': f'{self.root}/bin:{os.environ["PATH"]}',
                    'TEST_LOG': str(self.log), 'PLATFORM_APP_DIR': str(self.root / 'app'),
                    'PLATFORM_PROJECT': 'fixture', 'PLATFORM_ENVIRONMENT_TYPE': 'development',
                    'PLATFORM_BRANCH': 'edge', 'EDGE_BRANCH': 'edge', 'EDGE_ENVIRONMENT': 'edge',
                    'CI_SHARED_TOOLS': str(ROOT / 'scripts/edge'), 'EDGE_LAYOUT': 'multisite',
                    'EDGE_PREFLIGHT': 'required', 'EDGE_DEPLOY': 'standard', 'EDGE_CONTENT': 'structure-sync',
                    'EDGE_FASTLY': 'keep', 'EDGE_QA_STAFF': 'false', 'QA_PASSWORD': 'fixture-only'}
        for site in ('alpha', 'beta'):
            path = self.root / 'app/project/sites' / site
            path.mkdir(parents=True)
            (path / 'settings.php').touch()
        executable(self.root / 'app/vendor/bin/drush', '''
printf 'drush %s\\n' "$*" >> "$TEST_LOG"
if [[ -n "${FAIL_COMMAND:-}" && "$*" == *"$FAIL_COMMAND"* ]]; then exit 9; fi
if [[ "$*" == *moduleExists* ]]; then echo yes; fi
if [[ "${QA_LOG_ERROR:-false}" == true && "$*" == *password_qa_accounts* ]]; then echo '[error] Password update failed'; fi
''')
        executable(self.root / 'bin/platform', '''
printf 'platform %s\\n' "$1" >> "$TEST_LOG"
if [[ "$1" == sync ]]; then
  [[ "${FAIL_SYNC:-false}" != true ]]
elif [[ "$1" == ssh ]]; then
  bash -c "${!#}"
else
  exit 99
fi
''')

    def run_refresh(self, **overrides):
        return subprocess.run(['bash', str(ROOT / 'scripts/edge/refresh.sh')], env={**self.env, **overrides}, capture_output=True, text=True)

    def test_multisite_order_and_both_accounts(self):
        result = self.run_refresh()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        calls = self.log.read_text()
        self.assertEqual(calls.count('password_qa_accounts'), 2)
        self.assertLess(calls.index('upgrade-preflight'), calls.index('updatedb'))
        self.assertLess(calls.index('updatedb'), calls.index('config:import'))
        self.assertLess(calls.index('platform sync'), calls.index('drush'))

    def test_production_and_wrong_branch_refused_before_sync(self):
        for override in ({'PLATFORM_ENVIRONMENT_TYPE': 'production'}, {'PLATFORM_BRANCH': 'master'}):
            self.log.write_text('')
            self.assertNotEqual(self.run_refresh(**override).returncode, 0)
            self.assertNotIn('platform sync', self.log.read_text())

    def test_empty_environment_uses_edge_branch(self):
        result = self.run_refresh(EDGE_ENVIRONMENT='')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sync_failure_stops_before_finalisation(self):
        self.assertNotEqual(self.run_refresh(FAIL_SYNC='true').returncode, 0)
        self.assertNotIn('drush', self.log.read_text())

    def test_update_failure_stops_before_config(self):
        self.assertNotEqual(self.run_refresh(FAIL_COMMAND='updatedb').returncode, 0)
        self.assertNotIn('config:import', self.log.read_text())

    def test_qa_logged_error_is_failure(self):
        self.assertNotEqual(self.run_refresh(QA_LOG_ERROR='true').returncode, 0)
        self.assertNotIn('--uri=beta', self.log.read_text())

    def test_single_site_deploy_and_project_command(self):
        result = self.run_refresh(EDGE_LAYOUT='single', EDGE_URI='example.test', EDGE_DEPLOY='deploy',
                                  EDGE_PREFLIGHT='none', EDGE_QA_STAFF='true', EDGE_AFTER_COMMAND='nidirect:hwt-cache-clear')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text()
        self.assertIn('--uri=example.test', calls)
        self.assertIn('-y deploy', calls)
        self.assertIn('create_qa_staff_accounts', calls)
        self.assertIn('nidirect:hwt-cache-clear', calls)
        self.assertEqual(calls.count('password_qa_accounts'), 1)

    def test_missing_expected_site_fails(self):
        self.assertNotEqual(self.run_refresh(EDGE_SITES='missing').returncode, 0)
        self.assertNotIn('drush', self.log.read_text())

    def test_no_sites_fails(self):
        for path in (self.root / 'app/project/sites').glob('*/settings.php'):
            path.unlink()
        self.assertNotEqual(self.run_refresh().returncode, 0)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                    'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                    'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
                    'EDGE_BRANCH': 'edge', 'SOURCE_BRANCH': 'development', 'EDGE_PACKAGES': 'example/package:dev-main',
                    'PATH': f'{self.root}/bin:{os.environ["PATH"]}'}
        self.repo = self.root / 'work'
        self.git('init', '--bare', str(self.root / 'remote.git'), cwd=self.root)
        self.git('clone', str(self.root / 'remote.git'), str(self.repo), cwd=self.root)
        self.git('checkout', '-b', 'development')
        (self.repo / 'composer.json').write_text('{}\n')
        (self.repo / 'composer.lock').write_text('{}\n')
        self.git('add', '.')
        self.git('commit', '-m', 'fixture')
        self.git('push', 'origin', 'development')
        executable(self.root / 'bin/composer', '[[ "${FAIL_COMPOSER:-false}" != true ]]\n')

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', *args], cwd=cwd or self.repo, env=self.env, stderr=subprocess.DEVNULL, text=True).strip()

    def build(self, **overrides):
        return subprocess.run(['bash', str(ROOT / 'scripts/edge/build.sh')], cwd=self.repo,
                              env={**self.env, **overrides}, capture_output=True, text=True)

    def test_first_unchanged_and_source_only_changes(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.git('ls-remote', 'origin', 'refs/heads/edge')
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already contains', result.stdout)
        self.assertEqual(first, self.git('ls-remote', 'origin', 'refs/heads/edge'))
        self.git('checkout', 'development')
        (self.repo / 'new.txt').write_text('source change\n')
        self.git('add', 'new.txt')
        self.git('commit', '-m', 'source change without dependency change')
        self.git('push', 'origin', 'development')
        self.assertEqual(self.build().returncode, 0)
        self.assertNotEqual(first, self.git('ls-remote', 'origin', 'refs/heads/edge'))

    def test_composer_failure_does_not_publish(self):
        self.assertNotEqual(self.build(FAIL_COMPOSER='true').returncode, 0)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/edge'), '')

    def test_source_branch_cannot_be_target(self):
        self.assertNotEqual(self.build(EDGE_BRANCH='development').returncode, 0)

    def test_concurrent_publisher_is_not_overwritten(self):
        self.assertEqual(self.build().returncode, 0)
        executable(self.root / 'bin/composer', '\n'.join([
            'git checkout --orphan concurrent-fixture',
            'echo concurrent > concurrent.txt',
            'git add concurrent.txt',
            'git commit -m concurrent',
            'git push -f origin HEAD:refs/heads/edge',
            'git checkout edge',
        ]) + '\n')
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Edge changed during build', result.stderr)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/edge').split()[0], self.git('rev-parse', 'concurrent-fixture'))

    def test_theme_assets_included_but_dependency_mutation_excluded(self):
        executable(self.repo / 'theme.sh', '\n'.join([
            'mkdir -p theme/css', 'echo compiled > theme/css/new.css',
            'echo changed > package-lock.json',
        ]) + '\n')
        self.git('add', 'theme.sh')
        self.git('commit', '-m', 'theme fixture')
        self.git('push', 'origin', 'development')
        result = self.build(THEME_BUILD_SCRIPT='theme.sh', THEME_ASSET_PATHS=':(glob)theme/css/**/*.css')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('show', 'HEAD:theme/css/new.css'), 'compiled')
        self.assertNotIn('package-lock.json', self.git('ls-tree', '-r', '--name-only', 'HEAD'))

    def test_theme_failure_does_not_publish(self):
        executable(self.repo / 'theme.sh', 'exit 7\n')
        self.git('add', 'theme.sh')
        self.git('commit', '-m', 'failing theme fixture')
        self.git('push', 'origin', 'development')
        self.assertNotEqual(self.build(THEME_BUILD_SCRIPT='theme.sh').returncode, 0)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/edge'), '')


if __name__ == '__main__':
    unittest.main()
