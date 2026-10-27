# Shared application CI with the CircleCI CLI

New to this structure? Follow the [5–10 minute getting-started
guide](getting-started.md) before using this reference.

This shared configuration covers Unity 1–4, corp-lite, DEPT and NIDirect.
Each project supplies only settings that differ from the defaults. Build and
refresh still run on separate schedules, without a waiting job.

Existing projects keep using [shared-config.yml](legacy-ci.md) until they
switch. New YAML lives under `config/application`. The packaging script bundles
local scripts, then uses `circleci config pack` to assemble the YAML. Standalone
theme and supporting-package jobs remain on the legacy path.

For project setup and testing, see the [corp-lite walkthrough](walkthroughs/corp-lite.md)
and [Unity 1 notes](walkthroughs/unity1.md).

## Source layout

```text
config/application/
  @config.yml                  # Version and pipeline parameter declarations
  commands/*.yml               # Shared steps and bundled-script loader
  jobs/*.yml                   # Quality, Edge build and Edge refresh jobs
  workflows/*.yml              # Three shared workflow definitions
templates/application/
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
templates are starting points. Check versions, paths, SSH fingerprints and site
lists against your application branch before using them.

## Copy/paste local workflow

Run from the `webdev-ci` repository:

```bash
# Offline assembly using the installed CircleCI CLI.
bash scripts/pack-application.sh > /tmp/application.yml

# Check the configuration using CircleCI; this does not run jobs.
circleci config validate /tmp/application.yml
jq '.ci_action = "checks"' templates/application/corp-lite.json > /tmp/corp-lite-parameters.json
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

The setup defaults to `ci_action=none`: no application workflow runs until you
choose an action. Manual runs use `checks`, `edge-build` or `edge-refresh`.
Set the setup default to `auto` when ready for automatic checks on non-source
branch pushes. Configure PR triggers in CircleCI if only PRs should run checks.

Schedules must pass `ci_action=edge-build` or `ci_action=edge-refresh` explicitly.
Their names can remain `edge_build` and `edge_build_finalise`, but names no longer
select workflows. An `auto` scheduled or API run does nothing.

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
| `ci_action` | Trigger/setup setting: `none` (default), `auto`, `checks`, `edge-build`, `edge-refresh` |
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
| `build_themes` | Enable Node/native toolchain installation |
| `theme_build_script` | Relative Bash script receiving `--sites-dir`; set together with `build_themes` |
| `theme_sites_dir` | Relative theme sites directory |
| `theme_asset_paths` | Newline-separated Git pathspecs for compiled assets |
| `check_illegal_updates` | Enable the Unity parent-repository check |

Existing PHP, Node, code-path and SSH-fingerprint parameters remain available.
CircleCI owns type/enum validation; the offline tests check example values too.
Expected sites must exist. DEPT's existing `QA_USERNAMES` administrator creation
remains in its deployed post-deploy hook. Generic site hooks are not exposed.

## Set up an application project

Copy `templates/application/setup.yml` to the project's `.circleci/config.yml`
and its parameter example to `.circleci/application.json`. Keep project checks
such as Solr configset verification. The JSON contains only overrides; shared
defaults live in `config/application/@config.yml`.

Setup performs one download from `shared_ci_ref`, which defaults to `main`.
Set its default to `CORP-855` in the project's setup file for the trial, or pass
that value when triggering a pipeline. The shared branch must be published.
No application repository is changed by publishing shared code alone.

The packaging script includes a compressed copy of `scripts/edge` and `phpcs.sh`
in the generated configuration. Jobs unpack those files locally, without another
download. Each new pipeline gets the latest shared branch contents, and all jobs
within it use the same snapshot. Workspaces cannot cross from setup into the
continued workflows, so they are used only for dependencies within the checks
workflow.

See [how the script bundle works](shared-script-bundle.md) for the exact files,
decoding command, exported paths and a way to inspect the archive locally.

`ci_action` is declared in setup and the shared config. The pack script copies
the selected setup action into the generated config default. Explicit trigger
values also pass through automatically. Do not include `ci_action` or
`shared_ci_ref` in `application.json`: passing the same trigger parameter again
through continuation can cause a CircleCI parameter conflict. To enable `auto`
by default, change only the setup default; the pack script carries that value through.

Use `bash scripts/pack-application.sh` to produce runnable YAML. It calls the
CircleCI CLI after adding the bundle; a direct pack of `config/application` is
useful for inspecting structure but deliberately fails if a job needs scripts.
The wrapper needs Bash, tar, base64, jq and the CircleCI CLI.

Supply `PLATFORM_PROJECT` and credentials through the application's CircleCI
settings. Set `PLATFORM_APPLICATION_NAME` for multi-app projects if needed.
QA settings, including `QA_PASSWORD`, belong in the hosted environment.

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
