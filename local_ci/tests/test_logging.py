"""Live log following with incremental writes and no Docker dependency."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from local_ci.__main__ import follow_logs


class FollowLogsTests(unittest.TestCase):
    def test_incremental_unicode_final_output_and_job_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'job.log'
            log.write_bytes(b'first\n\xe2')
            (root / 'other.log').write_text('hidden')
            runner = Mock()
            runner.path.return_value = root
            def status(state):
                return {'state': state, 'jobs': {
                    'build': {'state': state, 'log': 'job.log'},
                    'other': {'state': state, 'log': 'other.log'}}}
            runner.status.side_effect = [status('running'), status('success')]
            stop = Mock()
            stop.is_set.return_value = False
            def append(_):
                with log.open('ab') as output:
                    output.write(b'\x82\xac last\n')
            stop.wait.side_effect = append
            output = io.StringIO()
            follow_logs(runner, 'id', job='build', output=output, stop=stop)
            self.assertEqual(output.getvalue(), '[build] running\nfirst\n[build] success\n€ last\n')
            self.assertEqual(stop.wait.call_count, 1)

    def test_unknown_job_is_rejected(self):
        runner = Mock()
        runner.status.return_value = {'state': 'success', 'jobs': {}}
        with self.assertRaisesRegex(ValueError, 'Unknown job'):
            follow_logs(runner, 'id', job='missing')
