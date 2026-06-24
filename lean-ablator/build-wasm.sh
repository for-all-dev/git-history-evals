#!/usr/bin/env bash
# Build the in-browser WASM module: web/ablator.{js,wasm}.
#
# The Lean compiler emits portable C; we recompile that C (plus a small C shim)
# with emscripten and link it against Lean's *prebuilt* wasm32 runtime
# (downloaded from the leanprover/lean4 GitHub release — the last one shipped is
# v4.15.0, which is why the project is pinned to v4.15.0). No server: the .wasm
# does everything client-side.
#
# Run inside the emscripten dev shell:
#     nix develop -c ./build-wasm.sh
# or:
#     nix shell nixpkgs#emscripten nixpkgs#zstd nixpkgs#curl -c ./build-wasm.sh
set -euo pipefail
cd "$(dirname "$0")"

LEAN_VER="4.15.0"
TC_DIR=".wasm/lean-${LEAN_VER}-linux_wasm32"
TC_URL="https://github.com/leanprover/lean4/releases/download/v${LEAN_VER}/lean-${LEAN_VER}-linux_wasm32.tar.zst"

command -v emcc >/dev/null || { echo "emcc not found — run inside 'nix develop' or 'nix shell nixpkgs#emscripten'"; exit 1; }

# 1. fetch + unpack the prebuilt wasm32 Lean toolchain (runtime libs + headers)
if [ ! -d "$TC_DIR" ]; then
  echo "[1/4] downloading Lean ${LEAN_VER} wasm32 toolchain ..."
  mkdir -p .wasm
  curl -sL -o .wasm/tc.tar.zst "$TC_URL"
  tar --use-compress-program=unzstd -xf .wasm/tc.tar.zst -C .wasm
fi
INC="$TC_DIR/include"
LIB="$TC_DIR/lib/lean"

# 2. generate the C for our Lean modules (no-op if already built)
echo "[2/4] lake build (emit C) ..."
lake build >/dev/null

# 3. emcc compile + link. The runtime libs are LTO bitcode built with
#    -fwasm-exceptions and -pthread, so we must match those flags.
echo "[3/4] emcc link ..."
mkdir -p web
EM_CACHE="${EM_CACHE:-$(pwd)/.wasm/emcache}"
export EM_CACHE
mkdir -p "$EM_CACHE"

emcc \
  -O2 -flto -fwasm-exceptions -pthread \
  -I "$INC" \
  .lake/build/ir/Ablator/*.c wasm/shim.c wasm/uv_stubs.c \
  -L "$LIB" -lInit -lStd -lleancpp -lleanrt \
  -sALLOW_MEMORY_GROWTH=1 \
  -sEXIT_RUNTIME=0 \
  -sMODULARIZE=1 -sEXPORT_NAME=createAblator \
  -sEXPORTED_FUNCTIONS='_ablate_json,_malloc,_free' \
  -sEXPORTED_RUNTIME_METHODS='ccall,UTF8ToString,stringToUTF8,lengthBytesUTF8' \
  -sPTHREAD_POOL_SIZE=2 \
  -o web/ablator.js

echo "[4/4] done -> web/ablator.js + web/ablator.wasm"
echo
echo "Serve the playground from any static host (pthreads need cross-origin"
echo "isolation; web/coi-serviceworker.js handles that on plain hosts):"
echo "    python3 -m http.server -d web 8000   # then open http://localhost:8000/"
