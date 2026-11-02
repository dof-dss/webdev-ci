# Local CI runner

The local runner combines this checkout's shared application configuration with
a project's `.circleci/application.json`, resolves a selected action and runs
supported jobs in Docker. It is an MVP for debugging the new application
workflows, not a complete CircleCI implementation.

The interface is a Python CLI and a token-authenticated HTTP API. There is no web
UI. The runner uses Python's standard library for its server and orchestration,
with a pinned PyYAML dependency for configuration loading. Docker Compose keeps
the runner, its dependencies and persistent state together on macOS or Linux.

## Architecture

```text
webdev-ci shared configuration + project parameters
                      |
                 local compiler
                      |
             resolved workflow plan
                      |
             local runner and state
                      |
       isolated Docker container for each job
                      |
      logs / workspace layers / project caches
```

The runner talks to the host Docker daemon through its socket. It creates
separate job containers; it does not run Docker inside Docker. The Compose
service holds persistent state in a named volume. Job source is a snapshot, so
commands in a job do not edit the project checkout.

The socket gives the runner control of the Docker daemon. Use it only with
configuration and project code you trust. The API binds to `127.0.0.1:8765` and
requires a token; it should not be exposed as a public service.

## Configuration and execution

Start from the `webdev-ci` checkout. Set `LOCAL_CI_PROJECTS` to the directory
containing the application checkouts you want to inspect. Compose mounts this
directory read-only at `/projects`:

```bash
export LOCAL_CI_PROJECTS=/absolute/path/to/projects
docker compose -f compose.local-ci.yml build
docker compose -f compose.local-ci.yml up -d
```

Building the runner downloads its base images, operating system packages and
pinned Python dependency. Prepare the job images selected by the plan separately
with `docker pull --platform linux/amd64 IMAGE` (adjust the platform if needed).
Job execution uses `--pull=never`, so a missing image fails rather than silently
downloading one. The default job platform is `linux/amd64`; use
`LOCAL_CI_PLATFORM` or `--platform` for another platform when the images support
it. Apple Silicon may need Docker's amd64 emulation for the default images.

`LOCAL_CI_DOCKER_SOCKET` defaults to `/var/run/docker.sock`. Set it to your
daemon's socket path if needed, for example with a rootless Linux setup.

The following commands show the CLI as it runs inside the service, where shared
configuration is mounted at `/shared` and the project root at `/projects`:

```bash
python -m local_ci plan \
  --project /projects/example \
  --parameters /projects/example/.circleci/application.json \
  --action checks

python -m local_ci run \
  --project /projects/example \
  --parameters /projects/example/.circleci/application.json \
  --action checks \
  --workflow build-test
```

Invoke those commands through Compose by replacing `python -m local_ci` with
`docker compose -f compose.local-ci.yml run --rm local-ci`, for example:

```bash
docker compose -f compose.local-ci.yml run --rm local-ci plan \
  --project /projects/example \
  --parameters /projects/example/.circleci/application.json \
  --action checks
```

The CLI reads and writes local state directly. `run` stays attached until the
run finishes; it does not require the HTTP server. Compose sets
`LOCAL_CI_STATE=/state` and shares the named state volume with each CLI
invocation. Native CLI use defaults to `.local-ci` unless `LOCAL_CI_STATE` is set.

Use the workflow name reported by `plan`. Planning resolves configuration and
shows the jobs before execution. It does not contact CircleCI's compilation
service. The local compiler supports the application's declared parameters and
the subset of configuration implemented by this runner. Unsupported features
fail explicitly; successful planning does not imply full CircleCI compatibility.

Manual actions and otherwise scheduled actions use the same `--action` option.
Schedules are descriptive metadata here: the service has no scheduler, and
selecting a scheduled action runs it only when requested.

Edge builds default to `--skip-push`, which selects the local, unpublished
build path described below. Edge refresh remains plan-only because it modifies
hosted data. Explicitly requesting publication through the API is rejected before execution.

Job networking is disabled by default. Pass `--allow-network` explicitly when a
supported run needs package registries or another network service. This grants
the job network access, so review the plan and its commands first. Explicit
image builds and pulls require network access independently of job networking.

