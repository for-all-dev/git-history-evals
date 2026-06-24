#!/usr/bin/env bash
# Build the WASM module into web/pkg, then the site is ready to serve.
set -euo pipefail
cd "$(dirname "$0")"
wasm-pack build --target web --out-dir web/pkg --no-default-features --features wasm
echo
echo "built web/pkg/  — serve the site with:"
echo "    python3 -m http.server -d web 8000   # then open http://localhost:8000/"
