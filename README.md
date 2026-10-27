# Web development CI

Shared CircleCI configuration for the DoF web development repositories.

## Start here

| Path | Status and purpose | Start here |
| --- | --- | --- |
| `shared-config.yml` | Existing configuration for applications, supporting packages and themes. | [Legacy CI guide](docs/legacy-ci.md) |
| `config/application/` | New shared jobs for Unity, corp-lite, DEPT and NIDirect. Projects must switch to these explicitly. | [Getting started in 5–10 minutes](docs/getting-started.md) |

New developers should start with the [getting-started
guide](docs/getting-started.md). It shows how to combine the YAML files and
check a project configuration.

Existing projects keep using `shared-config.yml` until their own CircleCI setup
is changed. Adding the new configuration does not switch their overnight builds.
The new setup downloads shared code once from `shared_ci_ref` (default `main`).
Use `CORP-855` to test shared changes before merging them. Legacy jobs still use
their existing downloads.

For a visual overview, see the [legacy flow diagram](docs/legacy-flow.md) and
the [new shared flow diagram](docs/application-flow.md).

## New application workflows

- [Getting started](docs/getting-started.md): tools, packing and local evaluation.
- [Application CI reference](docs/application-ci.md): parameters, scripts and adoption details.
- [Shared script bundle](docs/shared-script-bundle.md): how scripts are packed, unpacked and used by jobs.
- [Corp-lite walkthrough](docs/walkthroughs/corp-lite.md): project setup and staged rollout.
- [Unity 1 walkthrough](docs/walkthroughs/unity1.md): Unity-specific settings and testing.
- [Application templates](templates/application/README.md): reusable starting files, not live project settings.

To change a new job, start in `config/application`. The Edge scripts are in
`scripts/edge`, and their tests are in `scripts/tests/test_application_ci.py`.
Both old and new jobs use the root `phpcs.sh` helper.

## Where files belong

- `config/application/` and `scripts/`: shared implementation.
- `templates/application/`: reusable starting files.
- `docs/walkthroughs/`: project adoption and testing guides.
- Each application repository: its actual `.circleci/config.yml` and
  `.circleci/application.json`.

Generate packed YAML in `/tmp` or as a CI artifact. Keep project-specific settings
and generated snapshots out of this repository.

## Repository checks

`.circleci/config.yml` runs two independent jobs:

- `validate-legacy-config` checks the old YAML using sample job settings.
- `validate-application-config` runs the offline tests and checks the new YAML for each example project and action.

These checks do not run Edge builds or refresh data. The old and new
configurations are checked separately.

## Other tooling

[Manual Solr reconciliation](docs/solr-reconciliation.md) is run manually,
separately from CI.

## Contribution

> Contributors to repositories hosted in dof-dss are expected to follow the Contributor Covenant Code of Conduct, and those working within Government are also expected to follow the Northern Civil Service Code of Ethics and Civil Service Code. For details see https://github.com/dof-dss/contributor-code-of-conduct

All changes should be submitted with an appropriate pull request (PR) in GitHub.
Direct commits to `main` or `development` are not normally permitted.

## Licence

Unless stated otherwise, the codebase is released under the [MIT
License](http://www.opensource.org/licenses/mit-license.php). This covers both
the codebase and any sample code in the documentation.
