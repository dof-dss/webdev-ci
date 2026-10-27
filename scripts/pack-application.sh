#!/usr/bin/env bash
# Run during setup (or locally). Writes runnable CircleCI YAML to stdout.
# Bundle lifecycle and inspection commands: docs/shared-script-bundle.md
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
pack_dir=$(mktemp -d /tmp/webdev-ci-pack.XXXXXX)
trap 'rm -rf "$pack_dir"' EXIT
cp -R "$repo_root/config/application" "$pack_dir/application"
# Bake the setup default into continuation too; defaults are otherwise independent.
case "${CI_ACTION:-none}" in
  none|auto|checks|edge-build|edge-refresh) ;;
  *) echo 'Unknown CI_ACTION' >&2; exit 1 ;;
esac
sed -i "/^  ci_action:/,/^  source_branch:/s/default: none/default: ${CI_ACTION:-none}/" \
  "$pack_dir/application/@config.yml"
# 1. Archive only the files jobs need, preserving their repository-relative paths.
tar -czf "$pack_dir/scripts.tar.gz" -C "$repo_root" scripts/edge phpcs.sh
# 2. Encode the compressed archive as text for the generated YAML environment.
#    Base64 is transport encoding, not encryption. Never add secrets here.
base64 < "$pack_dir/scripts.tar.gz" | tr -d '\n' > "$pack_dir/bundle"
# 3. Replace the placeholder command in the temporary config copy. Its environment
#    holds the archive; its command holds the unpacker source, ready to execute.
#    The unpacker need not exist in the application's checkout. JSON is valid YAML.
jq -n --rawfile bundle "$pack_dir/bundle" --rawfile command "$repo_root/scripts/unpack-shared-tools.sh" \
  '{steps: [{run: {name: "Load shared scripts bundled during setup", environment: {CI_SHARED_BUNDLE: $bundle}, command: $command}}]}' \
  > "$pack_dir/application/commands/prepare_shared_tools.yml"
# 4. CircleCI assembles the config; jobs later invoke prepare_shared_tools.
circleci config pack "$pack_dir/application"