No host environment variables, SSH agent, SSH keys or cloud credentials are
automatically forwarded. Local Edge builds retain configured SSH fingerprints
as metadata and log that no private key is loaded; snapshot checkout requires
no SSH authentication. Other workflows still reject nonempty fingerprints until
local key injection is implemented. The source
snapshot includes tracked and nonignored project files, excluding `.git` and
known secret files. All symlinks are omitted, including links within the project.
The snapshot uses current working-tree bytes, including uncommitted edits and
nonignored new files; it does not clone a branch or include Git history. Review
your project for credentials stored under unexpected names before running it;
filename exclusions cannot identify every secret.

For a check run that does not need SSH, clear its hosted fingerprint without
editing the project file using `--set 'ssh_fingerprint=""'`. Other typed
overrides use the same `--set NAME=JSON` syntax, for example
`--set 'check_illegal_updates=false'`. `--allow-network` is usually needed for
Composer downloads; private dependencies still require credential support,
which this MVP does not provide.

## Evaluate a CORP-LITE Edge build

Use the same composed application configuration, expanded commands and shared
Edge build script as hosted CI. Local execution changes source checkout to a
working-tree snapshot and explicitly disables the publication phase. It runs
Composer and, when enabled, the theme toolchain and configured theme script.
It does not fetch `source_branch`, create branches, commit or push. Consequently
it cannot trigger an Upsun deployment through the shared publication script.
Project scripts and Composer plugins still execute normally; review them for
other external side effects before enabling network access.

From `webdev-ci`, define a convenience command:

```bash
lci() {
  docker compose -f compose.local-ci.yml run --rm local-ci "$@"
}
```

Replace `YOUR_CORP_LITE_DIRECTORY` with the actual checkout directory beneath
`LOCAL_CI_PROJECTS`. Plan a dependency-only build first:

```bash
lci plan \
  --project /projects/YOUR_CORP_LITE_DIRECTORY \
  --parameters /projects/YOUR_CORP_LITE_DIRECTORY/.circleci/application.json \
  --action edge-build --no-build-themes
```

If the project has not adopted `.circleci/application.json`, substitute
`--parameters /projects/webdev-ci/templates/application/corp-lite.json` when
`webdev-ci` is also beneath the mounted projects directory. That template
currently selects `dof-dss/nicsdru_origins_modules:dev-11.x`; review the effective
parameters against the project's intended build before executing.

The plan should select `build-edge`, show `EDGE_SKIP_PUBLISH=true` in the
expanded job environment, and omit the theme toolchain step. Prepare its Docker
image separately if it is not cached. Then execute the same selection:

```bash
lci run \
  --project /projects/YOUR_CORP_LITE_DIRECTORY \
  --parameters /projects/YOUR_CORP_LITE_DIRECTORY/.circleci/application.json \
  --action edge-build --workflow build-edge \
  --no-build-themes --allow-network
```

No `--skip-push` or empty fingerprint override is required. The selected JSON
parameter file (including `corp-lite.json`) supplies the fingerprint. If it is
absent or begins with `REPLACE_`, the runner looks in the project's
`.circleci/config.yml`: either the parameter's declared default or static
`continuation/continue` parameters. It does not execute setup shell commands.
Explicit parameter values, including an empty string, take precedence. Conflicting
config fingerprints require an explicit selection. A fingerprint identifies a key;
it does not make CircleCI's private key available locally.

To include theme compilation, replace `--no-build-themes` with
`--build-themes` and set the project's actual relative build script, for example
`--set 'theme_build_script="scripts/build-theme.sh"'`. The script must exist in
the project snapshot. The example path is illustrative, not a supplied CORP-LITE
script. It receives `--sites-dir` using `theme_sites_dir`; configure that
parameter to match the project. Theme preparation downloads NVM/Node and system
build packages inside the disposable job container, so network access is needed.
`--no-build-themes` skips both toolchain preparation and theme script execution,
even if the parameter file enables themes. These convenience flags override
`build_themes` without editing the parameter file.

