"""Persistent cache contracts without a daemon or dependency downloads."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from local_ci.__main__ import main
from local_ci.runner import Runner
from local_ci.tests.test_runner import FakeDocker


class CacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.runner = Runner(self.root / 'state', self.root, FakeDocker())
        self.log = io.BytesIO()

    def step(self, kind, body, runner=None):
        runner = runner or self.runner
        return runner._step(kind, body, 'container', {}, '/project', self.root,
                            self.root / 'source.tar', [], set(), 'build',
                            {'project': str(self.project)}, self.log, lambda: False)

    def save(self, key, value=b'cached dependency'):
        def archive(container, root, paths, target):
            with tarfile.open(target, 'w') as output:
                member = tarfile.TarInfo('project/vendor/value')
                member.size = len(value)
                output.addfile(member, io.BytesIO(value))
        with patch.object(self.runner, 'archive', side_effect=archive) as persist:
            self.step('save_cache', {'key': key, 'paths': ['vendor']})
        return persist

    def test_saved_files_are_reused_by_a_new_runner_and_keys_are_immutable(self):
        self.save('deps-v1')
        entries = self.runner.caches(self.project)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['paths'], ['/project/vendor'])
        self.assertGreater(entries[0]['bytes'], 0)
        self.assertIn('created_at', entries[0])
        self.assertEqual(self.save('deps-v1', b'replacement').call_count, 0)
        fresh = Runner(self.runner.state, self.root, FakeDocker())
        restored = []
        def restore(container, archive, target):
            self.assertEqual(target, '/')
            with tarfile.open(archive) as source:
                restored.append(source.extractfile('project/vendor/value').read())
        with patch.object(fresh, 'restore', side_effect=restore):
            self.step('restore_cache', {'keys': ['deps-v1']}, fresh)
        self.assertEqual(restored, [b'cached dependency'])
        self.assertIn(b'Cache already saved (immutable): deps-v1', self.log.getvalue())
        self.assertIn(b'Cache restored: deps-v1', self.log.getvalue())

    def test_restore_uses_ordered_prefixes_and_latest_matching_entry(self):
        for index, key in enumerate(['deps-old', 'deps-new', 'other-newest']):
            self.save(key)
            metadata = Path(self.runner.caches(self.project)[0]['archive']).with_suffix('.json')
            os.utime(metadata, ns=(index + 1, index + 1))
        with patch.object(self.runner, 'restore') as restore:
            self.step('restore_cache', {'keys': ['absent-', 'deps-', 'other-']})
        expected = self.runner.cache_directory(self.project) / (hashlib.sha256(b'deps-new').hexdigest() + '.tar')
        self.assertEqual(restore.call_args.args[1], expected)
        self.assertIn(b'Cache miss: absent-', self.log.getvalue())
        self.assertIn(b'Restoring cache: deps-new (requested: deps-)', self.log.getvalue())

    def test_failed_save_is_not_published_and_can_be_retried(self):
        def interrupted(container, root, paths, target):
            target.write_bytes(b'incomplete')
            raise subprocess.CalledProcessError(2, ['tar'])
        with patch.object(self.runner, 'archive', side_effect=interrupted):
            with self.assertRaises(subprocess.CalledProcessError):
                self.step('save_cache', {'key': 'deps-v1', 'paths': ['vendor']})
        self.assertEqual(self.runner.caches(self.project), [])
        self.assertEqual(list(self.runner.cache_directory(self.project).iterdir()), [])
        self.save('deps-v1')
        self.assertEqual(len(self.runner.caches(self.project)), 1)

    def test_missing_paths_are_skipped_and_empty_cache_is_not_published(self):
        def call(*args):
            if args[-1] == '/project/missing':
                raise subprocess.CalledProcessError(3, ['test'])
            return b''
        def archive(container, root, paths, target):
            self.assertEqual(paths, ['project/vendor'])
            target.write_bytes(b'fixture archive')
        with patch.object(self.runner.docker, 'call', side_effect=call), \
                patch.object(self.runner, 'archive', side_effect=archive) as persist:
            self.step('save_cache', {'key': 'mixed', 'paths': ['vendor', 'missing']})
            self.step('save_cache', {'key': 'empty', 'paths': ['missing']})
        self.assertEqual(persist.call_count, 1)
        self.assertEqual([entry['key'] for entry in self.runner.caches(self.project)], ['mixed'])
        self.assertIn(b'Cache not saved (no existing paths): empty', self.log.getvalue())

    def test_invalid_and_uncommitted_entries_are_ignored(self):
        self.save('valid')
        directory = self.runner.cache_directory(self.project)
        (directory / 'invalid.json').write_text('{')
        (directory / 'wrong-name.json').write_text(json.dumps({'key': 'valid'}))
        for key, contents in [('missing-archive', None), ('empty-archive', b'')]:
            name = hashlib.sha256(key.encode()).hexdigest()
            (directory / f'{name}.json').write_text(json.dumps({'key': key}))
            if contents is not None:
                (directory / f'{name}.tar').write_bytes(contents)
        (directory / 'uncommitted.tar').write_bytes(b'partial')
        self.assertEqual([entry['key'] for entry in self.runner.caches(self.project)], ['valid'])
        entry = self.runner.caches(self.project)[0]
        Path(entry['archive']).write_bytes(b'truncated')
        self.assertEqual(self.runner.caches(self.project), [])

    def test_inventory_cli_and_legacy_cache_metadata(self):
        self.save('legacy')
        entry = self.runner.caches(self.project)[0]
        Path(entry['archive']).with_suffix('.json').write_text(json.dumps({'key': 'legacy'}))
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            code = main(['--state', str(self.runner.state), 'caches', '--project', str(self.project)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())[0]['key'], 'legacy')
        self.assertEqual(self.runner.caches(self.root / 'other-project'), [])
