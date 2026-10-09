#!/usr/bin/env bash
# Post-vote migration only. Candidate packaging belongs to each source repository.
set -euo pipefail
fail() { echo "ERROR: $*" >&2; exit 1; }
version_check() {
  [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Invalid release version: $1"
}
path_check() {
  version_check "$1"
  candidate=${2:-$1}
  [[ $candidate == "$1" || ( ${candidate%/*} == "$1" && ${candidate##*/} =~ ^RC[1-9][0-9]*$ ) ]] || fail "Candidate path must be VERSION or VERSION/RCn"
}
svn_setup() {
  root=https://dist.apache.org/repos/dist
  auth=(--non-interactive --no-auth-cache)
  if [[ -n ${SVN_TEST_ROOT:-} ]]; then
    [[ $SVN_TEST_ROOT == file:///* && $SVN_TEST_ROOT != *'?'* && $SVN_TEST_ROOT != *'#'* ]] || fail "SVN_TEST_ROOT must be an absolute file:// URL"
    root=${SVN_TEST_ROOT%/}
  else
    [[ -n ${SVN_USERNAME:-} && -n ${SVN_PASSWORD:-} ]] || fail "SVN credentials are required"
    auth+=(--username "$SVN_USERNAME" --password "$SVN_PASSWORD")
  fi
  dev=$root/dev/hugegraph
  release=$root/release/hugegraph
}
svn_cmd() { svn "${auth[@]}" "$@"; }
[[ $# == 6 && $1 == promote ]] || fail "Usage: promote VERSION SVN_PATH UPDATE_KEYS DELETE_PKG PRE_VERSION"
shift
version=$1 update_keys=$3 delete_pkg=$4 previous=$5
path_check "$version" "$2"
[[ $update_keys == true || $update_keys == false ]] || fail "UPDATE_KEYS must be true or false"
[[ $delete_pkg == true || $delete_pkg == false ]] || fail "DELETE_PKG must be true or false"
if [[ $delete_pkg == true ]]; then
  version_check "$previous"
  [[ $previous != "$version" ]] || fail "Cannot delete the release being promoted"
fi
svn_setup
revision=$(svn_cmd info --show-item revision "$root")
[[ $(svn_cmd info -r "$revision" --show-item kind "$dev/$candidate") == dir ]] || fail "Candidate must be a directory"
existing=$(svn_cmd list -r "$revision" "$release")
while IFS= read -r entry; do
  [[ $entry != "$version/" ]] || fail "Release target already exists"
done <<< "$existing"
operations=(mv "$dev/$candidate" "$release/$version")
if [[ $update_keys == true ]]; then
  [[ $(svn_cmd info -r "$revision" --show-item kind "$dev/KEYS") == file ]] || fail "dev/KEYS must be a file"
  while IFS= read -r entry; do
    [[ $entry != KEYS ]] || operations+=(rm "$release/KEYS")
  done <<< "$existing"
  operations+=(cp "$revision" "$dev/KEYS" "$release/KEYS")
fi
if [[ $delete_pkg == true ]]; then
  [[ $(svn_cmd info -r "$revision" --show-item kind "$release/$previous") == dir ]] || fail "Previous release must be a directory"
  operations+=(rm "$release/$previous")
fi
# A single repository transaction avoids a partial promotion or KEYS update.
svnmucc "${auth[@]}" -r "$revision" -m "Promote HugeGraph $version after vote" "${operations[@]}"
printf 'Release URL: %s/%s\nCommitted revision: ' "$release" "$version"
svn_cmd info --show-item last-changed-revision "$release/$version"
svn_cmd list "$release/$version"
