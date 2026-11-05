#!/usr/bin/env bash
# Shared dependency and optional asset build. Publication belongs to build.sh.
set -euo pipefail
: "${EDGE_PACKAGES:?}"
theme_enabled=${THEME_BUILD_ENABLED:-}
if [[ -z "${THEME_BUILD_ENABLED+x}" ]]; then
  if [[ -n "${THEME_BUILD_SCRIPT:-}" ]]; then theme_enabled=true; else theme_enabled=false; fi
fi
case "$theme_enabled" in
  true|1) theme_enabled=true ;;
  false|0) theme_enabled=false ;;
  *) echo 'THEME_BUILD_ENABLED must be true or false' >&2; exit 1 ;;
esac
if [[ "$theme_enabled" == true ]]; then
  theme_script=${THEME_BUILD_SCRIPT:-}
  case "$theme_script" in
    ''|/*|..|../*|*/../*|*/..)
      echo 'THEME_BUILD_SCRIPT must be a relative file inside the checkout' >&2; exit 1 ;;
  esac
  checkout_root=$(pwd -P)
  # Resolve directory symlinks with Bash 3-compatible tools. Reject a final
  # symlink too, so scripts outside the checkout can never be followed.
  if [[ ! -f "$theme_script" || -L "$theme_script" ]]; then
    echo 'THEME_BUILD_SCRIPT must be a regular file, not a symlink' >&2; exit 1
  fi
  theme_directory=$(cd "$(dirname "./$theme_script")" && pwd -P)
  case "$theme_directory/" in
    "$checkout_root/"*) ;;
    *) echo 'THEME_BUILD_SCRIPT resolves outside the checkout' >&2; exit 1 ;;
  esac
fi
read -r -a packages <<< "$EDGE_PACKAGES"
composer require --no-interaction "${packages[@]}"
if [[ "$theme_enabled" == true ]]; then
  bash -e "./$theme_script" --sites-dir "$PWD/${THEME_SITES_DIR:-project/sites}"
fi
