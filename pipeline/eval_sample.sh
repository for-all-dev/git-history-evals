#!/usr/bin/env bash
# Run the agentic baseline over a per-repo sample. `ablate-baseline` takes ONE src tree per
# invocation, so the sample is grouped by repo and each group runs against its own checkout.
# Usage: eval_sample.sh <eval-dir> <model> [max_turns] [parallel_repos] [--mode {leaves,whole}]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

E_RAW="$1"; MODEL="$2"; TURNS="${3:-30}"; P="${4:-6}"
MODE=leaves
if [ "${5:-}" = "--mode" ]; then
  MODE="${6:?--mode requires a value}"
fi

# We `cd` into baselines/ below, so a relative $E would resolve against the wrong directory and
# silently break. Resolve it to an absolute path up front, or fail loudly.
case "$E_RAW" in
  /*) E="$E_RAW" ;;
  *)
    if [ -d "$E_RAW" ]; then
      E="$(cd "$E_RAW" && pwd)"
    else
      echo "error: eval-dir '$E_RAW' is a relative path that does not exist yet" \
           "(sample_disjoint.py must create it first, or pass an absolute path)" >&2
      exit 2
    fi
    ;;
esac

export E MODEL TURNS ROOT MODE
run_one() {   # $1 = "<repo> <src>"
  local repo src
  repo="${1%% *}"; src="${1##* }"
  ( cd "$ROOT/baselines" && uv run ablate-baseline "$E/$repo.jsonl" "$ROOT/$src" \
      --model "$MODEL" --max-turns "$TURNS" --out "$E/res_$repo.jsonl" ) > "$E/log_$repo.txt" 2>&1
  # tag each result row with the ablation mode, so downstream results are self-describing
  if [ -s "$E/res_$repo.jsonl" ]; then
    python3 -c "
import json
rows = [json.loads(l) for l in open('$E/res_$repo.jsonl') if l.strip()]
for r in rows:
    r['sample_mode'] = '$MODE'
with open('$E/res_$repo.jsonl', 'w') as f:
    for r in rows:
        f.write(json.dumps(r) + '\n')
"
  fi
  echo "done $repo ($(grep -c . "$E/res_$repo.jsonl" 2>/dev/null || echo 0) results)"
}
export -f run_one
python3 -c "
import json
for m in json.load(open('$E/manifest.json')): print(m['repo'], m['src'])
" | xargs -d'\n' -P "$P" -I{} bash -c 'run_one "$@"' _ {}
cat "$E"/res_*.jsonl > "$E/results.jsonl" 2>/dev/null
echo "EVAL DONE: $(grep -c . "$E/results.jsonl") results (mode=$MODE)"
