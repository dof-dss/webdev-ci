#!/usr/bin/env bash
set -euo pipefail
# This is run on the target before sync, and again before Drupal mutations.
: "${PLATFORM_ENVIRONMENT_TYPE:?Missing hosted environment type}"
: "${PLATFORM_BRANCH:?Missing hosted branch}" "${EDGE_BRANCH:?Missing expected branch}"
[[ "$PLATFORM_ENVIRONMENT_TYPE" != production && "$PLATFORM_BRANCH" == "$EDGE_BRANCH" ]] || {
  echo 'Refusing refresh: target is production or does not match the Edge branch' >&2
  exit 1
}
