#!/usr/bin/env bash
# Rebuild the three WASM ablators and copy their outputs into public/wasm/.
#
# Vercel's build image has no rustup/emscripten/dune/nix, so the .wasm bundles
# CANNOT be built at deploy time — they are prebuilt here and committed under
# public/wasm/ (which Vercel then serves statically). Run this locally whenever
# an ablator changes, then commit the refreshed public/wasm/.
#
# Needs `nix` (each ablator's flake provides its own toolchain). Usage:
#     ./scripts/sync-wasm.sh            # build all three
#     ./scripts/sync-wasm.sh rocq       # build just one (lean|isabelle|rocq)
set -euo pipefail
cd "$(dirname "$0")/.."
WEB="$PWD"
AB="$WEB/../ablators"

command -v nix >/dev/null || { echo "nix not found — needed to build the ablators"; exit 1; }

want="${1:-all}"
do_lang() { [ "$want" = all ] || [ "$want" = "$1" ]; }

if do_lang lean; then
  echo "==> lean (emscripten)"
  ( cd "$AB/lean" && nix develop -c ./build-wasm.sh )
  mkdir -p "$WEB/public/wasm/lean"
  cp "$AB/lean/web/ablator.js" "$AB/lean/web/ablator.wasm" \
     "$AB/lean/web/coi-serviceworker.js" "$WEB/public/wasm/lean/"
fi

if do_lang isabelle; then
  echo "==> isabelle (wasm-pack)"
  ( cd "$AB/isabelle/rust" && nix develop -c ./build-wasm.sh )
  mkdir -p "$WEB/public/wasm/isabelle/pkg"
  # copy runtime files only — NOT wasm-pack's generated .gitignore (which is `*`
  # and would hide the whole pkg from git).
  cp "$AB/isabelle/rust/web/pkg/isabelle_ablator.js" \
     "$AB/isabelle/rust/web/pkg/isabelle_ablator_bg.wasm" \
     "$AB/isabelle/rust/web/pkg/isabelle_ablator.d.ts" \
     "$AB/isabelle/rust/web/pkg/isabelle_ablator_bg.wasm.d.ts" \
     "$WEB/public/wasm/isabelle/pkg/"
fi

if do_lang rocq; then
  echo "==> rocq (wasm_of_ocaml)"
  ( cd "$AB/rocq" && nix develop -c ./build-wasm.sh )
  mkdir -p "$WEB/public/wasm/rocq"
  cp "$AB/rocq/web/ablator.js" "$WEB/public/wasm/rocq/"
  rm -rf "$WEB/public/wasm/rocq/ablator.assets"
  cp -r "$AB/rocq/web/ablator.assets" "$WEB/public/wasm/rocq/"
fi

echo
echo "done. Review + commit public/wasm/:"
echo "    git add public/wasm && git status public/wasm"
