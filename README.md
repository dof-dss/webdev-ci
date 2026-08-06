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
reads the Solr service types declared for the source and edge environments, and
only runs the helper when those declared versions differ.

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
2. It uses `platform services` to read the service types deployed to the source
   and edge environments. DDEV configuration is not used for this production
   decision.
3. If the edge declares no Solr service, or both environments declare the same
   Solr version, the command exits successfully without bootstrapping Drupal.
4. If the edge declares a different Solr version, CircleCI downloads the latest
   helper from `webdev-ci/main` and runs it only in the edge environment.
5. The helper discovers enabled Search API Solr indexes. Each affected site
   receives one Drupal cache rebuild followed by a clear and throttled rebuild.
   Transient Solr timeouts are retried from the remaining Search API tracker
   state, without clearing partial progress. Sites without an enabled Solr
   index are logged and skipped.

If source and edge intentionally remain on different Solr versions, the edge
indexes are rebuilt after every data sync. This is intentional: each sync
refreshes Drupal's database and tracker state from that differently versioned
source environment.

The command fails rather than guessing if the source environment cannot be
identified, an environment declares multiple Solr service versions, an affected
site cannot reach Solr after the bounded retry window, clearing fails, indexing
stops making progress, or Drush reports a deterministic non-Solr error.

The script supports both repository layouts used by the consuming projects:

- `project/sites` for Unity and Corp Lite multisite projects.
- `web/sites` for DEPT, nidirect, and other standard Drupal projects.

The sites directory can be overridden with `SITES_ROOT`, and the Drupal root
with `DRUPAL_ROOT`, when a project uses another layout. No project-specific Solr
version is hard-coded in the shared configuration.

### Local debugging

The helper is rebuild-only because CircleCI makes the version decision before
invoking it. Running it directly in DDEV therefore clears and rebuilds every
enabled local Search API Solr index. Use a disposable local index:

```bash
ddev exec env \
  PLATFORM_APP_DIR=/var/www/html \
  CHUNK_PAUSE_SECONDS=0 \
  INDEX_PAUSE_SECONDS=0 \
  SITE_PAUSE_SECONDS=0 \
  SOLR_READY_DELAY_SECONDS=0 \
  bash -s \
  < /path/to/webdev-ci/scripts/reconcile-solr-indexes.sh
```

Use `bash -x -s` instead of `bash -s` to trace commands during local debugging.
There is no dry-run mode in the helper. To inspect the declared Upsun versions
without rebuilding, use `platform services --columns type` for the source and
edge environments.

### Recovering an interrupted edge rebuild

The helper also has a non-destructive `resume` mode. It does not clear an index;
it continues whatever items are already marked as remaining in the Search API
tracker. Exact site and index filters keep recovery scoped to the failed work.

Download the helper locally, then run it through the Upsun CLI:

```bash
curl --fail --location --silent --show-error \
  https://raw.githubusercontent.com/dof-dss/webdev-ci/main/scripts/reconcile-solr-indexes.sh \
  --output /tmp/reconcile-solr-indexes.sh

upsun ssh -p "$PLATFORM_PROJECT" -e edge -- env \
  RECONCILE_MODE=resume \
  SITE_FILTER=communityrelations \
  INDEX_FILTER=default_content \
  INDEX_CHUNK_SIZE=50 \
  INDEX_BATCH_SIZE=5 \
  CHUNK_PAUSE_SECONDS=20 \
  INDEX_RETRY_DELAY_SECONDS=90 \
  bash -s < /tmp/reconcile-solr-indexes.sh
```

Omit `INDEX_FILTER` to resume all enabled Solr indexes for the selected site.
Omit both filters to resume all discovered sites. Resume mode is appropriate
after a partial indexing failure; use the normal rebuild mode when an index was
never cleared and scheduled after the data sync.

### Tuning

The defaults are intentionally conservative for small shared Upsun Solr
services:

- `INDEX_CHUNK_SIZE=100` and `INDEX_BATCH_SIZE=5` bound each indexing call and
  reduce peak request pressure.
- `CHUNK_PAUSE_SECONDS=15`, `INDEX_PAUSE_SECONDS=30`, and
  `SITE_PAUSE_SECONDS=45` throttle work between chunks, indexes, and sites.
- `SOLR_READY_RETRIES=12`, `SOLR_READY_DELAY_SECONDS=20`, and
  `CLEAR_RETRIES=5` allow a temporarily loading core to become available.
- `INDEX_RETRIES=5` and `INDEX_RETRY_DELAY_SECONDS=60` retry known transient
  Solr failures. If a timed-out call made tracker progress, the helper cools
  down and resumes from the new remaining count rather than clearing again.
- `RECONCILE_MODE=rebuild` is the nightly default. `RECONCILE_MODE=resume`,
  `SITE_FILTER`, and `INDEX_FILTER` provide targeted manual recovery.

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
