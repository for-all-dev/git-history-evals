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
- **Seed** is hidden behind an **↻ Random** button (re-rolls the seed); the raw
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

## AFP importer (issue #113)

The **Import AFP entry** control loads a real [Archive of Formal
Proofs](https://www.isa-afp.org/) theory into the Isabelle source pane. Because
isa-afp.org sends no permissive CORS, a pure-static site can't fetch it
directly; instead we **pre-mirror** a curated, version-pinned set of entries
into a world-readable bucket we control and fetch raw `.thy` text from there
client-side. This keeps the deploy fully static — **no server, and no DO
credentials on Vercel** (reads are anonymous, objects are public-read).

- `scripts/mirror-afp.py` downloads AFP release tarballs, extracts every `.thy`,
  uploads them public-read to `s3://forall-git-evals/afp/<Entry>/…`, and writes
  the manifest `afp/index.json`. Re-run to refresh or extend the curated set:

  ```bash
  python scripts/mirror-afp.py                 # mirror the curated set
  python scripts/mirror-afp.py --entry Kruskal # a subset
  python scripts/mirror-afp.py --dry-run       # download + plan, no uploads
  ```

  (Requires `s3cmd` configured for the DO Space; not needed to *run* the site.)

- `src/lib/afp.ts` reads the manifest + theory text. The mirror base URL is
  `https://forall-git-evals.nyc3.digitaloceanspaces.com/afp` by default;
  override with the **non-secret** build env var `VITE_AFP_BASE_URL` to point at
  a different bucket/CDN.

- **One-time bucket CORS** (required, since the browser reads cross-origin):

  ```bash
  cat > cors.xml <<'XML'
  <CORSConfiguration>
    <CORSRule>
      <AllowedOrigin>*</AllowedOrigin>
      <AllowedMethod>GET</AllowedMethod>
      <AllowedMethod>HEAD</AllowedMethod>
      <AllowedHeader>*</AllowedHeader>
      <MaxAgeSeconds>3600</MaxAgeSeconds>
    </CORSRule>
  </CORSConfiguration>
  XML
  s3cmd setcors cors.xml s3://forall-git-evals
  ```
