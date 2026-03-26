#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <DRUPAL_DEPLOY_PATH> <PHPCS_CHECK_DIRS> <IGNORE_DIRS>"
  echo "  DRUPAL_DEPLOY_PATH: Path to Drupal deployment (e.g., /home/docker/project)"
  echo "  PHPCS_CHECK_DIRS: Space-separated directories to check, relative to DRUPAL_DEPLOY_PATH or absolute"
  echo "  IGNORE_DIRS: Space-separated directories to ignore, relative to DRUPAL_DEPLOY_PATH or absolute"
  exit 1
fi

DRUPAL_DEPLOY_PATH="$1"
PHPCS_CHECK_DIRS_RAW="$2"
IGNORE_DIRS_RAW="$3"

PHPCS_PATH="${DRUPAL_DEPLOY_PATH}/vendor/bin/phpcs"
PHPCS_EXTENSIONS="php,inc,module,theme"

DRUPAL_EXCLUDED_SNIFFS=(
  Drupal.Commenting.DocComment
  Drupal.Commenting.ClassComment
)

DRUPAL_PRACTICE_EXCLUDED_SNIFFS=(
  DrupalPractice.Objects.StrictSchemaDisabled
)

normalize_paths_to_array() {
  local raw="$1"
  local -n out_array=$2
  local item trimmed fullpath

  out_array=()

  # Split on normal shell whitespace.
  read -r -a items <<< "$raw"

  for item in "${items[@]}"; do
    trimmed="$(echo "$item" | xargs)"
    [ -z "$trimmed" ] && continue

    if [[ "$trimmed" = /* ]]; then
      fullpath="$trimmed"
    else
      fullpath="${DRUPAL_DEPLOY_PATH}/${trimmed}"
    fi

    out_array+=("$fullpath")
  done
}

normalize_paths_to_array "$PHPCS_CHECK_DIRS_RAW" CHECK_DIRS
normalize_paths_to_array "$IGNORE_DIRS_RAW" IGNORE_DIRS_ARRAY

if [ "${#CHECK_DIRS[@]}" -eq 0 ]; then
  echo "ERROR: No PHPCS check directories were provided."
  exit 1
fi

IGNORE_PATHS=""
if [ "${#IGNORE_DIRS_ARRAY[@]}" -gt 0 ]; then
  IGNORE_PATHS="$(IFS=, ; echo "${IGNORE_DIRS_ARRAY[*]}")"
fi

echo "----------------------------------------------------------------------"
echo ">>> Running coding standard checks in:"
printf ' - %s\n' "${CHECK_DIRS[@]}"
echo ">>> Ignoring directories:"
if [ -n "$IGNORE_PATHS" ]; then
  printf ' - %s\n' "${IGNORE_DIRS_ARRAY[@]}"
else
  echo " - none"
fi
echo "----------------------------------------------------------------------"

"${PHPCS_PATH}" --config-set installed_paths \
  "${DRUPAL_DEPLOY_PATH}/vendor/drupal/coder/coder_sniffer,${DRUPAL_DEPLOY_PATH}/vendor/slevomat/coding-standard"

EXCLUDE="$(IFS=, ; echo "${DRUPAL_EXCLUDED_SNIFFS[*]}")"

if ! "${PHPCS_PATH}" -nq \
  --standard=Drupal \
  --extensions="${PHPCS_EXTENSIONS}" \
  --exclude="${EXCLUDE}" \
  ${IGNORE_PATHS:+--ignore="${IGNORE_PATHS}"} \
  "${CHECK_DIRS[@]}"; then
  echo "🚫 Drupal coding standards checks failed, see above for details 🚫"
  exit 1
fi

EXCLUDE="$(IFS=, ; echo "${DRUPAL_PRACTICE_EXCLUDED_SNIFFS[*]}")"

if ! "${PHPCS_PATH}" -nq \
  --standard=DrupalPractice \
  --extensions="${PHPCS_EXTENSIONS}" \
  --exclude="${EXCLUDE}" \
  ${IGNORE_PATHS:+--ignore="${IGNORE_PATHS}"} \
  "${CHECK_DIRS[@]}"; then
  echo "🚫 Drupal best practice checks failed, see above for details 🚫"
  exit 1
fi

echo "LGTM ✅"