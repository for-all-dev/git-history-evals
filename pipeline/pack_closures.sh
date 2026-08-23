#!/usr/bin/env bash
# Pack a repo's built .lake closure into a zstd tarball and publish it to the DO Space,
# so a third party can score challenges against that repo without an hours-long build.
# Complements upload_ablations.sh (which ships JSONL only) -- see issue #143.
#
# The archive is the WHOLE checkout directory named in repos.tsv's `checkout` column, not
# just `.lake` -- some repos have several lake roots nested under one checkout (leanda:
# Ben-Or/epfd/twophase; symcrust: SymCrypt/SymCRust/lean), so packing the checkout root is
# the only way to capture every one of them in a single archive.
#
#   usage: pack_closures.sh <repo-name> [<repo-name> ...]
#     env: BUCKET_ACCESS_KEY, BUCKET_SECRET_KEY  (required unless NO_UPLOAD=1; never
#                                                  hardcode -- see upload_ablations.sh)
#          ZSTD_LEVEL=N    compression level (default 9)
#          ZSTD_THREADS=N  zstd worker threads (default 4 -- keep this <=4 so a pack run
#                           does not starve other builds/evals sharing the machine; tar
#                           itself is single-threaded, only zstd is parallelised)
#          NO_UPLOAD=1     build + hash + record the row locally, skip the s3cmd put
#                           (the packed .tar.zst is kept under the mktemp workdir printed
#                           in the log, for local inspection / manual upload)
#          PIPELINE_DATA_ROOT=<dir>  operate against repos.tsv/data/lean/closures.tsv
#                           under <dir> instead of this script's own checkout -- for
#                           when the corpus lives in a separate checkout (e.g. this
#                           script's checkout is a git worktree and the built corpus
#                           only exists in the main working tree)
#
# Publishes to s3://forall-evals/ablations/lean/closures/<name>-<revision-short>.tar.zst
# (PRIVATE by default, same as upload_ablations.sh) and records
# name/revision/sha256/sizes as a row in pipeline/closures.tsv (re-running for a repo
# replaces its existing row rather than duplicating it; object_key stays relative to
# the ablations/ prefix, so rows written before the bucket consolidation still
# resolve). See fetch_closure.sh to download + verify what this uploads.
set -uo pipefail
ROOT="${PIPELINE_DATA_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT" || exit 1

if [ "${NO_UPLOAD:-0}" != "1" ]; then
  : "${BUCKET_ACCESS_KEY:?set BUCKET_ACCESS_KEY}"
  : "${BUCKET_SECRET_KEY:?set BUCKET_SECRET_KEY}"
fi
[ $# -ge 1 ] || { echo "usage: pack_closures.sh <repo-name> [<repo-name> ...]" >&2; exit 1; }

# Bucket prefix holding the closures. `object_key` in closures.tsv is relative to
# this, so historical rows keep working across the bucket consolidation.
SPACE="${CLOSURE_SPACE:-s3://forall-evals/ablations}"
ZSTD_LEVEL="${ZSTD_LEVEL:-9}"
ZSTD_THREADS="${ZSTD_THREADS:-4}"
CLOSURES_TSV=pipeline/closures.tsv
[ -f "$CLOSURES_TSV" ] || printf 'name\trevision\trevision_short\tsha256\tcompressed_bytes\tuncompressed_bytes\tobject_key\n' > "$CLOSURES_TSV"

CFG=""
if [ "${NO_UPLOAD:-0}" != "1" ]; then
  CFG="$(mktemp)"
  umask 077; cat > "$CFG" <<CFGEOF
[default]
access_key = $BUCKET_ACCESS_KEY
secret_key = $BUCKET_SECRET_KEY
host_base = nyc3.digitaloceanspaces.com
host_bucket = %(bucket)s.nyc3.digitaloceanspaces.com
use_https = True
CFGEOF
fi
cleanup() { [ -n "$CFG" ] && rm -f "$CFG"; }
trap cleanup EXIT

pack_one() {
  local name="$1" row rname rlang rurl rev rcheckout rpath rminedir rtoolchain
  local checkout rev_short obj key work tarfile outfile rc uncompressed compressed sha tmp

  row=$(awk -F'\t' -v n="$name" 'NR>1 && $1==n{print; exit}' pipeline/repos.tsv)
  [ -n "$row" ] || { echo "!! no such repo in repos.tsv: $name" >&2; return 1; }
  IFS=$'\t' read -r rname rlang rurl rev rcheckout rpath rminedir rtoolchain <<<"$row"
  checkout="$rcheckout"
  [ -d "$checkout" ] || { echo "!! checkout not found: $checkout (run clone_repos.sh + build first)" >&2; return 1; }
  find "$checkout" -maxdepth 6 -type d -path '*/.lake/build' 2>/dev/null | grep -q . \
    || { echo "!! no .lake/build under $checkout -- repo does not look built" >&2; return 1; }

  rev_short="${rev:0:12}"
  obj="$name-$rev_short.tar.zst"
  key="lean/closures/$obj"

  work="$(mktemp -d)"
  tarfile="$work/$name.tar"
  outfile="$work/$obj"

  echo "== tar $checkout -> $tarfile"
  tar -cf "$tarfile" -C "$(dirname "$checkout")" "$(basename "$checkout")"
  rc=$?
  # rc==1 means "some files changed as we read them" -- benign, since other evals are
  # running reads/writes against data/lean concurrently; anything higher is a real failure
  if [ "$rc" -gt 1 ]; then
    echo "!! tar failed ($rc) for $name" >&2
    rm -rf "$work"
    return 1
  fi

  uncompressed=$(stat -c%s "$tarfile")
  echo "== zstd -T$ZSTD_THREADS -$ZSTD_LEVEL -> $outfile"
  zstd -T"$ZSTD_THREADS" -"$ZSTD_LEVEL" -q -f -o "$outfile" "$tarfile"
  rm -f "$tarfile"

  compressed=$(stat -c%s "$outfile")
  sha=$(sha256sum "$outfile" | cut -d' ' -f1)

  if [ "${NO_UPLOAD:-0}" != "1" ]; then
    echo "== upload -> $SPACE/$key"
    s3cmd -c "$CFG" --no-progress put "$outfile" "$SPACE/$key" || {
      echo "!! upload failed for $name" >&2
      rm -rf "$work"
      return 1
    }
    rm -rf "$work"
  else
    echo "== NO_UPLOAD=1, skipping upload ($outfile kept for inspection)"
  fi

  # replace any existing row for this name, then append the fresh one (idempotent re-run)
  tmp="$CLOSURES_TSV.tmp.$$"
  awk -F'\t' -v n="$name" 'NR==1 || $1!=n' "$CLOSURES_TSV" > "$tmp" && mv "$tmp" "$CLOSURES_TSV"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$rev" "$rev_short" "$sha" "$compressed" "$uncompressed" "$key" >> "$CLOSURES_TSV"

  echo "$name @ $rev_short  sha256=$sha  compressed=$(numfmt --to=iec "$compressed" 2>/dev/null || echo "${compressed}B")  uncompressed=$(numfmt --to=iec "$uncompressed" 2>/dev/null || echo "${uncompressed}B")"
}

status=0
for name in "$@"; do
  pack_one "$name" || status=1
done
echo "PACK DONE"
exit $status
