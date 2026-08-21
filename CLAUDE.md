# proof engineering evals via syntactic proof ablation

An obvious instrument in the secure program synthesis (SPS) arsenal is formal methods. While
previously prohibitively expensive due to the labor of proof engineers, we expect the cost to sink
due to AI-driven proof synthesis. The bottleneck for evals and RL environments that could push this
forward is data: real proof-engineering repos (CompCert, seL4, fiat-crypto, Nova, and the long tail
of Lean projects) contain enormous amounts of verified ground truth, but none of it is packaged as
an eval.

This repo turns those repos into evals by **syntactic proof ablation**. Given a proof file, an
ablator picks a theorem (the *corollary*), takes its transitive in-file dependency closure, deletes
one lemma from that closure, and replaces the proof steps that used it with holes (`sorry` /
`Admitted` / `oops`). The solver must re-derive the deleted lemma and close the holes so the file
compiles. Both sides of every published pair are **compile-validated** by the real prover, so the
ground truth is not a guess about what a commit intended.

The project previously mined git histories for `(commit t, commit t+1)` challenge pairs. That
method was retired in August 2026 — see `docs/SoW.md` for the pivot, its date, and its rationale.

## IMPORTANT: See @./docs/SoW.md for milestones

keep us on track.

## Deliverable

Compile-validated ablation evals over real proof-engineering repos, published to HuggingFace
(`for-all-dev/ablation-eval`), plus a prover-agnostic agentic baseline harness and reported
baselines for current language models. Stretch goal: post-training on these evals-as-envs with
open-weight models.

## Quality checks

Run these from `./baselines/` every now and then, and **certainly before every commit**:

```bash
uv run ruff format        # autoformat
uv run ruff check --fix   # lint + autofix
uv run ty check           # type check
uv run pytest             # tests
```

The ablators have their own toolchains: `nix develop ablators/lean` (`lake test`),
`ablators/rocq` (`dune test`), `ablators/isabelle` (`cargo test` under `ablators/isabelle/rust`).

## repo structure

- `./ablators/`: the three **ablators** — each parses a proof file, replaces selected
  proofs/lemmas with holes, and emits `(challenge, solution)` JSONL pairs sharing the
  `record.py` schema. Kept in lockstep, each also built to WASM for the website.
  - `./ablators/lean/`: Lean 4 / lake, for `.lean` (`Main.lean`, core in `Ablator/`).
  - `./ablators/rocq/`: OCaml / dune, for Coq/Rocq `.v` (CLI `bin/main.ml`, core in `lib/`).
  - `./ablators/isabelle/rust/`: Rust / cargo, for Isabelle `.thy` (clap CLI).
  - `./ablators/isabelle/flake.nix`: pins the **official** Isabelle releases the baseline
    evaluator must run under — `isabelle-2025` for **l4v** (@429d778 needs the base release),
    `isabelle-2025-2` for the **AFP**. nixpkgs' Isabelle breaks `smt` reconstruction, so it
    cannot be used.

  Key flags (all three): `--delete-lemmas[=N]` / `--delete-lemmas-leaves` (delete N eligible
  lemmas + hole their in-file users at smallest-enclosing-block granularity);
  `--corollary-delete-lemmas[=N]` / `-uniform` / `-leaves` (same, but candidates are restricted
  to one random theorem's — the "corollary" — transitive in-file dependency closure: pick a
  corollary uniformly, draw deletions from its closure fan-in-weighted (or uniform), re-picking a
  corollary only when the closure runs dry); `--count N`;
  `--shrink-challenge-minimal` / `--shrink-solution-minimal` (slice to the corollary's dependency
  closure); `--compact` (JSONL); `--text`; `--check-build`. Ablators **skip emitting a record
  when nothing was deleted/holed** (challenge == solution) so trivial challenges never reach a
  baseline. (Isabelle-only: apply-scripts are prefix-cut by default — keep a prefix of `apply`
  steps, `sorry` the rest — or dropped whole with `--ablate-scripts`.)

- `./baselines/`: uv project — the **prover-agnostic agentic baseline harness**, package
  `apply_ablate`. `solve.py` is a pydantic-ai ReAct loop that re-derives the deleted lemma(s)
  into a compiling, hole-free file; `provers/` has the Coq/Isabelle/Lean backends (`coqc`;
  session-aware `isabelle build` with prebuilt deps + `skip_proofs`; bare `lean` with a
  reconstructed `LEAN_PATH` — never `lake env`, which would write through the `.lake` symlink
  into src, #119); `apply.py` splices a challenge into a work copy (symlinking heavy dep dirs
  like `.lake`); `record.py` is the shared JSONL schema; `obs.py` wires Logfire
  (`instrument_pydantic_ai` + per-compile/per-outcome spans); `difficulty/` is the
  feature/label layer for the difficulty classifier. Pre-flight validation marks challenges that
  don't compile `malformed` and empty-diff ones `trivial`, both excluded from the PASS rate.

- `./pipeline/`: the ablation pipeline — mine -> build -> validate -> index -> publish -> eval.
  `run.sh` is the single entry point; `repos.tsv` (name, language, url, revision, path,
  toolchain) is the single source of truth for the corpus; `clone_repos.sh` materialises it.
  `publish_hf.sh` rebuilds and publishes the `for-all-dev/ablation-eval` easy/hard splits;
  `upload_ablations.sh` pushes bulk JSONL to DO Spaces. See `pipeline/README.md`, and
  **read its "Validation is a build problem" section before trusting any `malformed` count** —
  an incompletely built source tree reports every challenge as malformed (this understated
  hex-dev by 2,881 challenges).

