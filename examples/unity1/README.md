# Test the new configuration with Unity 1

This is a **Drupal 11** example for `dof-dss/nicsdru_unity` (Unity 1). It is
based on the local `D8UN-3553` ref at
`30477b31d304a40b0b80c988ebc87da513a6d8ec`, not the current Unity working tree.
Check these settings against your application branch before using them.

## Files to review

- `packed.yml`: real output of `circleci config pack config/application`.
  It combines the shared YAML files but still needs project parameter values.
  Edit the source files under `config/application`, then regenerate this file.
- `parameters.json`: Unity 1 values read from the Drupal 11 ref, plus settings for the shared jobs. Always supply this file when processing the snapshot.
- `setup.yml`: setup template that keeps Unity's Solr configuration check.
  It downloads and packs the latest shared configuration from `main`, then continues with the
  project parameters. Copy it into Unity when ready to test.

Key choices: PHP 8.3, Node 18, the ref's `11.x-dev` package constraints,
multisite layout, the 16 sites declared in that ref's `project/project.yml`,
required `upgrade-preflight`, Unity safe content import, Fastly uninstall before
configuration import, QA enablement/password resets, and CI theme compilation.
The source branch is explicitly `D8UN-3553`; target Git branch/environment is
`edge`. Review the source before enabling nightly runs and change it back to
`development` only once development contains the intended Drupal 11 code.

You can pack and inspect local changes now. To run them on CircleCI, first
publish the shared configuration and scripts to `webdev-ci/main`. Setup and jobs
fetch the latest `main` when they run, without a SHA parameter.

## 1. Combine the YAML and run local tests

```bash
cd /home/johan/dof-dss/webdev-ci
circleci config pack config/application > /tmp/unity1-packed.yml
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_application_ci.py
```

No remote job runs. The tests use fake platform/Drush/Composer commands and
local temporary Git repositories. To refresh the checked-in review snapshot:

```bash
circleci config pack config/application > examples/unity1/packed.yml
```

## 2. Compile each workflow without executing it

These commands send configuration to CircleCI's compilation service. They do not
push application code, run tests on CircleCI, or contact Upsun.

```bash
cd /home/johan/dof-dss/webdev-ci
circleci config validate /tmp/unity1-packed.yml

for action in checks edge-build edge-refresh; do
  jq --arg action "$action" '.ci_action = $action' \
    examples/unity1/parameters.json > "/tmp/unity1-$action.parameters.json"
  circleci config process /tmp/unity1-packed.yml \
    --pipeline-parameters "/tmp/unity1-$action.parameters.json" \
    > "/tmp/unity1-$action.processed.yml"
done

less /tmp/unity1-checks.processed.yml
less /tmp/unity1-edge-build.processed.yml
less /tmp/unity1-edge-refresh.processed.yml
```

Review the jobs and commands in the output, particularly `SOURCE_BRANCH`,
`EDGE_BRANCH`, `EDGE_ENVIRONMENT`, `EDGE_SITES` and the file patterns used to
select compiled theme assets. Setting `ci_action` selects the workflow directly,
so these checks do not need a schedule name.

## 3. Run the quality checks on CircleCI

Before this step, separately publish the shared implementation to
`webdev-ci/main` and prepare a Unity test branch based on the intended Drupal 11
code, containing `setup.yml` as `.circleci/config.yml` and `parameters.json` as
`.circleci/application.json`. Publish that test branch before running the
commands below.

The following commands start real CircleCI jobs and use build credits. It
fetches configuration from the named branch; it does not upload
`/tmp/unity1-packed.yml`. Set the two placeholders to real values:

```bash
unity1_test_branch='REPLACE_WITH_PUBLISHED_UNITY_TEST_BRANCH'
unity1_definition_id='REPLACE_WITH_PIPELINE_DEFINITION_UUID'

circleci pipeline run \
  --project gh/dof-dss/nicsdru_unity \
  --definition-id "$unity1_definition_id" \
  --branch "$unity1_test_branch" \
  --param ci_action=checks

circleci run watch \
  --project gh/dof-dss/nicsdru_unity \
  --branch "$unity1_test_branch" \
  --timeout 20m
```

This runs `build`, then PHPCS, deprecated-code PHPStan, disallowed-functions
PHPStan and the Unity parent-repository check. The parent check compares against
`maestro-drupal-unity`; differences in that baseline can legitimately fail it.
The `checks` action does not invoke the Edge publishing or refresh jobs.

## 4. Run the Edge jobs

These commands change the Edge branch or environment. Get permission for the
intended operation before running them. Check the source branch, target
environment and the parent environment whose data will be copied. Confirm that
the deployed application has the required Drush commands and that its deployment
hooks work with the refresh steps.

The CircleCI project needs the correct project/application settings and SSH/API
credentials. QA settings belong in the Upsun environment. Keep secrets out of
these example files.

**Edge build** resolves packages, compiles themes and pushes/replaces the `edge`
branch. The push is rejected if another build has changed the branch since this
build checked it (`--force-with-lease`). A successful push can trigger an Upsun
deployment:

```bash
circleci pipeline run \
  --project gh/dof-dss/nicsdru_unity \
  --definition-id "$unity1_definition_id" \
  --branch "$unity1_test_branch" \
  --param ci_action=edge-build
```

`--branch` selects the pipeline config/checkout. The build script separately
fetches `source_branch` (`D8UN-3553` here); changes made only on the test branch
are therefore not automatically the source of the Edge build.

**Edge refresh** replaces Edge database/files from its parent, then runs schema,
configuration, content and QA operations across every configured site:

```bash
circleci pipeline run \
  --project gh/dof-dss/nicsdru_unity \
  --definition-id "$unity1_definition_id" \
  --branch "$unity1_test_branch" \
  --param ci_action=edge-refresh
```

Run refresh only after checking the intended deployment has completed. The two
nightly schedules remain independent; the refresh does not wait for the build or
check which commit is deployed. The refresh script uses deployed application
code, not the pipeline test branch.

## Isolate a quality job further

`ci_action=checks` runs the dependency build followed by all quality checks. To
test just one check, copy `config/application` to `/tmp`, keep only the
`build-test.yml` workflow, and edit its `jobs` list. Keep `build` and the check
you want, for example `coding_standards` with `requires: [build]`. The `build`
job installs the dependencies the check needs.

Pack the temporary directory with `circleci config pack`. To run it on CircleCI,
you must publish it as the configuration for a test pipeline; `pipeline run`
cannot execute a local YAML file.

The shared implementation and adoption caveats are described in [the application
CI guide](../../docs/application-ci.md).
