"""Local CLI and a small authenticated HTTP interface to the same runner."""
from __future__ import annotations

import argparse
import codecs
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from .runner import Runner


def token_for(runner):
    configured = os.environ.get('LOCAL_CI_TOKEN')
    if configured:
        return configured
    path = runner.state / 'api-token'
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(descriptor, 'w') as output:
        value = secrets.token_urlsafe(32)
        output.write(value)
    return value


def request_plan(runner, body):
    allowed = {'project', 'parameters', 'action', 'branch', 'trigger_source', 'schedule_name',
               'workflow', 'allow_network', 'platform', 'skip_push', 'config_path'}
    if set(body) - allowed:
        raise ValueError(f'Unknown fields: {set(body) - allowed}')
    if not isinstance(body.get('project'), str):
        raise ValueError('project must be a directory path')
    root = os.environ.get('LOCAL_CI_PROJECTS_ROOT')
    if root and not Path(body['project']).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Project must be under LOCAL_CI_PROJECTS_ROOT')
    if not isinstance(body.get('parameters', {}), dict):
        raise ValueError('parameters must be an object')
    if type(body.get('allow_network', False)) is not bool:
        raise ValueError('allow_network must be a boolean')
    if type(body.get('skip_push', False)) is not bool:
        raise ValueError('skip_push must be a boolean')
    if body.get('config_path') is not None:
        if not isinstance(body['config_path'], str):
            raise ValueError('config_path must be a YAML file path')
        config = Path(body['config_path']).resolve()
        if root and not (config.is_relative_to(Path(root).resolve()) or config.is_relative_to(runner.shared_root)):
            raise ValueError('Config must be under the project root or shared config root')
    return runner.plan(**body)


