#!/usr/bin/env bash
# Mine l4v (Isabelle) with the Rust ablator: one ablation per eligible corollary, deleting a
# SINGLE lemma from that corollary's in-file dependency closure (leaf-level holing), solution
# sliced to the holes' minimal closure. Per-file timeout: l4v has some enormous theories.
# Usage: mine_l4v.sh <out.jsonl> [seed] [timeout_s] [jobs]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/ablators/isabelle/rust/target/release/ablate"
SRC="$ROOT/data/isabelle/l4v"
OUT="$1"; SEED="${2:-42}"; TMO="${3:-120}"; JOBS="${4:-16}"
WORK="$(dirname "$OUT")/_mine_l4v"; rm -rf "$WORK"; mkdir -p "$WORK"
export BIN SRC SEED TMO WORK
mine_one() {
  local F="$1" tag tmp rc
  tag="$(echo "$F" | md5sum | cut -c1-16)"
  tmp="$WORK/$tag.jsonl"
  timeout -k 10 "$TMO" "$BIN" --corollary-delete-lemmas-leaves-all=1 --shrink-solution-minimal \
      --compact --seed "$SEED" -d "$SRC" "$F" > "$tmp" 2>/dev/null
  rc=$?
  if [ $rc -eq 124 ]; then echo "TIMEOUT $F" >> "$WORK/skipped"; rm -f "$tmp"
  elif [ $rc -ne 0 ]; then echo "ERROR(rc=$rc) $F" >> "$WORK/skipped"; rm -f "$tmp"
  elif [ ! -s "$tmp" ]; then rm -f "$tmp"; fi
}
export -f mine_one
: > "$WORK/skipped"
find "$SRC" -name '*.thy' -not -path '*/.git/*' -print0 \
  | xargs -0 -P "$JOBS" -I{} bash -c 'mine_one "$@"' _ {}
cat "$WORK"/*.jsonl > "$OUT" 2>/dev/null
cp "$WORK/skipped" "$OUT.skipped" 2>/dev/null
echo "records=$(wc -l < "$OUT")  skipped=$(wc -l < "$OUT.skipped")  theories=$(find "$SRC" -name '*.thy' -not -path '*/.git/*' | wc -l)"
