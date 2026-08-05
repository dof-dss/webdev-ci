# Web development CI config

This repo houses the shared CI config for most webdev repositories.

It allows us to more easily maintain common elements of CI config across our projects. Pipelines can now be given parameters, per project, which allows for localised overrides or sensible defaults to permit variation across projects.

## How it works

Circle CI dynamic pipelines (https://circleci.com/docs/using-dynamic-configuration/#a-basic-example) with Circle CI orbs (https://circleci.com/docs/orb-intro/) for preprocessing our config.

## How to use it

Use this starting template in your project's `.circleci/config.yml` file.

Adjust the following JSON parameters that are passed into the pipeline continuation step:

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

## Edge Solr version reconciliation

The shared edge-finalisation jobs run `scripts/reconcile-solr-indexes.sh` after
the data sync. The CircleCI command detects the edge environment's parent,
reads the live Solr version from that source environment, and passes it to the
edge environment. An edge Search API index is cleared and rebuilt only when its
live Solr version differs from the source version.

This is needed because `platform sync data` copies Drupal's database and files,
but the source and edge environments can have different Solr service versions.
The copied database includes Search API tracker state, while the edge Solr index
remains a separate service. When versions differ, rebuilding makes the tracker
and index reflect work performed against the edge service. Tracker and document
counts are deliberately not used: Search API processors can reject documents
while still marking them processed, so unequal counts are not by themselves
evidence of a broken index.

### Execution flow

1. CircleCI asks Platform.sh for the edge environment's parent—the same
   environment used as the data-sync source.
2. It downloads the helper from `webdev-ci/main` and verifies its SHA-256 hash
   against the shared configuration.
3. It runs the helper in `detect` mode on the source. This reads enabled
   Search API Solr indexes and requires one unambiguous live version.
4. It runs the helper in `reconcile` mode on the edge, passing that source
   version as `SOURCE_SOLR_VERSION`.
5. Matching indexes are logged and left untouched. Differing indexes receive
   one Drupal cache rebuild per site, followed by a clear and chunked rebuild.

If source and edge intentionally remain on different Solr versions, the edge
indexes are rebuilt after every data sync. This is intentional: each sync
refreshes Drupal's database and tracker state from that differently versioned
source environment.

The command fails rather than guessing if the source environment cannot be
identified, Drupal or Solr is unavailable, version detection returns `0.0.0`,
multiple source versions are found, clearing fails, or indexing stops making
progress.

The script supports both repository layouts used by the consuming projects:

- `project/sites` for Unity and Corp Lite multisite projects.
- `web/sites` for DEPT, nidirect, and other standard Drupal projects.

The sites directory can be overridden with `SITES_ROOT`, and the Drupal root
with `DRUPAL_ROOT`, when a project uses another layout. Versions are detected
from each environment's live Search API Solr connector; no project-specific
Solr version is hard-coded in the shared configuration. If the versions match,
the command does not inspect counts or mutate the index.

### Script modes and local debugging

`detect` is read-only and can be run inside a downstream project's DDEV
container from the project root:

```bash
ddev exec env PLATFORM_APP_DIR=/var/www/html \
  bash -s -- detect \
  < /path/to/webdev-ci/scripts/reconcile-solr-indexes.sh
```

`reconcile` requires a three-part source version. Supplying a version different
from local Solr deliberately clears and rebuilds the local indexes:

```bash
ddev exec env \
  PLATFORM_APP_DIR=/var/www/html \
  SOURCE_SOLR_VERSION=9.9.0 \
  CHUNK_PAUSE_SECONDS=0 \
  INDEX_PAUSE_SECONDS=0 \
  SITE_PAUSE_SECONDS=0 \
  SOLR_READY_DELAY_SECONDS=0 \
  bash -s -- reconcile \
  < /path/to/webdev-ci/scripts/reconcile-solr-indexes.sh
```

Use `bash -x -s` instead of `bash -s` to trace commands during local debugging.
There is no separate dry-run mode: `detect` is the safe inspection mode, while
`reconcile` is allowed to mutate indexes when versions differ.

### Tuning

The defaults are intended to reduce sustained load on shared environments:

- `INDEX_CHUNK_SIZE=500` and `INDEX_BATCH_SIZE=25` bound each indexing call.
- `CHUNK_PAUSE_SECONDS=5`, `INDEX_PAUSE_SECONDS=10`, and
  `SITE_PAUSE_SECONDS=20` throttle work between batches, indexes, and sites.
- `SOLR_READY_RETRIES=12`, `SOLR_READY_DELAY_SECONDS=10`, and
  `CLEAR_RETRIES=3` allow a temporarily loading core to become available.

These values can be overridden as environment variables. Pause values may be
zero; chunk sizes and retry counts must remain greater than zero.

### Tests

Run the focused regression coverage with:

```bash
bash scripts/tests/reconcile-solr-indexes.sh
```

## Contribution

> Contributors to repositories hosted in dof-dss are expected to follow the Contributor Covenant Code of Conduct, and those working within Government are also expected to follow the Northern Civil Service Code of Ethics and Civil Service Code. For details see https://github.com/dof-dss/contributor-code-of-conduct

All changes should be submitted with an appropriate pull request (PR) in GitHub. Direct commits to `main` or `development` are not normally permitted.

## Licence

Unless stated otherwise, the codebase is released under the [MIT License](http://www.opensource.org/licenses/mit-license.php). This covers both the codebase and any sample code in the documentation.
