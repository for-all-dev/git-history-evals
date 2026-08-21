# git-history-evals

**Proof-engineering evals built by syntactic proof ablation.**

Take a real, verified proof file. Pick a theorem — the **corollary**. Take its transitive in-file
dependency closure, delete one lemma from that closure, and replace the proof steps that cited it
with holes (`sorry` / `Admitted` / `oops`). Slice the file down to the corollary's closure so the
context stays small. That ablated file is the challenge; the original is the solution. The solver
must re-derive the deleted lemma and close the holes so the file compiles.

Both sides of every published pair are checked by the real prover before publication, so the
ground truth is a proof that actually compiles rather than an inference about what a commit meant
to do.

The published dataset is **[`for-all-dev/ablation-eval`](https://huggingface.co/datasets/for-all-dev/ablation-eval)**
(`easy` / `hard` splits). The **Proof Ablation Playground** in `website/` runs all three ablators as WASM
client-side, so you can paste a theory and see the challenge it becomes without installing a
prover (see `website/README.md`).

> The project began as a git-history miner: challenges were `(commit t, commit t+1)` pairs. That
> method was retired in August 2026 in favour of ablation. `docs/SoW.md` records the pivot and why.

## Two modes

Both delete one lemma from a corollary's closure; they differ in how the lemma's *users* are holed.

| mode | what gets holed | artifacts |
|---|---|---|
| `corollary-leaves` | only the **leaf tactic steps** that cited the lemma — the rest of each user's proof survives | `artifacts/lean-ablate/` |
| `corollary-whole` | each user's **entire proof body** — solve from the statement alone (strictly harder) | `artifacts/lean-ablate-whole/` |

## What's here

- `ablators/{lean,rocq,isabelle}` — three ablators (Lean 4/lake, OCaml/dune, Rust/cargo) sharing
  one record schema, each also compiled to WASM for the website.
- `baselines/` — the prover-agnostic agentic baseline harness (`ablate-baseline`): a pydantic-ai
  ReAct loop that re-derives the deleted lemma, scored by real compilation.
- `pipeline/` — mine → build → validate → index → publish → eval. `repos.tsv` pins the corpus.
- `artifacts/` — per-repo manifests (repo, revision, ablator flags, seed, counts) per mode.
- `data/` — source repos by language (`data/lean/…`, `data/isabelle/l4v`, `data/rocq/…`).
- `website/` — the Proof Ablation Playground.

Load a published eval and run a solver against it: `baselines/quickstart.py` (`uv run python quickstart.py --list-repos` from `baselines/`).

## Prereqs

- `nix` (flakes enabled) — `flake.nix` provides the pipeline toolchain
- `uv` (only if you want to run `baselines/` outside nix)
- `ANTHROPIC_API_KEY` in a `.env` at the repo root, for baseline runs

## Quickstart

```bash
nix develop                                   # wrapped cc, elan/lake, ablate-baseline, s3cmd
bash pipeline/clone_repos.sh lean             # fetch source repos at pinned revisions
bash pipeline/run.sh all /path/to/scratch     # mine -> validate -> index -> eval, both modes
```

One stage at a time:

| command | what |
|---|---|
| `run.sh mine <scratch> [leaves\|whole]` | ablate every repo (syntactic — needs no build) |
| `run.sh validate <scratch> [leaves\|whole]` | really compile challenge + ground truth; write `artifacts/` |
| `run.sh index <scratch>` | regenerate `_index.json` (counts + sha256 per blob) |
| `run.sh eval <scratch> <n> [model]` | sample, solve, train the difficulty model, test on a disjoint sample |

Publishing:

```bash
bash pipeline/upload_ablations.sh   # bulk JSONL -> s3://forall-ablations/lean/<mode>/<repo>/
bash pipeline/publish_hf.sh         # easy/hard splits -> for-all-dev/ablation-eval
```

See `pipeline/README.md` — and **read its "Validation is a build problem" section before trusting
any `malformed` count**: an incompletely built source tree reports every challenge as malformed.

## Running a baseline

From inside the relevant prover's nix shell (so `coqc` / `isabelle` / `lean` are on PATH):

```bash
cd baselines
uv run ablate-baseline <challenges.jsonl> <src-checkout> --max-turns 40 --out results.jsonl
```

Outcomes are `PASS` / `trivial` / `malformed` / `turn-limit` / `harness-err`; `trivial` and
`malformed` are excluded from the PASS rate. Results and the ablator bugs that running it exposed
are written up in `docs/ablation-baseline-findings.md`.

## Ablating one file directly

```bash
nix develop ablators/lean
lake exe ablate MyTheory.lean --corollary-delete-lemmas-leaves --shrink-solution-minimal --compact
```

Equivalents: `dune exec bin/main.exe -- file.v …` (`ablators/rocq`),
`cargo run --features cli --bin ablate -- file.thy …` (`ablators/isabelle/rust`).

## Testing

```bash
cd baselines && uv run ruff check && uv run ty check && uv run pytest
nix develop ablators/lean --command lake exe ablate-test
nix develop ablators/rocq --command dune test
```

## More depth

- `CLAUDE.md` — repo-wide agent/developer context
- `docs/SoW.md` — statement of work, milestones, and the August 2026 pivot
- `docs/ablation-baseline-findings.md` — agentic baseline results and parity work across provers
- `docs/dataset-issues.md` — the dataset inspection that motivated retiring git-history mining
- `pipeline/README.md` — pipeline internals, l4v/Isabelle constraints, validation caveats
- `baselines/README.md` — `apply-ablate` stages and prover backends
- `website/README.md` — WASM playground architecture and deployment
