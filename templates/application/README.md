# Application starting points

These files help a Drupal application adopt the shared jobs. They are templates,
not copies of any project's live configuration.

1. Copy `setup.yml` to the application's `.circleci/config.yml`.
2. Choose `corp-lite.json`, `unity.json`, `dept.json` or `nidirect.json`, review its
   settings, and copy it to the application's `.circleci/application.json`.
3. Replace the SSH fingerprint placeholder with the project's CircleCI key
   fingerprint. Review package constraints, paths, target branch and site settings.
4. Preserve project setup checks, such as Solr configset verification.

The setup starts in manual-only mode (`ci_action=none`). Its shared branch defaults
to `main`; choose `CORP-855` for a trial of that published branch. Set these in
setup or the pipeline trigger, not in `application.json`.

Package constraints are examples: the corp-lite, Unity and NIDirect templates
use Drupal 11 package branches, while DEPT uses Drupal 10 branches. They must
match the application you are adopting. Drupal itself is selected by the
application's Composer files, not a shared CI version parameter.

Keep the resulting project configuration in the application repository. Future
changes to these templates do not update files already copied into projects.
Generate packed YAML in `/tmp` or as a CI artifact; do not commit project snapshots
here.

See the [corp-lite walkthrough](../../docs/walkthroughs/corp-lite.md),
[Unity 1 walkthrough](../../docs/walkthroughs/unity1.md) and
[getting-started guide](../../docs/getting-started.md).
