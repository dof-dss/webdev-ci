"""Local Edge selection and publication guard contracts; no Docker or network."""
import base64
import gzip
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

from local_ci.__main__ import main, request_plan
from local_ci.compiler import _pack
from local_ci.runner import Runner
from local_ci.tests.test_runner import FakeDocker


SHARED = Path(__file__).resolve().parents[2]


class LocalEdgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'corp-lite'
        self.project.mkdir()
        self.parameters = json.loads((SHARED / 'templates/application/corp-lite.json').read_text())
        self.parameters['ssh_fingerprint'] = ''
        self.docker = FakeDocker()
        self.runner = Runner(self.root / 'state', SHARED, self.docker)

    def plan(self, **kwargs):
        return self.runner.plan(self.project, self.parameters, action='edge-build', **kwargs)

    def edge_job(self, plan):
        return plan['workflows']['build-edge']['jobs'][0]

    def test_execution_defaults_to_skip_push_and_rejects_publication(self):
        safe = self.plan()
        self.assertTrue(safe['local_execution']['skip_push'])
        self.runner.validate(safe, 'build-edge')
        plan = self.plan(skip_push=False)
        with self.assertRaises(ValueError):
            self.runner.submit(plan, 'build-edge')
        self.assertEqual(self.docker.calls, [])
        self.assertEqual(list((self.runner.state / 'runs').iterdir()), [])

    def test_fingerprint_defaults_from_config_with_parameter_precedence(self):
        (self.project / '.circleci').mkdir()
        config = {'jobs': {'setup': {'steps': [{'continuation/continue': {
            'parameters': json.dumps({'ssh_fingerprint': 'SHA256:project-fixture'})}}]}}}
        (self.project / '.circleci/config.yml').write_text(yaml.safe_dump(config))
        for supplied, expected in [(None, 'SHA256:project-fixture'),
                                   ('REPLACE_WITH_PROJECT_SSH_KEY_FINGERPRINT', 'SHA256:project-fixture'),
                                   ('SHA256:parameter-fixture', 'SHA256:parameter-fixture'), ('', '')]:
            parameters = dict(self.parameters)
            if supplied is None:
                parameters.pop('ssh_fingerprint')
            else:
                parameters['ssh_fingerprint'] = supplied
            plan = self.runner.plan(self.project, parameters, action='edge-build')
            self.assertEqual(plan['parameters']['ssh_fingerprint'], expected)
            self.runner.validate(plan, 'build-edge')
            self.assertIn('not_loaded', plan['local_execution']['ssh_keys'])

    def test_local_plan_keeps_shared_build_steps_and_disables_publication(self):
        plan = self.plan(skip_push=True)
        self.assertTrue(plan['local_execution']['skip_push'])
        self.assertEqual(plan['local_execution']['source'], 'working-tree-snapshot')
        job = self.edge_job(plan)
        environment = {**job['config']['docker'][0].get('environment', {}),
                       **job['config'].get('environment', {})}
        self.assertEqual(environment['EDGE_SKIP_PUBLISH'], 'true')
        steps = job['config']['steps']
        commands = [step['run'] for step in steps if isinstance(step, dict) and 'run' in step]
        names = [step.get('name', '') for step in commands if isinstance(step, dict)]
        self.assertNotIn('Prepare existing theme toolchain', names)
        self.assertTrue(any('build.sh' in step.get('command', '') for step in commands if isinstance(step, dict)))
        self.runner.submit(plan, 'build-edge')

    def test_step_environment_cannot_reenable_publication(self):
        plan = self.plan(skip_push=True)
        job = self.edge_job(plan)
        self.runner._step('run', {'command': 'true', 'environment': {'EDGE_SKIP_PUBLISH': 'false'}},
                          'container', job['config'], '/project', self.root,
                          self.root / 'source.tar', [], set(), job['name'], plan,
                          io.BytesIO(), lambda: False)
        command = self.docker.calls[-1]
        self.assertIn('EDGE_SKIP_PUBLISH=true', command)
        self.assertNotIn('EDGE_SKIP_PUBLISH=false', command)

    def test_refresh_is_never_unblocked_by_skip_push(self):
        with self.assertRaises(ValueError):
            self.runner.plan(self.project, self.parameters, action='edge-refresh', skip_push=True)
        plan = self.runner.plan(self.project, self.parameters, action='edge-refresh')
        plan['local_execution'] = {'skip_push': True}
        self.assertTrue(plan['workflows'])
        for workflow in plan['workflows']:
            with self.subTest(workflow=workflow), self.assertRaises(ValueError):
                self.runner.submit(plan, workflow)
        self.assertEqual(self.docker.calls, [])

    def test_theme_build_requires_script_and_expands_toolchain_when_supplied(self):
        self.parameters['build_themes'] = True
        with self.assertRaises(ValueError):
            self.plan(skip_push=True)
        self.parameters['theme_build_script'] = 'scripts/build-theme.sh'
        plan = self.plan(skip_push=True)
        steps = self.edge_job(plan)['config']['steps']
        self.assertTrue(any(isinstance(step, dict) and isinstance(step.get('run'), dict)
                            and step['run'].get('name') == 'Prepare existing theme toolchain'
                            for step in steps))

    def test_composed_application_config_can_be_selected_explicitly(self):
        composed = self.project / 'composed.yml'
        composed.write_text(yaml.safe_dump(_pack(SHARED)))
        plan = self.plan(skip_push=True, config_path=composed)
        self.assertEqual(plan['source']['config_path'], str(composed.resolve()))
        self.runner.submit(plan, 'build-edge')

    def test_old_composed_publication_script_is_rejected_before_docker(self):
        packed = _pack(SHARED)
        environment = packed['commands']['prepare_shared_tools']['steps'][0]['run']['environment']
        old = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(environment['CI_SHARED_BUNDLE'])), mode='r:gz') as source, \
                tarfile.open(fileobj=old, mode='w') as output:
            for member in source:
                content = source.extractfile(member).read() if member.isfile() else None
                if member.name == 'scripts/edge/build.sh':
                    content = b'#!/bin/bash\ngit push origin HEAD:edge\n'
                    member.size = len(content)
                output.addfile(member, io.BytesIO(content) if content is not None else None)
        environment['CI_SHARED_BUNDLE'] = base64.b64encode(gzip.compress(old.getvalue())).decode()
        composed = self.project / 'old-composed.yml'
        composed.write_text(yaml.safe_dump(packed))
        plan = self.plan(skip_push=True, config_path=composed)
        with self.assertRaisesRegex(ValueError, 'recompose'):
            self.runner.submit(plan, 'build-edge')
        self.assertEqual(self.docker.calls, [])
        self.assertEqual(list((self.runner.state / 'runs').iterdir()), [])

    def test_artifacts_report_changes_and_exclude_credentials_links_and_dependencies(self):
        def write_tar(path, files, symlinks=()):
            with tarfile.open(path, 'w') as archive:
                for name, content in files.items():
                    member = tarfile.TarInfo(name)
                    data = content.encode()
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                for name in symlinks:
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/outside'
                    archive.addfile(member)
        source = self.root / 'source.tar'
        write_tar(source, {'composer.json': 'before', 'removed.txt': 'gone'})
        def export(container, workdir, target):
            write_tar(target, {'composer.json': 'after', 'web/theme/style.css': 'css',
                               '.env': 'secret', 'vendor/package/file.php': 'package',
                               'web/theme/node_modules/pkg/index.js': 'module'}, ['linked.txt'])
        self.docker.export_project = export
        info = {'log': 'job-0.log'}
        self.runner.capture_artifacts(self.edge_job(self.plan(skip_push=True)),
                                      'container', '/project', self.root, source, info)
        changes = json.loads((self.root / info['artifacts']['changes']).read_text())
        self.assertEqual(changes['added'], ['web/theme/style.css'])
        self.assertEqual(changes['modified'], ['composer.json'])
        self.assertEqual(changes['deleted'], ['removed.txt'])
        self.assertEqual(changes['publication'], 'skipped')
        with tarfile.open(self.root / info['artifacts']['archive']) as archive:
            self.assertEqual(archive.getnames(), ['composer.json', 'web/theme/style.css'])

    def test_api_skip_push_must_be_boolean(self):
        for value in ['true', 1, None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                request_plan(self.runner, {'project': str(self.project), 'skip_push': value})

    def test_cli_theme_flags_override_parameter_file(self):
        parameters = self.project / 'params.json'
        parameters.write_text(json.dumps({'build_themes': True}))
        with patch('local_ci.__main__.Runner', return_value=self.runner), \
                patch('local_ci.__main__.request_plan', return_value={}) as compile_plan, \
                patch('sys.stdout', new_callable=io.StringIO):
            result = main(['plan', '--project', str(self.project), '--parameters', str(parameters),
                           '--action', 'edge-build', '--skip-push', '--no-build-themes'])
        self.assertEqual(result, 0)
        body = compile_plan.call_args.args[1]
        self.assertTrue(body['skip_push'])
        self.assertIs(body['parameters']['build_themes'], False)
