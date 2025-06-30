# Web development CI config

This repo houses the shared CI config for most webdev repositories.

It allows us to more easily maintain common elements of CI config across our projects. Pipelines can now be given parameters, per project, which allows for localised overrides or sensible defaults to permit variation across projects.

## How it works

Circle CI dynamic pipelines (https://circleci.com/docs/using-dynamic-configuration/#a-basic-example) with Circle CI orbs (https://circleci.com/docs/orb-intro/) for preprocessing our config.

## How to use it

Use this starting template in your project's `.circleci/config.yml` file.

Adjust the following JSON parameters that are passed into the pipeline continuation step.:

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

## Gotchas

- YAML anchors can't be preprocessed with pipeline parameters (yet), so you'll see these re-declared to prioritise parameter usage over lack of repetition.
- There's a preprocess/setup step involved now where a continuation orb will take the central config file, and preprocess it into a final YAML file which is then executed in the usual workflow steps.

## Contribution

> Contributors to repositories hosted in dof-dss are expected to follow the Contributor Covenant Code of Conduct, and those working within Government are also expected to follow the Northern Civil Service Code of Ethics and Civil Service Code. For details see https://github.com/dof-dss/contributor-code-of-conduct

All changes should be submitted with an appropriate pull request (PR) in GitHub. Direct commits to `main` or `development` are not normally permitted.

## Licence

Unless stated otherwise, the codebase is released under the [MIT License](http://www.opensource.org/licenses/mit-license.php). This covers both the codebase and any sample code in the documentation.