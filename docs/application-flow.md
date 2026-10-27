# New shared CI flow

All application projects use the same jobs and workflows. A project supplies
settings for its versions, code paths, sites and Edge behaviour.

```mermaid
flowchart TD
    manual["Manual run: checks, edge-build or edge-refresh"] --> setup
    push["Application branch push: auto"] --> setup
    schedules["Separate nightly schedules: build and later refresh"] --> setup

    subgraph application["Application repository: Unity, corp-lite, DEPT or NIDirect"]
        setup["Setup: checkout and keep project-specific checks"]
        settings[".circleci/application.json: project parameters"]
    end

    shared["webdev-ci: config/application YAML files"]
    setup --> download["Download selected shared branch once"]
    shared --> download
    download --> pack["Bundle scripts, then circleci config pack"]
    pack --> continue["CircleCI continuation: shared YAML plus project parameters"]
    settings --> continue
    continue --> select{"Select workflow using ci_action or automatic trigger rules"}

    select -->|"checks"| deps["build: install dependencies once"]
    deps --> phpcs["coding_standards"]
    deps --> deprecated["deprecated_code_phpstan"]
    deps --> disallowed["disallowed_functions"]
    deps --> parent["Optional Unity parent-repository check"]

    select -->|"edge-build"| build["edge_build_shared: load shared scripts"]
    build --> source["Fetch configured application source_branch"]
    source --> packages["Resolve packages and optionally compile themes"]
    packages --> publish["Publish Edge branch if files changed; reject concurrent branch changes"]
    publish --> deploy["Upsun deployment triggered by branch push"]

    select -->|"edge-refresh"| refresh["edge_refresh_shared: load shared scripts"]
    refresh --> guard["Check target is non-production and has expected branch"]
    guard --> sync["Copy parent database and files"]
    sync --> finalise["Check target again; run updates, config/content imports and QA setup per site"]

    scripts["webdev-ci: scripts/edge"] -.->|"Included in generated configuration"| build
    scripts -.->|"Included in generated configuration"| refresh
```

The build and refresh remain separate scheduled workflows. There is no waiting
job between them and no check that a particular commit has finished deploying.
The refresh job stays running while its remote commands finish.

The setup starts in manual-only mode (`ci_action=none`). Trigger an explicit
action to test it. Later, set the setup default to `auto` for branch checks and
configure PR triggers in CircleCI. Each schedule must pass its action explicitly;
schedule names do not select workflows.

## Testing from CORP-855

Set `shared_ci_ref` to `CORP-855` in the setup template or pipeline trigger.
Its default is `main`. Setup downloads that branch once and includes its scripts
in the generated YAML. Jobs unpack the bundle without downloading shared code.

Keep these branch choices separate:

| Choice | What it supplies |
| --- | --- |
| Application pipeline branch | The project's setup file and checkout |
| Shared CI branch, `shared_ci_ref` | Shared YAML and scripts; `CORP-855` during the trial |
| `source_branch` | Application code used to build Edge |

Publishing changes to the shared branch does not switch existing legacy
projects. Each project must first adopt the new setup. Repository validation
jobs check both configurations separately; they do not run these Edge workflows.

See [getting started](getting-started.md) for local checks and the
[application reference](application-ci.md) for project settings.
