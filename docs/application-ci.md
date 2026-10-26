# Shared application CI with the CircleCI CLI

New to this structure? Follow the [5–10 minute getting-started
guide](getting-started.md) before using this reference.

This shared configuration covers Unity 1–4, corp-lite, DEPT and NIDirect. It
retains the `edge_build` and `edge_build_finalise` schedules and their existing
time separation. There is no wait/polling job or external coordinator.

The [legacy entry point](legacy-ci.md) remains `shared-config.yml`. Projects
using that file keep their existing jobs. The new YAML files live under
`config/application`. Use `circleci config pack` to combine them. Standalone
theme and supporting-package workflows remain on the existing path.

For a Unity 1 Drupal 11 example and commands to test it, see
[examples/unity1](../examples/unity1/README.md).

## Source layout

```text
config/application/
  @config.yml                  # Version and pipeline parameter declarations
  commands/*.yml               # Shared steps and latest-main script loader
  jobs/*.yml                   # Quality, Edge build and Edge refresh jobs
  workflows/*.yml              # Three shared workflow definitions
examples/application/
  unity.json                   # Plain CircleCI parameter maps
  corp-lite.json
  dept.json
  nidirect.json
  setup.yml                    # Consumer setup/continuation template
scripts/edge/*.sh               # Executable build and refresh logic
```

The pack command uses directory and file names as YAML keys. For example,
`jobs/edge_build_shared.yml` becomes the `edge_build_shared` job. Each file
contains the settings for that job or command; `@config.yml` holds the top-level
settings.

All projects use the same YAML, with parameters for project differences. The
examples are starting points. Check versions, paths, SSH fingerprints and site
lists against your application branch before using them.

## Copy/paste local workflow

Run from the `webdev-ci` repository:

```bash
# Offline assembly using the installed CircleCI CLI.
circleci config pack config/application > /tmp/application.yml

# Check the configuration using CircleCI; this does not run jobs.
circleci config validate /tmp/application.yml
jq '.ci_action = "checks"' examples/application/corp-lite.json > /tmp/corp-lite-parameters.json
circleci config process /tmp/application.yml \
  --pipeline-parameters /tmp/corp-lite-parameters.json \
  > /tmp/corp-lite-processed.yml

# Offline regression tests; requires Python 3, PyYAML and the CircleCI CLI.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_application_ci.py
```

`pack` is local. `validate` and `process` use CircleCI's compilation service;
these need network access. `process` fills in parameters and expands reusable
commands. CLI v1 does not execute jobs locally.

To compile the Edge build or refresh workflow, set `ci_action` to `edge-build`
or `edge-refresh` in the temporary parameter file instead. Do not overwrite the
project's parameter file merely to test a different action. Compiling only
checks the configuration. Running an Edge workflow publishes code or replaces
hosted data; selecting an action does not make it a dry run.

Local packing tests inspect real CLI output and every example parameter map.
Runtime tests use temporary Git repositories and fake Composer, platform and
Drush executables: no DDEV, Upsun sessions or remote publication. Repository CI
also compiles each example with each action when this change is published.

## Workflows and parameters

- `build-test` installs dependencies once, then runs PHPCS, PHPStan and
  disallowed-function checks in parallel. It also checks for changes to Unity
  parent-repository code when `check_illegal_updates` is true.
- `build-edge` resolves development packages, optionally compiles themes and
  publishes the disposable Edge branch.
- `build-edge-finalise` copies data from the parent environment, then runs
  updates and QA setup for each site. The CircleCI job runs until this finishes.

`ci_action=auto` selects the existing schedules, or ordinary quality checks for
non-source-branch pushes. API runs with `ci_action=auto` do nothing. Explicit
`checks`, `edge-build` and `edge-refresh` values select only that workflow.

| Parameter example | Layout | Update sequence | Additional behaviour |
| --- | --- | --- | --- |
| unity | Multisite | Required preflight, updatedb, config import | Theme build, Unity safe content importer, Fastly uninstall, parent-repo checks |
| corp-lite | Multisite | Required preflight, updatedb, config import | Safe blocks/taxonomies and QA restoration per site |
| dept | Single | Full Drush deploy, including deploy hooks | Safe content import and QA staff setup |
| nidirect | Single | updatedb, config import | Safe content import and hospital waiting-times cache clear |

All projects enable QA accounts and reset their passwords. Structure Sync
content imports run only when the module is enabled. No menus or Solr bulk
reindexing are added. Failed bootstraps, missing required commands and logged
Drush errors fail the job, including QA commands which report an error but
return status zero.

