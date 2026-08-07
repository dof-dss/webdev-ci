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

remaining="$(< "${MOCK_STATE_FILE}")"
command_line="$*"
printf '%s\n' "${command_line}" >> "${MOCK_COMMAND_LOG}"

if [[ "${command_line}" == *'loadMultiple()'* ]]; then
  if [[ "${MOCK_HAS_SOLR_INDEX}" == true ]]; then
    printf 'SOLR_INDEX\tdefault_index\n'
  fi
elif [[ "${command_line}" == *'getRemainingItemsCount()'* ]]; then
  printf '%s\n' "${remaining}"
elif [[ "${command_line}" == *'isAvailable()'* ]]; then
  exit 0
elif [[ "${command_line}" == *'search-api:clear'* ]]; then
  printf '%s\n' "${MOCK_ITEMS_AFTER_CLEAR}" > "${MOCK_STATE_FILE}"
  echo '[error] Fastly service ID is absent on this edge environment.'
  echo '[success] Index was successfully cleared.'
elif [[ "${command_line}" == *'search-api:index'* ]]; then
  if [[ "${MOCK_FATAL_INDEX_ERROR:-false}" == true ]]; then
    echo '[error] PHP Fatal error: deterministic indexing failure.'
    exit 1
  fi
  if [[ "${MOCK_TRANSIENT_ONCE:-false}" != false && ! -e "${MOCK_TRANSIENT_MARKER}" ]]; then
    touch "${MOCK_TRANSIENT_MARKER}"
    if [[ "${MOCK_TRANSIENT_ONCE}" == progress ]]; then
      remaining=$(( remaining / 2 ))
    fi
    printf '%s\n' "${remaining}" > "${MOCK_STATE_FILE}"
    echo '[error] SearchApiSolrException: Operation timed out after 5000 milliseconds.'
    exit 1
  fi
  printf '0\n' > "${MOCK_STATE_FILE}"
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

run_case() {
  local name="$1"
  local layout="$2"
  local has_solr_index="$3"
  local expected_output="$4"
  local expected_remaining="$5"
  local mode="${6:-rebuild}"
  local transient_once="${7:-false}"
  local site_filter="${8:-}"
  local index_filter="${9:-}"
  local app_root="${TEST_ROOT}/${name}/app"
  local state_file="${TEST_ROOT}/${name}/remaining"
  local command_log="${TEST_ROOT}/${name}/commands"
  local transient_marker="${TEST_ROOT}/${name}/transient-fired"
  local output

  create_app "${app_root}" "${layout}"
  printf '10\n' > "${state_file}"
  : > "${command_log}"

  if ! output=$(
    PATH="${TEST_ROOT}/bin:${PATH}" \
    PLATFORM_APP_DIR="${app_root}" \
    MOCK_STATE_FILE="${state_file}" \
    MOCK_COMMAND_LOG="${command_log}" \
    MOCK_TRANSIENT_MARKER="${transient_marker}" \
    MOCK_HAS_SOLR_INDEX="${has_solr_index}" \
    MOCK_ITEMS_AFTER_CLEAR=10 \
    MOCK_TRANSIENT_ONCE="${transient_once}" \
    RECONCILE_MODE="${mode}" \
    SITE_FILTER="${site_filter}" \
    INDEX_FILTER="${index_filter}" \
    CHUNK_PAUSE_SECONDS=0 \
    INDEX_PAUSE_SECONDS=0 \
    SITE_PAUSE_SECONDS=0 \
    SOLR_READY_DELAY_SECONDS=0 \
    INDEX_RETRY_DELAY_SECONDS=0 \
    bash "${SCRIPT_UNDER_TEST}" 2>&1
  ); then
    echo "${output}" >&2
    fail "${name} returned a failure status"
  fi

  [[ "${output}" == *"${expected_output}"* ]] || {
    echo "${output}" >&2
    fail "${name} did not contain expected output: ${expected_output}"
  }
  [[ "$(< "${state_file}")" == "${expected_remaining}" ]] ||
    fail "${name} ended with $(< "${state_file}") items; expected ${expected_remaining}"
}

run_case \
  web_site_without_solr web false \
  'no enabled Search API Solr indexes; skipping' 10

run_case \
  project_site_with_solr project true \
  'rebuild requested manually' 0

run_case \
  transient_timeout_with_progress project true \
  'Solr timed out after making progress' 0 rebuild progress

run_case \
  transient_timeout_without_progress project true \
  'transient Solr failure with no tracker progress' 0 rebuild no_progress

run_case \
  resume_without_clear project true \
  'resume requested manually' 0 resume false example default_index

resume_log="${TEST_ROOT}/resume_without_clear/commands"
if grep -q 'search-api:clear' "${resume_log}"; then
  fail 'resume mode cleared an existing index'
fi
grep -q 'search-api:index' "${resume_log}" ||
  fail 'resume mode did not continue indexing'

fatal_root="${TEST_ROOT}/fatal_index_error/app"
fatal_state="${TEST_ROOT}/fatal_index_error/remaining"
fatal_log="${TEST_ROOT}/fatal_index_error/commands"
create_app "${fatal_root}" project
printf '10\n' > "${fatal_state}"
: > "${fatal_log}"
if fatal_output=$(
  PATH="${TEST_ROOT}/bin:${PATH}" \
  PLATFORM_APP_DIR="${fatal_root}" \
  MOCK_STATE_FILE="${fatal_state}" \
  MOCK_COMMAND_LOG="${fatal_log}" \
  MOCK_TRANSIENT_MARKER="${TEST_ROOT}/fatal_index_error/transient-fired" \
  MOCK_HAS_SOLR_INDEX=true \
  MOCK_ITEMS_AFTER_CLEAR=10 \
  MOCK_FATAL_INDEX_ERROR=true \
  CHUNK_PAUSE_SECONDS=0 \
  INDEX_PAUSE_SECONDS=0 \
  SITE_PAUSE_SECONDS=0 \
  SOLR_READY_DELAY_SECONDS=0 \
  INDEX_RETRY_DELAY_SECONDS=0 \
  bash "${SCRIPT_UNDER_TEST}" 2>&1
); then
  fail 'deterministic indexing error returned success'
fi
[[ "${fatal_output}" == *'indexing failed with a non-Solr error'* ]] || {
  echo "${fatal_output}" >&2
  fail 'deterministic indexing error did not report the expected cause'
}
[[ "$(grep -c 'search-api:index' "${fatal_log}")" == 1 ]] ||
  fail 'deterministic indexing error was retried'

echo 'reconcile-solr-indexes tests passed.'
