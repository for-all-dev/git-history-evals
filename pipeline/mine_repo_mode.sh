#!/usr/bin/env bash
# Mine one Lean repo, one ablator process per file, run in parallel across files
# (each file's records are deterministic given the seed). A per-file timeout skips
# pathological huge files (logged to <out>.skipped so nothing is silently dropped).
#
# Usage: mine_repo.sh <src_dir> <strip_prefix> <out.jsonl> [seed] [per_file_timeout_s] [jobs]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/ablators/lean/.lake/build/bin/ablate"
SRC="$1"; STRIP="$2"; OUT="$3"; SEED="${4:-42}"; TMO="${5:-90}"; JOBS="${6:-24}"
WORK="$(dirname "$OUT")/_mine_$(basename "$OUT" .jsonl)"
rm -rf "$WORK"; mkdir -p "$WORK"
export BIN STRIP SEED TMO WORK ABLATE_MODE
mine_one() {
  local F="$1"
  local tag; tag="$(echo "$F" | md5sum | cut -c1-16)"
  local tmp="$WORK/$tag.jsonl"
  timeout -k 10 "$TMO" "$BIN" ${ABLATE_MODE:---corollary-delete-lemmas-all} --shrink-solution-minimal \
      --compact --seed "$SEED" -d "$STRIP" "$F" > "$tmp" 2>/dev/null
  local rc=$?
  if [ $rc -eq 124 ]; then echo "TIMEOUT $F" >> "$WORK/skipped"; rm -f "$tmp"
  elif [ $rc -ne 0 ]; then echo "ERROR(rc=$rc) $F" >> "$WORK/skipped"; rm -f "$tmp"
  elif [ ! -s "$tmp" ]; then rm -f "$tmp"; fi
}
export -f mine_one
: > "$WORK/skipped"
# -print0/-0: paths may contain quotes/spaces (e.g. loom's `NonDetT'` dir), which
# break default xargs word-splitting and silently abort after the first file.
find "$SRC" -name '*.lean' -not -path '*/.lake/*' -print0 \
  | xargs -0 -P "$JOBS" -I{} bash -c 'mine_one "$@"' _ {}
cat "$WORK"/*.jsonl > "$OUT" 2>/dev/null
cp "$WORK/skipped" "$OUT.skipped" 2>/dev/null
echo "records=$(wc -l < "$OUT")  skipped=$(wc -l < "$OUT.skipped" 2>/dev/null || echo 0)  files=$(find "$SRC" -name '*.lean' -not -path '*/.lake/*' | wc -l)"
