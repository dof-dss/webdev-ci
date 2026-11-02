# Debug corp-lite jobs in local Docker

Run these commands from the `webdev-ci` checkout. The local runner calls the
static-analysis workflow `build-test` (action `checks`) and the Edge build
workflow `build-edge` (action `edge-build`). These are application workflows,
not the validation workflow for webdev-ci itself.

Corp-lite owns `.circleci/parameters.json`. Both hosted CircleCI and this local
runner use that file. Its current settings target Drupal 10, with theme building
and the Unity parent check disabled. Change project settings in corp-lite's own
PRs; starter templates do not override them.

## DDEV shortcut in corp-lite

Corp-lite includes a host wrapper for the Docker Compose commands below. Run it
from the application checkout:

```bash
cd /home/johan/dof-dss/corp-lite
ddev local-ci build
ddev local-ci plan static_analysis
ddev local-ci run static_analysis
ddev local-ci run edge_build
```

It reads corp-lite's `.circleci/parameters.json`, uses the sibling webdev-ci
checkout and streams run output. Runs allow network access for dependencies;
add `--offline` to disable job networking. Static analysis applies the empty
SSH fingerprint override locally. Edge builds enforce `--skip-push`.

Use `WEBDEV_CI_DIR=/path/to/webdev-ci` to choose another shared checkout. Other
runner options pass through, such as `--no-build-themes`. Inspect retained runs
with `ddev local-ci status RUN_ID` or `ddev local-ci logs RUN_ID --follow`.
The job image still needs to be cached; the preparation section below explains
how to pull it if missing. The controller is rebuilt only by `build`.

The wrapper uses the explicit Compose project name `webdev-ci` so DDEV's
`COMPOSE_PROJECT_NAME` cannot make the runner reuse the application's network.
If you saw DDEV services reported as runner orphans, retry with the updated
wrapper. Do not use `--remove-orphans` to resolve that warning.

## Prepare once

You need Docker with Compose. Python and the runner's dependencies are installed
inside its image. Rebuild the controller after updating `local_ci` code:

```bash
cd /home/johan/dof-dss/webdev-ci
export LOCAL_CI_PROJECTS=/home/johan/dof-dss
docker compose -f compose.local-ci.yml build
```

Compose mounts the projects directory read-only at `/projects`. The runner reads
`/projects/corp-lite/.circleci/parameters.json` automatically from `--project`.
There is no temporary settings file to copy. An explicit `--parameters PATH`
remains available for experiments with an alternative file.

For local static analysis, `--set 'ssh_fingerprint=""'` skips CircleCI key
injection. This changes only the current run, leaving the canonical JSON intact.
It does not supply private package credentials.

Check the job image is cached:

```bash
docker image inspect thecodingmachine/php:8.3-v4-fpm --format '{{.Id}}'
```

If missing, download it explicitly before running jobs:

```bash
docker pull --platform linux/amd64 thecodingmachine/php:8.3-v4-fpm
```

## Static analysis

Inspect the expanded configuration first:

```bash
docker compose -f compose.local-ci.yml run --rm local-ci plan \
  --project /projects/corp-lite \
  --set 'ssh_fingerprint=""' --action checks > /tmp/corp-lite-checks-plan.json
jq '.workflows["build-test"].jobs | map(.name)' /tmp/corp-lite-checks-plan.json
```

Expect `build`, `coding_standards`, `deprecated_code_phpstan` and
`disallowed_functions`. Then run:

```bash
docker compose -f compose.local-ci.yml run --rm local-ci run \
  --project /projects/corp-lite \
  --set 'ssh_fingerprint=""' --action checks --workflow build-test --allow-network --live
```

The dependency build runs first; each check gets its own container and the
saved dependency workspace. Jobs run sequentially locally even though hosted
checks can run in parallel. `--allow-network` permits Composer downloads. Private
packages can still fail without credentials. No SSH session or Git push is
needed for snapshot checkout.

## Edge build without publication

```bash
docker compose -f compose.local-ci.yml run --rm local-ci plan \
  --project /projects/corp-lite \
  --action edge-build --skip-push --no-build-themes \
  > /tmp/corp-lite-edge-plan.json
jq '.local_execution' /tmp/corp-lite-edge-plan.json

docker compose -f compose.local-ci.yml run --rm local-ci run \
  --project /projects/corp-lite \
  --action edge-build --workflow build-edge \
  --skip-push --no-build-themes --allow-network --live
```

`--skip-push` is already the local Edge default; it is written explicitly here
so the intent is clear. The runner also enforces it when executing commands.
The shared build script skips Git fetch, branch creation, commits and pushes,
and runs Composer against the snapshot. It does not deploy or refresh data.

This tests current working-tree files, including uncommitted changes, rather
than fetching `source_branch`. Composer plugins and project scripts still run;
network access is not restricted to package registries. Publication is disabled
in the shared script, not through a general network firewall against Git hosts.

## Inspect the result

The run prints an ID. Use it to retrieve status and logs:

```bash
docker compose -f compose.local-ci.yml run --rm local-ci status RUN_ID
docker compose -f compose.local-ci.yml run --rm local-ci logs RUN_ID
docker compose -f compose.local-ci.yml run --rm local-ci logs RUN_ID --job edge_build_shared
```

State lives under `webdev-ci/.local-ci/` by default. Edge job status lists
`build-output.tar` and `changes.json` artifact paths under that run's directory.
Use them to inspect changed Composer files and assets. The application checkout
is not modified. Containers are retained for inspection; remove only the exact
container IDs you no longer need. Do not use a global Docker prune.

A passing local run does not validate CircleCI's setup/continuation service,
credentials, PR triggers, branch publication or Upsun deployment. Keep CircleCI
configuration validation and a controlled hosted run as separate checks.

## Optional: run the controller with host Python

Docker Compose is the primary route above. If you prefer native Python, install
`local_ci/requirements.txt` in a virtual environment and use:

```bash
python3 -m local_ci plan \
  --project /home/johan/dof-dss/corp-lite \
  --set 'ssh_fingerprint=""' --action checks
```

Use host paths for native Python and `/projects/...` paths for Compose. Jobs
still run in Docker with either controller. Their recorded project paths differ,
so caches are scoped separately between these two routes.