- `./data/`: source repos, organised by language: `data/lean/<repo>` (the mined Lean corpus +
  `_triage/` of unmined candidates), `data/isabelle/l4v`, `data/rocq/{CompCert,fiat-crypto,BRiCk}`.
  Only the Rocq/Isabelle repos are git submodules; the Lean checkouts are pinned in
  `pipeline/repos.tsv` and materialised by `clone_repos.sh` — they are multi-GB once built, so
  they are gitignored rather than vendored.

- `./artifacts/`: mined datasets, one directory per mode:
  - `artifacts/lean-ablate/<repo>/manifest.json` — **corollary-leaves** mode (only the leaf
    tactic steps that cited the deleted lemma are holed).
  - `artifacts/lean-ablate-whole/<repo>/manifest.json` — **corollary-whole** mode (each user's
    entire proof body is holed; strictly harder).

  Each manifest records repo, git revision, proof assistant, exact ablator flags, seed, the
  validation command, and mined/kept/malformed counts, so a published row always traces back to
  an exact tree. `_index.json` + `README.md` per mode are regenerated by `pipeline/write_index.py`.
  Bulk `*.jsonl` payloads are sha256-addressed blobs declared in the manifests and gitignored.
  (`artifacts/cedar-spec-eval/` is a leftover bundle from the retired git-history miner.)

- `./website/`: the **Proof Ablation Playground** — a Vite/React static site (deployed on Vercel)
  that runs all three ablators as WASM entirely in the browser. Paste a theory, get back the
  ablated challenge file or the JSON eval records. The `.wasm` bundles are prebuilt and committed
  under `public/wasm/`. See `website/README.md`.

- `./flake.nix`: the pipeline toolchain — a `cc` wrapper adding `-D_GNU_SOURCE` (Lean C FFI),
  elan/lake, s3cmd, and a uv2nix-built python env exposing `ablate-baseline` with no uv at
  runtime (`nix run .#ablate-baseline`).

- `./docs/`: findings and planning. `ablation-baseline-findings.md` (agentic baseline results +
  ablator bugs found by running it), `dataset-issues.md` (the inspection that motivated the
  pivot), `lean-ablate-datasets.md`, `lean-ablate-repo-gotchas.md`, `difficulty-features.md`,
  `rocq-ablate-candidates.md`, `contamination.agents.md`, `SoW.md`.

- `model-roles.json` at the repo root configures the cheap/mid/decision model roles.

### CLI tools

From `./pipeline/` (inside `nix develop`):

- `bash pipeline/clone_repos.sh lean` — fetch source repos at their pinned revisions
- `bash pipeline/run.sh all <scratch>` — mine -> validate -> index -> eval, both modes
- `bash pipeline/run.sh mine|validate|index|eval <scratch> …` — one stage at a time
- `bash pipeline/upload_ablations.sh` — publish bulk JSONL to `s3://forall-ablations/lean/<mode>/<repo>/`
- `bash pipeline/publish_hf.sh` — rebuild + publish the `for-all-dev/ablation-eval` easy/hard splits

From `./baselines/`:

- `uv run ablate-baseline <challenges.jsonl> <src> [--model … --max-turns N --timeout S --out F]`
  — run the pydantic-ai ReAct agent over ablator-generated challenges, scoring each by real
  compilation; reports PASS / trivial / malformed / turn-limit / harness-err. Run inside the
  relevant prover's nix shell (so `coqc`/`isabelle`/`lean` are on PATH) with `ANTHROPIC_API_KEY`
  in the repo `.env`. (For Coq, use the opam `coqc` that built the `.vo`, not the
  `ablators/rocq` nix shell's Rocq.)
- `uv run apply-ablate <jsonl> <index> <src> <dst> [--prepare --check --full-check …]` —
  materialise a single challenge (or its solution) onto disk and verify it builds.
- `uv run difficulty extract <challenges.jsonl>` / `uv run difficulty build-table
  <challenges.jsonl> <results.jsonl>` — the difficulty-classifier feature/label layer. `extract`
  flattens each enriched ablator record into a fixed per-challenge feature vector (fan-in,
  sub-proof/tactic/cyclomatic aggregates over deleted lemmas + holes + corollary, sizes,
  closure_size); `build-table` joins those to `ablate-baseline` outcomes via the stable
  `challenge_id`. The proof-complexity metrics are computed *inside* the ablators (visible in the
  JSONL); Python only aggregates. See `docs/difficulty-features.md`.

Ablator CLIs (each from its own `nix develop`):

- `ablators/lean`: `lake exe ablate <file.lean> [flags]` (tests: `lake exe ablate-test`)
- `ablators/rocq`: `dune exec bin/main.exe -- <file.v> [flags]`
- `ablators/isabelle/rust`: `cargo run --features cli --bin ablate -- <file.thy> [flags]`

### source data repos

- 57 Lean repos pinned in `pipeline/repos.tsv` (the mined corpus)
- https://github.com/seL4/l4v (Isabelle)
- https://github.com/mit-plv/fiat-crypto (Coq)
- https://github.com/AbsInt/CompCert (Coq)
- https://github.com/bluerock-io/BRiCk (Coq)
