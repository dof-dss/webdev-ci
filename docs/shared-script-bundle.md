# How shared scripts reach a job

`CI_SHARED_BUNDLE` is a compressed copy of `scripts/edge/` and `phpcs.sh`, encoded
as Base64 text. It is generated output, not a setting to edit or a secret.

Setup downloads the chosen shared branch once. Bundling keeps its YAML and scripts
together, so later jobs do not download a potentially newer version of the scripts.

## 1. Assemble the bundle during setup

The [setup template](../templates/application/setup.yml) downloads `shared_ci_ref`
and runs [pack-application.sh](../scripts/pack-application.sh) from that download.
You can run the same script locally:

```bash
bash scripts/pack-application.sh > /tmp/application.yml
```

The script:

1. Copies `config/application/` into a temporary directory.
2. Archives `scripts/edge/` and `phpcs.sh` using `tar` and gzip.
3. Base64-encodes that archive into `CI_SHARED_BUNDLE`.
4. Generates `prepare_shared_tools` with both the bundle and the source of
   [unpack-shared-tools.sh](../scripts/unpack-shared-tools.sh).
5. Runs `circleci config pack` on the temporary config and writes the YAML.

The source [prepare_shared_tools.yml](../config/application/commands/prepare_shared_tools.yml)
is a placeholder. Only the temporary copy is replaced. The generated command
contains the unpacker itself, so the application does not need a copy of it.

## 2. Unpack inside the job

When a job calls `prepare_shared_tools`, CircleCI runs the embedded unpacker:

```bash
printf '%s' "${CI_SHARED_BUNDLE:?Missing shared script bundle}" \
  | base64 --decode \
  | tar -xz -C "$shared_dir"
```

This restores the following files in a new temporary directory inside that job:

```text
/tmp/webdev-ci.<random>/
  phpcs.sh
  scripts/edge/
    build.sh
    guard.sh
    refresh.sh
    finalise.sh
```

The unpacker writes two exports to `$BASH_ENV`. CircleCI loads that file in later
Bash steps:

| Variable | Points to |
| --- | --- |
| `CI_SHARED_ROOT` | The extracted temporary directory |
| `CI_SHARED_TOOLS` | Its `scripts/edge` directory |

Each job extracts its own copy of the same bundle. The files are not transferred
between jobs through a workspace.

## 3. Use the extracted scripts

| Job | What it runs after `prepare_shared_tools` |
| --- | --- |
| [edge_build_shared](../config/application/jobs/edge_build_shared.yml) | `bash "$CI_SHARED_TOOLS/build.sh"` |
| [edge_refresh_shared](../config/application/jobs/edge_refresh_shared.yml) | `bash "$CI_SHARED_TOOLS/refresh.sh"` |
| [coding_standards](../config/application/jobs/coding_standards.yml) | `bash "$CI_SHARED_ROOT/phpcs.sh"` with the project's analysis settings |

The refresh script also reads `guard.sh` and `finalise.sh` from that directory
and sends them to the target environment as part of the refresh operation.

## Inspect a bundle without running it

After packing, this command lists the archive's files. It needs Python and PyYAML
and does not extract files, execute scripts or contact a service:

```bash
python3 - <<'PY'
import base64
import io
import tarfile
import yaml

with open('/tmp/application.yml') as source:
    config = yaml.safe_load(source)
step = config['commands']['prepare_shared_tools']['steps'][0]['run']
bundle = step['environment']['CI_SHARED_BUNDLE']
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(bundle)), mode='r:gz') as archive:
    for member in archive.getmembers():
        print(member.name)
PY
```

To change behaviour, edit the original scripts and pack again. Do not edit the
encoded text. The offline tests unpack the generated bundle and compare its
files with the source files in the checkout.
