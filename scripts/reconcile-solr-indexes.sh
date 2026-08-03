#!/usr/bin/env bash

# Compare Drupal's Search API tracker counts with the number of documents that
# can actually be queried from Solr. Clear and fully rebuild only indexes whose
# counts differ. Intended to run inside a deployed Platform.sh application.

set -uo pipefail

APP_ROOT="${PLATFORM_APP_DIR:-/app}"
SITES_ROOT="${APP_ROOT}/project/sites"
INDEX_CHUNK_SIZE="${INDEX_CHUNK_SIZE:-500}"
INDEX_BATCH_SIZE="${INDEX_BATCH_SIZE:-25}"
CHUNK_PAUSE_SECONDS="${CHUNK_PAUSE_SECONDS:-5}"
INDEX_PAUSE_SECONDS="${INDEX_PAUSE_SECONDS:-10}"
SITE_PAUSE_SECONDS="${SITE_PAUSE_SECONDS:-20}"
SOLR_READY_RETRIES="${SOLR_READY_RETRIES:-12}"
SOLR_READY_DELAY_SECONDS="${SOLR_READY_DELAY_SECONDS:-10}"
CLEAR_RETRIES="${CLEAR_RETRIES:-3}"
VERIFY_RETRIES="${VERIFY_RETRIES:-6}"

if command -v drush >/dev/null 2>&1; then
  DRUSH=(drush)
elif [[ -x "${APP_ROOT}/vendor/bin/drush" ]]; then
  DRUSH=("${APP_ROOT}/vendor/bin/drush")
else
  echo "ERROR: Drush was not found." >&2
  exit 1
fi

if [[ ! -d "${SITES_ROOT}" ]]; then
  echo "ERROR: Site directory not found: ${SITES_ROOT}" >&2
  exit 1
fi

