#!/usr/bin/env bash
set -euo pipefail

# Require all arguments.
if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <DRUPAL_DEPLOY_PATH> <PHPCS_CHECK_DIR> <IGNORE>"
  echo "  DRUPAL_DEPLOY_PATH: Path to Drupal deployment (e.g., /var/www/deploy)"
  echo "  PHPCS_CHECK_DIR: Directory to check (e.g., web/modules/custom)"
  echo "  IGNORE: Comma-separated directories to ignore, relative to DRUPAL_DEPLOY_PATH or absolute"
  echo "          (e.g., web/themes/custom/mytheme/node_modules,web/modules/custom/my_module/tests)"
  exit 1
fi

DRUPAL_DEPLOY_PATH="$1"
PHPCS_CHECK_DIR="$2"
IGNORE_INPUT="$3"

PHPCS_PATH="${DRUPAL_DEPLOY_PATH}/vendor/bin/phpcs"
PHPCBF_PATH="${DRUPAL_DEPLOY_PATH}/vendor/bin/phpcbf"

PHPCS_EXTENSIONS="php,inc,module,theme"

# Exclude some fussier/less valuable sniffs.
DRUPAL_EXCLUDED_SNIFFS=(
  "Drupal.Commenting.DocComment"
  "Drupal.Commenting.ClassComment"
)

DRUPAL_PRACTICE_EXCLUDED_SNIFFS=(
  "DrupalPractice.Objects.StrictSchemaDisabled"
)

join_by_comma() {
  local IFS=","
  echo "$*"
}

normalise_ignore_paths() {
  local input="$1"
  local -a ignore_items=()
  local -a resolved_paths=()
  local item trimmed fullpath

  if [ -n "$input" ]; then
    IFS=',' read -ra ignore_items <<< "$input"

    for item in "${ignore_items[@]}"; do
      trimmed="$(echo "$item" | xargs)"

      # Skip empty values.
      if [ -z "$trimmed" ]; then
        continue
      fi

      if [[ "$trimmed" = /* ]]; then
        fullpath="$trimmed"
      else
        fullpath="${DRUPAL_DEPLOY_PATH}/${trimmed}"
      fi

      resolved_paths+=("$fullpath")
    done
  fi

  join_by_comma "${resolved_paths[@]}"
}

IGNORE_PATHS="$(normalise_ignore_paths "$IGNORE_INPUT")"

echo "----------------------------------------------------------------------"
echo ">>> Running coding standard checks in: ${PHPCS_CHECK_DIR}"
echo ">>> Ignoring directories: ${IGNORE_PATHS:-<none>}"
echo "----------------------------------------------------------------------"

if [ ! -x "${PHPCS_PATH}" ]; then
  echo "ERROR: PHPCS executable not found at ${PHPCS_PATH}"
  exit 1
fi

# Configure PHPCS with all required external standards.
"${PHPCS_PATH}" --config-set installed_paths \
"${DRUPAL_DEPLOY_PATH}/vendor/drupal/coder/coder_sniffer,${DRUPAL_DEPLOY_PATH}/vendor/sirbrillig/phpcs-variable-analysis,${DRUPAL_DEPLOY_PATH}/vendor/slevomat/coding-standard"

DRUPAL_EXCLUDE="$(join_by_comma "${DRUPAL_EXCLUDED_SNIFFS[@]}")"
DRUPAL_PRACTICE_EXCLUDE="$(join_by_comma "${DRUPAL_PRACTICE_EXCLUDED_SNIFFS[@]}")"

echo ">>> Running Drupal coding standards checks..."
if [ -n "${IGNORE_PATHS}" ]; then
  "${PHPCS_PATH}" -nq \
    --standard=Drupal \
    --extensions="${PHPCS_EXTENSIONS}" \
    --exclude="${DRUPAL_EXCLUDE}" \
    --ignore="${IGNORE_PATHS}" \
    "${PHPCS_CHECK_DIR}"
else
  "${PHPCS_PATH}" -nq \
    --standard=Drupal \
    --extensions="${PHPCS_EXTENSIONS}" \
    --exclude="${DRUPAL_EXCLUDE}" \
    "${PHPCS_CHECK_DIR}"
fi

echo ">>> Running Drupal best practice checks..."
if [ -n "${IGNORE_PATHS}" ]; then
  "${PHPCS_PATH}" -nq \
    --standard=DrupalPractice \
    --extensions="${PHPCS_EXTENSIONS}" \
    --exclude="${DRUPAL_PRACTICE_EXCLUDE}" \
    --ignore="${IGNORE_PATHS}" \
    "${PHPCS_CHECK_DIR}"
else
  "${PHPCS_PATH}" -nq \
    --standard=DrupalPractice \
    --extensions="${PHPCS_EXTENSIONS}" \
    --exclude="${DRUPAL_PRACTICE_EXCLUDE}" \
    "${PHPCS_CHECK_DIR}"
fi

echo "LGTM ✅"