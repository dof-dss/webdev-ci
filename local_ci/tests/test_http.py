"""Opt-in loopback test of the real service authentication and planning API."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@unittest.skipUnless(os.environ.get('LOCAL_CI_TEST_HTTP'), 'Set LOCAL_CI_TEST_HTTP=1 to bind a loopback test server')
class HttpTests(unittest.TestCase):
    def test_health_authentication_planning_and_edge_execution_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = {**os.environ, 'LOCAL_CI_TOKEN': 'local-test-fixture-token',
                   'LOCAL_CI_PROJECTS_ROOT': temporary}
            process = subprocess.Popen([sys.executable, '-m', 'local_ci', '--state', temporary + '/state',
                                        'serve', '--port', str(port)], env=env, stdout=subprocess.DEVNULL)
            base = f'http://127.0.0.1:{port}'
            def request(path, body=None, auth=True):
                headers = {'Authorization': 'Bearer local-test-fixture-token'} if auth else {}
                data = None if body is None else json.dumps(body).encode()
                with urlopen(Request(base + path, data=data, headers=headers), timeout=3) as response:
                    return json.load(response)
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        self.assertEqual(request('/health', auth=False), {'status': 'ok'})
                        break
                    except URLError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                with self.assertRaises(HTTPError) as error:
                    request('/plans', {'project': temporary}, auth=False)
                self.assertEqual(error.exception.code, 401)
                plan = request('/plans', {'project': temporary, 'action': 'checks'})
                self.assertEqual(set(plan['workflows']), {'build-test'})
                with self.assertRaises(HTTPError) as error:
                    request('/runs', {'project': temporary, 'action': 'edge-build', 'skip_push': False})
                self.assertEqual(error.exception.code, 400)
                self.assertIn('--skip-push', error.exception.read().decode())
                self.assertEqual(list((Path(temporary) / 'state/runs').iterdir()), [])
            finally:
                process.terminate()
                process.wait(timeout=10)
            self.assertEqual(process.returncode, 0)


if __name__ == '__main__':
    unittest.main()
