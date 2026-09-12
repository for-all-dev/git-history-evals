#!/usr/bin/env bash
# Download + unpack a prebuilt .lake closure published by pack_closures.sh, so a third
# party can score challenges against that repo without an hours-long build. See issue
# #143 and pipeline/closures.tsv (the manifest this reads: name, revision, sha256,
# sizes, object_key).
#
#   usage: fetch_closure.sh <repo-name> [<dest-dir>]
#     dest-dir defaults to data/lean/<repo-name>. That matches repos.tsv's `checkout`
#     column for every repo in the published demo subset, but is NOT correct for the
#     handful of corpus repos whose checkout is nested one level deeper (e.g. symcrust
#     -> data/lean/symcrust/SymCrypt) -- none of those are in the demo subset, so pass
#     an explicit dest-dir for those rather than relying on the default.
#     env: BUCKET_ACCESS_KEY, BUCKET_SECRET_KEY  (required -- the Space is private)
set -euo pipefail
cd "$(dirname "$0")/.."

: "${BUCKET_ACCESS_KEY:?set BUCKET_ACCESS_KEY}"
: "${BUCKET_SECRET_KEY:?set BUCKET_SECRET_KEY}"
NAME="${1:?usage: fetch_closure.sh <repo-name> [<dest-dir>]}"
DEST="${2:-data/lean/$NAME}"
CLOSURES_TSV=pipeline/closures.tsv
# Bucket prefix holding the closures. `object_key` in closures.tsv is relative to
# this, so historical rows keep working across the bucket consolidation.
SPACE="${CLOSURE_SPACE:-s3://forall-evals/ablations}"

[ -f "$CLOSURES_TSV" ] || { echo "!! $CLOSURES_TSV not found" >&2; exit 1; }
ROW=$(awk -F'\t' -v n="$NAME" 'NR>1 && $1==n{print; exit}' "$CLOSURES_TSV")
[ -n "$ROW" ] || { echo "!! no closure recorded for '$NAME' in $CLOSURES_TSV" >&2; exit 1; }
IFS=$'\t' read -r name revision revision_short sha256 compressed_bytes uncompressed_bytes object_key <<<"$ROW"

[ -e "$DEST" ] && { echo "!! $DEST already exists -- refusing to overwrite" >&2; exit 1; }

WORK="$(mktemp -d)"
CFG="$WORK/s3cfg"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
umask 077; cat > "$CFG" <<CFGEOF
[default]
access_key = $BUCKET_ACCESS_KEY
secret_key = $BUCKET_SECRET_KEY
host_base = nyc3.digitaloceanspaces.com
host_bucket = %(bucket)s.nyc3.digitaloceanspaces.com
use_https = True
CFGEOF

TARBALL="$WORK/$(basename "$object_key")"
echo "== fetch $SPACE/$object_key -> $TARBALL"
s3cmd -c "$CFG" --no-progress get "$SPACE/$object_key" "$TARBALL"

echo "== verify sha256"
GOT=$(sha256sum "$TARBALL" | cut -d' ' -f1)
[ "$GOT" = "$sha256" ] || { echo "!! sha256 mismatch: expected $sha256 got $GOT" >&2; exit 1; }

EXTRACT="$WORK/extract"; mkdir -p "$EXTRACT"
echo "== extract -> $EXTRACT"
tar -I zstd -xf "$TARBALL" -C "$EXTRACT"

TOPDIRS=()
while IFS= read -r d; do TOPDIRS+=("$d"); done < <(find "$EXTRACT" -mindepth 1 -maxdepth 1 -type d)
[ "${#TOPDIRS[@]}" -eq 1 ] || { echo "!! archive did not contain exactly one top-level directory" >&2; exit 1; }

mkdir -p "$(dirname "$DEST")"
mv "${TOPDIRS[0]}" "$DEST"

echo "$name @ $revision_short  ->  $DEST  ($(du -sh "$DEST" | cut -f1))"
echo "FETCH DONE"
