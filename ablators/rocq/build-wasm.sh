#!/usr/bin/env bash
# Build the in-browser WASM module into web/ablator.{js,wasm}, then the site is
# ready to serve. The Coq ablator core is pure OCaml; we compile it to bytecode
# with dune, then to WebAssembly with wasm_of_ocaml (no Coq/prover at runtime).
#
# Run inside the dev shell (which provides dune + js_of_ocaml + wasm_of_ocaml):
#     nix develop -c ./build-wasm.sh
set -euo pipefail
cd "$(dirname "$0")"

command -v wasm_of_ocaml >/dev/null || {
  echo "wasm_of_ocaml not found — run inside 'nix develop' (or 'nix develop -c ./build-wasm.sh')"
  exit 1
}

# 1. build the bytecode entry point under the `wasm` profile (enables wasm/dune)
echo "[1/2] dune build (bytecode) ..."
dune build --profile wasm wasm/ablator_wasm.bc

# 2. compile the bytecode to WebAssembly + JS loader
echo "[2/2] wasm_of_ocaml ..."
mkdir -p web
wasm_of_ocaml compile _build/default/wasm/ablator_wasm.bc -o web/ablator.js

echo
echo "built web/ablator.js + web/ablator.assets/ (the .wasm)"
echo "serve the playground from any static host:"
echo "    python3 -m http.server -d web 8000   # then open http://localhost:8000/"
