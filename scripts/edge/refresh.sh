#!/usr/bin/env bash
set -euo pipefail
EDGE_ENVIRONMENT="${EDGE_ENVIRONMENT:-${EDGE_BRANCH:-}}"
: "${PLATFORM_PROJECT:?}" "${EDGE_ENVIRONMENT:?}" "${EDGE_BRANCH:?}" "${CI_SHARED_TOOLS:?}"
target=(-p "$PLATFORM_PROJECT" -e "$EDGE_ENVIRONMENT")
[[ -z "${PLATFORM_APPLICATION_NAME:-}" ]] || target+=(--app "$PLATFORM_APPLICATION_NAME")
# Quote values once for the remote shell. Scripts travel over stdin, not a
# mutable remote download. No backgrounding, polling or blanket retries.
remote_env='env'
for key in EDGE_BRANCH EDGE_LAYOUT EDGE_SITES EDGE_URI EDGE_PREFLIGHT EDGE_DEPLOY EDGE_CONTENT EDGE_FASTLY EDGE_QA_STAFF EDGE_AFTER_COMMAND; do
  printf -v argument '%q' "$key=${!key:-}"
  remote_env+=" $argument"
done
platform ssh "${target[@]}" -- "$remote_env bash -s" < "$CI_SHARED_TOOLS/guard.sh"
# Upsun syncs from the target environment's parent. That parent must be the
# intended production environment; confirm this when enrolling a consumer.
platform sync data -p "$PLATFORM_PROJECT" -e "$EDGE_ENVIRONMENT" -y
cat "$CI_SHARED_TOOLS/guard.sh" "$CI_SHARED_TOOLS/finalise.sh" |
  platform ssh "${target[@]}" -- "$remote_env bash -s"
