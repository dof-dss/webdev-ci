#!/usr/bin/env bash
# Run only in a disposable CI checkout. Publishes the configured Edge branch.
set -euo pipefail
case "${EDGE_PUBLISH:-false}" in
  true|1) EDGE_PUBLISH=true ;;
  false|0) EDGE_PUBLISH=false ;;
  *) echo 'EDGE_PUBLISH must be true or false' >&2; exit 1 ;;
esac
: "${EDGE_BRANCH:?}" "${SOURCE_BRANCH:?}" "${EDGE_PACKAGES:?}"
[[ "$EDGE_BRANCH" != "$SOURCE_BRANCH" && "$EDGE_BRANCH" != main && "$EDGE_BRANCH" != master ]] || { echo 'Refusing to overwrite a source branch' >&2; exit 1; }
git check-ref-format --branch "$EDGE_BRANCH" >/dev/null
git check-ref-format --branch "$SOURCE_BRANCH" >/dev/null
[[ -z "$(git status --porcelain)" ]] || { echo 'Build requires a clean checkout' >&2; exit 1; }
export GIT_AUTHOR_NAME='Circle CI' GIT_COMMITTER_NAME='Circle CI'
export GIT_AUTHOR_EMAIL='circleci@localhost' GIT_COMMITTER_EMAIL='circleci@localhost'
git fetch origin "$SOURCE_BRANCH"
source_sha=$(git rev-parse FETCH_HEAD)
# Capture the lease before building; never overwrite a concurrent publisher.
remote_sha=$(git ls-remote --heads origin "refs/heads/$EDGE_BRANCH" | awk '{print $1}')
git checkout -B "$EDGE_BRANCH" "$source_sha"
bash "$(dirname "${BASH_SOURCE[0]}")/build-application.sh"
git add -- composer.json composer.lock
# Explicit newline-separated Git pathspecs, never 'git add .'. Include new assets.
while IFS= read -r path; do
  [[ -z "$path" ]] || git add -- "$path"
done <<< "${THEME_ASSET_PATHS:-}"
if ! git diff --cached --quiet; then
  git commit -m 'Build Edge dependencies and assets'
fi
# Keep inspection output outside the checkout so it cannot enter the commit.
artifact_dir=${EDGE_ARTIFACT_DIR:-/tmp/edge-build-artifacts}
mkdir -p "$artifact_dir"
git diff --binary "$source_sha" HEAD > "$artifact_dir/build.patch"
git diff --stat "$source_sha" HEAD > "$artifact_dir/changes.txt"
git show -s --format=fuller HEAD > "$artifact_dir/commit.txt"
printf 'source_branch=%s\nsource_commit=%s\nedge_branch=%s\npublish=%s\n' "$SOURCE_BRANCH" "$source_sha" "$EDGE_BRANCH" "${EDGE_PUBLISH:-false}" > "$artifact_dir/build.txt"
cp composer.json composer.lock "$artifact_dir/"
if [[ "${EDGE_PUBLISH:-false}" == false ]]; then
  echo "Edge build prepared at $(git rev-parse HEAD); publication disabled. Artifacts: $artifact_dir"
  exit 0
fi
# Compare trees as generated commits have different timestamps across runs.
if [[ -n "$remote_sha" ]]; then
  git fetch origin "$EDGE_BRANCH"
  [[ "$(git rev-parse FETCH_HEAD)" == "$remote_sha" ]] || { echo 'Edge changed during build' >&2; exit 1; }
  if git diff --quiet "$remote_sha" HEAD; then
    echo "Edge already contains the resulting tree: $remote_sha"
    exit 0
  fi
fi
git push "--force-with-lease=refs/heads/$EDGE_BRANCH:$remote_sha" origin "HEAD:refs/heads/$EDGE_BRANCH"
echo "Published Edge commit $(git rev-parse HEAD)"
