#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_UNDER_TEST="${REPOSITORY_ROOT}/scripts/reconcile-solr-indexes.sh"
TEST_ROOT="$(mktemp -d)"

trap 'rm -rf "${TEST_ROOT}"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

mkdir -p "${TEST_ROOT}/bin"
cat > "${TEST_ROOT}/bin/drush" <<'MOCK_DRUSH'
#!/usr/bin/env bash

set -euo pipefail

state="$(< "${MOCK_STATE_FILE}")"
command_line="$*"

if [[ "${command_line}" == *'loadMultiple()'* ]]; then
  printf 'SOLR_INDEX\tdefault_index\n'
elif [[ "${command_line}" == *'getSolrVersion(TRUE)'* ]]; then
  printf 'SOLR_VERSION\t%s\n' "${MOCK_TARGET_VERSION}"
elif [[ "${command_line}" == *'getRemainingItemsCount()'* ]]; then
  if [[ "${state}" == cleared ]]; then
    printf '%s\n' "${MOCK_ITEMS_AFTER_CLEAR}"
  else
    printf '0\n'
  fi
elif [[ "${command_line}" == *'isAvailable()'* ]]; then
  exit 0
elif [[ "${command_line}" == *'search-api:clear'* ]]; then
  printf 'cleared\n' > "${MOCK_STATE_FILE}"
  echo '[error] Fastly service ID is absent on this edge environment.'
  echo '[success] Index was successfully cleared.'
elif [[ "${command_line}" == *'search-api:index'* ]]; then
  printf 'indexed\n' > "${MOCK_STATE_FILE}"
  echo '[success] Tracker items were processed.'
elif [[ "${command_line}" == *'cache:rebuild'* ]]; then
  echo '[success] Cache rebuild complete.'
else
  echo "Unexpected mock Drush command: ${command_line}" >&2
  exit 1
fi
MOCK_DRUSH
chmod +x "${TEST_ROOT}/bin/drush"

create_app() {
  local app_root="$1"
  local layout="$2"

  mkdir -p "${app_root}/web"
  if [[ "${layout}" == project ]]; then
    mkdir -p "${app_root}/project/sites/example"
    touch "${app_root}/project/sites/example/settings.php"
  else
    mkdir -p "${app_root}/web/sites/default"
    touch "${app_root}/web/sites/default/settings.php"
  fi
}

run_reconcile_case() {
  local name="$1"
  local layout="$2"
  local source_version="$3"
  local target_version="$4"
  local expected_output="$5"
  local expected_state="$6"
  local app_root="${TEST_ROOT}/${name}/app"
  local state_file="${TEST_ROOT}/${name}/state"
  local output

  create_app "${app_root}" "${layout}"
  printf 'initial\n' > "${state_file}"

  if ! output=$(
    PATH="${TEST_ROOT}/bin:${PATH}" \
    PLATFORM_APP_DIR="${app_root}" \
    SOURCE_SOLR_VERSION="${source_version}" \
    MOCK_STATE_FILE="${state_file}" \
    MOCK_TARGET_VERSION="${target_version}" \
    MOCK_ITEMS_AFTER_CLEAR=10 \
    CHUNK_PAUSE_SECONDS=0 \
    INDEX_PAUSE_SECONDS=0 \
    SITE_PAUSE_SECONDS=0 \
    SOLR_READY_DELAY_SECONDS=0 \
    bash "${SCRIPT_UNDER_TEST}" reconcile 2>&1
  ); then
    echo "${output}" >&2
    fail "${name} returned a failure status"
  fi

  [[ "${output}" == *"${expected_output}"* ]] || {
    echo "${output}" >&2
    fail "${name} did not contain expected output: ${expected_output}"
  }
  [[ "$(< "${state_file}")" == "${expected_state}" ]] ||
    fail "${name} ended in state $(< "${state_file}"); expected ${expected_state}"
}

run_detect_case() {
  local app_root="${TEST_ROOT}/detect/app"
  local state_file="${TEST_ROOT}/detect/state"
  local output

  create_app "${app_root}" project
  printf 'initial\n' > "${state_file}"
  output=$(
    PATH="${TEST_ROOT}/bin:${PATH}" \
    PLATFORM_APP_DIR="${app_root}" \
    MOCK_STATE_FILE="${state_file}" \
    MOCK_TARGET_VERSION=9.9.0 \
    MOCK_ITEMS_AFTER_CLEAR=10 \
    bash "${SCRIPT_UNDER_TEST}" detect
  )
  [[ "${output}" == *$'DETECTED_SOLR_VERSION\t9.9.0'* ]] ||
    fail "detect mode did not emit the source version"
  [[ "$(< "${state_file}")" == initial ]] ||
    fail "detect mode mutated the index"
}

run_detect_case

run_reconcile_case \
  web_sites_matching_version web \
  8.11.2 8.11.2 \
  'matches the source environment; no rebuild needed' initial

run_reconcile_case \
  project_sites_changed_version project \
  9.8.0 9.9.0 \
  'rebuilding for Solr version change' indexed

echo 'reconcile-solr-indexes tests passed.'
