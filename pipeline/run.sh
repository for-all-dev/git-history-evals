#!/usr/bin/env bash
# The ablation pipeline. One entry point, one stage at a time (or `all`).
#
#   run.sh mine     <scratch> [leaves|whole]   ablate every repo (syntactic — no build needed)
#   run.sh validate <scratch> [leaves|whole]   compile challenge + ground truth; write artifacts
#   run.sh index    <scratch>                  regenerate artifacts/.../_index.json
#   run.sh eval     <scratch> <n> [model] [--mode leaves|whole]
#                                              sample n from the given split (default leaves),
#                                              run the solver, train + test the difficulty model
#                                              on a disjoint second sample
#   run.sh all      <scratch>                  mine -> validate -> index -> eval (both modes)
#
# Repos come from `repos.tsv` (name, language, url, revision, path, toolchain) — the single
# source of truth. `clone_repos.sh` materialises them; they are gitignored, not vendored.
#
# Run inside `nix develop` (wrapped cc, elan/lake, ablate-baseline). See README.md — in
# particular "Validation is a build problem", which is where the corpus is won or lost.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODEL_DEFAULT=mistral:labs-leanstral-1-5
TURNS=${TURNS:-50}
JOBS=${JOBS:-24}

say() { echo "[$(date +%H:%M:%S)] $*"; }

# name <TAB> path <TAB> mine_dir.
#   path     = what the VALIDATOR gets. It must contain everything the lakefile can reach:
#              symcrust's `require aeneas from "../../../aeneas"` escapes its own project dir, so
#              the validator gets the workspace (SymCrypt + its pinned aeneas), not just the project.
#   mine_dir = what the MINER walks — the repo's own sources, so we do not ablate a vendored dep.
repos() { tail -n +2 pipeline/repos.tsv | awk -F'\t' '$2=="lean" {print $1"\t"$6"\t"$7}'; }

mode_flags() {  # $1 = leaves|whole  ->  sets ABLATE_MODE, ARTIFACT_DIR
  if [ "$1" = leaves ]; then
    export ABLATE_MODE=--corollary-delete-lemmas-leaves-all; ARTIFACT_DIR=lean-ablate
  else
    export ABLATE_MODE=--corollary-delete-lemmas-all;        ARTIFACT_DIR=lean-ablate-whole
  fi
}

stage_mine() {
  local S="$1" mode="$2"; mode_flags "$mode"
  local M="$S/mined_$mode"; mkdir -p "$M"
  say "mining mode=$mode ($ABLATE_MODE)"
  while IFS=$'\t' read -r name src mine; do
    [ -z "$name" ] && continue
    [ -s "$M/$name.jsonl" ] && continue
    # walk mine_dir, but strip `src` so file_path is relative to what the validator will get
    bash pipeline/mine_repo_mode.sh "${mine:-$src}" "$src" "$M/$name.jsonl" 42 90 "$JOBS"
  done < <(repos)
  say "mode=$mode: $(cat "$M"/*.jsonl 2>/dev/null | grep -c . || echo 0) records"
}

stage_validate() {
  local S="$1" mode="$2"; mode_flags "$mode"
  local M="$S/mined_$mode" D="$S/dry_$mode"; mkdir -p "$D"
  say "validating mode=$mode"
  while IFS=$'\t' read -r name src mine; do
    [ -z "$name" ] && continue
    [ -s "$M/$name.jsonl" ] || continue
    [ -s "$D/$name.jsonl" ] && continue
    bash pipeline/par_dryrun.sh "$M/$name.jsonl" "$src" "$D/$name.jsonl" "$JOBS"
    python3 pipeline/keep_good.py "$M/$name.jsonl" "$D/$name.jsonl" "$D/$name.good.jsonl"
    python3 pipeline/finalize_mode.py "$name" "$M/$name.jsonl" "$D/$name.jsonl" \
        "$D/$name.good.jsonl" "$src" "$ARTIFACT_DIR" "$ABLATE_MODE"
  done < <(repos)
  say "mode=$mode validated"
}

