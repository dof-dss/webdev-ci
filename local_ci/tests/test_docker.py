"""Opt-in smoke test using an explicitly prepared image; never pulls images."""
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

from local_ci.compiler import _pack
from local_ci.runner import Runner


@unittest.skipUnless(os.environ.get('LOCAL_CI_TEST_IMAGE'), 'Set LOCAL_CI_TEST_IMAGE to a cached Bash/GNU-tar image')
class DockerSmokeTests(unittest.TestCase):
    def test_composed_corp_lite_edge_build_with_optional_theme_and_no_git(self):
        shared = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / 'corp-lite'
            (project / 'test-bin').mkdir(parents=True)
            (project / 'scripts').mkdir()
            (project / 'composer.json').write_text('{}\n')
            (project / 'composer.lock').write_text('original\n')
            composer = project / 'test-bin/composer'
            composer.write_text('#!/bin/bash\nset -eu\n'
                                'echo "composer $*"\n'
                                'echo resolved > composer.lock\n'
                                'mkdir -p vendor/fixture\n'
                                'echo dependency > vendor/fixture/data\n')
            composer.chmod(0o755)
            git = project / 'test-bin/git'
            git.write_text('#!/bin/sh\necho "$*" >> git-calls\nexit 99\n')
            git.chmod(0o755)
            (project / 'scripts/build-theme.sh').write_text(
                'test "$1" = --sites-dir\n'
                'test "$2" = "$PWD/project/sites"\n'
                'mkdir -p theme/css\necho compiled > theme/css/main.css\necho theme-built\n')
            document = _pack(shared)
            definition = document['jobs']['edge_build_shared']
            definition['docker'][0]['image'] = os.environ['LOCAL_CI_TEST_IMAGE']
            # Preserve supplementary groups needed by this image's sudo entrypoint.
            definition['docker'][0]['user'] = '1000'
            definition['docker'][0]['environment']['XDEBUG_CLIENT_HOST'] = '127.0.0.1'
            # Exercise the real composed steps and bundled script, using fixture
            # executables instead of package downloads/native toolchain installs.
            definition['steps'].insert(1, {'run': {
                'name': 'Fixture executables',
                'command': 'printf \'export PATH="%s/test-bin:$PATH"\\n\' "$PWD" >> "$BASH_ENV"'}})
            for step in definition['steps']:
                if isinstance(step, dict) and 'when' in step:
                    step['when']['steps'] = [{'run': {'name': 'Fixture theme toolchain', 'command': 'echo toolchain-ready'}}]
            composed = root / 'composed.yml'
            composed.write_text(yaml.safe_dump(document))
            parameters = json.loads((shared / 'templates/application/multisite.json').read_text())
            parameters.update(edge_build_dofdss_packages='fixture/package:^1',
                              ssh_fingerprint='', theme_build_script='scripts/build-theme.sh')
            runner = Runner(root / 'state', shared)
            containers = []

            def source_snapshot(_, destination):
                with tarfile.open(destination, 'w') as archive:
                    for source in project.rglob('*'):
                        if source.is_file():
                            archive.add(source, arcname=str(source.relative_to(project)), recursive=False)
                return []

            try:
                for themes in (False, True):
                    plan = runner.plan(project, {**parameters, 'build_themes': themes}, action='edge-build',
                                       skip_push=True, config_path=composed)
                    run_id = runner.submit(plan, platform=os.environ.get('LOCAL_CI_TEST_PLATFORM', 'linux/amd64'))
                    with patch('local_ci.runner.snapshot', side_effect=source_snapshot):
                        result = runner.execute(run_id)
                    info = result['jobs']['edge_build_shared']
                    if 'container' in info:
                        containers.append(info['container'])
                    logs = runner.logs(run_id)
                    self.assertEqual(result['state'], 'success', json.dumps(result, indent=2) + logs)
                    self.assertIn('composer require --no-interaction fixture/package:^1', logs)
                    self.assertIn('publication skipped', logs)
                    self.assertEqual('toolchain-ready' in logs, themes)
                    self.assertEqual('theme-built' in logs, themes)
                    report = json.loads((runner.path(run_id) / info['artifacts']['changes']).read_text())
                    self.assertIn('composer.lock', report['modified'])
                    self.assertEqual('theme/css/main.css' in report['added'], themes)
                    with tarfile.open(runner.path(run_id) / info['artifacts']['archive']) as archive:
                        names = archive.getnames()
                        self.assertNotIn('git-calls', names)
                        self.assertFalse(any(name.startswith('vendor/') for name in names))
                    self.assertEqual((project / 'composer.lock').read_text(), 'original\n')
                    self.assertFalse((project / 'theme').exists())
            finally:
                if containers:
                    subprocess.run(['docker', 'rm', '-f', *containers], check=True, capture_output=True)

    def test_workspace_shell_environment_cache_and_source_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / 'project'
            project.mkdir()
            (project / 'composer.lock').write_text('fixture')
            (project / 'marker').write_text('original')
            runner = Runner(root / 'state', root)
            containers = []

            def source_snapshot(_, destination):
                with tarfile.open(destination, 'w') as archive:
                    archive.add(project / 'composer.lock', arcname='composer.lock')
                    archive.add(project / 'marker', arcname='marker')
                return ['composer.lock', 'marker']

            def job(name, steps, requires=()):
                return {'name': name, 'definition': name, 'requires': list(requires), 'config': {
                    'docker': [{'image': os.environ['LOCAL_CI_TEST_IMAGE'], 'user': '1000',
                                'environment': {'XDEBUG_CLIENT_HOST': '127.0.0.1'}}],
                    'shell': '/bin/bash -euo pipefail', 'steps': steps}}

            plan = {'project': str(project), 'source': {'branch': 'local'}, 'workflows': {'checks': {'jobs': [
                job('build', ['checkout',
                    {'restore_cache': {'keys': ['deps-{{ checksum "composer.lock" }}']}},
                    {'run': {'command': 'mkdir -p deps; echo cached > deps/value; '
                             'echo "export SHARED_TEST=from-bash-env" >> "$BASH_ENV"'}},
                    {'run': {'command': 'test "$SHARED_TEST" = from-bash-env; echo shell-env-ok'}},
                    {'save_cache': {'key': 'deps-{{ checksum "composer.lock" }}', 'paths': ['deps']}},
                    {'persist_to_workspace': {'root': './', 'paths': ['./']}}]),
                job('first-reader', [{'attach_workspace': {'at': './'}},
                    {'run': {'command': 'test "$(cat marker)" = original; echo modified > marker'}}], ['build']),
                job('second-reader', [{'attach_workspace': {'at': './'}},
                    {'run': {'command': 'test "$(cat marker)" = original; test "$(cat deps/value)" = cached; echo isolated-ok'}}], ['build']),
            ]}}}
            try:
                for attempt in range(2):
                    run_id = runner.submit(plan, 'checks', platform=os.environ.get('LOCAL_CI_TEST_PLATFORM', 'linux/amd64'))
                    with patch('local_ci.runner.snapshot', side_effect=source_snapshot):
                        result = runner.execute(run_id)
                    containers.extend(info['container'] for info in result['jobs'].values() if 'container' in info)
                    self.assertEqual(result['state'], 'success', json.dumps(result, indent=2) + runner.logs(run_id))
                    self.assertIn('shell-env-ok', runner.logs(run_id))
                    self.assertIn('isolated-ok', runner.logs(run_id))
                    if attempt:
                        self.assertIn('Restoring cache:', runner.logs(run_id))
                self.assertEqual((project / 'marker').read_text(), 'original')
                self.assertFalse((project / 'deps').exists())
                self.assertEqual(len(set(containers)), 6)
            finally:
                # Only exact containers created by this test; never a global prune.
                if containers:
                    subprocess.run(['docker', 'rm', '-f', *containers], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
