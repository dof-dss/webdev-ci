# Manual Solr bulk reconciliation

This helper is independent of both CI configuration paths.

Nightly edge-finalisation jobs do not rebuild Search API Solr indexes.
Rebuilding indexes is a manual task. The time and resources needed depend on the
Solr service size, number of sites and amount of content.

`scripts/reconcile-solr-indexes.sh` finds enabled Search API Solr indexes. It
can rebuild them from scratch or resume unfinished indexing, with pauses to
reduce load. Check the target environment and choose a mode before running it.

`platform sync data` copies Drupal's database and files, including Search API
tracker state, but the target Solr index remains a separate service. Check how
much indexing Drupal reports as remaining, whether the Solr service can handle
the work, and whether the index needs clearing before choosing `rebuild` or
`resume`.

The script supports both repository layouts used by the consuming projects:

- `project/sites` for Unity and Corp Lite multisite projects.
- `web/sites` for DEPT, nidirect, and other standard Drupal projects.

The sites directory can be overridden with `SITES_ROOT`, and the Drupal root
with `DRUPAL_ROOT`, when a project uses another layout. No project-specific Solr
version is hard-coded in the shared configuration.

### Local debugging

Running the helper in its default `rebuild` mode clears and rebuilds every
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
There is no dry-run mode in the helper. Inspect the target environment and its
Search API tracker state separately before running it.

### Running against Upsun

The helper also has a non-destructive `resume` mode. It does not clear an index;
it continues whatever items are already marked as remaining in the Search API
tracker. Use site and index filters to retry only the work that failed.

From a local `webdev-ci` checkout, stream the helper through the Upsun CLI:

```bash
upsun ssh -p "$PLATFORM_PROJECT" -e edge -- env \
  RECONCILE_MODE=resume \
  SITE_FILTER=communityrelations \
  INDEX_FILTER=default_content \
  INDEX_CHUNK_SIZE=50 \
  INDEX_BATCH_SIZE=5 \
  CHUNK_PAUSE_SECONDS=20 \
  INDEX_RETRY_DELAY_SECONDS=90 \
  bash -s < scripts/reconcile-solr-indexes.sh
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
- `RECONCILE_MODE=rebuild` is the script default. `RECONCILE_MODE=resume`,
  `SITE_FILTER`, and `INDEX_FILTER` provide targeted manual recovery.

These values can be overridden as environment variables. Pause values may be
zero; chunk sizes and retry counts must remain greater than zero.

### Tests

Run the script tests with:

```bash
bash scripts/tests/reconcile-solr-indexes.sh
```

