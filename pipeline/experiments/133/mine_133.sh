#!/usr/bin/env bash
# #133 deletion-count sweep — mining + validation (no API spend).
#
# Design: --corollary-delete-lemmas-leaves-all N with the SAME seed picks the same
# corollaries per file at every depth; each record deletes N ancestor lemmas from that
# corollary's closure. We mine the ~113 paired-sample files at N in {1,2,3,5}, keep only
# (file, corollary) tuples that (a) appear in the original paired EASY sample, (b) yield
# a record with exactly N deletions at EVERY depth, (c) compile-validate on both sides at
# every depth — so the problem distribution is held fixed across depths by construction,
# not by hope. Depth-1 challenge_ids are compared against the paired sample so the
# 50-turn grid outcomes can be reused for that arm if they match.
set -uo pipefail
ROOT=/home/q/Documents/Work/safeguarded/forall/git-history-evals
SW="$ROOT/scratch-wave3/depth-sweep"
BIN="$ROOT/ablators/lean/.lake/build/bin/ablate"
cd "$ROOT"
DEPTHS="1 2 3 5"

echo "== mine start $(date -Is)"
python3 - <<'PY' > "$SW/files.tsv"
import json, glob, pathlib
root = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals")
seen = set()
for entry in json.load(open(root/"scratch-wave3/paired/easy/manifest.json")):
    repo, src = entry["repo"], entry["src"]
    for line in open(root/f"scratch-wave3/paired/easy/{repo}.jsonl"):
        r = json.loads(line)
        key = (repo, r["file_path"])
        if key in seen:
            continue
        seen.add(key)
        print(f"{repo}\t{src}\t{r['file_path']}")
PY
wc -l "$SW/files.tsv"

export BIN SW
mine_one() {  # args: depth repo src file
  local N="$1" repo="$2" src="$3" file="$4"
  local tag; tag="$(printf '%s' "$repo/$file" | md5sum | cut -c1-12)"
  timeout -k 10 180 "$BIN" --corollary-delete-lemmas-leaves-all "$N" \
      --shrink-solution-minimal --compact --seed 42 -d "$src" "$src/$file" \
      > "$SW/depth$N/_raw/$tag.jsonl" 2>>"$SW/depth$N/mine.err" || true
}
export -f mine_one

for N in $DEPTHS; do
  mkdir -p "$SW/depth$N/_raw"
  awk -F'\t' -v n="$N" '{print n"\t"$0}' "$SW/files.tsv"
done | xargs -d'\n' -P 12 -I{} bash -c 'IFS=$'"'"'\t'"'"' read -r n repo src file <<<"{}"; mine_one "$n" "$repo" "$src" "$file"'
for N in $DEPTHS; do
  cat "$SW/depth$N/_raw"/*.jsonl > "$SW/depth$N/mined.jsonl" 2>/dev/null
  echo "depth$N mined records: $(wc -l < "$SW/depth$N/mined.jsonl")"
done

echo "== select common corollaries $(date -Is)"
python3 "$SW/select_133.py" pre
for N in $DEPTHS; do
  echo "== validate depth$N $(date -Is)"
  for f in "$SW/depth$N"/pre/*.jsonl; do
    repo="$(basename "$f" .jsonl)"
    src="$(awk -F'\t' -v r="$repo" '$1==r{print $2; exit}' "$SW/files.tsv")"
    bash pipeline/par_dryrun.sh "$f" "$src" "$SW/depth$N/dry_$repo.jsonl" 8
    python3 pipeline/keep_good.py "$f" "$SW/depth$N/dry_$repo.jsonl" "$SW/depth$N/good_$repo.jsonl"
  done
done
echo "== final common-set trees $(date -Is)"
python3 "$SW/select_133.py" post
echo "MINE133 DONE $(date -Is)"
