# Proof Ablation Playground

A single static site combining the three WASM proof ablators — **Lean**
(Lean-in-Lean → emscripten), **Isabelle/HOL** (Rust → wasm-pack), and
**Rocq/Coq** (OCaml → wasm_of_ocaml). Paste a theory in any of the three; the
site auto-detects the language, and the matching ablator — running **entirely in
your browser** — deletes a fan-in-weighted theorem from a randomly selected
corollary and returns the *context-minimized challenge file* (or the JSON eval
records it becomes). Implements issue #112.

## What it does (issue #112)

- **Default:** delete one theorem, weighted by fan-in, from a random corollary's
  dependency closure. A slider raises the number of theorems to delete.
- **Output modes:** the ablated challenge file (default) or the generated JSON
  evals. In JSON mode a *repeat* slider generates N deduplicated variants.
- **Seed** is hidden behind an **↻ Generate** button (re-rolls the seed); the raw
  seed is editable under *Advanced*.
- **Nice-to-haves:** language auto-detection, lightweight syntax highlighting,
  collapsible JSON (challenge + solution unfolded by default), copy buttons.

## Architecture

Each backend exposes a different JS ABI; `src/ablators/{lean,isabelle,rocq}.ts`
wrap them behind one `Ablator` interface (`src/ablators/types.ts`). Isabelle and
Rocq take the rich JSON-opts ABI and support the corollary/fan-in default
natively; Lean's numeric ABI predates those knobs, so it approximates by blanking
the N most fan-in-central bodies (surfaced as a note in the UI).

## Deployment (Vercel, static)

No server is needed — all computation is client-side WASM. Vercel's build image
lacks the proof toolchains, so **the `.wasm` bundles are prebuilt and committed**
under `public/wasm/` (see `public/wasm/README.md`); Vercel only runs `vite build`
and serves `dist/` from its CDN.

- `vercel.json` sets the COOP/COEP headers the Lean backend needs
  (`SharedArrayBuffer` for its pthreaded runtime) and long-caches `/wasm/*`.
- `vite.config.ts` sets the same headers for `vite dev` / `vite preview`.

```bash
bun install
bun run dev        # http://localhost:5173
bun run build      # -> dist/
./scripts/sync-wasm.sh   # rebuild + refresh public/wasm/ (needs nix)
```

Deploy: point Vercel at this directory (root `website/`); the committed
`public/wasm/` ships as-is. It also works on any static host — on hosts that
can't set COOP/COEP (e.g. GitHub Pages), `public/wasm/lean/coi-serviceworker.js`
provides the isolation fallback.