# Sample -> solve -> train, then a DISJOINT sample -> score -> solve -> measure. The second
# sample's predictions are locked in BEFORE its outcomes exist, and it is deduplicated against
# the first by challenge TEXT (not challenge_id — an earlier ablator shipped byte-identical
# challenges under different ids, which leaked problems across the split).
stage_eval() {
  local S="$1" n="${2:-100}" model="${3:-$MODEL_DEFAULT}" mode="${4:-leaves}"
  # leaves is the pre-#125 default, so it keeps the original evalA/evalB dir names for
  # backwards compatibility; whole (and any future split) gets its own namespace.
  local sub=""; [ "$mode" != leaves ] && sub="_$mode"
  local A="$S/evalA$sub" B="$S/evalB$sub" model_out="$S/model$sub.joblib"

  say "sample A (n=$n, mode=$mode) -> $model, max-turns $TURNS"
  python3 pipeline/sample_disjoint.py "$A" "$n" --seed 42 --mode "$mode"
  bash pipeline/eval_sample.sh "$A" "$model" "$TURNS" 8 --mode "$mode"
  cat "$A"/res_*.jsonl > "$A/results.jsonl" 2>/dev/null

  say "training the difficulty model on sample A"
  ( cd baselines && uv run difficulty build-table "$A/sample.jsonl" "$A/results.jsonl" \
      --out-jsonl "$A/table.jsonl" )
  ( cd baselines && uv run difficulty train "$A/table.jsonl" --out "$model_out" )

  say "sample B (n=$n, disjoint, mode=$mode) -> score FIRST, then solve"
  python3 pipeline/sample_disjoint.py "$B" "$n" --exclude "$A/sample.jsonl" --seed 43 --mode "$mode"
  ( cd baselines && uv run difficulty score "$B/sample.jsonl" --model "$model_out" \
      --out-jsonl "$B/scored.jsonl" )
  bash pipeline/eval_sample.sh "$B" "$model" "$TURNS" 8 --mode "$mode"
  cat "$B"/res_*.jsonl > "$B/results.jsonl" 2>/dev/null

  say "held-out discrimination (ROC-AUC) + calibration (Brier)"
  python3 pipeline/score_predictions.py "$B/scored.jsonl" "$B/results.jsonl" \
    | tee "$B/metrics.txt"
}

cmd="${1:-}"; S="${2:-}"
[ -z "$cmd" ] || [ -z "$S" ] && { sed -n '2,12p' "$0"; exit 2; }
mkdir -p "$S"

# Pull a `--mode <leaves|whole>` flag out of the remaining args wherever it appears (used by
# `eval`); everything else keeps its original positional meaning.
MODE=leaves
rest=("${@:3}")
filtered=()
i=0
while [ $i -lt ${#rest[@]} ]; do
  if [ "${rest[$i]}" = "--mode" ]; then
    MODE="${rest[$((i + 1))]:?--mode requires a value}"
    i=$((i + 2))
  else
    filtered+=("${rest[$i]}")
    i=$((i + 1))
  fi
done
set -- "$cmd" "$S" "${filtered[@]}"

case "$cmd" in
  mine)     stage_mine "$S" "${3:-leaves}" ;;
  validate) stage_validate "$S" "${3:-leaves}" ;;
  index)    python3 pipeline/write_index.py ;;
  eval)     stage_eval "$S" "${3:-100}" "${4:-$MODEL_DEFAULT}" "$MODE" ;;
  all)
    for mode in leaves whole; do
      stage_mine "$S" "$mode"
      stage_validate "$S" "$mode"
    done
    python3 pipeline/write_index.py
    for mode in leaves whole; do
      stage_eval "$S" "${3:-100}" "${4:-$MODEL_DEFAULT}" "$mode"
    done
    ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
say "done: $cmd"
