# Where to change the configuration

`application/` contains the new shared CircleCI configuration:

- `@config.yml` defines pipeline parameters, such as the PHP version.
- `commands/` contains steps reused by jobs.
- `jobs/` defines the work to run.
- `workflows/` selects jobs and their running order.

From the repository root, combine these files into one YAML file:

```bash
circleci config pack config/application > /tmp/application.yml
```

Existing projects still use [`shared-config.yml`](../shared-config.yml). Edit
that file only when changing the old jobs. Do not replace it with the packed
output above.

The two configurations are separate, so both can have a job called `build`.
Changes to one do not change the other.

See the [application reference](../docs/application-ci.md) and [legacy
guide](../docs/legacy-ci.md). Both configurations use the root `phpcs.sh`
helper.
