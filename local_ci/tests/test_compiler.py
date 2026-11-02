"""Offline compiler contract tests against the application configuration."""
import base64
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

from local_ci.compiler import _pack, compile_application, compile_config


ROOT = Path(__file__).resolve().parents[2]


class CompilerTests(unittest.TestCase):
    def compile(self, parameters=None, document=None, **metadata):
        if document is None:
            return compile_application(ROOT, parameters or {}, **metadata)
        with patch('local_ci.compiler._pack', return_value=document):
            return compile_application(ROOT, parameters or {}, **metadata)

    def test_all_templates_and_actions(self):
        selections = {'none': [], 'checks': ['build-test'],
                      'edge-build': ['build-edge'], 'edge-refresh': ['build-edge-finalise']}
        templates = list((ROOT / 'templates/application').glob('*.json'))
        self.assertEqual(len(templates), 4, 'Expected all application templates to be available')
        for template in templates:
            parameters = json.loads(template.read_text())
            for action, selected in selections.items():
                with self.subTest(template=template.stem, action=action):
                    result = self.compile({**parameters, 'ci_action': action})
                    self.assertEqual(list(result['workflows']), selected)
                    json.dumps(result)
                    for workflow in result['workflows'].values():
                        for job in workflow['jobs']:
                            config = job['config']
                            self.assertNotIn('parameters', config)
                            self.assertNotIn('<< parameters.', json.dumps(config))
                            self.assertNotIn('<< pipeline.', json.dumps(config))

    def test_packed_config_matches_shared_compilation(self):
        document = _pack(ROOT)
        original = copy.deepcopy(document)
        parameters = {'ci_action': 'edge-build', 'build_themes': True,
                      'node_version': '22'}
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'composed.yml'
            config_path.write_text(yaml.safe_dump(document))
            before = config_path.read_bytes()
            packed = compile_config(config_path, parameters, branch='example',
                                    trigger_source='scheduled_pipeline', schedule_name='nightly')
            shared = self.compile(parameters, document, branch='example',
                                  trigger_source='scheduled_pipeline', schedule_name='nightly')
            self.assertEqual(packed['config'], shared['config'])
            self.assertEqual(packed['parameters'], shared['parameters'])
            self.assertEqual(packed['workflows'], shared['workflows'])
            self.assertEqual(packed['source'], {
                'config_path': str(config_path.resolve()), 'branch': 'example',
                'trigger_source': 'scheduled_pipeline', 'schedule_name': 'nightly'})
            self.assertEqual(config_path.read_bytes(), before)
        self.assertEqual(document, original)

    def test_packed_config_rejects_unsupported_root_and_nonmapping_sections(self):
        documents = [None, [], {'version': 2.1, 'setup': True},
                     {'version': 2.1, 'orbs': {'example': 'example/orb@1'}}]
        documents.extend({'version': 2.1, section: []}
                         for section in ('parameters', 'jobs', 'commands', 'workflows'))
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'composed.yml'
            for document in documents:
                config_path.write_text(yaml.safe_dump(document))
                with self.subTest(document=document), self.assertRaises(ValueError):
                    compile_config(config_path, {})

    def test_defaults_and_workflow_metadata(self):
        self.assertEqual(self.compile()['workflows'], {})
        for trigger in ('api', 'scheduled_pipeline', 'webhook'):
            result = self.compile({'ci_action': 'checks'}, trigger_source=trigger, schedule_name='nightly')
            self.assertEqual(list(result['workflows']), ['build-test'])
        self.assertEqual(self.compile({'ci_action': 'auto'}, branch='feature', trigger_source='api')['workflows'], {})
        self.assertEqual(self.compile({'ci_action': 'auto'}, branch='development', trigger_source='webhook')['workflows'], {})
        selected = self.compile({'ci_action': 'auto'}, branch='feature', trigger_source='webhook')
        self.assertEqual(list(selected['workflows']), ['build-test'])
        self.assertEqual(selected['source']['branch'], 'feature')

    def test_invalid_parameters(self):
        for parameters in ({'not_declared': 'x'}, {'qa_staff': 'false'},
                           {'php_version': 8.3}, {'ci_action': 'missing'}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.compile(parameters)

    def test_shared_bundle_fidelity(self):
        result = self.compile({'ci_action': 'checks'})
        step = result['config']['commands']['prepare_shared_tools']['steps'][0]['run']
        self.assertEqual(step['command'], (ROOT / 'scripts/unpack-shared-tools.sh').read_text())
        raw = base64.b64decode(step['environment']['CI_SHARED_BUNDLE'])
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            files = {member.name for member in archive.getmembers() if member.isfile()}
            expected = {'phpcs.sh', *(str(path.relative_to(ROOT)) for path in (ROOT / 'scripts/edge').rglob('*') if path.is_file())}
            self.assertEqual(files, expected)
            for name in expected:
                self.assertEqual(archive.extractfile(name).read(), (ROOT / name).read_bytes())
        self.assertEqual(step['environment']['CI_SHARED_BUNDLE'], self.compile()['config']['commands']['prepare_shared_tools']['steps'][0]['run']['environment']['CI_SHARED_BUNDLE'])

    def test_check_dag_and_job_filter(self):
        result = self.compile({'ci_action': 'checks'})
        jobs = result['workflows']['build-test']['jobs']
        self.assertEqual([job['name'] for job in jobs], ['build', 'coding_standards', 'deprecated_code_phpstan', 'disallowed_functions'])
        self.assertEqual(jobs[0]['requires'], [])
        for job in jobs[1:]:
            self.assertEqual(job['requires'], ['build'])
        enabled = self.compile({'ci_action': 'checks', 'check_illegal_updates': True})
        self.assertEqual(enabled['workflows']['build-test']['jobs'][-1]['name'], 'check_illegal_updates')

    def test_nested_command_scopes_preserve_typed_environment(self):
        document = _pack(ROOT)
        document['commands']['inner'] = {
            'parameters': {'flag': {'type': 'boolean'}, 'label': {'type': 'string'}},
            'steps': [{'run': {'environment': {'FLAG': '<< parameters.flag >>'},
                               'command': 'echo << parameters.label >> >> "$BASH_ENV"'}}],
        }
        document['commands']['outer'] = {
            'parameters': {'enabled': {'type': 'boolean', 'default': False}},
            'steps': [{'inner': {'flag': '<< parameters.enabled >>', 'label': 'inner'}}],
        }
        document['jobs']['build']['steps'] = [{'outer': {'enabled': True}}]
        result = self.compile({'ci_action': 'checks'}, document)
        run = result['workflows']['build-test']['jobs'][0]['config']['steps'][0]['run']
        self.assertIs(run['environment']['FLAG'], True)
        self.assertEqual(run['command'], 'echo inner >> "$BASH_ENV"')

    def test_step_conditions_and_nvm_environment(self):
        without = self.compile({'ci_action': 'edge-build', 'build_themes': False})
        with_theme = self.compile({'ci_action': 'edge-build', 'build_themes': True, 'node_version': '22'})
        steps = with_theme['workflows']['build-edge']['jobs'][0]['config']['steps']
        short_steps = without['workflows']['build-edge']['jobs'][0]['config']['steps']
        self.assertEqual(len(steps), len(short_steps) + 1)
        toolchain = next(step['run'] for step in steps if isinstance(step, dict) and step.get('run', {}).get('name') == 'Prepare existing theme toolchain')
        self.assertIn('nvm install 22', toolchain['command'])
        self.assertIn('$BASH_ENV', toolchain['command'])

    def test_unknown_dependencies_cycles_and_aliases(self):
        document = _pack(ROOT)
        workflow = document['workflows']['build-test']
        workflow['jobs'] = [{'build': {'name': 'first', 'php_version': '8.3', 'ssh_fingerprint': ''}},
                            {'build': {'name': 'second', 'php_version': '8.3', 'ssh_fingerprint': '', 'requires': ['first']}}]
        result = self.compile({'ci_action': 'checks'}, document)
        self.assertEqual(result['workflows']['build-test']['jobs'][1]['requires'], ['first'])
        for dependency in ('unknown', 'second'):
            bad = copy.deepcopy(document)
            bad['workflows']['build-test']['jobs'][0]['build']['requires'] = [dependency]
            with self.subTest(dependency=dependency), self.assertRaises(ValueError):
                self.compile({'ci_action': 'checks'}, bad)

    def test_filtered_dependency_blocks_descendants(self):
        document = _pack(ROOT)
        document['workflows']['build-test']['jobs'][0]['build']['filters'] = False
        result = self.compile({'ci_action': 'checks'}, document)
        self.assertEqual(result['workflows']['build-test']['jobs'], [])

    def test_unsupported_syntax_fails_closed(self):
        for edit in ('machine', 'unknown-step', 'checkout-path', 'branch-filter', 'multi-container', 'condition'):
            document = _pack(ROOT)
            if edit == 'machine':
                document['jobs']['build']['machine'] = True
            elif edit == 'unknown-step':
                document['jobs']['build']['steps'].append({'store_artifacts': {'path': 'output'}})
            elif edit == 'checkout-path':
                document['jobs']['build']['steps'].append({'checkout': {'path': '/unsupported'}})
            elif edit == 'branch-filter':
                document['workflows']['build-test']['jobs'][0]['build']['filters'] = {'branches': {'only': 'main'}}
            elif edit == 'multi-container':
                document['jobs']['build']['docker'].append({'image': 'postgres:16'})
            else:
                document['workflows']['build-test']['when'] = {'matches': {'pattern': '*', 'value': 'x'}}
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.compile({'ci_action': 'checks'}, document)

    def test_recursive_command_rejected(self):
        document = _pack(ROOT)
        document['commands']['recurse'] = {'steps': ['recurse']}
        document['jobs']['build']['steps'] = ['recurse']
        with self.assertRaisesRegex(ValueError, 'Recursive command'):
            self.compile({'ci_action': 'checks'}, document)


if __name__ == '__main__':
    unittest.main()
