# Prebuilt WASM ablators

These bundles are **committed on purpose**. The three ablators compile to
WebAssembly from three different toolchains (emscripten, wasm-pack, and
wasm_of_ocaml, driven by `nix`), none of which exist in Vercel's build image.
So we prebuild them locally and commit the outputs here; Vercel then just serves
them as static files and runs the plain `vite build` (no proof toolchain).

At runtime everything is **client-side** — the `.wasm` does the ablation in the
browser, nothing is uploaded and there is no server component.

```
lean/       ablator.js + ablator.wasm (+ coi-serviceworker.js)   emscripten
isabelle/   pkg/isabelle_ablator.js + _bg.wasm                    wasm-pack (--target web)
rocq/       ablator.js + ablator.assets/*.wasm                    wasm_of_ocaml
```

Regenerate after changing an ablator (needs `nix`):

```bash
../../scripts/sync-wasm.sh            # all three
../../scripts/sync-wasm.sh rocq       # just one
```

The Lean runtime is linked with `-pthread`, so it needs `SharedArrayBuffer`,
which requires a cross-origin-isolated context. That is set up by COOP/COEP
headers — in `vercel.json` for production and `vite.config.ts` for dev/preview.
`lean/coi-serviceworker.js` is a fallback for static hosts (e.g. GitHub Pages)
that cannot set those headers; it is not needed on Vercel.
