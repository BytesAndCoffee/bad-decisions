#!/usr/bin/env bash
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
registry=$(mktemp -d)
trap 'rm -rf "$registry"' EXIT

install -m 0644 "$here/pack.json" "$registry/example-pack.json"
mkdir -p "$here/dist"
test ! -e "$here/dist/example-pack.carddeck" || {
  echo "remove $here/dist/example-pack.carddeck before rebuilding" >&2
  exit 1
}

BAD_DECISIONS_PACK_DIR="$registry" bad-decisions pack export \
  example-pack "$here/dist/example-pack.carddeck"
bad-decisions pack validate "$here/dist/example-pack.carddeck"
