"""Executor contract tests; no Docker daemon, networking or Git mutations."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from local_ci.runner import Docker, DockerCommandError, Runner, duration, snapshot
from local_ci.__main__ import request_plan, token_for


class FakeDocker:
    def __init__(self, codes=()):
        self.calls = []
        self.copies = []
        self.codes = iter(codes)
        self.containers = 0

    def call(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == 'create':
            self.containers += 1
            return f'container-{self.containers}'.encode()
        if args[0] == 'exec' and args[-2:] in [('id', '-u'), ('id', '-g')]:
            return b'1000\n'
        return b''

    def copy_to(self, container, source, target):
        self.copies.append((container, Path(source), target))

    def stream(self, args, log, cancelled, timeout):
        self.calls.append(tuple(args))
        log.write(b'fake job output\n')
        return next(self.codes, 0)


def job(name, requires=(), steps=None):
    return {'name': name, 'requires': list(requires), 'config': {
        'docker': [{'image': 'example:test'}],
        'steps': steps if steps is not None else [{'run': 'echo hello'}],
    }}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.docker = FakeDocker()
        self.runner = Runner(self.root / 'state', self.root, self.docker)

    def plan(self, *jobs, workflow='build-test'):
        return {'project': str(self.project), 'source': {'branch': 'local'},
                'workflows': {workflow: {'jobs': list(jobs)}}}

    def execute(self, plan):
        run_id = self.runner.submit(plan)
        with patch('local_ci.runner.subprocess.check_output', return_value=b''):
            return run_id, self.runner.execute(run_id)

    def test_timeout_parser_rejects_ambiguous_units(self):
        for value, seconds in [('12s', 12), ('2m', 120), ('1h', 3600)]:
            self.assertEqual(duration(value), seconds)
        for value in ['10', '-1s', '1.5m', '1d', '2m extra', None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                duration(value)

    def test_docker_error_reports_daemon_diagnostic_without_environment(self):
        command = ['docker', 'create', '--env', 'PRIVATE_VALUE=fixture', 'missing:image']
        error = subprocess.CalledProcessError(1, command, stderr=b'No such image: missing:image\n')
        with patch('local_ci.runner.subprocess.run', side_effect=error):
            with self.assertRaises(DockerCommandError) as caught:
                Docker().call(*command[1:])
        message = str(caught.exception)
        self.assertIn('No such image: missing:image', message)
        self.assertNotIn('PRIVATE_VALUE', message)
        self.assertIsInstance(caught.exception, subprocess.CalledProcessError)

    def test_container_creation_error_is_retained_in_status_and_logs(self):
        error = DockerCommandError(1, ['docker', 'create'], stderr=b'No such image: example:test')
        with patch.object(self.docker, 'call', side_effect=error):
            run_id, status = self.execute(self.plan(job('build')))
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['jobs']['build']['steps'], [])
        self.assertIn('No such image', status['jobs']['build']['error'])
        self.assertIn('No such image', self.runner.logs(run_id))

    def test_preflight_rejects_unsafe_or_unsupported_runs_before_state_creation(self):
        plans = [self.plan(job('edge'), workflow='build-edge'),
                 self.plan(job('edge'), workflow='build-edge-finalise'),
                 self.plan(job('keys', steps=[{'add_ssh_keys': {'fingerprints': ['secret-key']}}])),
                 self.plan(job('unknown', steps=[{'setup_remote_docker': {}}])),
                 self.plan(job('invalid', steps=[{'run': {'command': 'true', 'background': True}}]))]
        multiple_images = self.plan(job('services'))
        multiple_images['workflows']['build-test']['jobs'][0]['config']['docker'].append({'image': 'db:test'})
        plans.append(multiple_images)
        for plan in plans:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.runner.submit(plan)
        self.assertEqual(list((self.runner.state / 'runs').iterdir()), [])
        self.assertEqual(self.docker.calls, [])

    def test_snapshot_uses_working_bytes_and_excludes_credentials_and_symlinks(self):
        files = {'src/app.php': '<?php echo "local edit";', '.env': 'private',
                 'nested/.env.test': 'private', 'auth.json': 'private',
                 '.aws/credentials': 'private', '.git/config': 'private',
                 'certificate.pem': 'private', 'normal.txt': 'new file'}
        for name, content in files.items():
            target = self.project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        outside = self.root / 'outside.txt'
        outside.write_text('outside')
        (self.project / 'link.txt').symlink_to(outside)
        listed = b'\0'.join(name.encode() for name in [*files, 'link.txt', 'missing.txt']) + b'\0'
        destination = self.root / 'source.tar'
        with patch('local_ci.runner.subprocess.check_output', return_value=listed) as git:
            included = snapshot(self.project, destination)
        self.assertEqual(included, ['normal.txt', 'src/app.php'])
        with tarfile.open(destination) as archive:
            self.assertEqual(archive.extractfile('src/app.php').read(), files['src/app.php'].encode())
        self.assertIn('--exclude-standard', git.call_args.args[0])
        self.assertEqual(outside.read_text(), 'outside')

    def test_exited_entrypoint_reports_logs_without_waiting(self):
        original = self.docker.call

        def stopped(*args, **kwargs):
            if args[0] == 'exec' and '/tmp/local-ci-ready' in args:
                raise subprocess.CalledProcessError(1, ['docker', *args])
            if args[0] == 'inspect':
                return b'exited'
            if args[0] == 'logs':
                return b'fixture entrypoint failure\n'
            return original(*args, **kwargs)

        with patch.object(self.docker, 'call', side_effect=stopped), patch('local_ci.runner.time.sleep') as sleep:
            run_id, status = self.execute(self.plan(job('broken')))
        self.assertEqual(status['state'], 'failed')
        self.assertIn('entrypoint exited', status['jobs']['broken']['error'])
        self.assertIn('fixture entrypoint failure', self.runner.logs(run_id))
        sleep.assert_not_called()

    def test_success_retains_separate_containers_and_records_logs(self):
        run_id, status = self.execute(self.plan(job('first'), job('second', ['first'])))
        self.assertEqual(status['state'], 'success')
        self.assertEqual([entry['state'] for entry in status['jobs'].values()], ['success', 'success'])
        self.assertNotEqual(status['jobs']['first']['container'], status['jobs']['second']['container'])
        creates = [args for args in self.docker.calls if args[0] == 'create']
        self.assertEqual(len(creates), 2)
        for args in creates:
            self.assertEqual(args[args.index('--network') + 1], 'none')
            self.assertIn('--pull=never', args)
            self.assertNotIn('--volume', args)
        self.assertFalse(any(args[0] == 'rm' for args in self.docker.calls))
        self.assertIn('fake job output', self.runner.logs(run_id, 'first'))
        self.assertTrue((self.runner.path(run_id) / 'plan.json').exists())
        with self.assertRaisesRegex(ValueError, 'Unknown job'):
            self.runner.logs(run_id, 'missing')

    def test_failure_blocks_dependents_but_runs_cleanup_and_independent_jobs(self):
        self.docker.codes = iter([1, 0, 0])
        steps = [{'run': 'false'}, {'run': 'skip'},
                 {'run': {'command': 'cleanup', 'when': 'always'}}]
        _, status = self.execute(self.plan(job('fails', steps=steps), job('dependent', ['fails']), job('independent')))
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['jobs']['dependent']['state'], 'blocked')
        self.assertEqual(status['jobs']['independent']['state'], 'success')
        self.assertEqual([step['state'] for step in status['jobs']['fails']['steps']], ['failed', 'skipped', 'success'])

    def test_cancel_before_execution_does_not_snapshot_or_create_containers(self):
        run_id = self.runner.submit(self.plan(job('first')))
        self.assertTrue(self.runner.cancel(run_id)['cancel_requested'])
        with patch('local_ci.runner.snapshot') as copy_source:
            status = self.runner.execute(run_id)
        copy_source.assert_not_called()
        self.assertEqual(status['state'], 'cancelled')
        self.assertEqual(status['jobs']['first']['state'], 'cancelled')
        self.assertEqual(self.docker.calls, [])

    def test_cancel_during_job_stops_remaining_steps_and_dependents(self):
        run_id = self.runner.submit(self.plan(job('first', steps=[{'run': 'first'}, {'run': 'second'}]),
                                              job('dependent', ['first'])))
        def cancelling_stream(args, log, cancelled, timeout):
            self.runner.cancel(run_id)
            self.assertTrue(cancelled())
            return 0
        with patch.object(self.docker, 'stream', side_effect=cancelling_stream) as stream:
            with patch('local_ci.runner.subprocess.check_output', return_value=b''):
                status = self.runner.execute(run_id)
        stream.assert_called_once()
        self.assertEqual(status['state'], 'cancelled')
        self.assertEqual(status['jobs']['first']['state'], 'cancelled')
        self.assertEqual(status['jobs']['dependent']['state'], 'cancelled')
        self.assertTrue(any(args[0] == 'stop' for args in self.docker.calls))

    def test_keyboard_interrupt_cancels_run_job_and_current_step_and_stops_container(self):
        run_id = self.runner.submit(self.plan(job('first'), job('dependent', ['first'])))
        with patch.object(self.docker, 'stream', side_effect=KeyboardInterrupt):
            with patch('local_ci.runner.subprocess.check_output', return_value=b''):
                status = self.runner.execute(run_id)
        self.assertEqual(status['state'], 'cancelled')
        self.assertEqual(status['jobs']['first']['state'], 'cancelled')
        current_step = status['jobs']['first']['steps'][0]
        self.assertEqual(current_step['state'], 'cancelled')
        self.assertIn('finished_at', current_step)
        self.assertEqual(status['jobs']['dependent']['state'], 'cancelled')
        self.assertTrue(any(args[0] == 'stop' for args in self.docker.calls))

    def test_run_ids_cannot_escape_state_or_reexecute_finished_run(self):
        for run_id in ['../outside', 'a' * 31, 'a' * 32]:
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                self.runner.status(run_id)
        run_id, _ = self.execute(self.plan(job('first')))
        with self.assertRaisesRegex(ValueError, 'Only queued'):
            self.runner.execute(run_id)

    def layer(self, name, files):
        path = self.root / f'{name}.tar'
        with tarfile.open(path, 'w') as archive:
            for filename in files:
                content = b'content'
                item = tarfile.TarInfo(filename)
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
        return {'job': name, 'archive': path}

    def attach(self, layers, ancestors):
        return self.runner._step('attach_workspace', {'at': '/workspace'}, 'container', {},
                                 '/project', self.root, self.root / 'source.tar', layers,
                                 ancestors, 'consumer', self.plan(), io.BytesIO(), lambda: False)

    def test_workspace_only_restores_dependency_ancestors(self):
        layers = [self.layer('producer', ['result.txt']), self.layer('unrelated', ['private.txt'])]
        with patch.object(self.runner, 'restore') as restore:
            self.attach(layers, {'producer'})
        restore.assert_called_once_with('container', layers[0]['archive'], '/workspace')

    def test_workspace_file_collision_fails_instead_of_silent_overwrite(self):
        layers = [self.layer('one', ['./result.txt']), self.layer('two', ['result.txt'])]
        with patch.object(self.runner, 'restore'), self.assertRaisesRegex(ValueError, 'collision'):
            self.attach(layers, {'one', 'two'})

    def test_workspace_allows_a_dependent_job_to_update_ancestor_files(self):
        original = self.layer('build', ['result.txt'])
        updated = self.layer('transform', ['./result.txt'])
        updated['ancestors'] = {'build'}
        with patch.object(self.runner, 'restore') as restore:
            self.attach([original, updated], {'build', 'transform'})
        self.assertEqual([call.args[1] for call in restore.call_args_list],
                         [original['archive'], updated['archive']])

    def test_cache_checksums_use_container_file_bytes(self):
        with patch.object(self.docker, 'call', return_value=b'lock contents') as call:
            key = self.runner.cache_key('container', 'deps-{{ checksum "composer.lock" }}', '/project')
        self.assertEqual(key, 'deps-' + hashlib.sha256(b'lock contents').hexdigest())
        call.assert_called_once_with('exec', 'container', 'cat', '/project/composer.lock')
        with self.assertRaises(ValueError):
            self.runner.validate_key('deps-{{ .Branch }}')

    def test_same_cache_key_is_separate_for_each_project(self):
        def archive(container, root, paths, target):
            target.write_bytes(b'archive')
        with patch.object(self.runner, 'archive', side_effect=archive) as persist:
            for project in [self.project, self.root / 'other-project']:
                plan = self.plan()
                plan['project'] = str(project)
                self.runner._step('save_cache', {'key': 'deps-v1', 'paths': ['vendor']},
                                  'container', {}, '/project', self.root, self.root / 'source.tar',
                                  [], set(), 'producer', plan, io.BytesIO(), lambda: False)
        self.assertEqual(persist.call_count, 2)
        targets = [call.args[3] for call in persist.call_args_list]
        self.assertEqual(targets[0].name, targets[1].name)
        self.assertNotEqual(targets[0].parent, targets[1].parent)

    def test_api_request_rejects_project_escape_and_network_string(self):
        with patch.dict(os.environ, {'LOCAL_CI_PROJECTS_ROOT': str(self.project)}):
            with self.assertRaisesRegex(ValueError, 'under LOCAL_CI_PROJECTS_ROOT'):
                request_plan(self.runner, {'project': str(self.root)})
            with self.assertRaisesRegex(ValueError, 'boolean'):
                request_plan(self.runner, {'project': str(self.project), 'allow_network': 'false'})

    def test_generated_api_token_is_stable_and_private(self):
        with patch.dict(os.environ, {'LOCAL_CI_TOKEN': ''}):
            first = token_for(self.runner)
            self.assertEqual(first, token_for(self.runner))
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual((self.runner.state / 'api-token').stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {'LOCAL_CI_TOKEN': 'explicit-local-token'}):
            self.assertEqual(token_for(self.runner), 'explicit-local-token')


class StreamLifecycleTests(unittest.TestCase):
    def stream_closed_output_process(self, *, cancelled, timeout):
        # Capture the real constructor before patching the shared subprocess module.
        popen = subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = popen([sys.executable, '-c',
                           'import os, time; os.close(1); os.close(2); time.sleep(5)'],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            children.append(child)
            return child
        try:
            with patch('local_ci.runner.subprocess.Popen', side_effect=launch):
                Docker().stream(['exec', 'fake-container'], io.BytesIO(), cancelled, timeout)
        finally:
            for child in children:
                try:
                    self.assertIsNotNone(child.poll(), 'stream must reap its child process')
                    self.assertTrue(child.stdout.closed, 'stream must close its output pipe')
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
                    child.stdout.close()

    def test_eof_does_not_disable_inactivity_timeout(self):
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'No output'):
            self.stream_closed_output_process(cancelled=lambda: False, timeout=0.1)
        self.assertLess(time.monotonic() - start, 3)

    def test_eof_does_not_disable_cancellation(self):
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'Run cancelled'):
            self.stream_closed_output_process(cancelled=lambda: time.monotonic() - start > 0.1,
                                              timeout=60)
        self.assertLess(time.monotonic() - start, 3)


if __name__ == '__main__':
    unittest.main()
