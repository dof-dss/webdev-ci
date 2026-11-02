"""Small Docker executor for the application configuration, not a CircleCI emulator."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import subprocess
import tarfile
import tempfile
import time
import uuid

import yaml

from .compiler import compile_application, compile_config


def timestamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def duration(value):
    match = re.fullmatch(r'(\d+)(s|m|h)', str(value))
    if not match:
        raise ValueError(f'Invalid timeout: {value}')
    return int(match[1]) * {'s': 1, 'm': 60, 'h': 3600}[match[2]]


def safe_source_path(name):
    """Never automatically copy likely credential stores or Git metadata."""
    path = PurePosixPath(name)
    forbidden = {'.git', '.ssh', '.aws', '.gnupg', '.local-ci', '__pycache__',
                 'auth.json', '.npmrc', '.netrc', '.pypirc', 'credentials',
                 'credentials.json', 'settings.local.php', '.env', 'id_rsa',
                 'id_ed25519'}
    return (not path.is_absolute() and '..' not in path.parts
            and not any(part in forbidden or part.startswith('.env.')
                        or part.endswith(('.pem', '.key', '.p12', '.pfx')) for part in path.parts))


def snapshot(project, destination):
    """Copy working-tree bytes selected by Git; do not alter the user's checkout."""
    files = subprocess.check_output(
        ['git', '-c', f'safe.directory={project}', '-C', str(project),
         'ls-files', '-z', '--cached', '--others', '--exclude-standard'])
    included = []
    with tarfile.open(destination, 'w') as archive:
        for raw in sorted(set(files.split(b'\0')) - {b''}):
            name = os.fsdecode(raw)
            path = project / name
            if not safe_source_path(name) or not path.exists() or path.is_symlink() or not path.is_file():
                continue
            # A tracked directory must not redirect reads outside the selected project.
            if not path.resolve().is_relative_to(project.resolve()):
                continue
            archive.add(path, arcname=name, recursive=False)
            included.append(name)
    return included


def configured_fingerprint(project):
    """Read static CircleCI defaults/continuation parameters; never execute setup."""
    path = project / '.circleci/config.yml'
    if not path.is_file():
        return None
    try:
        config = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as error:
        raise ValueError(f'Invalid YAML in {path}') from error
    candidates = []
    declaration = config.get('parameters', {}).get('ssh_fingerprint', {})
    if isinstance(declaration, dict) and 'default' in declaration:
        candidates.append(declaration['default'])
    for job in config.get('jobs', {}).values():
        for step in job.get('steps', []):
            if not isinstance(step, dict):
                continue
            continuation = step.get('continuation/continue')
            if not isinstance(continuation, dict):
                continue
            values = continuation.get('parameters', {})
            if isinstance(values, str):
                try:
                    values = json.loads(values)
                except ValueError:
                    continue
            if isinstance(values, dict) and 'ssh_fingerprint' in values:
                candidates.append(values['ssh_fingerprint'])
    candidates = {value for value in candidates if isinstance(value, str) and value
                  and '<<' not in value and not value.startswith('REPLACE_')}
    if len(candidates) > 1:
        raise ValueError('Multiple SSH fingerprints in config.yml; select one with --set')
    return next(iter(candidates), None)


class DockerCommandError(subprocess.CalledProcessError):
    """Keep Docker's diagnostic without dumping command arguments/environment."""
    def __str__(self):
        detail = self.stderr or self.stdout or b'No diagnostic output was returned.'
        if isinstance(detail, bytes):
            detail = detail.decode(errors='replace')
        operation = self.cmd[1] if len(self.cmd) > 1 else 'command'
        return f'Docker {operation} failed (exit {self.returncode}): {detail.strip()}'


