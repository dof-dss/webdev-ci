#!/usr/bin/env bash

# Rebuild enabled Search API Solr indexes in an edge environment.
#
# CircleCI compares the Solr service types declared by the data-sync source and
# target Upsun environments before invoking this helper. It does not invoke the
# script when the target has no Solr service or when the declared versions
# match. Keeping that decision in shared-config.yml means this script only needs
# to perform one job: safely reconcile the target indexes after a version
# change. The default mode clears and rebuilds indexes; resume mode continues
# tracker work without clearing already indexed documents.
#
# Sites without an enabled Search API Solr index are logged and skipped. This
# supports multisite projects where only some sites use search as well as
# projects that declare a shared Solr service but currently have no active
# indexes.
#
# The script discovers Unity/Corp Lite sites under project/sites and standard
# Drupal projects under web/sites. PLATFORM_APP_DIR defaults to /app, while
# DRUPAL_ROOT and SITES_ROOT can override non-standard deployments. Chunk sizes,
# pauses, readiness retries, and operation retries can be overridden using the
# variables declared below. Exact site and index filters make the same helper
# suitable for targeted recovery after an interrupted overnight build.

set -uo pipefail

APP_ROOT="${PLATFORM_APP_DIR:-/app}"
DRUPAL_ROOT="${DRUPAL_ROOT:-${APP_ROOT}/web}"
SITES_ROOT="${SITES_ROOT:-}"
INDEX_CHUNK_SIZE="${INDEX_CHUNK_SIZE:-100}"
INDEX_BATCH_SIZE="${INDEX_BATCH_SIZE:-5}"
CHUNK_PAUSE_SECONDS="${CHUNK_PAUSE_SECONDS:-15}"
INDEX_PAUSE_SECONDS="${INDEX_PAUSE_SECONDS:-30}"
SITE_PAUSE_SECONDS="${SITE_PAUSE_SECONDS:-45}"
SOLR_READY_RETRIES="${SOLR_READY_RETRIES:-12}"
SOLR_READY_DELAY_SECONDS="${SOLR_READY_DELAY_SECONDS:-20}"
CLEAR_RETRIES="${CLEAR_RETRIES:-5}"
INDEX_RETRIES="${INDEX_RETRIES:-5}"
INDEX_RETRY_DELAY_SECONDS="${INDEX_RETRY_DELAY_SECONDS:-60}"
RECONCILE_MODE="${RECONCILE_MODE:-rebuild}"
SITE_FILTER="${SITE_FILTER:-}"
INDEX_FILTER="${INDEX_FILTER:-}"

if command -v drush >/dev/null 2>&1; then
  DRUSH=(drush)
elif [[ -x "${APP_ROOT}/vendor/bin/drush" ]]; then
  DRUSH=("${APP_ROOT}/vendor/bin/drush")
else
  echo "ERROR: Drush was not found." >&2
  exit 1
fi

if [[ ! -d "${DRUPAL_ROOT}" ]]; then
  echo "ERROR: Drupal root not found: ${DRUPAL_ROOT}" >&2
  exit 1
fi

if [[ -z "${SITES_ROOT}" ]]; then
  for sites_root_candidate in "${APP_ROOT}/project/sites" "${DRUPAL_ROOT}/sites"; do
    if [[ -d "${sites_root_candidate}" ]] &&
      find "${sites_root_candidate}" -mindepth 2 -maxdepth 2 -type f -name settings.php -print -quit | grep -q .; then
      SITES_ROOT="${sites_root_candidate}"
      break
    fi
  done
fi

if [[ -z "${SITES_ROOT}" || ! -d "${SITES_ROOT}" ]]; then
  echo "ERROR: Drupal sites directory was not found under ${APP_ROOT}/project/sites or ${DRUPAL_ROOT}/sites." >&2
  exit 1
fi