To evaluate an already composed application YAML file, add
`--config /projects/YOUR_CORP_LITE_DIRECTORY/path/to/composed.yml` to either
command. This must be the application configuration, with its bundled shared
scripts from the current `webdev-ci` checkout, rather than the setup/continuation
configuration. Older bundles are rejected before execution; recompose them with
this checkout. Parameter expansion
and workflow selection still happen locally; unsupported CircleCI syntax fails
explicitly. Without `--config`, the runner composes the current shared YAML and
scripts from this checkout.

Add `--live` to `run` to stream job activity and command output while it runs.
Live output goes to stderr; the run ID and final JSON status remain on stdout.
To watch an existing run from another terminal, use `lci logs RUN_ID --follow`
(optionally with `--job JOB_NAME`). Following stops when the run finishes;
interrupting the log viewer does not cancel the build. Output is also retained
in the usual log files. Commands may buffer their own output.

Use the returned run ID with `lci status RUN_ID` and `lci logs RUN_ID`. A successful
run confirms dependency resolution and the selected build steps on the current
checkout bytes, including uncommitted changes and nonignored new files. The
original checkout remains untouched. Retained containers and run artifacts let
you inspect resulting Composer files and configured assets. This does not test
remote branch publication, deployment, or database refresh. Private Composer
packages still require credential support, which is not implemented.

The job status lists artifact paths relative to its run directory in the state
volume. `build-output.tar` retains the resulting project files and `changes.json`
records added, changed and removed files compared with the source snapshot.
Dependency directories (`vendor` and `node_modules`), symlinks and known
credential filenames are excluded.
Inspect these together with the step logs to evaluate the generated result.

With the service running, copy an artifact to the host using the relative path
reported by `lci status RUN_ID`. Replace `ARTIFACT_ARCHIVE` with the job's
`artifacts.archive` value (and use `artifacts.changes` for the JSON report):

```bash
docker compose -f compose.local-ci.yml cp \
  local-ci:/state/runs/RUN_ID/ARTIFACT_ARCHIVE ./edge-build-output.tar
tar -tf ./edge-build-output.tar
tar -xOf ./edge-build-output.tar composer.json
```

Choose a fresh destination filename if preserving an earlier exported result.
Inspect the archive before extracting files into a separate review directory;
avoid extracting it over the source checkout.

## Workspaces, caches and logs

Each job starts with an isolated container filesystem. A job can persist a
workspace as an immutable archive layer. Downstream jobs attach layers from
their dependency ancestors, so an unrelated job's files do not become visible
through a shared writable directory. These archives survive container exits in
the runner's state volume.

Workspace and cache paths must be explicit paths; wildcard patterns are not
supported. Conflicting files from independent upstream jobs fail when attached.
Layers from sequential upstream jobs can update files inherited from ancestors.

Caches are separate from workspaces and scoped by project. The runner executes
the composed config's `save_cache` and `restore_cache` steps automatically. Keep
the same state mount and project path across runs: saved archives live under
`/state/caches/PROJECT_HASH/`, outside the individual run directories, and survive
container removal and runner restarts. With a host bind mount for `/state`, this
directory is also visible in your IDE.

For example, a job can reuse dependency downloads and installed packages:

```yaml
- restore_cache:
    keys:
      - composer-v1-{{ checksum "composer.lock" }}
      - composer-v1-
- run: composer install --no-interaction
- save_cache:
    key: composer-v1-{{ checksum "composer.lock" }}
    paths:
      - vendor
      - ~/.composer/cache
```

Keys support literal text and `{{ checksum "file" }}` (SHA-256 of the file in the
job container). Restore keys are tried in the configured order; each selects the
newest saved key with that prefix. Caches are immutable: saving an existing key
reuses its original archive. Change the key's version prefix to force a fresh
cache. Missing paths are logged and skipped; an empty set of paths is not saved.
Interrupted saves are not published as reusable entries. Save steps still obey
normal job ordering: a preceding failed step prevents a later save.

Cache saves, hits, misses and the actual restored key appear in job logs and
`run --live`. List saved keys, sizes, paths and archive locations with:

```bash
docker compose -f compose.local-ci.yml run --rm local-ci caches \
  --project /projects/corp-lite
```

Older cache entries may lack creation dates and path metadata but remain usable.
Only local caches are supported; hosted CircleCI caches are not downloaded.
The shared Composer install command already declares cache steps. The current
Edge build job does not, so it will not populate or restore caches until cache
steps are added to that job. Use caches for reusable dependencies, and workspaces
for outputs passed between jobs in one workflow.

Run state includes the resolved configuration snapshot, job metadata and logs.
Use the returned run ID to inspect or cancel a run:

```bash
python -m local_ci status RUN_ID
python -m local_ci logs RUN_ID
python -m local_ci logs RUN_ID --job JOB_NAME
python -m local_ci cancel RUN_ID
```

Keep the same state volume attached when using these commands. Containers are
retained initially, including failed containers, to allow Docker inspection.
Logs and persisted state remain available after the runner restarts.

To inspect a retained container, find its container ID in the run metadata and
use `docker inspect CONTAINER_ID`. Job command output is captured by the runner;
use its `logs` command rather than the container's main-process log. Cleanup is
manual in this version: remove only the reviewed container IDs with
`docker rm CONTAINER_ID` when their debugging state is no longer needed. Do not
remove the named state volume if you want to retain run history, workspace
archives and caches. Stopping the service with
`docker compose -f compose.local-ci.yml down` preserves that volume.

## HTTP API

The Compose service starts `python -m local_ci serve`. Set `LOCAL_CI_TOKEN` to
provide a token, or let the runner generate one in `/state/api-token` with
owner-only file permissions. Retrieve the generated token deliberately with:

```bash
docker compose -f compose.local-ci.yml run --rm local-ci token
```

Protect that value as a credential. Send it as `Authorization: Bearer TOKEN`
for API requests. Only the health check is unauthenticated.

| Method and path | Purpose |
| --- | --- |
| `GET /health` | Check service availability. |
| `POST /plans` | Compile and inspect a workflow. |
| `POST /runs` | Start a run in the background. |
| `GET /runs/RUN_ID` | Read run status and metadata. |
| `GET /runs/RUN_ID/logs?job=JOB_NAME` | Read logs, optionally restricted to a job. |
| `POST /runs/RUN_ID/cancel` | Request cancellation. |

`POST /plans` and `POST /runs` default to `skip_push: true` for local Edge builds.
The explicit property remains supported; `skip_push: false` cannot execute an Edge build.
Set `parameters.build_themes` to a JSON boolean and provide
`parameters.theme_build_script` when enabling themes. `config_path` optionally
selects a composed application YAML file; in the Compose service it must be
under `/projects` or `/shared`. API paths refer to the service filesystem.

The CLI can also set `--branch`, `--trigger-source` and `--schedule-name` to
inspect selection with explicit pipeline metadata. These options do not create
a Git branch, register a schedule or trigger a hosted pipeline.

## Limits and next steps

The first version executes jobs serially in dependency order. It does not
provide retry, resume, parallel job scheduling, a hosted executor, or complete
support for CircleCI orbs, contexts and executor types. It does not reproduce
all CircleCI filesystem, networking or environment behavior. A local success is
useful debugging evidence; hosted validation and execution still matter.

Useful follow-up work is a supported-feature compatibility reference, broader
executor coverage, a run-history UI, explicit retention and cleanup commands,
and parallel execution after workspace behavior is well tested. Retry and
resume need an explicit policy for retained containers, cache writes and
partially completed workspace layers.

Run the offline suite with `python -m unittest discover -s local_ci/tests -v`.
Two integration suites are opt-in: set `LOCAL_CI_TEST_HTTP=1` for the real
loopback API test, and `LOCAL_CI_TEST_IMAGE=IMAGE` for the real Docker smoke test.
The Docker test requires a cached image with Bash and GNU tar; it disables
networking, creates disposable containers, and removes only its own containers.
It never pulls images. Install the pinned `local_ci/requirements.txt` in a
project virtual environment for native Python use, or run tests in the built
Compose image.
