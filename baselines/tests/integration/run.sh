#!/usr/bin/env bash
# End-to-end integration: for each prover, run the REAL ablator on a synthetic
# project to emit a JSONL, then `apply-ablate --prepare --full-check` it and assert
# the holed challenge builds (holes allowed) and the recovered solution builds clean.
#
# Opt-in (needs the prover toolchains via nix). Run from anywhere:
#     tests/integration/run.sh            # all provers
#     tests/integration/run.sh lean       # one prover
#
# Each prover runs inside its ablator's nix dev shell (which provides both the
# toolchain to build/run the ablator and the prover binary: coqc / lake / isabelle).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
HARNESS="$REPO/baselines"
WORK="$HERE/_work"
FIX="$HERE/fixtures"
rm -rf "$WORK"; mkdir -p "$WORK"

PROVERS=("${@:-coq isabelle lean}")
# shellcheck disable=SC2206
PROVERS=(${PROVERS[*]})

pass=0; fail=0; skipped=0

# Run a prover end-to-end. Args: name flake_dir gen_cmd_template
# The gen and check both run inside one `nix develop` so they share the toolchain.
run_prover() {
  local name="$1" flake="$2" script="$3"
  echo "===================== $name ====================="
  if ! command -v nix >/dev/null 2>&1; then
    echo "SKIP $name: nix not found"; skipped=$((skipped+1)); return
  fi
  # Canary: make sure the prover's nix dev shell actually builds/enters before
  # committing to the full gen+check run. A missing/unreachable toolchain (no
  # network to fetch flake inputs, no substituter cache, etc.) should read as a
  # clear per-prover SKIP, not an opaque FAIL indistinguishable from a real bug.
  if ! nix develop "path:$flake" --no-write-lock-file -c true >/dev/null 2>&1; then
    echo "SKIP $name: nix dev shell for $flake did not build (toolchain unavailable in this environment)"
    skipped=$((skipped+1)); return
  fi
  local src="$WORK/$name/src" dst="$WORK/$name/dst" jsonl="$WORK/$name/out.jsonl"
  mkdir -p "$WORK/$name"
  cp -r "$FIX/$name" "$src"
  if nix develop "path:$flake" --no-write-lock-file -c bash -c "
        set -euo pipefail
        export REPO='$REPO' HARNESS='$HARNESS' SRC='$src' DST='$dst' JSONL='$jsonl'
        $script
        echo '--- apply-ablate --prepare --full-check ---'
        cd \"\$HARNESS\"
        uv run apply-ablate \"\$JSONL\" 0 \"\$SRC\" \"\$DST\" --overwrite --prepare --full-check
     "; then
    echo "PASS $name"; pass=$((pass+1))
  else
    echo "FAIL $name"; fail=$((fail+1))
  fi
}

want() { for p in "${PROVERS[@]}"; do [ "$p" = "$1" ] && return 0; done; return 1; }

if want coq; then
  run_prover coq "$REPO/ablators/rocq" '
    cd "$REPO/ablators/rocq"
    dune exec bin/main.exe -- --all --compact -d "$SRC" "$SRC/Sample.v" > "$JSONL"
  '
fi

if want isabelle; then
  run_prover isabelle "$REPO/ablators/isabelle/rust" '
    cd "$REPO/ablators/isabelle/rust"
    cargo build --quiet --bin ablate --features cli
    ./target/debug/ablate --all --compact -d "$SRC" "$SRC/Sample.thy" > "$JSONL"
  '
fi

if want lean; then
  run_prover lean "$REPO/ablators/lean" '
    cd "$REPO/ablators/lean"
    [ -x ./.lake/build/bin/ablate ] || lake build
    ./.lake/build/bin/ablate --all --compact -d "$SRC" "$SRC/Sample.lean" > "$JSONL"
  '
fi

echo "================================================="
echo "integration: $pass passed, $fail failed, $skipped skipped"
[ "$fail" -eq 0 ]