class Docker:
    def call(self, *args, **kwargs):
        try:
            result = subprocess.run(['docker', *args], check=True, capture_output=True, **kwargs)
            # Docker returns container stderr separately even when fetching logs.
            return result.stdout + result.stderr if args and args[0] == 'logs' else result.stdout
        except subprocess.CalledProcessError as error:
            raise DockerCommandError(error.returncode, error.cmd, output=error.stdout,
                                     stderr=error.stderr) from error

    def copy_to(self, container, source, target):
        self.call('cp', str(source), f'{container}:{target}')

    def export_project(self, container, workdir, destination):
        # Build results exclude dependency trees and the same known credential names
        # excluded from source snapshots. This also avoids exporting container secrets.
        exclusions = ['.git', '.local-ci', '__pycache__', 'vendor', 'node_modules', '.env', '.env.*', '.ssh', '.aws', '.gnupg',
                      'auth.json', '.npmrc', '.netrc', '.pypirc', 'credentials', 'credentials.json',
                      'settings.local.php', 'id_rsa', 'id_ed25519', '*.pem', '*.key', '*.p12', '*.pfx']
        args = ['docker', 'exec', container, 'tar', '-cf', '-', '-C', workdir]
        args += [f'--exclude={pattern}' for pattern in exclusions]
        with destination.open('wb') as output:
            subprocess.run([*args, '.'], stdout=output, stderr=subprocess.PIPE, check=True)

    def stream(self, args, log, cancelled, timeout):
        """Stream without buffering; enforce cancellation and output inactivity timeout."""
        process = subprocess.Popen(['docker', *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        last_output = time.monotonic()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                if cancelled():
                    raise RuntimeError('Run cancelled')
                if time.monotonic() - last_output > timeout:
                    raise RuntimeError(f'No output for {timeout}s')
                for key, _ in selector.select(0.2):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    log.write(data)
                    log.flush()
                    last_output = time.monotonic()
                if not selector.get_map() and process.poll() is not None:
                    return process.returncode
        finally:
            selector.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()


class Runner:
    def __init__(self, state, shared_root, docker=None):
        self.state = Path(state).resolve()
        self.shared_root = Path(shared_root).resolve()
        self.docker = docker or Docker()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.state / 'runs').mkdir(exist_ok=True)
        (self.state / 'caches').mkdir(exist_ok=True)

    def plan(self, project, parameters=None, action='checks', branch='local',
             trigger_source='api', schedule_name='', skip_push=None, config_path=None, **ignored):
        project = Path(project).resolve(strict=True)
        if skip_push is None:
            skip_push = action == 'edge-build'
        if type(skip_push) is not bool:
            raise ValueError('skip_push must be a boolean')
        if skip_push and action != 'edge-build':
            raise ValueError('--skip-push is supported only for edge-build; Edge refresh remains blocked')
        values = dict(parameters or {})
        fingerprint = values.get('ssh_fingerprint')
        if fingerprint is None or (isinstance(fingerprint, str) and fingerprint.startswith('REPLACE_')):
            inherited = configured_fingerprint(project)
            if inherited is not None:
                values['ssh_fingerprint'] = inherited
        values['ci_action'] = action
        compiler = compile_config if config_path else compile_application
        result = compiler(Path(config_path) if config_path else self.shared_root, values, branch=branch,
                          trigger_source=trigger_source, schedule_name=schedule_name)
        if action == 'edge-build' and result['parameters']['build_themes'] and not result['parameters']['theme_build_script']:
            raise ValueError('build_themes requires a configured theme_build_script')
        result['project'] = str(project)
        if skip_push:
            result['local_execution'] = {'skip_push': True, 'source': 'working-tree-snapshot',
                                         'ssh_keys': 'not_loaded; hosted fingerprints retained as metadata'}
            for workflow in result['workflows'].values():
                for job in workflow['jobs']:
                    if job['definition'] == 'edge_build_shared':
                        job['config'].setdefault('environment', {})['EDGE_SKIP_PUBLISH'] = 'true'
        return result

    @staticmethod
    def local_edge(plan, job):
        return (plan.get('local_execution', {}).get('skip_push') is True
                and job.get('definition', job['name']) == 'edge_build_shared')

    def verify_edge_bundle(self, job):
        bundles = []
        for step in job['config']['steps']:
            if isinstance(step, dict) and isinstance(step.get('run'), dict):
                bundle = step['run'].get('environment', {}).get('CI_SHARED_BUNDLE')
                if bundle:
                    bundles.append(bundle)
        try:
            if len(bundles) != 1:
                raise ValueError('Expected one shared script bundle')
            with tarfile.open(fileobj=io.BytesIO(base64.b64decode(bundles[0], validate=True)), mode='r:gz') as archive:
                for name in ('build.sh', 'build-application.sh'):
                    member = archive.extractfile('scripts/edge/' + name)
                    if member is None or member.read() != (self.shared_root / 'scripts/edge' / name).read_bytes():
                        raise ValueError('Unsupported bundled Edge build script')
        except (ValueError, KeyError, OSError, tarfile.TarError) as error:
            raise ValueError('Local Edge requires the current shared build scripts; recompose the config with this webdev-ci checkout') from error

    def path(self, run_id):
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise ValueError('Invalid run ID')
        path = self.state / 'runs' / run_id
        if not path.is_dir():
            raise ValueError('Unknown run ID')
        return path

    def status(self, run_id):
        return json.loads((self.path(run_id) / 'status.json').read_text())

    def cancel(self, run_id):
        path = self.path(run_id)
        (path / 'cancel').touch()
        return {'id': run_id, 'cancel_requested': True}

    def logs(self, run_id, job=None):
        path = self.path(run_id)
        statuses = self.status(run_id)['jobs']
        if job is not None and job not in statuses:
            raise ValueError('Unknown job')
        names = [job] if job else statuses
        return ''.join(f'--- {name} ---\n' + (path / statuses[name]['log']).read_text(errors='replace')
                       for name in names if (path / statuses[name]['log']).exists())

    def cache_directory(self, project):
        scope = hashlib.sha256(str(Path(project).resolve()).encode()).hexdigest()[:24]
        return self.state / 'caches' / scope

    def caches(self, project):
        """List committed caches, newest first; partial saves are never reusable."""
        entries = []
        for metadata in self.cache_directory(project).glob('*.json'):
            try:
                entry = json.loads(metadata.read_text())
                key = entry['key']
                if not isinstance(key, str) or metadata.stem != hashlib.sha256(key.encode()).hexdigest():
                    continue
                archive = metadata.with_suffix('.tar')
                size = archive.stat().st_size
                if not size:
                    continue
                if 'bytes' in entry and entry['bytes'] != size:
                    continue
                entry.update(bytes=size, archive=str(archive))
                entries.append((metadata.stat().st_mtime_ns, entry))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return [entry for _, entry in sorted(entries, key=lambda item: item[0], reverse=True)]

    def validate(self, plan, workflow):
        if workflow not in plan['workflows']:
            raise ValueError(f'Workflow {workflow!r} not selected; available: {list(plan["workflows"])}')
        if workflow == 'build-edge-finalise':
            raise ValueError('Edge refresh remains plan-only: its scripts mutate hosted data')
        jobs = plan['workflows'][workflow]['jobs']
        if workflow == 'build-edge' and plan.get('local_execution', {}).get('skip_push') is not True:
            raise ValueError('Edge build execution requires --skip-push; local publication is disabled')
        for job in jobs:
            definition = job.get('definition', job['name'])
            if definition == 'edge_refresh_shared':
                raise ValueError('Edge refresh remains plan-only: its scripts mutate hosted data')
            if definition == 'edge_build_shared':
                if not self.local_edge(plan, job):
                    raise ValueError('Edge build execution requires --skip-push')
                self.verify_edge_bundle(job)
                if job['config'].get('environment', {}).get('EDGE_SKIP_PUBLISH') != 'true':
                    raise ValueError('Local Edge must disable publication')
            config = job['config']
            allowed = {'description', 'docker', 'steps', 'shell', 'working_directory', 'environment'}
            if set(config) - allowed:
                raise ValueError(f'Unsupported job options: {set(config) - allowed}')
            if len(config.get('docker', [])) != 1:
                raise ValueError('Exactly one Docker image is required')
            if set(config['docker'][0]) - {'image', 'environment', 'user'}:
                raise ValueError('Unsupported Docker image options')
            for step in config['steps']:
                kind, body = (step, {}) if isinstance(step, str) else next(iter(step.items()))
                if kind not in {'checkout', 'run', 'add_ssh_keys', 'persist_to_workspace',
                                'attach_workspace', 'restore_cache', 'save_cache'}:
                    raise ValueError(f'Unsupported step: {kind}')
                if kind == 'run':
                    body = {'command': body} if isinstance(body, str) else body
                    if set(body) - {'command', 'name', 'shell', 'environment', 'working_directory',
                                    'no_output_timeout', 'when'}:
                        raise ValueError(f'Unsupported run options: {set(body)}')
                    if not isinstance(body.get('command'), str):
                        raise ValueError('Run command must be a string')
                    if body.get('when', 'on_success') not in {'on_success', 'on_fail', 'always'}:
                        raise ValueError('Unsupported run when')
                    duration(body.get('no_output_timeout', '10m'))
                if kind == 'add_ssh_keys' and any(body.get('fingerprints', [])) and not self.local_edge(plan, job):
                    raise ValueError('Local SSH key injection is not implemented; no host keys are loaded')
                if kind in {'persist_to_workspace', 'save_cache'}:
                    if not isinstance(body.get('paths'), list) or not body['paths']:
                        raise ValueError('Persist/cache paths must be a nonempty list')
                if kind == 'save_cache':
                    self.validate_key(body['key'])
                if kind == 'restore_cache':
                    for key in body.get('keys', [body.get('key', '')]):
                        self.validate_key(key)
        return jobs

    @staticmethod
    def validate_key(key):
        without_checksums = re.sub(r'{{\s*checksum\s+"[^"]+"\s*}}', '', key)
        if '{{' in without_checksums:
            raise ValueError('Only checksum "file" cache templates are supported')

    def submit(self, plan, workflow=None, allow_network=False, platform=None):
        selected = list(plan['workflows'])
        workflow = workflow or (selected[0] if len(selected) == 1 else None)
        jobs = self.validate(plan, workflow)
        run_id = uuid.uuid4().hex
        path = self.state / 'runs' / run_id
        path.mkdir(mode=0o700)
        status = {'id': run_id, 'workflow': workflow, 'state': 'queued', 'created_at': timestamp(),
                  'allow_network': allow_network, 'platform': platform,
                  'local_execution': plan.get('local_execution'),
                  'jobs': {job['name']: {'state': 'queued', 'log': f'job-{index}.log', 'steps': []}
                           for index, job in enumerate(jobs)}}
        write_json(path / 'plan.json', plan)
        write_json(path / 'status.json', status)
        return run_id

    def execute(self, run_id):
        path = self.path(run_id)
        # A cross-process lock also covers direct CLI runs and persistent caches.
        with (self.state / 'runner.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._execute(run_id, path)

    def _execute(self, run_id, path):
        status = self.status(run_id)
        if status['state'] != 'queued':
            raise ValueError('Only queued runs may be executed')
        plan = json.loads((path / 'plan.json').read_text())
        jobs = self.validate(plan, status['workflow'])
        def save():
            write_json(path / 'status.json', status)
        cancelled = lambda: (path / 'cancel').exists()
        status.update(state='running', started_at=timestamp())
        save()
        workspace = []
        ancestors = {}
        try:
            if cancelled():
                raise RuntimeError('Run cancelled')
            source = path / 'source.tar'
            files = snapshot(Path(plan['project']), source)
            write_json(path / 'source-files.json', files)
            pending = list(jobs)
            while pending:
                ready = next((job for job in pending if all(dep in ancestors for dep in job['requires'])), None)
                if ready is None:
                    raise ValueError('Unresolvable job dependencies')
                pending.remove(ready)
                name = ready['name']
                ancestors[name] = set(ready['requires'])
                for dep in ready['requires']:
                    ancestors[name].update(ancestors[dep])
                if cancelled() or any(status['jobs'][dep]['state'] != 'success' for dep in ready['requires']):
                    status['jobs'][name]['state'] = 'cancelled' if cancelled() else 'blocked'
                    save()
                    continue
                self._job(ready, status, path, source, workspace, ancestors[name], plan, cancelled, save)
            status['state'] = ('cancelled' if cancelled() else
                               'success' if all(job['state'] == 'success' for job in status['jobs'].values())
                               else 'failed')
        except (Exception, KeyboardInterrupt) as error:
            if isinstance(error, KeyboardInterrupt):
                self.cancel(run_id)
            status.update(state='cancelled' if cancelled() else 'failed', error=str(error))
        finally:
            for job in status['jobs'].values():
                if job['state'] == 'queued':
                    job['state'] = 'cancelled' if cancelled() else 'blocked'
            status['finished_at'] = timestamp()
            save()
        return status

    def _job(self, job, status, path, source, workspace, ancestors, plan, cancelled, save):
        info = status['jobs'][job['name']]
        config = job['config']
        image = config['docker'][0]
        workdir = config.get('working_directory', '/home/docker/project')
        if not workdir.startswith('/') or workdir.startswith('/root'):
            raise ValueError('Use an absolute working_directory outside /root')
        container = None
        info.update(state='running', started_at=timestamp())
        save()
        with (path / info['log']).open('ab', buffering=0) as log:
            try:
                log.write(f'[{timestamp()}] Starting container: {image["image"]}\n'.encode())
                args = ['create', '--pull=never', '--label', f'webdev-ci.local-run={status["id"]}',
                        '--name', f'webdev-ci-{status["id"][:12]}-{uuid.uuid4().hex[:8]}',
                        '--network', 'bridge' if status['allow_network'] else 'none',
                        '--workdir', workdir]
                if status['platform']:
                    args += ['--platform', status['platform']]
                if image.get('user'):
                    args += ['--user', str(image['user'])]
                environment = {**image.get('environment', {}), **config.get('environment', {}),
                               'BASH_ENV': '/tmp/local-ci-bash-env', 'CIRCLECI': 'true',
                               'CI': 'true', 'CIRCLE_JOB': job['name'],
                               'CIRCLE_BRANCH': plan['source']['branch'], 'CIRCLE_WORKING_DIRECTORY': workdir}
                if self.local_edge(plan, job):
                    environment['EDGE_SKIP_PUBLISH'] = 'true'
                for key, value in environment.items():
                    args += ['--env', f'{key}={self.env_string(value)}']
                args += [image['image'], '/bin/sh', '-c',
                         'touch /tmp/local-ci-ready; trap "exit 0" TERM INT; while :; do sleep 1; done']
                container = self.docker.call(*args).decode().strip()
                info['container'] = container
                save()
                self.docker.call('start', container)
                deadline = time.monotonic() + 60
                while True:
                    if cancelled():
                        raise RuntimeError('Run cancelled')
                    try:
                        self.docker.call('exec', container, 'test', '-e', '/tmp/local-ci-ready')
                        break
                    except subprocess.CalledProcessError:
                        state = self.docker.call('inspect', '--format', '{{.State.Status}}', container).decode().strip()
                        if state in {'exited', 'dead'}:
                            startup_log = self.docker.call('logs', '--tail', '50', container)
                            log.write(startup_log)
                            raise RuntimeError(f'Image entrypoint exited before job startup ({state}); see startup logs')
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Image entrypoint did not finish startup within 60s')
                        time.sleep(0.1)
                uid = self.docker.call('exec', container, 'id', '-u').decode().strip()
                gid = self.docker.call('exec', container, 'id', '-g').decode().strip()
                self.docker.call('exec', '--user', '0', container, '/bin/sh', '-c',
                                 f'mkdir -p {shlex.quote(workdir)}; touch /tmp/local-ci-bash-env; '
                                 f'chown {int(uid)}:{int(gid)} {shlex.quote(workdir)} /tmp/local-ci-bash-env')
                failed = False
                for index, step in enumerate(config['steps']):
                    if cancelled():
                        raise RuntimeError('Run cancelled')
                    kind, body = (step, {}) if isinstance(step, str) else next(iter(step.items()))
                    if kind == 'run' and isinstance(body, str):
                        body = {'command': body}
                    when = body.get('when', 'on_success') if kind == 'run' else 'on_success'
                    record = {'index': index, 'kind': kind, 'name': body.get('name', kind),
                              'state': 'skipped', 'started_at': timestamp()}
                    info['steps'].append(record)
                    if (failed and when == 'on_success') or (not failed and when == 'on_fail'):
                        save()
                        continue
                    record['state'] = 'running'
                    save()
                    log.write(f'\n[{timestamp()}] step {index}: {record["name"]}\n'.encode())
                    try:
                        code = self._step(kind, body, container, config, workdir, path, source,
                                          workspace, ancestors, job['name'], plan, log, cancelled)
                        record.update(state='success' if code == 0 else 'failed', exit_code=code)
                        failed |= code != 0
                    except Exception as error:
                        record.update(state='failed', error=str(error))
                        log.write(f'{error}\n'.encode())
                        failed = True
                    record['finished_at'] = timestamp()
                    log.write(f'[{timestamp()}] step {index}: {record["state"]} (exit {record.get("exit_code", "unknown")})\n'.encode())
                    save()
                info['state'] = 'cancelled' if cancelled() else 'failed' if failed else 'success'
            except (Exception, KeyboardInterrupt) as error:
                if isinstance(error, KeyboardInterrupt):
                    self.cancel(status['id'])
                info.update(state='cancelled' if cancelled() else 'failed', error=str(error))
                log.write(f'{error}\n'.encode())
            finally:
                if container:
                    if self.local_edge(plan, job):
                        try:
                            log.write(f'[{timestamp()}] Collecting build output\n'.encode())
                            self.capture_artifacts(job, container, workdir, path, source, info)
                        except Exception as error:
                            info['artifact_error'] = str(error)
                            if info['state'] == 'success':
                                info['state'] = 'failed'
                            log.write(f'Could not collect build output: {error}\n'.encode())
                    try:
                        self.docker.call('stop', '--time', '1', container)
                    except subprocess.CalledProcessError as error:
                        log.write(error.stderr or b'Failed to stop container\n')
                info['finished_at'] = timestamp()
                for record in info['steps']:
                    if record['state'] == 'running':
                        record.update(state='cancelled' if cancelled() else 'failed', finished_at=timestamp())
                save()

    def capture_artifacts(self, job, container, workdir, path, source, info):
        directory = path / ('artifacts-' + info['log'].removesuffix('.log'))
        directory.mkdir(exist_ok=True)
        target = directory / 'build-output.tar'
        def hashes(archive_path):
            result = {}
            with tarfile.open(archive_path) as archive:
                for member in archive:
                    name = member.name.removeprefix('./')
                    if member.isfile() and safe_source_path(name) and not any(part in {'vendor', 'node_modules'} for part in PurePosixPath(name).parts):
                        result[name] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
            return result
        # Filter again before exposing the bundle: no symlinks, hard links, absolute
        # paths, traversal or known secret names are carried into exported output.
        with tempfile.TemporaryDirectory(prefix='.capture-', dir=directory) as temporary:
            raw = Path(temporary) / 'container-output.tar'
            self.docker.export_project(container, workdir, raw)
            with tarfile.open(raw) as archive, tarfile.open(target, 'w') as output:
                for member in archive:
                    name = member.name.removeprefix('./')
                    if member.isfile() and safe_source_path(name) and not any(part in {'vendor', 'node_modules'} for part in PurePosixPath(name).parts):
                        member.name = name
                        output.addfile(member, archive.extractfile(member))
        before, after = hashes(source), hashes(target)
        changes = {'added': sorted(after.keys() - before.keys()),
                   'modified': sorted(name for name in before.keys() & after.keys() if before[name] != after[name]),
                   'deleted': sorted(before.keys() - after.keys()),
                   'excluded': ['vendor/', 'node_modules/', 'symlinks', 'known credential files'],
                   'publication': 'skipped', 'source': 'working-tree-snapshot'}
        write_json(directory / 'changes.json', changes)
        info['artifacts'] = {'archive': str(target.relative_to(path)),
                             'changes': str((directory / 'changes.json').relative_to(path))}

    @staticmethod
    def env_string(value):
        return str(value).lower() if isinstance(value, bool) else str(value)

    def _step(self, kind, body, container, config, workdir, path, source, workspace,
              ancestors, job_name, plan, log, cancelled):
        if kind == 'add_ssh_keys':
            if any(body.get('fingerprints', [])):
                log.write(b'Configured CircleCI SSH fingerprints retained. Local snapshot checkout needs no SSH key; no private key was loaded. Private dependency authentication is not configured.\n')
            else:
                log.write(b'No SSH keys requested or injected.\n')
        elif kind == 'checkout':
            self.restore(container, source, workdir)
        elif kind == 'run':
            args = ['exec', '--workdir', self.expand_path(body.get('working_directory', workdir), workdir)]
            environment = dict(body.get('environment', {}))
            definitions = {job['name']: job for workflow in plan['workflows'].values() for job in workflow['jobs']}
            if self.local_edge(plan, definitions.get(job_name, {'name': job_name})):
                environment['EDGE_SKIP_PUBLISH'] = 'true'
            for key, value in environment.items():
                args += ['--env', f'{key}={self.env_string(value)}']
            script = path / f'script-{uuid.uuid4().hex}.sh'
            script.write_text(body['command'] + '\n')
            target = f'/tmp/{script.name}'
            self.docker.copy_to(container, script, target)
            args += [container, *shlex.split(body.get('shell', config.get('shell', '/bin/bash -eo pipefail'))), target]
            return self.docker.stream(args, log, cancelled, duration(body.get('no_output_timeout', '10m')))
        elif kind == 'persist_to_workspace':
            archive = path / f'workspace-{len(workspace)}.tar'
            root = self.expand_path(body['root'], workdir)
            self.archive(container, root, body['paths'], archive)
            workspace.append({'job': job_name, 'archive': archive, 'ancestors': set(ancestors)})
        elif kind == 'attach_workspace':
            # Conflicting files from independent ancestors fail rather than overwrite.
            seen = {}
            for layer in workspace:
                if layer['job'] not in ancestors:
                    continue
                with tarfile.open(layer['archive']) as archive:
                    members = {member.name.removeprefix('./') for member in archive.getmembers() if not member.isdir()}
                collisions = {member for member in members if member in seen
                              and seen[member] != layer['job']
                              and seen[member] not in layer.get('ancestors', set())}
                if collisions:
                    raise ValueError(f'Workspace file collision: {sorted(collisions)[:3]}')
                seen.update({member: layer['job'] for member in members})
                self.restore(container, layer['archive'], self.expand_path(body['at'], workdir))
        elif kind in {'save_cache', 'restore_cache'}:
            cache = self.cache_directory(plan['project'])
            cache.mkdir(exist_ok=True)
            if kind == 'save_cache':
                key = self.cache_key(container, body['key'], workdir)
                name = hashlib.sha256(key.encode()).hexdigest()
                target = cache / f'{name}.tar'
                if any(entry['key'] == key for entry in self.caches(plan['project'])):
                    log.write(f'Cache already saved (immutable): {key}\n'.encode())
                    return 0
                # Archive explicit container paths relative to /. Missing optional
                # directories must not invalidate the other cache contents.
                paths = [self.container_path(container, item, workdir).lstrip('/') for item in body['paths']]
                self.validate_archive_paths(paths)
                existing = []
                for item in paths:
                    try:
                        self.docker.call('exec', container, '/bin/sh', '-c',
                                         'if test -e "$1" || test -L "$1"; then exit 0; else exit 3; fi',
                                         'local-ci-cache', '/' + item)
                    except subprocess.CalledProcessError as error:
                        if error.returncode != 3:
                            raise
                        log.write(f'Cache path missing; skipped: /{item}\n'.encode())
                    else:
                        existing.append(item)
                if not existing:
                    log.write(f'Cache not saved (no existing paths): {key}\n'.encode())
                    return 0
                log.write(f'Saving cache: {key}\n'.encode())
                # Only publish the metadata after a complete archive is in place.
                # execute() holds the cross-process runner lock during this write.
                with tempfile.TemporaryDirectory(prefix='.save-', dir=cache) as staging:
                    archive = Path(staging) / 'cache.tar'
                    self.archive(container, '/', existing, archive)
                    size = archive.stat().st_size
                    archive.replace(target)
                    write_json(cache / f'{name}.json', {'key': key, 'created_at': timestamp(),
                                                       'bytes': size, 'paths': ['/' + item for item in existing]})
                log.write(f'Cache saved: {key} ({size} bytes)\n'.encode())
            else:
                entries = self.caches(plan['project'])
                for template in body.get('keys', [body.get('key', '')]):
                    key = self.cache_key(container, template, workdir)
                    match = next((entry for entry in entries if entry['key'].startswith(key)), None)
                    if match:
                        log.write(f'Restoring cache: {match["key"]} (requested: {key})\n'.encode())
                        self.restore(container, Path(match['archive']), '/')
                        log.write(f'Cache restored: {match["key"]} ({match["bytes"]} bytes)\n'.encode())
                        break
                    log.write(f'Cache miss: {key}\n'.encode())
                else:
                    log.write(b'No matching local cache.\n')
        return 0

    def cache_key(self, container, template, workdir):
        def checksum(match):
            filename = self.expand_path(match[1], workdir)
            content = self.docker.call('exec', container, 'cat', filename)
            return hashlib.sha256(content).hexdigest()
        return re.sub(r'{{\s*checksum\s+"([^"]+)"\s*}}', checksum, template)

    @staticmethod
    def expand_path(value, workdir):
        if value.startswith('~') or '$' in value:
            raise ValueError('This path must be absolute or relative to the job working directory')
        return value if value.startswith('/') else str(PurePosixPath(workdir) / value)

    def container_path(self, container, value, workdir):
        if value.startswith('~/'):
            home = self.docker.call('exec', container, '/bin/sh', '-c', 'printf %s "$HOME"').decode()
            return home + value[1:]
        return self.expand_path(value, workdir)

    @staticmethod
    def validate_archive_paths(paths):
        for item in paths:
            if item.startswith('/') or '..' in PurePosixPath(item).parts:
                raise ValueError('Persist paths must stay under their root')
            if any(character in item for character in '*?['):
                raise ValueError('Persist path globs are not supported; use explicit paths')

    def archive(self, container, root, paths, target):
        # Pass literal paths as argv; no shell evaluation or wildcard expansion.
        self.validate_archive_paths(paths)
        with target.open('wb') as output:
            subprocess.run(['docker', 'exec', container, 'tar', '-cf', '-', '-C', root, '--', *paths],
                           stdout=output, stderr=subprocess.PIPE, check=True)

    def restore(self, container, archive, target):
        remote = f'/tmp/{uuid.uuid4().hex}.tar'
        self.docker.copy_to(container, archive, remote)
        self.docker.call('exec', container, '/bin/sh', '-c',
                         f'mkdir -p {shlex.quote(target)} && tar -xf {shlex.quote(remote)} -C {shlex.quote(target)}')
