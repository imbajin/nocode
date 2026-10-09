#!/usr/bin/env bash
# Prepare signed candidates, upload to dev, or promote after a successful vote.
# Credentials belong in SVN_USERNAME/SVN_PASSWORD; never enable shell tracing.
set +x
set -euo pipefail
export LC_ALL=C
shopt -s nullglob

fail() { echo "ERROR: $*" >&2; exit 1; }
version_check() {
  [[ $1 =~ ^[0-9]+(\.[0-9]+){1,3}(-[A-Za-z0-9][A-Za-z0-9.-]*)?$ && $1 != *SNAPSHOT* ]] || fail "Invalid release version: $1"
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
verify_packages() {
  local directory=$1 package name file status expected checksum digest
  if [[ -n ${GPG_KEY_ID:-} ]]; then
    [[ $GPG_KEY_ID =~ ^[A-Fa-f0-9]{40}$ || $GPG_KEY_ID =~ ^[A-Fa-f0-9]{64}$ ]] || fail "GPG_KEY_ID must be a full fingerprint"
    expected=$(gpg --batch --with-colons --fingerprint "$GPG_KEY_ID" | awk -F: '$1 == "fpr" { print $10; exit }')
    [[ -n $expected ]] || fail "Signing key is unavailable"
  fi
  packages=("$directory"/*.tar.gz)
  ((${#packages[@]} > 0)) || fail "No tar.gz packages found"
  files=()
  for package in "${packages[@]}"; do
    name=${package##*/}
    [[ $name == "apache-hugegraph-$version-src.tar.gz" || $name == "apache-hugegraph-$version.tar.gz" ||
       $name == "apache-hugegraph-toolchain-$version-src.tar.gz" || $name == "apache-hugegraph-toolchain-$version.tar.gz" ||
       $name == "apache-hugegraph-computer-$version-src.tar.gz" || $name == "apache-hugegraph-ai-$version-src.tar.gz" ]] || fail "Unexpected package: $name"
    for file in "$package" "$package.asc" "$package.sha512"; do
      [[ -f $file && ! -L $file ]] || fail "Missing file or symbolic link: $file"
      files+=("${file##*/}")
    done
    status=$(gpg --batch --status-fd 1 --verify "$package.asc" "$package")
    if [[ -n ${GPG_KEY_ID:-} ]]; then
      awk -v fingerprint="$expected" '$2 == "VALIDSIG" && ($3 == fingerprint || $NF == fingerprint) { valid=1 } END { exit !valid }' <<< "$status" || fail "Signature uses a different key: $name"
    fi
    checksum=$(cat "$package.sha512")
    digest=${checksum%% *}
    [[ $digest =~ ^[a-fA-F0-9]{128}$ && $checksum == "$digest  $name" ]] || fail "Unexpected checksum entry: $name"
    (cd "$directory" && shasum -a 512 --check "$name.sha512")
  done
  local entries=("$directory"/* "$directory"/.[!.]* "$directory"/..?*)
  ((${#entries[@]} == ${#files[@]})) || fail "Candidate output contains unexpected files"
}
work=
trap 'if [[ -n $work ]]; then rm -rf -- "$work"; fi' EXIT
command=${1:-}
[[ $# -gt 0 ]] && shift
case "$command" in
  validate-path)
    [[ $# == 2 ]] || fail "Usage: validate-path VERSION SVN_PATH"
    path_check "$1" "$2"
    echo "$candidate"
    ;;
  prepare)
    [[ $# == 5 ]] || fail "Usage: prepare COMPONENT VERSION SVN_PATH SOURCE_DIR OUTPUT_DIR"
    component=$1 version=$2 source=$4 output=$5
    path_check "$version" "$3"
    case "$component" in
      server) project=hugegraph ;;
      toolchain|computer|ai) project=hugegraph-$component ;;
      *) fail "Unknown component: $component" ;;
    esac
    source=$(cd "$source" && pwd)
    sha=$(git -C "$source" rev-parse --verify 'HEAD^{commit}')
    [[ -z $(git -C "$source" status --porcelain --untracked-files=no) ]] || fail "Source checkout has tracked changes"
    mkdir -p "$output"
    output=$(cd "$output" && pwd)
    entries=("$output"/* "$output"/.[!.]* "$output"/..?*)
    ((${#entries[@]} == 0)) || fail "Output directory must be empty"
    prefix=apache-$project-$version
    git -C "$source" archive --format=tar.gz --prefix="$prefix-src/" --output="$output/$prefix-src.tar.gz" "$sha"
    if [[ $component == server || $component == toolchain ]]; then
      [[ -f $source/target/$prefix.tar.gz && ! -L $source/target/$prefix.tar.gz ]] || fail "Missing binary package: target/$prefix.tar.gz"
      cp "$source/target/$prefix.tar.gz" "$output/"
    fi
    signing=(--batch --armor --detach-sign)
    [[ -z ${GPG_KEY_ID:-} ]] || signing+=(--local-user "$GPG_KEY_ID")
    for package in "$output"/*.tar.gz; do
      gpg "${signing[@]}" --output "$package.asc" "$package"
      (cd "$output" && shasum -a 512 "${package##*/}" > "${package##*/}.sha512")
    done
    verify_packages "$output"
    printf 'Source SHA: %s\nCandidate: %s\n' "$sha" "$candidate"
    printf '%s\n' "${files[@]}"
    ;;
  upload)
    [[ $# == 3 ]] || fail "Usage: upload VERSION SVN_PATH OUTPUT_DIR"
    version=$1 output=$3
    path_check "$version" "$2"
    output=$(cd "$output" && pwd)
    verify_packages "$output"
    svn_setup
    work=$(mktemp -d)
    # Sparse checkout discovers existing candidates without downloading old packages.
    svn_cmd checkout --depth immediates "$dev" "$work/wc"
    if [[ -d $work/wc/$version ]]; then
      svn_cmd update --set-depth immediates "$work/wc/$version"
    fi
    if [[ $candidate != "$version" && -d $work/wc/$candidate ]]; then
      svn_cmd update --set-depth immediates "$work/wc/$candidate"
    fi
    mkdir -p "$work/wc/$candidate"
    for file in "${files[@]}"; do
      [[ ! -e $work/wc/$candidate/$file ]] || fail "Refusing to overwrite existing candidate: $file"
      cp "$output/$file" "$work/wc/$candidate/$file"
      svn_cmd add --parents "$work/wc/$candidate/$file"
    done
    svn_cmd commit "$work/wc" -m "Add HugeGraph $version candidate $candidate"
    printf 'Candidate URL: %s/%s\nCommitted revision: ' "$dev" "$candidate"
    svn_cmd info --show-item last-changed-revision "$dev/$candidate"
    printf '%s\n' "${files[@]}"
    ;;
  promote)
    [[ $# == 5 ]] || fail "Usage: promote VERSION SVN_PATH UPDATE_KEYS DELETE_PKG PRE_VERSION"
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
    ;;
  *) fail "Expected prepare, upload, promote, or validate-path" ;;
esac