Each project JSON file supplies values for these CircleCI parameters:

| Parameter | Meaning |
| --- | --- |
| `ci_action` | `auto`, `checks`, `edge-build`, `edge-refresh` |
| `source_branch` | Source for the disposable Edge build; default `development` |
| `edge_branch` | Git branch to publish and verify remotely |
| `environment` | Upsun environment ID; empty uses `edge_branch` |
| `edge_build_dofdss_packages` | Composer constraints used by the Edge build |
| `layout` | `single` or `multisite` |
| `sites` | Expected multisite directory names, space-separated; empty discovers `project/sites/*/settings.php`, excluding `default` |
| `uri` | Single-site Drush URI; default `default` |
| `preflight` | `required` or `none` |
| `deploy` | `standard` or full Drush `deploy` |
| `content` | `unity`, `structure-sync`, `none` |
| `fastly` | `keep` or `uninstall`; uninstall requires standard update mode |
| `qa_staff` | Also run QA staff-account creation |
| `after_command` | One additional Drush command name, without shell arguments |
| `site_hook` | Relative Bash script called with phase and site URI |
| `build_themes` | Enable Node/native toolchain installation |
| `theme_build_script` | Relative Bash script receiving `--sites-dir`; set together with `build_themes` |
| `theme_sites_dir` | Relative theme sites directory |
| `theme_asset_paths` | Newline-separated Git pathspecs for compiled assets |
| `check_illegal_updates` | Enable the Unity parent-repository check |

Existing PHP, Node, code-path and SSH-fingerprint parameters remain available.
CircleCI owns type/enum validation; the offline tests check example values too.
Site hooks receive `before-config` (standard mode only) and `after-config` after
imports/QA work. They execute from the deployed repository. Expected sites and
configured hooks must exist. DEPT's existing `QA_USERNAMES` administrator
creation remains in its hosted post-deploy hook.

## Set up an application project

Copy `examples/application/setup.yml` to your project's `.circleci/config.yml`
and its reviewed parameter example to `.circleci/application.json`. The setup
template:

1. Downloads the latest shared archive from `main`.
2. Runs `circleci config pack` on its `config/application` directory.
3. Uses `jq` to forward the requested `ci_action` with the project parameters.
4. Passes the packed YAML and JSON parameter file to `continuation/continue`.

No Python or PyYAML is needed in project setup. Preserve project-specific setup
checks such as Solr configset verification when adopting the template. Each job
needing shared scripts fetches the latest `main` at the time it runs; PHPCS uses
the wrapper from that archive too. Changes published to `main` reach projects
automatically. If `main` changes between downloads, jobs in the same pipeline
may use different versions.

Local packing can inspect uncommitted YAML. Running the new setup on CircleCI
requires the new configuration and scripts to have been published to `main`
first.

Supply `PLATFORM_PROJECT` and authentication through the application’s CircleCI
environment; optionally supply `PLATFORM_APPLICATION_NAME` for multi-app
projects. QA settings, including `QA_PASSWORD`, belong in the hosted
environment, not parameter files.

## Before switching an existing project

The Edge build recreates its branch from the source branch; Edge-only manual
commits are not preserved. Only Composer files and configured asset paths are
staged. No-change builds succeed; source-only changes still publish. The script
skips the push if the files are unchanged. It also rejects the push if another
build has updated the Edge branch since this build checked it. This uses Git’s
`--force-with-lease` option. Themes are compiled in CircleCI.

Before sync and before finalisation, the remote guard checks non-production type
and the expected Git branch. Sync copies from the target's **parent**: confirm
that this is the intended production source. The refresh relies on the gap
between schedules to allow deployment to finish. It does not check the deployed
commit, so it may use older code if the build fails. Existing deployment hooks
still run; test them alongside the new refresh steps. Required multisite
preflight commands must already be deployed. Preserve non-production Fastly
configuration.

Start with a trial on one corp-lite project. After its next nightly refresh,
check every site and its QA logins. Then test Unity themes, DEPT and NIDirect.
Enable only one refresh workflow for each environment.

To switch back, restore the project’s previous setup or revert the shared change
on `main`. This does not undo data already copied. Get permission before testing
operations on hosted environments.

Existing projects keep using `shared-config.yml`. Its sample settings in
`.circleci/fixtures/legacy-workflows.yml` are used to validate it. The new setup
uses only `config/application`.

Edit the configuration your project uses. Apply fixes to both only when needed.
Changes to the shared `phpcs.sh` helper affect both configurations.
