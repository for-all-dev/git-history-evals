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

## Leaderboard (issue #142)

`#leaderboard` (top-nav tab) is a second static page rendering agentic-baseline results — no
backend, no build-time data fetch. It `fetch()`s `public/leaderboard/index.json` and the file it
points at, both plain static assets, so publishing a real result is a data-only PR (see below).

- Data format: a JSON array of `apply_ablate.aggregate.GroupResult` objects — the exact output of
  `ablate-aggregate` (`baselines/src/apply_ablate/aggregate.py`, landed in #160), keyed by
  `(model, mode, max_turns)`. `src/leaderboard/types.ts` mirrors that schema.
- Columns: model, split (`mode`), turn budget (`max_turns`), macro PASS + 95% CI, micro PASS + CI,
  the **scorable denominator** (`scorable / total`), repo count, and the full outcome breakdown —
  `malformed` and `context_exceeded` are shown, not folded into a PASS rate, so a submission that
  hides broken/unreachable challenges is visibly incomparable to one that doesn't.
- Real model-grid results (issue #130) don't exist yet, so `public/leaderboard/` currently ships a
  small, clearly-labeled **sample** (`*.sample.*`) produced by actually running the aggregator over
  synthetic rows — not hand-transcribed. `index.json.status: "sample"` makes the page render an
  explicit "awaiting model grid" banner instead of presenting that sample as real results.
- **Submitting a real result**: run `ablate-baseline` per repo for the (model, mode, turn budget)
  cell, build a manifest, run `uv run ablate-aggregate manifest.json --out-json results.json` from
  `baselines/`, drop `results.json` into `website/public/leaderboard/`, flip `index.json` to
  `{"status": "real", "file": "results.json"}`, and PR it. Full steps + rationale:
  `public/leaderboard/README.md`.

## AFP importer (issue #113)

The **Import AFP entry** control loads a real [Archive of Formal
Proofs](https://www.isa-afp.org/) theory into the Isabelle source pane. Because
isa-afp.org sends no permissive CORS, a pure-static site can't fetch it
directly; instead we **pre-mirror the whole AFP**, version-pinned, into a
world-readable bucket we control and fetch raw `.thy` text from there
client-side. This keeps the deploy fully static — **no server, and no DO
credentials on Vercel** (reads are anonymous, objects are public-read). The
current mirror is the `afp-2026-07-07` release: **1000 entries / ~10.2k
theories / ~294 MB**.

- `scripts/mirror-afp.py` mirrors the corpus. `--full` downloads the single AFP
  release tarball once, extracts every `.thy`, and uploads (parallel `s3cmd`)
  public-read to `s3://forall-evals/ablations/isabelle/_data/afp/<Entry>/…`:

  ```bash
  python scripts/mirror-afp.py --full              # whole AFP (the deliverable)
  python scripts/mirror-afp.py --full --workers 12 # more parallel uploaders
  python scripts/mirror-afp.py --entry Kruskal     # curated subset instead
  python scripts/mirror-afp.py --full --dry-run    # download + stage, no upload
  ```

  (Requires `s3cmd` configured for the DO Space; not needed to *run* the site.)

- **Split manifest** (keeps first paint light for 1000 entries):
  - `<prefix>/index.json` — lightweight: `{ schema:"afp-mirror/2", release, entries:
    [{name, n_theories, afp_url}] }`. Loaded once when the panel opens.
  - `<prefix>/<Entry>/theories.json` — that entry's theory list (`{file, url, bytes}`),
    fetched lazily when the entry is selected.

- `src/lib/afp.ts` reads the manifest + theory text; the UI is a searchable
  entry combobox (1000 entries) + theory dropdown. The mirror base URL is
  `https://forall-evals.nyc3.digitaloceanspaces.com/ablations/isabelle/_data/afp` by default;
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
  s3cmd setcors cors.xml s3://forall-evals
  ```
