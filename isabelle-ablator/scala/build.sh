#!/usr/bin/env bash
# Compile the ablator against the bundled Isabelle/Scala classpath.
# Requires `isabelle` on PATH (provided by `nix develop`).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
isabelle scalac -d build/ablator.jar src/*.scala
echo "built $(pwd)/build/ablator.jar"
