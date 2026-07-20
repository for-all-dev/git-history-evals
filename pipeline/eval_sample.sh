#!/usr/bin/env bash
# Run the agentic baseline over a per-repo sample. `ablate-baseline` takes ONE src tree per
# invocation, so the sample is grouped by repo and each group runs against its own checkout.
# Usage: eval_sample.sh <eval-dir> <model> [max_turns] [parallel_repos]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E="$1"; MODEL="$2"; TURNS="${3:-30}"; P="${4:-6}"
export E MODEL TURNS ROOT
run_one() {   # $1 = "<repo> <src>"
  local repo src
  repo="${1%% *}"; src="${1##* }"
  ( cd "$ROOT/baselines" && uv run ablate-baseline "$E/$repo.jsonl" "$ROOT/$src" \
      --model "$MODEL" --max-turns "$TURNS" --out "$E/res_$repo.jsonl" ) > "$E/log_$repo.txt" 2>&1
  echo "done $repo ($(grep -c . "$E/res_$repo.jsonl" 2>/dev/null || echo 0) results)"
}
export -f run_one
python3 -c "
import json
for m in json.load(open('$E/manifest.json')): print(m['repo'], m['src'])
" | xargs -d'\n' -P "$P" -I{} bash -c 'run_one "$@"' _ {}
cat "$E"/res_*.jsonl > "$E/results.jsonl" 2>/dev/null
echo "EVAL DONE: $(grep -c . "$E/results.jsonl") results"
