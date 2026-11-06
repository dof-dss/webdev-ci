#!/usr/bin/env bash
# Embedded as the generated prepare_shared_tools command by pack-application.sh.
# Runs inside each job that needs shared scripts. No network download is needed.
# See docs/shared-script-bundle.md for the full assembly -> unpack -> use flow.
set -euo pipefail
shared_dir=$(mktemp -d /tmp/webdev-ci.XXXXXX)
# Decode Base64, decompress gzip and restore the archived paths into this job.
printf '%s' "${CI_SHARED_BUNDLE:?Missing shared script bundle}" | base64 --decode | tar -xz -C "$shared_dir"
# CircleCI sources BASH_ENV in later Bash steps, making these paths available to
# build.sh / refresh.sh (CI_SHARED_TOOLS) and the PHPCS helper (CI_SHARED_ROOT).
printf 'export CI_SHARED_TOOLS=%q\n' "$shared_dir/scripts/edge" >> "$BASH_ENV"
printf 'export CI_SHARED_ROOT=%q\n' "$shared_dir" >> "$BASH_ENV"
