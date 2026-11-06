# Legacy shared CI

This documents the existing `shared-config.yml` configuration file.
Applications, supporting packages and theme repositories which download that
file continue to use its jobs and commands. The file stays at its current root
path so existing raw GitHub URLs keep working.

For the new opt-in application workflows, start with [getting
started](getting-started.md). New application jobs are maintained under
`config/application`; they are not concatenated into `shared-config.yml`.

## Files used by the old setup

- `shared-config.yml`: legacy jobs, commands and parameters, including
  `build_edge`, `sync_data`, `unity_edge_build` and `unity_sync_data`.
- `phpcs.sh`: established shared PHPCS helper used by legacy and new workflows.
- `.circleci/fixtures/legacy-workflows.yml`: sample job settings used to
  check the old configuration. CI validates these without running the jobs.

Keep the old jobs working until projects switch to the new setup. Do not replace
this configuration file with a packed new configuration. Supporting-package and
theme jobs remain on this path for now.

The following setup example describes the legacy mechanism; it is not the new
application setup template. Check an existing application repository for its
workflows and parameters.

## How it works

The project first downloads the shared configuration, then asks CircleCI to run
it with the project’s parameters. CircleCI calls this [dynamic
configuration](https://circleci.com/docs/using-dynamic-configuration/). The
continuation orb is a reusable CircleCI package that starts this second step.

## How to use it

Use this starting template in your project's `.circleci/config.yml` file.

Adjust the following JSON parameters that are passed into the pipeline
continuation step:

- `php_version`: make sure this matches your production value.
- `drupal_core_version`: Set as required.
- `coding_standards_dirs`: Space separated directories to check using PHPCS, use `$PROJECT_ROOT` to keep paths consistent.
- `deprecated_code_dirs`: As above but for `drupal-check` tool.


```
version: 2.1

setup: true

orbs:
  continuation: circleci/continuation@0.1.2

workflows:
  setup:
    jobs:
      - setup-and-dispatch

jobs:
  setup-and-dispatch:
    docker:
      - image: cimg/base:stable
    steps:
      - run:
          name: Fetch shared CI config
          command: |
            wget https://raw.githubusercontent.com/dof-dss/webdev-ci/refs/heads/main/shared-config.yml -O shared-config.yml
      - continuation/continue:
          configuration_path: shared-config.yml
          parameters: |
            {
              "php_version": "8.3",
              "drupal_core_version": "10.4",
              "coding_standards_dirs": "${PROJECT_ROOT}/web/modules/custom ${PROJECT_ROOT}/web/modules/origins ${PROJECT_ROOT}/web/themes/custom",
              "deprecated_code_dirs": "${PROJECT_ROOT}/web/modules/custom ${PROJECT_ROOT}/web/modules/origins ${PROJECT_ROOT}/web/themes/custom"
            }


```

## Setup notes

- Some YAML settings are repeated so they can use pipeline parameters.
- The setup job downloads the shared file. The continuation step then passes
  that file and the project parameters to CircleCI.


## Validate the legacy configuration file

From the repository root:

```bash
cat shared-config.yml .circleci/fixtures/legacy-workflows.yml > /tmp/legacy-ci.yml
circleci config validate /tmp/legacy-ci.yml
```

Validation uses CircleCI's compilation service. It does not run the jobs. The
repository workflow reports this separately as `validate-legacy-config`.
