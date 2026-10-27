#!/usr/bin/env bash
# Runs on the hosted application after guard.sh, against freshly synced data.
set -euo pipefail
[[ "${EDGE_DEPLOY:-standard}" != deploy || "${EDGE_FASTLY:-keep}" != uninstall ]] || {
  echo 'Fastly uninstall requires standard update mode' >&2; exit 1;
}
app_root="${PLATFORM_APP_DIR:-/app}"
cd "$app_root"
drupal_root="$app_root/web"
drush_bin="$app_root/vendor/bin/drush"
[[ -x "$drush_bin" ]] || { echo 'Project Drush is unavailable' >&2; exit 1; }
sites=()
case "${EDGE_LAYOUT:?}" in
  multisite)
    if [[ -n "${EDGE_SITES:-}" ]]; then
      read -r -a sites <<< "$EDGE_SITES"
    else
      for settings in "$app_root"/project/sites/*/settings.php; do
        [[ -f "$settings" ]] || continue
        site=$(basename "$(dirname "$settings")")
        [[ "$site" == default ]] || sites+=("$site")
      done
    fi
    ;;
  single) sites=("${EDGE_URI:-default}") ;;
  *) echo 'Unknown site layout' >&2; exit 1 ;;
esac
[[ ${#sites[@]} -gt 0 ]] || { echo 'No sites discovered' >&2; exit 1; }
run_drush() {
  local output status=0
  output=$("$drush_bin" --root="$drupal_root" --uri="$site" --no-ansi -y "$@" 2>&1) || status=$?
  printf '%s\n' "$output"
  # Some Origins QA commands log errors but return exit status zero.
  [[ $status == 0 ]] && ! grep -qiE '\[error\]|\[fatal\]' <<< "$output"
}
for site in "${sites[@]}"; do
  if [[ "$EDGE_LAYOUT" == multisite ]]; then
    [[ "$site" =~ ^[a-zA-Z0-9_-]+$ && -f "$app_root/project/sites/$site/settings.php" ]] || {
      echo "Missing or invalid expected site: $site" >&2; exit 1;
    }
  fi
  echo "Finalising $site"
  # php:eval requires a full Drupal bootstrap; no translated status-text parsing.
  run_drush php:eval 'if (!\Drupal::hasContainer()) { throw new \RuntimeException("Bootstrap failed"); }'
  if [[ "${EDGE_PREFLIGHT:-none}" == required ]]; then
    run_drush upgrade-preflight
  fi
  if [[ "${EDGE_DEPLOY:-standard}" == deploy ]]; then
    # Preserve DEPT's complete Drush deploy sequence including deploy hooks.
    run_drush deploy
  else
    run_drush updatedb
    if [[ "${EDGE_FASTLY:-keep}" == uninstall ]]; then
      enabled=$(run_drush php:eval 'echo \Drupal::moduleHandler()->moduleExists("fastly") ? "yes" : "no";')
      [[ "$enabled" != yes ]] || run_drush pm:uninstall fastly
    fi
    run_drush cache:rebuild
    run_drush config:import
  fi
  case "${EDGE_CONTENT:-none}" in
    unity) run_drush import-all-if-installed safe ;;
    structure-sync)
      enabled=$(run_drush php:eval 'echo \Drupal::moduleHandler()->moduleExists("structure_sync") ? "yes" : "no";')
      if [[ "$enabled" == yes ]]; then
        run_drush import-blocks --choice=safe
        run_drush import-taxonomies --choice=safe
      fi
      ;;
    none) ;;
    *) echo 'Unknown content import mode' >&2; exit 1 ;;
  esac
  # Fail before logging account details if the required secret is absent.
  [[ -n "${QA_PASSWORD:-}" ]] || { echo 'QA_PASSWORD is required' >&2; exit 1; }
  run_drush bulk_update_qa_accounts enable
  run_drush password_qa_accounts
  [[ "${EDGE_QA_STAFF:-false}" != true ]] || run_drush create_qa_staff_accounts
  [[ -z "${EDGE_AFTER_COMMAND:-}" ]] || run_drush "$EDGE_AFTER_COMMAND"
  run_drush cache:rebuild
  echo "Completed $site"
done
