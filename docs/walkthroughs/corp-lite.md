# Adopt the shared CI in corp-lite

The shared implementation belongs in webdev-ci. The configuration CircleCI runs
for corp-lite belongs in corp-lite:

```text
corp-lite/.circleci/
  config.yml          # Setup, shared branch and project setup checks
  application.json    # Reviewed paths, package constraints, sites and Edge settings
```

This guide describes adoption; it does not mean the application has been migrated.

## Prepare an application test branch

Publish the shared implementation on `webdev-ci/CORP-855`. In a corp-lite test
branch, copy the [setup template](../../templates/application/setup.yml) into
`.circleci/config.yml` and use [corp-lite.json](../../templates/application/corp-lite.json)
as the starting point for `.circleci/application.json`.

Review the values against the application branch. The previously inspected
`CORP-833--db-update` branch was Drupal 10, while the template's package constraint
is for Drupal 11. Do not carry that constraint into a Drupal 10 build. Use the
application's Composer files and current CI settings to choose the right one.

In the project-owned files:

- Set `shared_ci_ref` to `CORP-855` in setup. Keep `ci_action=none` for manual testing.
- Preserve the `scripts/solr/verify-configsets` step after checkout.
- Set the project's SSH fingerprint and review analysis paths and exclusions.
- Set `source_branch` explicitly to the application branch intended for Edge.
- Review `edge_branch`, `environment`, and the expected `sites` from the project's
  `project/project.yml`. The template uses multisite mode, required preflight and
  safe Structure Sync imports, with no theme build.

The new setup no longer reads `.circleci/workflow-config.yml`. Remove that obsolete
file in the application's adoption PR once no setup uses it; Git retains the
previous configuration for rollback.

## Compile the configuration before running it

Run from webdev-ci, pointing `corp_lite_repo` at your actual application checkout:

```bash
corp_lite_repo=/path/to/corp-lite
bash scripts/pack-application.sh > /tmp/corp-lite-packed.yml
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_application_ci.py

circleci config validate /tmp/corp-lite-packed.yml
for action in checks edge-build edge-refresh; do
  jq --arg action "$action" '.ci_action = $action' \
    "$corp_lite_repo/.circleci/application.json" \
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
the source branch, environment, parent data source, credentials, deployed
`upgrade-preflight` command and QA settings.

- Trigger `ci_action=edge-build` to resolve packages and publish Edge. A push
  can trigger an Upsun deployment.
- After confirming deployment completed, trigger `ci_action=edge-refresh` to
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
   passing `ci_action=edge-build`. Disable the old schedule for the same target
   before enabling its replacement.
3. Keep refresh on a later, independent schedule. When adopting the new refresh
   job, pass `ci_action=edge-refresh` and disable the old equivalent.
4. Check the first overnight build, deployment, refresh and QA logins.

The schedule gap allows time for deployment. Refresh does not check that the
build succeeded or that the expected commit is deployed. Each pipeline downloads
the latest shared branch independently. Schedule names do not select actions.

Once the trial is accepted and the shared changes are merged, change
`shared_ci_ref` to `main`. Rollback restores the application's previous setup and
schedules; it does not undo copied data.