def serve(runner, host, port):
    token = token_for(runner)
    active = {}
    active_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, value):
            payload = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def handle_request(self):
            url = urlsplit(self.path)
            if self.command == 'GET' and url.path == '/health':
                return self.respond(200, {'status': 'ok'})
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode()):
                return self.respond(401, {'error': 'Bearer token required'})
            try:
                parts = url.path.strip('/').split('/')
                if self.command == 'POST':
                    length = int(self.headers.get('Content-Length', '0'))
                    if length < 0 or length > 1024 * 1024:
                        raise ValueError('Request body limit is 1 MiB')
                    body = json.loads(self.rfile.read(length) or b'{}')
                    if not isinstance(body, dict):
                        raise ValueError('Body must be a JSON object')
                    if parts == ['plans']:
                        return self.respond(200, request_plan(runner, body))
                    if parts == ['runs']:
                        plan = request_plan(runner, body)
                        run_id = runner.submit(plan, body.get('workflow'), body.get('allow_network', False),
                                               body.get('platform', os.environ.get('LOCAL_CI_PLATFORM', 'linux/amd64')))
                        worker = threading.Thread(target=runner.execute, args=(run_id,), daemon=True)
                        with active_lock:
                            active[run_id] = worker
                        worker.start()
                        return self.respond(202, {'id': run_id})
                    if len(parts) == 3 and parts[0] == 'runs' and parts[2] == 'cancel':
                        return self.respond(200, runner.cancel(parts[1]))
                if self.command == 'GET' and len(parts) >= 2 and parts[0] == 'runs':
                    if len(parts) == 2:
                        return self.respond(200, runner.status(parts[1]))
                    if len(parts) == 3 and parts[2] == 'logs':
                        job = parse_qs(url.query).get('job', [None])[0]
                        return self.respond(200, {'logs': runner.logs(parts[1], job)})
                return self.respond(404, {'error': 'Unknown endpoint'})
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.respond(400, {'error': str(error)})

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, *_):
            # Avoid writing request URLs/parameters to a second, unstructured log.
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.timeout = 1
    previous_term = signal.getsignal(signal.SIGTERM)
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    print(f'Local CI listening on {host}:{port}; token stored in {runner.state}/api-token', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with active_lock:
            workers = list(active.items())
        for run_id, worker in workers:
            if worker.is_alive():
                runner.cancel(run_id)
        for _, worker in workers:
            worker.join(timeout=5)
        signal.signal(signal.SIGTERM, previous_term)


def follow_logs(runner, run_id, job=None, output=None, stop=None):
    """Tail persisted output without replaying it; stop after the final drain."""
    output = output or sys.stdout
    stop = stop or threading.Event()
    path = runner.path(run_id)
    offsets, decoders, states = {}, {}, {}
    while True:
        status = runner.status(run_id)
        if job is not None and job not in status['jobs']:
            raise ValueError('Unknown job')
        finished = status['state'] not in {'queued', 'running'} or stop.is_set()
        for name, info in status['jobs'].items():
            if job is not None and name != job:
                continue
            if states.get(name) != info['state']:
                print(f'[{name}] {info["state"]}', file=output, flush=True)
                states[name] = info['state']
            logfile = path / info['log']
            if logfile.exists():
                with logfile.open('rb') as source:
                    source.seek(offsets.get(name, 0))
                    data = source.read()
                    offsets[name] = source.tell()
                decoder = decoders.setdefault(name, codecs.getincrementaldecoder('utf-8')(errors='replace'))
                text = decoder.decode(data, final=finished)
                if text:
                    print(text, end='', file=output, flush=True)
        if finished:
            return
        stop.wait(0.2)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run webdev-ci application workflows in local Docker containers')
    parser.add_argument('--state', default=os.environ.get('LOCAL_CI_STATE', '.local-ci'))
    parser.add_argument('--shared-root', default=os.environ.get('LOCAL_CI_SHARED_ROOT', str(Path(__file__).resolve().parent.parent)))
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('plan', 'run'):
        sub = commands.add_parser(command)
        sub.add_argument('--project', required=True)
        sub.add_argument('--parameters', help='Project parameter JSON; defaults to PROJECT/.circleci/application.json if present')
        sub.add_argument('--config', dest='config_path', help='Use a packed application YAML file instead of composing the shared config directory')
        sub.add_argument('--skip-push', action='store_true', default=None, help='Skip Git preparation and publication (already the default for local Edge builds)')
        themes = sub.add_mutually_exclusive_group()
        themes.add_argument('--build-themes', dest='build_themes', action='store_true', default=None)
        themes.add_argument('--no-build-themes', dest='build_themes', action='store_false')
        sub.add_argument('--set', action='append', default=[], metavar='NAME=JSON',
                         help='Override one parameter with a JSON value without editing project settings')
        sub.add_argument('--action', default='checks', choices=['none', 'auto', 'checks', 'edge-build', 'edge-refresh'])
        sub.add_argument('--branch', default='local')
        sub.add_argument('--trigger-source', default='api', choices=['api', 'webhook', 'scheduled_pipeline'])
        sub.add_argument('--schedule-name', default='')
        if command == 'run':
            sub.add_argument('--workflow')
            sub.add_argument('--live', action='store_true', help='Stream job activity and output to stderr while running')
            sub.add_argument('--allow-network', action='store_true', help='Allow job downloads and outbound network access')
            sub.add_argument('--platform', default=os.environ.get('LOCAL_CI_PLATFORM', 'linux/amd64'))
    for command in ('status', 'logs', 'cancel'):
        sub = commands.add_parser(command)
        sub.add_argument('run_id')
        if command == 'logs':
            sub.add_argument('--job')
            sub.add_argument('--follow', '-f', action='store_true', help='Follow output until the run finishes')
    sub = commands.add_parser('caches', help='List saved local caches for a project')
    sub.add_argument('--project', required=True)
    commands.add_parser('token', help='Print the local service bearer token')
    sub = commands.add_parser('serve')
    sub.add_argument('--host', default='127.0.0.1')
    sub.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    runner = Runner(args.state, args.shared_root)
    try:
        if args.command in {'plan', 'run'}:
            parameters_path = Path(args.parameters) if args.parameters else Path(args.project) / '.circleci/application.json'
            values = json.loads(parameters_path.read_text()) if parameters_path.exists() else {}
            if args.parameters and not parameters_path.exists():
                raise ValueError('Parameter file does not exist')
            if not isinstance(values, dict):
                raise ValueError('Parameter file must contain a JSON object')
            for override in args.set:
                key, separator, value = override.partition('=')
                if not separator or not key:
                    raise ValueError('--set requires NAME=JSON')
                values[key] = json.loads(value)
            if args.build_themes is not None:
                values['build_themes'] = args.build_themes
            plan = request_plan(runner, {'project': args.project, 'parameters': values, 'action': args.action,
                                        'branch': args.branch, 'trigger_source': args.trigger_source,
                                        'schedule_name': args.schedule_name,
                                        'skip_push': args.skip_push if args.skip_push is not None else args.action == 'edge-build',
                                        'config_path': args.config_path})
            if args.command == 'plan':
                print(json.dumps(plan, indent=2))
                return 0
            run_id = runner.submit(plan, args.workflow, args.allow_network, args.platform)
            print(json.dumps({'id': run_id}), flush=True)
            stop = threading.Event()
            follower = None
            if args.live:
                follower = threading.Thread(target=follow_logs, args=(runner, run_id),
                                            kwargs={'output': sys.stderr, 'stop': stop}, daemon=True)
                follower.start()
            try:
                status = runner.execute(run_id)
            except KeyboardInterrupt:
                runner.cancel(run_id)
                raise
            finally:
                stop.set()
                if follower:
                    follower.join()
            print(json.dumps(status, indent=2))
            return 0 if status['state'] == 'success' else 1
        if args.command == 'serve':
            serve(runner, args.host, args.port)
            return 0
        if args.command == 'token':
            print(token_for(runner))
            return 0
        if args.command == 'caches':
            print(json.dumps(runner.caches(args.project), indent=2))
            return 0
        if args.command == 'logs':
            if args.follow:
                follow_logs(runner, args.run_id, args.job)
            else:
                print(runner.logs(args.run_id, args.job), end='')
            return 0
        print(json.dumps(getattr(runner, args.command)(args.run_id), indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'local-ci: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
