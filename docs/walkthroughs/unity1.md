# Adopt the shared CI in Unity 1

Unity 1 uses the same central jobs as the other applications. Keep its actual
`.circleci/config.yml` and `.circleci/application.json` in `dof-dss/nicsdru_unity`,
not in webdev-ci. No project configuration or packed YAML snapshot is maintained
alongside this guide.

Start with [setup.yml](../../templates/application/setup.yml) and
[unity.json](../../templates/application/unity.json). Follow the
[corp-lite walkthrough](corp-lite.md) for local compilation, manual trials,
PR triggers and independent nightly schedules, substituting the Unity checkout
and CircleCI project.

Review these Unity-specific settings before adopting:

- Preserve `scripts/solr/verify-configsets` in the application's setup.
- Choose PHP, Node and package constraints compatible with the application branch.
  The template uses Drupal 11 package branches; do not apply it to a Drupal 10
  application without adapting those constraints.
- Set the project's SSH fingerprint and expected multisite list using its own
  configuration. Avoid copying a site list from an older Unity snapshot.
- Set `source_branch` explicitly for the trial. This is separate from the pipeline
  branch and the shared `webdev-ci/CORP-855` branch.
- Review the theme build script, sites directory and generated asset patterns.
  Only selected assets are committed by the Edge build.
- Confirm required preflight, Unity safe content imports, Fastly handling and QA
  commands are appropriate for the deployed application.
- Keep `check_illegal_updates` enabled if the project should check changes against
  its Unity parent repository. Review its results against the intended baseline.

Run `checks` first. Test theme compilation and Edge publication separately, then
verify deployment before testing refresh and QA logins on every site. Hosted Edge
operations require permission for the target operation.

Generate inspection files in `/tmp`, using the application's current parameter
file. Publish any setup or parameter changes through the application's own PRs.
