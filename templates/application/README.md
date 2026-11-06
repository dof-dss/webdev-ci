# Application starting points

Choose a layout, then keep the reviewed settings in the application's own repo:

| File | Starting layout |
| --- | --- |
| `single-site.json` | One Drupal installation using `web/` and a single Drush URI |
| `multisite.json` | Several sites under `project/sites/`, with `web/` as the Drupal root |
| `setup.yml` | CircleCI setup that loads shared jobs and project parameters |

Copy `setup.yml` to the project's `.circleci/config.yml` and the chosen JSON file
to `.circleci/parameters.json`. That project-owned JSON becomes the canonical
settings file for CircleCI. Templates do not
supply settings at runtime or overwrite project files later.

## Adapt the settings

- Replace the SSH fingerprint placeholder with the project's CircleCI key fingerprint.
- Replace `REPLACE_WITH_PACKAGE:CONSTRAINT` with the actual Composer constraints
  for Edge builds, separated by spaces. Match the application's Drupal version.
- For multisite, replace `REPLACE_WITH_SITE_DIRECTORY_NAMES` with the expected
  directory names, separated by spaces. These are names such as `site_a site_b`,
  not URLs. An empty `sites` value instead discovers `project/sites/*/settings.php`,
  excluding `default`.
- For single-site, review the `uri` used by Drush (`default` initially).
- Review PHP version, analysis paths, ignored directories, application source
  branch (in setup) and Edge target. Remove analysis paths that do not exist in the project.
- Preserve project setup checks, such as Solr configset verification.

These generic templates use shared defaults for optional behaviour: no theme
build, content import, required preflight or Unity parent check. Add options such
as `content`, `preflight`, `deploy`, `build_themes` and `check_illegal_updates` to
project parameters only when needed. See the [parameter reference](../../docs/application-ci.md).

Placeholders allow configuration inspection but must be replaced before hosted
execution or a real Edge package build. Drupal itself is selected by the
application's Composer files.

Setup starts in manual-only mode (`ci_action=none`), with shared code from `main`.
Choose `CORP-855` to trial that published shared branch. Set `ci_action`, `shared_ci_ref`, `source_branch` and `edge_publish` in setup or
the trigger, not in `parameters.json`. Source defaults to `development`; Edge
publication defaults to `false`. Set `edge_publish=true` for actual publication,
including nightly build schedules.

Keep project configuration in its own repository. Generate packed YAML in `/tmp`
or as a CI artifact. See the [corp-lite walkthrough](../../docs/walkthroughs/corp-lite.md),
[Unity 1 walkthrough](../../docs/walkthroughs/unity1.md) and
[getting-started guide](../../docs/getting-started.md).
