#!/usr/bin/env bash
# Shared deprecation policy for application CI and DDEV. Run at project root.
set -euo pipefail
[[ $# -gt 0 ]] || { echo 'Usage: phpstan DIRECTORY [DIRECTORY ...] [PHPStan options]' >&2; exit 2; }
[[ -f vendor/bin/phpstan ]] || { echo 'Run composer install in the application first.' >&2; exit 1; }
project_root=$(pwd -P)
for dependency in mglaman/phpstan-drupal/extension.neon phpstan/phpstan-deprecation-rules/rules.neon; do
  [[ -f "vendor/$dependency" ]] || { echo "Missing vendor/$dependency; install the project development dependencies." >&2; exit 1; }
done
config=$(mktemp /tmp/webdev-ci-phpstan.XXXXXX.neon)
trap 'rm -f "$config"' EXIT
# Composer extension-installer already registers these extensions when present.
# Otherwise include them explicitly, using absolute paths from this temp config.
: > "$config"
if [[ ! -f vendor/phpstan/extension-installer/src/GeneratedConfig.php ]]; then
printf 'includes:\n    - "%s/vendor/mglaman/phpstan-drupal/extension.neon"\n    - "%s/vendor/phpstan/phpstan-deprecation-rules/rules.neon"\n' "$project_root" "$project_root" > "$config"
fi
cat >> "$config" <<'NEON'
parameters:
    level: 2
    reportUnmatchedIgnoredErrors: false
    fileExtensions:
        - php
        - module
        - theme
        - inc
        - install
        - profile
        - engine
    excludePaths:
        - '*/tests/Drupal/Tests/Listeners/Legacy/*'
        - '*/tests/fixtures/*.php'
        - '*/settings*.php'
        - '*/node_modules/*'
    ignoreErrors:
        - '#Unsafe usage of new static\(\)#'
        - '#\\Drupal calls should be avoided in classes, use dependency injection instead#'
        - '#Plugin definitions cannot be altered.#'
        - '#Missing cache backend declaration for performance.#'
        - '#Plugin manager has cache backend specified but does not declare cache tags.#'
NEON
# Reject alternate configs/levels so local invocations retain the CI policy.
for argument in "$@"; do
  case "$argument" in -c*|--configuration*|-l*|--level*) echo 'Configuration and level are owned by the shared CI policy.' >&2; exit 2 ;; esac
done
vendor/bin/phpstan analyse --configuration="$config" --memory-limit="${PHP_INI_MEMORY_LIMIT:-1G}" "$@"