for setting in \
  INDEX_CHUNK_SIZE INDEX_BATCH_SIZE CHUNK_PAUSE_SECONDS INDEX_PAUSE_SECONDS \
  SITE_PAUSE_SECONDS SOLR_READY_RETRIES SOLR_READY_DELAY_SECONDS CLEAR_RETRIES \
  INDEX_RETRIES INDEX_RETRY_DELAY_SECONDS; do
  if [[ ! "${!setting}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: ${setting} must be a non-negative integer." >&2
    exit 1
  fi
done

if (( INDEX_CHUNK_SIZE == 0 || INDEX_BATCH_SIZE == 0 || SOLR_READY_RETRIES == 0 || CLEAR_RETRIES == 0 || INDEX_RETRIES == 0 )); then
  echo "ERROR: Chunk sizes and retry counts must be greater than zero." >&2
  exit 1
fi

if [[ "${RECONCILE_MODE}" != rebuild && "${RECONCILE_MODE}" != resume ]]; then
  echo "ERROR: RECONCILE_MODE must be either rebuild or resume." >&2
  exit 1
fi

mapfile -t SITES < <(
  find "${SITES_ROOT}" -mindepth 2 -maxdepth 2 -type f -name settings.php \
    -printf '%h\n' | sed 's#.*/##' | sort
)

if (( ${#SITES[@]} == 0 )); then
  echo "ERROR: No Drupal sites were found under ${SITES_ROOT}." >&2
  exit 1
fi

if [[ -n "${SITE_FILTER}" ]]; then
  site_found=false
  for site in "${SITES[@]}"; do
    if [[ "${site}" == "${SITE_FILTER}" ]]; then
      SITES=("${site}")
      site_found=true
      break
    fi
  done
  if [[ "${site_found}" != true ]]; then
    echo "ERROR: Site filter did not match a discovered site: ${SITE_FILTER}" >&2
    exit 1
  fi
fi

echo "Using Drupal root ${DRUPAL_ROOT} and sites directory ${SITES_ROOT} in ${RECONCILE_MODE} mode."

pause_for() {
  local seconds="$1"
  if (( seconds > 0 )); then
    sleep "${seconds}"
  fi
}

drush_for_site() {
  local site="$1"
  shift
  "${DRUSH[@]}" --root="${DRUPAL_ROOT}" --uri="${site}" "$@"
}

discover_solr_indexes() {
  local site="$1"

  # Restrict work to enabled Search API indexes backed by Search API Solr.
  # The tab-prefixed record distinguishes results from Drush status output.
  drush_for_site "${site}" php:eval '
    foreach (\Drupal::entityTypeManager()->getStorage("search_api_index")->loadMultiple() as $index) {
      $server = $index->getServerInstanceIfAvailable();
      if ($index->status() && $server && $server->getBackendId() === "search_api_solr") {
        echo "SOLR_INDEX\t", $index->id(), PHP_EOL;
      }
    }
  '
}

get_remaining_items() {
  local site="$1"
  local index="$2"
  local remaining

  if ! remaining=$(RECONCILE_INDEX_ID="${index}" drush_for_site "${site}" php:eval '
    $index = \Drupal::entityTypeManager()
      ->getStorage("search_api_index")
      ->load(getenv("RECONCILE_INDEX_ID"));
    if (!$index) {
      throw new \RuntimeException("Search API index was not found.");
    }
    echo $index->getTrackerInstance()->getRemainingItemsCount();
  '); then
    return 1
  fi

  if [[ ! "${remaining}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: ${site}/${index}: unexpected remaining-item count: ${remaining}" >&2
    return 1
  fi

  printf '%s\n' "${remaining}"
}

wait_for_solr_index() {
  local site="$1"
  local index="$2"
  local attempt

  for (( attempt = 1; attempt <= SOLR_READY_RETRIES; attempt++ )); do
    if RECONCILE_INDEX_ID="${index}" drush_for_site "${site}" php:eval '
      $index = \Drupal::entityTypeManager()
        ->getStorage("search_api_index")
        ->load(getenv("RECONCILE_INDEX_ID"));
      if (!$index || !$index->getServerInstance()->isAvailable()) {
        throw new \RuntimeException("Solr core is not ready.");
      }
    ' >/dev/null 2>&1; then
      return 0
    fi

    if (( attempt < SOLR_READY_RETRIES )); then
      echo "${site}/${index}: Solr is not ready (attempt ${attempt}/${SOLR_READY_RETRIES}); waiting ${SOLR_READY_DELAY_SECONDS}s."
      pause_for "${SOLR_READY_DELAY_SECONDS}"
    fi
  done

  return 1
}

clear_solr_index() {
  local site="$1"
  local index="$2"
  local attempt
  local clear_output
  local clear_succeeded

  for (( attempt = 1; attempt <= CLEAR_RETRIES; attempt++ )); do
    if ! wait_for_solr_index "${site}" "${index}"; then
      return 1
    fi

    clear_succeeded=true
    if ! clear_output=$(drush_for_site "${site}" --yes search-api:clear "${index}" 2>&1); then
      clear_succeeded=false
    fi
    printf '%s\n' "${clear_output}"

    # Ignore unrelated Drupal hook errors when Search API reports a successful
    # clear, but continue to reject failed commands and known Solr errors.
    if [[ "${clear_succeeded}" == true ]] &&
      ! grep -qE 'SearchApiSolrException|SolrCore is loading|Solr HTTP error|Solr endpoint .*unreachable' <<< "${clear_output}"; then
      return 0
    fi

    if (( attempt < CLEAR_RETRIES )); then
      echo "${site}/${index}: clear attempt ${attempt}/${CLEAR_RETRIES} failed; waiting ${SOLR_READY_DELAY_SECONDS}s." >&2
      pause_for "${SOLR_READY_DELAY_SECONDS}"
    fi
  done

  return 1
}

rebuild_solr_index() {
  local site="$1"
  local index="$2"
  local before
  local after
  local failed_attempts=0
  local index_output
  local index_succeeded
  local transient_failure
  local transient_failure_pattern='SearchApiSolrException|SolrCore is loading|Solr HTTP error|Solr endpoint .*unreachable|Operation timed out|pending server tasks could not be executed'

  # Use bounded Drush calls and retry transient Solr failures without clearing
  # partial progress. Deterministic non-Solr failures still fail immediately.
  while true; do
    if ! before=$(get_remaining_items "${site}" "${index}"); then
      return 1
    fi
    if (( before == 0 )); then
      return 0
    fi
    if ! wait_for_solr_index "${site}" "${index}"; then
      return 1
    fi

    echo "${site}/${index}: ${before} items remaining; processing up to ${INDEX_CHUNK_SIZE}."
    index_succeeded=true
    if ! index_output=$(drush_for_site "${site}" search-api:index \
      --limit="${INDEX_CHUNK_SIZE}" \
      --batch-size="${INDEX_BATCH_SIZE}" \
      "${index}" 2>&1); then
      index_succeeded=false
    fi
    printf '%s\n' "${index_output}"

    transient_failure=false
    if grep -qE "${transient_failure_pattern}" <<< "${index_output}"; then
      transient_failure=true
    fi

    if [[ "${index_succeeded}" != true && "${transient_failure}" != true ]]; then
      echo "ERROR: ${site}/${index}: indexing failed with a non-Solr error." >&2
      return 1
    fi

    if ! after=$(get_remaining_items "${site}" "${index}"); then
      return 1
    fi

    if (( after < before )); then
      failed_attempts=0
      if [[ "${transient_failure}" == true ]]; then
        echo "NOTICE: ${site}/${index}: Solr timed out after making progress (${before} -> ${after}); cooling down before resuming." >&2
        pause_for "${INDEX_RETRY_DELAY_SECONDS}"
      elif (( after > 0 )); then
        pause_for "${CHUNK_PAUSE_SECONDS}"
      fi
      continue
    fi

    (( failed_attempts++ ))
    if (( failed_attempts >= INDEX_RETRIES )); then
      if [[ "${transient_failure}" == true ]]; then
        echo "ERROR: ${site}/${index}: Solr remained unavailable after ${failed_attempts} indexing attempts." >&2
      else
        echo "ERROR: ${site}/${index}: indexing made no progress after ${failed_attempts} attempts." >&2
      fi
      return 1
    fi

    if [[ "${transient_failure}" == true ]]; then
      echo "NOTICE: ${site}/${index}: transient Solr failure with no tracker progress (attempt ${failed_attempts}/${INDEX_RETRIES}); waiting ${INDEX_RETRY_DELAY_SECONDS}s." >&2
    else
      echo "NOTICE: ${site}/${index}: no tracker progress (attempt ${failed_attempts}/${INDEX_RETRIES}); waiting ${INDEX_RETRY_DELAY_SECONDS}s." >&2
    fi
    pause_for "${INDEX_RETRY_DELAY_SECONDS}"
  done
}

failed_indexes=()
completed_indexes=()
skipped_sites=()
sites_without_solr=()
matched_index_filter=false

for site in "${SITES[@]}"; do
  if ! index_output=$(discover_solr_indexes "${site}" 2>&1); then
    echo "NOTICE: ${site}: Drupal did not bootstrap; skipping." >&2
    skipped_sites+=("${site}")
    continue
  fi

  mapfile -t solr_indexes < <(awk -F '\t' '$1 == "SOLR_INDEX" { print $2 }' <<< "${index_output}")
  if (( ${#solr_indexes[@]} == 0 )); then
    echo "${site}: no enabled Search API Solr indexes; skipping."
    sites_without_solr+=("${site}")
    continue
  fi

  if [[ -n "${INDEX_FILTER}" ]]; then
    filtered_indexes=()
    for index in "${solr_indexes[@]}"; do
      if [[ "${index}" == "${INDEX_FILTER}" ]]; then
        filtered_indexes+=("${index}")
        matched_index_filter=true
      fi
    done
    solr_indexes=("${filtered_indexes[@]}")
    if (( ${#solr_indexes[@]} == 0 )); then
      echo "${site}: enabled Solr indexes do not match filter ${INDEX_FILTER}; skipping."
      continue
    fi
  fi

  # Rebuild Drupal's caches once per affected site before mutating its indexes.
  if ! drush_for_site "${site}" --yes cache:rebuild; then
    echo "ERROR: ${site}: cache rebuild failed." >&2
    for index in "${solr_indexes[@]}"; do
      failed_indexes+=("${site}/${index} (cache rebuild)")
    done
    continue
  fi

  for index in "${solr_indexes[@]}"; do
    echo "===== ${site}/${index}: ${RECONCILE_MODE} for declared Solr version change ====="
    operation_failed=false
    if [[ "${RECONCILE_MODE}" == rebuild ]] && ! clear_solr_index "${site}" "${index}"; then
      operation_failed=true
    elif ! rebuild_solr_index "${site}" "${index}"; then
      operation_failed=true
    fi

    if [[ "${operation_failed}" == true ]]; then
      echo "ERROR: ${site}/${index}: ${RECONCILE_MODE} failed." >&2
      failed_indexes+=("${site}/${index} (${RECONCILE_MODE})")
    elif [[ "$(get_remaining_items "${site}" "${index}")" == 0 ]]; then
      completed_indexes+=("${site}/${index}")
    else
      echo "ERROR: ${site}/${index}: tracker items remain after ${RECONCILE_MODE}." >&2
      failed_indexes+=("${site}/${index} (verification)")
    fi
    pause_for "${INDEX_PAUSE_SECONDS}"
  done

  pause_for "${SITE_PAUSE_SECONDS}"
done

if [[ -n "${INDEX_FILTER}" && "${matched_index_filter}" != true ]]; then
  echo "ERROR: Index filter did not match an enabled Search API Solr index: ${INDEX_FILTER}" >&2
  exit 1
fi

echo "Solr reconciliation complete: ${#completed_indexes[@]} completed, ${#sites_without_solr[@]} sites without Solr indexes, ${#skipped_sites[@]} sites skipped."

if (( ${#failed_indexes[@]} > 0 )); then
  printf 'Failed: %s\n' "${failed_indexes[*]}" >&2
  exit 1
fi
