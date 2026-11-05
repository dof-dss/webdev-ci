# Adopt the shared CI in corp-lite

The shared implementation belongs in webdev-ci. The configuration CircleCI runs
for corp-lite belongs in corp-lite:

```text
corp-lite/.circleci/
  config.yml          # Setup, shared branch and project setup checks
  parameters.json    # Reviewed paths, package constraints, sites and Edge settings
```

The local corp-lite setup now uses this layout and starts in manual-only mode.
Publication and hosted validation are separate rollout steps.

## Prepare an application test branch

Publish the shared implementation on `webdev-ci/CORP-855`. In a corp-lite test
branch, copy the [setup template](../../templates/application/setup.yml) into
`.circleci/config.yml` and use [multisite.json](../../templates/application/multisite.json)
as the starting point for `.circleci/parameters.json`.

The generic multisite template has placeholders, not corp-lite's settings.
Corp-lite's canonical file is `.circleci/parameters.json` in its own repository.
Keep its package constraints aligned with that application branch, including the
Drupal 11 constraints on `D8UN-833`.

In the project-owned files:

- Set `shared_ci_ref` to `CORP-855` in setup. Keep `ci_action=none` for manual testing.
- Preserve the `scripts/solr/verify-configsets` step after checkout.
- Set the project's SSH fingerprint and review analysis paths and exclusions.
- Set `source_branch` in setup or the trigger to the application branch intended for Edge.
- Review `edge_branch`, `environment`, and the expected `sites` from the project's
  `project/project.yml`. Corp-lite enables safe Structure Sync
  imports in its own settings. The current Drupal 10 trial uses `preflight=none`;
  only require preflight on branches that provide that command.

The local adoption removes `.circleci/workflow-config.yml`; shared workflows
now live in webdev-ci. Git retains the previous setup for rollback.

## Compile the configuration before running it

Run from webdev-ci, pointing `corp_lite_repo` at your actual application checkout:

```bash
corp_lite_repo=/path/to/corp-lite
bash scripts/pack-application.sh > /tmp/corp-lite-packed.yml
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_application_ci.py

circleci config validate /tmp/corp-lite-packed.yml
for action in checks edge-build edge-finalise; do
  jq --arg action "$action" '.ci_action = $action' \
    "$corp_lite_repo/.circleci/parameters.json" \
    > "/tmp/corp-lite-$action.parameters.json"
  circleci config process /tmp/corp-lite-packed.yml \
    --pipeline-parameters "/tmp/corp-lite-$action.parameters.json" \
    > "/tmp/corp-lite-$action.processed.yml"
done
less /tmp/corp-lite-checks.processed.yml
```

Packing and tests are local. Validation and processing contact CircleCI but do
not run jobs. The temporary parameter files include an action only for compilation;
do not add it to the application's committed JSON.

## Run manually first

Publish the application test branch. In the corp-lite CircleCI project, choose
**Trigger Pipeline**, select that branch, and pass `ci_action=checks`. Setup
already selects the shared `CORP-855` branch. Check its log confirms that branch.
Expect dependency installation followed by PHPCS and the two PHPStan checks.
No Edge workflow should run.

Before running Edge actions, obtain permission for the target operation. Verify
the source branch, environment, parent data source, credentials and QA settings. If `preflight=required` is selected, confirm
`upgrade-preflight` is deployed first.

- Trigger `ci_action=edge-build` with `edge_publish=false` first to inspect build artifacts.
  Set `edge_publish=true` explicitly when ready to publish Edge. A push
  can trigger an Upsun deployment.
- After confirming deployment completed, trigger `ci_action=edge-finalise` to
  copy parent database/files and run updates, configuration/content imports and
  QA setup for every configured site.

Neither action is a dry run. Refresh uses the deployed application, not the
pipeline checkout. Inspect every site and test QA logins afterwards.

## Enable PR checks and nightly runs

1. Change the setup's `ci_action` default to `auto`. Configure CircleCI's project
   triggers for PRs and subsequent commits. Merge the setup into the application
   branch developers target, still selecting shared `CORP-855`, and verify a new
   PR and follow-up commit.
2. Configure the nightly build on an application branch containing the new setup,
   passing `ci_action=edge-build` and boolean `edge_publish=true`. Disable the old schedule for the same target
   before enabling its replacement.
3. Keep refresh on a later, independent schedule. When adopting the new refresh
   job, pass `ci_action=edge-finalise` and disable the old equivalent.
4. Check the first overnight build, deployment, refresh and QA logins.

The schedule gap allows time for deployment. Refresh does not check that the
build succeeded or that the expected commit is deployed. Each pipeline downloads
the latest shared branch independently. Schedule names do not select actions.

Once the trial is accepted and the shared changes are merged, change
`shared_ci_ref` to `main`. Rollback restores the application's previous setup and
schedules; it does not undo copied data.