for setting in \
  INDEX_CHUNK_SIZE INDEX_BATCH_SIZE CHUNK_PAUSE_SECONDS INDEX_PAUSE_SECONDS \
  SITE_PAUSE_SECONDS SOLR_READY_RETRIES SOLR_READY_DELAY_SECONDS CLEAR_RETRIES \
  VERIFY_RETRIES; do
  if [[ ! "${!setting}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: ${setting} must be a non-negative integer." >&2
    exit 1
  fi
done

if (( INDEX_CHUNK_SIZE == 0 || INDEX_BATCH_SIZE == 0 || SOLR_READY_RETRIES == 0 || CLEAR_RETRIES == 0 || VERIFY_RETRIES == 0 )); then
  echo "ERROR: Chunk sizes and retry counts must be greater than zero." >&2
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

pause_for() {
  local seconds="$1"
  if (( seconds > 0 )); then
    sleep "${seconds}"
  fi
}

drush_for_site() {
  local site="$1"
  shift
  "${DRUSH[@]}" --root="${APP_ROOT}/web" --uri="${site}" "$@"
}

discover_solr_indexes() {
  local site="$1"

  drush_for_site "${site}" php:eval '
    foreach (\Drupal::entityTypeManager()->getStorage("search_api_index")->loadMultiple() as $index) {
      $server = $index->getServerInstanceIfAvailable();
      if ($index->status() && $server && $server->getBackendId() === "search_api_solr") {
        echo "SOLR_INDEX\t", $index->id(), PHP_EOL;
      }
    }
  '
}

get_index_counts() {
  local site="$1"
  local index="$2"

  RECONCILE_INDEX_ID="${index}" drush_for_site "${site}" php:eval '
    $index = \Drupal::entityTypeManager()
      ->getStorage("search_api_index")
      ->load(getenv("RECONCILE_INDEX_ID"));
    if (!$index) {
      throw new \RuntimeException("Search API index was not found.");
    }

    $tracker_count = $index->getTrackerInstance()->getIndexedItemsCount();
    $query = $index->query();
    $query->range(0, 0);
    $query->setOption("search_api_bypass_access", TRUE);
    $solr_count = $query->execute()->getResultCount();
    printf("SOLR_COUNTS\t%d\t%d\n", $tracker_count, $solr_count);
  ' | awk -F '\t' '$1 == "SOLR_COUNTS" { print $2 "\t" $3 }'
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

    # Search API can log a backend exception followed by a success message and
    # still return zero, so inspect the output as well as the exit status.
    if [[ "${clear_succeeded}" == true ]] && ! grep -qE '\[error\]|SearchApiSolrException|SolrCore is loading' <<< "${clear_output}"; then
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
  local stalled_attempts=0

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
    if ! drush_for_site "${site}" search-api:index \
      --limit="${INDEX_CHUNK_SIZE}" \
      --batch-size="${INDEX_BATCH_SIZE}" \
      "${index}"; then
      return 1
    fi

    if ! after=$(get_remaining_items "${site}" "${index}"); then
      return 1
    fi

    if (( after >= before )); then
      (( stalled_attempts++ ))
      if (( stalled_attempts >= 3 )); then
        echo "ERROR: ${site}/${index}: indexing made no progress after ${stalled_attempts} attempts." >&2
        return 1
      fi
    else
      stalled_attempts=0
    fi

    if (( after > 0 )); then
      pause_for "${CHUNK_PAUSE_SECONDS}"
    fi
  done
}

verify_alignment() {
  local site="$1"
  local index="$2"
  local attempt
  local counts
  local tracker_count
  local solr_count

  for (( attempt = 1; attempt <= VERIFY_RETRIES; attempt++ )); do
    if counts=$(get_index_counts "${site}" "${index}"); then
      IFS=$'\t' read -r tracker_count solr_count <<< "${counts}"
      if [[ "${tracker_count}" =~ ^[0-9]+$ && "${solr_count}" =~ ^[0-9]+$ && "${tracker_count}" -eq "${solr_count}" ]]; then
        echo "${site}/${index}: verified ${solr_count} documents in Solr."
        return 0
      fi
    fi

    if (( attempt < VERIFY_RETRIES )); then
      echo "${site}/${index}: counts have not converged; waiting ${SOLR_READY_DELAY_SECONDS}s."
      pause_for "${SOLR_READY_DELAY_SECONDS}"
    fi
  done

  echo "ERROR: ${site}/${index}: tracker count ${tracker_count:-unknown} does not match Solr count ${solr_count:-unknown}." >&2
  return 1
}

failed_indexes=()
rebuilt_indexes=()
aligned_indexes=()
skipped_sites=()

echo "Checking Drupal tracker counts against live Solr counts across ${#SITES[@]} sites."

for site in "${SITES[@]}"; do
  echo
  echo "===== ${site}: checking Solr indexes ====="

  if ! index_output=$(discover_solr_indexes "${site}" 2>&1); then
    echo "NOTICE: ${site}: Drupal did not bootstrap; skipping."
    skipped_sites+=("${site}")
    continue
  fi

  mapfile -t solr_indexes < <(awk -F '\t' '$1 == "SOLR_INDEX" { print $2 }' <<< "${index_output}")
  if (( ${#solr_indexes[@]} == 0 )); then
    echo "No enabled Solr indexes found; skipping ${site}."
    skipped_sites+=("${site}")
    continue
  fi

  mismatched_indexes=()
  for index in "${solr_indexes[@]}"; do
    if ! counts=$(get_index_counts "${site}" "${index}"); then
      echo "ERROR: ${site}/${index}: could not query tracker and Solr counts." >&2
      failed_indexes+=("${site}/${index} (count check)")
      continue
    fi

    IFS=$'\t' read -r tracker_count solr_count <<< "${counts}"
    if [[ ! "${tracker_count}" =~ ^[0-9]+$ || ! "${solr_count}" =~ ^[0-9]+$ ]]; then
      echo "ERROR: ${site}/${index}: invalid counts: tracker=${tracker_count:-unknown}, Solr=${solr_count:-unknown}." >&2
      failed_indexes+=("${site}/${index} (invalid counts)")
      continue
    fi

    if (( tracker_count == solr_count )); then
      echo "${site}/${index}: aligned at ${solr_count} documents."
      aligned_indexes+=("${site}/${index}")
    else
      echo "${site}/${index}: mismatch detected (tracker=${tracker_count}, Solr=${solr_count}); scheduling a full rebuild."
      mismatched_indexes+=("${index}")
    fi
  done

  if (( ${#mismatched_indexes[@]} == 0 )); then
    continue
  fi

  if ! drush_for_site "${site}" --yes cache:rebuild; then
    echo "ERROR: ${site}: cache rebuild failed; mismatched indexes were not rebuilt." >&2
    for index in "${mismatched_indexes[@]}"; do
      failed_indexes+=("${site}/${index} (cache rebuild)")
    done
    continue
  fi

  for index in "${mismatched_indexes[@]}"; do
    echo "===== ${site}/${index}: clearing mismatched index ====="
    if ! clear_solr_index "${site}" "${index}"; then
      echo "ERROR: ${site}/${index}: clear failed." >&2
      failed_indexes+=("${site}/${index} (clear)")
      pause_for "${INDEX_PAUSE_SECONDS}"
      continue
    fi

    echo "===== ${site}/${index}: rebuilding full index ====="
    if ! rebuild_solr_index "${site}" "${index}"; then
      echo "ERROR: ${site}/${index}: rebuild failed." >&2
      failed_indexes+=("${site}/${index} (rebuild)")
      pause_for "${INDEX_PAUSE_SECONDS}"
      continue
    fi

    if verify_alignment "${site}" "${index}"; then
      rebuilt_indexes+=("${site}/${index}")
    else
      failed_indexes+=("${site}/${index} (verification)")
    fi

    pause_for "${INDEX_PAUSE_SECONDS}"
  done

  pause_for "${SITE_PAUSE_SECONDS}"
done

echo
echo "Solr reconciliation complete: ${#aligned_indexes[@]} already aligned, ${#rebuilt_indexes[@]} rebuilt, ${#skipped_sites[@]} sites skipped, ${#failed_indexes[@]} failures."

if (( ${#failed_indexes[@]} > 0 )); then
  printf 'Failed: %s\n' "${failed_indexes[*]}" >&2
  exit 1
fi
