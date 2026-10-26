# Getting started in 5–10 minutes

This guide covers the **new shared application workflows**. If your project
still downloads `shared-config.yml`, see the [legacy CI guide](legacy-ci.md).

This guide gets you from a checkout of `webdev-ci` to a configuration you can
review for Unity, corp-lite, DEPT or NIDirect. You do not need to commit changes
or start a hosted job to follow it.

## What you need

| Tool | Used for |
| --- | --- |
| Git and Bash | Working with the repository and running script tests |
| [CircleCI CLI](https://cli.circleci.com/) | Packing, validating and processing configuration |
| jq | Selecting project parameters and the workflow to inspect |
| Python 3 and PyYAML | Optional local tests |

Start in your `webdev-ci` checkout. Check the main tools:

```bash
git --version
bash --version
circleci version
jq --version
```

Packing works offline. Validation and processing need network access to
CircleCI's compilation service; they do not execute jobs. Docker, DDEV, PHP,
Node and the Upsun CLI are not needed for this walkthrough. CircleCI CLI v1 does
not provide a local job executor.

## 1. Find your starting point

| Where | What to look at |
| --- | --- |
| [config/application](../config/application/) | Shared jobs, commands, workflows and parameter declarations |
| [examples/application](../examples/application/) | Project parameter files and the project setup template |
| [Unity 1 example](../examples/unity1/README.md) | A concrete Drupal 11 configuration and job evaluation commands |
| [scripts/edge](../scripts/edge/) | Build, sync and per-site finalisation scripts |

There are three workflows: check code quality, build the Edge branch, and
refresh Edge data. Edge is the test environment used for these builds. Its build
and data refresh run on separate schedules.

## 2. Pack the YAML locally

From the repository root:

```bash
circleci config pack config/application > /tmp/webdev-ci.yml
less /tmp/webdev-ci.yml
```

You now have one complete YAML document assembled from the component files.
Parameter references such as `<< pipeline.parameters.php_version >>` are still
present: packing combines files; processing resolves those references.

## 3. Choose a project and inspect its checks

This example uses corp-lite. Substitute `unity.json`, `dept.json` or
`nidirect.json` for another project. For the Unity 1 Drupal 11 example, use
`examples/unity1/parameters.json` instead.

```bash
jq '.ci_action = "checks"' examples/application/corp-lite.json \
  > /tmp/webdev-ci-parameters.json

circleci config validate /tmp/webdev-ci.yml

circleci config process /tmp/webdev-ci.yml \
  --pipeline-parameters /tmp/webdev-ci-parameters.json \
  > /tmp/webdev-ci-processed.yml

less /tmp/webdev-ci-processed.yml
```

Look for the selected workflow, its jobs, PHP version and code paths. Change
`"checks"` to `"edge-build"` or `"edge-refresh"` in the `jq` command to inspect
either Edge workflow. These commands only compile configuration; running the
Edge jobs would publish code or replace hosted data.

## 4. Run the offline tests (optional)

If Python 3 and PyYAML are already available:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_application_ci.py
```

If PyYAML is missing, use a temporary virtual environment:

```bash
ci_test_env=$(mktemp -d /tmp/webdev-ci-tests.XXXXXX)
python3 -m venv "$ci_test_env"
"$ci_test_env/bin/python" -m pip install PyYAML==6.0.3
PYTHONDONTWRITEBYTECODE=1 "$ci_test_env/bin/python" \
  scripts/tests/test_application_ci.py
```

Creating the virtual environment requires Python's venv support; installing
PyYAML needs package-download access. The tests themselves run locally, using
temporary Git repositories and fake Composer, platform and Drush commands. They
finish with `OK` when successful. They do not contact Upsun or publish code.

The tests cover:

- YAML files combine correctly, job dependencies are valid, and example
  settings have the right types. Schedules, branches and manual actions
  select the expected workflows.
- Old and new configurations remain separate.
- Loading shared scripts from `main`, using a local archive instead of GitHub.
- Git pushes behave correctly when nothing changes, only source code changes,
  compiled assets change, or another build updates the branch first.
- Failure handling: Composer or theme failures prevent publication; sync or
  database-update failures stop finalisation; logged QA errors fail the run.
- Commands run in the right order for single-site and multisite projects, use
  the expected sites, reject the wrong target environment, and run QA setup.

These tests check the scripts using fake external commands. They do not test
real package downloads, theme builds, Upsun, Drupal updates or QA logins. They
also check workflow selection locally, without CircleCI. Use CircleCI validation
and a trial run on a test project to check those parts.

## What next?

Edit a component under `config/application` or a temporary copy of a project's
parameters, then repeat packing, processing and the relevant tests. Generated
files in `/tmp` are inspection outputs, not the source to edit.

For real CircleCI execution, start with the [Unity 1 evaluation
guide](../examples/unity1/README.md). First, add the setup template and
parameter file to a test branch in the application repository. The shared
changes must also be published to `webdev-ci/main`. Setup and jobs fetch the
latest `main` when they run; they cannot execute unpublished local changes
through that path.

For all parameters, hooks, schedules and adoption details, see the [application
CI reference](application-ci.md).
