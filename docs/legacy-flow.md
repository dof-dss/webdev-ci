# Legacy CI flow

This shows the existing corp-lite setup. Each application keeps its own workflow
file and combines it with jobs downloaded from `webdev-ci/main`.

```mermaid
flowchart TD
    push["Application branch push"] --> setup
    buildSchedule["Nightly schedule: edge_build"] --> setup
    refreshSchedule["Later schedule: edge_build_finalise"] --> setup

    subgraph application["Application repository: corp-lite"]
        setup["Run .circleci/config.yml: checkout and verify Solr configsets"]
        local[".circleci/workflow-config.yml: project workflows"]
        settings["Project parameters in .circleci/config.yml"]
    end

    shared["webdev-ci/main/shared-config.yml: shared jobs and commands"]
    setup --> download["Download shared-config.yml"]
    shared --> download
    download --> combine["Combine shared jobs with local workflows"]
    local --> combine
    combine --> continue["CircleCI continuation: run combined configuration"]
    settings --> continue
    continue --> select{"Select workflow by branch and trigger"}

    select -->|"Eligible branch push"| deps["build: install dependencies"]
    deps --> phpcs["coding_standards"]
    deps --> deprecated["deprecated_code"]
    deps --> disallowed["disallowed_functions"]

    select -->|"edge_build schedule"| edge["build_edge: resolve packages and publish Edge branch"]
    edge --> deploy["Upsun deployment triggered by branch push"]

    select -->|"edge_build_finalise schedule"| sync["sync_data: copy parent database and files"]
    sync --> qa["Run password_qa_accounts"]
```

The nightly build and data refresh are independent. The gap between their
schedules allows time for deployment; the refresh does not wait for the build.

Unity uses separate `unity_edge_build` and `unity_sync_data` jobs in the same
shared file. These include theme building and per-site refresh work, which is
why the legacy jobs differ between projects.

The diagram focuses on normal checks and nightly runs. The inspected corp-lite
workflow also has a separate API-only `manual-edge-build` route on `DEPT-edge`,
where `sync_data` depends on `build_edge`. That dependency does not apply to the
nightly schedules. Branch-push checks are not inherently PR-only; CircleCI's
project trigger settings also determine when pipelines start.

See the [legacy guide](legacy-ci.md) for the setup example.
