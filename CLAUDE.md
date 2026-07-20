# proof engineering evals via git history

An obvious instrument in the secure program synthesis (SPS) arsenal is formal methods. While previously prohibitively expensive due to the labor of the proof engineers, we now expect it to sink in cost due to AI driven proof synthesis (and it already has). 

One kinda silly bottleneck to the evals and RL envs that could push this forward faster is cultural— proof engineers from real world codebases like CompCert, SeL4, fiat-crypto, Nova, etc. don’t necessarily know what an eval is and why it’s valuable to register their naturally-occurring data to inspect. I have an unfinished e-book trying to solve this cultural gap. 

This codebase, which I prototyped but didn’t finish, targets a specific proof engineering repo, the specs and proofs of Dalek25519 (a cryptographic primitive library that Signal the messaging app uses), currently underway by BAIF: https://github.com/Beneficial-AI-Foundation/git-history-proof-engineering-eval 
In it, I “mine” the git history to extract challenge problems from commit at time t, which have a ground truth in that they’re solved in the commit at time t+1 in many cases. In doing this (as you’ll see in the code), the hardcoded .git directory scraper makes some assumptions about patterns in commit messages and more generally the conventions with which git is used for collaboration. 

The proper swing at proof engineering evals via git histories would be an agentic miner/scraper, which dynamically finds those assumptions and patterns on the fly, so you have one scaffold and you drop any proof engineering codebase you please into it. 

This effort should also involve conducting baselines.

## IMPORTANT: See @./docs/SoW.md for milestones

keep us on track. 

## Deliverable 
Evals for at least the Nova hypervisor specs and proofs, SeL4, Compcert, and Fiat-Crypto registered to inspect and listed on huggingface. The generalized scaffold dynamically synthesizing “miner” scripts that walk across the git histories. Reporting baselines of how current language models do, which includes demonstration of how to download the data from huggingface and make a solver. Stretch goal: demonstrate actual posttraining on these eval-as-envs with open weight models. 

## Quality checks

Run these from `./scaffold/` (and `./experiments/`) every now and then, and
**certainly before every commit**:

```bash
uv run ruff format        # autoformat
uv run ruff check --fix   # lint + autofix
uv run ty check           # type check
uv run pytest             # tests
```

## repo structure

- `./scaffold/`: Python project (uv-managed) containing:
  - `src/scaffold/`: the **profile-driven** miner/scraper. Two tiers: (1) `profiler/` — a pydantic-ai + `pydantic-ai-harness` CodeMode **calibration agent** that explores a repo + its git history and synthesises a `RepoProfile`; (2) the deterministic engine (`git_walker`, `pattern_detector`, `analyzers/ProfileAnalyzer`) that runs the full history parameterised by that profile — no LLM in the hot loop. `profile.py` is the `RepoProfile` contract (globs, hole markers, declaration patterns, commit-signal banks, tactic vocab/groups — everything that used to be hardcoded, now data). `dataset.py` writes mined evals as manifest-schema version dirs (see `artifacts/MANIFEST_SCHEMA.md`).
  - `src/quali/`: qualitative study tool — uses pydantic-ai to analyze per-theorem proof evolution trajectories (human baseline for contrast with agent trajectories)
  - `src/scripts/analysis/`: proof lifecycle reporting scripts
- `./experiments/`: second uv project — the eval *runner* for fiat-crypto per-commit challenges:
  - `run_experiment.py`: single-shot Claude baseline across all slots (`eval-baseline`)
  - `agent/`: pydantic-ai ReAct agent (`runner.py`, `agent.py`, `tools.py`, `deps.py`) + `run_agent_experiment.py` (`eval-agent`)
  - `shared/`: splice, prompts, compile helpers used by both drivers
  - `docker/`: layered Dockerfiles — `base` (coq + uv), `deps` (opam layer), `commit` (per-SHA fiat-crypto checkout + warm build)
  - `orchestrate/`: bash + docker-compose glue. `run-all.sh` spawns a detached tmux session with one window per SHA; each window runs `run-commit.sh` against a `gen-compose.py`-produced compose file. `aggregate.sh` pulls per-SHA named volumes (`results-<prefix>`) into `results/<run_id>/`. `attach.sh` attaches to a running session.
  - `summary.py`: cross-run aggregator — per-(mode, deletion_size) drift ratios (vo_bytes, compile_time, proof_chars/lines, tactic_count) vs human reference, baseline-vs-agent deltas, per-metric Pearson r vs deletion_size as a faithfulness check
  - `results/<run_id>/`: host-side mirror of the per-SHA volumes (see `experiments/results/README.md`)
- **Ablators** (syntactic proof-ablation tools — each parses a proof file and replaces
  selected proofs/lemmas with holes, emitting `(challenge, solution)` JSONL pairs; all
  share the `record.py`-compatible schema). Four implementations, kept in lockstep:
  - `./rocq-ablator/`: OCaml/dune, for Coq/Rocq `.v` (CLI `bin/main.ml` + WASM).
  - `./lean-ablator/`: Lean 4/lake, for `.lean` (`Main.lean`, core in `Ablator/`, WASM).
  - `./isabelle-ablator/rust/`: Rust/cargo, for Isabelle `.thy` (clap CLI + WASM).
  Key flags (all four): `--delete-lemmas[=N]` / `--delete-lemmas-leaves` (delete N
  eligible lemmas + hole their in-file users at smallest-enclosing-block granularity),
  `--corollary-delete-lemmas[=N]` / `-uniform` / `-leaves` (same, but candidates are
  restricted to one random theorem's — the "corollary" — transitive in-file dependency
  closure: pick a corollary uniformly, draw deletions from its closure fan-in-weighted
  (or uniform), re-picking a corollary only when the closure runs dry),
  `--count N`, `--shrink-challenge-minimal` / `--shrink-solution-minimal` (slice to the
  holes' dependency closure), `--compact` (JSONL), `--text`, `--check-build`. Ablators
  **skip emitting a record when nothing was deleted/holed** (challenge == solution) so
  trivial challenges never reach a baseline. (Isabelle-only: apply-scripts are
  prefix-cut by default — keep a prefix of `apply` steps, `sorry` the rest — or dropped
  whole with `--ablate-scripts`.)
- `./baselines/`: uv project — the **prover-agnostic agentic baseline harness**
  (formerly `harness/`; the older single-shot whole-file baseline is now
  `./baselines-old/`). Package `apply_ablate`: `solve.py` is a pydantic-ai ReAct loop
  that re-derives the deleted lemma(s) into a compiling, hole-free file; `provers/`
  has the Coq/Isabelle/Lean backends (`coqc`, session-aware `isabelle build` with
  prebuilt-deps + `skip_proofs`, `lake env lean`); `apply.py` splices a challenge into
  a work copy (symlinking heavy dep dirs like `.lake`); `record.py` is the shared JSONL
  schema; `obs.py` wires Logfire (`instrument_pydantic_ai` + per-compile/per-outcome
  spans). Pre-flight validation marks challenges that don't compile `malformed` and
  empty-diff ones `trivial`, both excluded from the PASS rate.
- `./ablators/isabelle/flake.nix`: pins the **official** Isabelle releases the baseline evaluator
  must run under — `isabelle-2025` for **l4v** (@429d778 needs the base release), `isabelle-2025-2`
  for the **AFP**. nixpkgs' Isabelle breaks `smt` reconstruction, so it cannot be used. (The Scala
  ablator that used to own this flake has been deleted; the Rust ablator does the ablation.)
- `./data/`: source repos, **organised by language**: `data/lean/<repo>` (50 mined Lean repos +
  `_triage/` of unmined candidates), `data/isabelle/l4v`, `data/rocq/{CompCert,fiat-crypto,BRiCk}`.
  Only the 4 Rocq/Isabelle repos are git submodules; the Lean checkouts are pinned in
  `pipeline/repos.tsv` (url + revision + toolchain) and materialised by `pipeline/clone_repos.sh`
  — they are multi-GB once built, so they are gitignored rather than vendored.
- `./pipeline/`: the ablation pipeline — mine -> build -> validate -> publish. See
  `pipeline/README.md`; **read its "Validation is a build problem" section before trusting any
  `malformed` count**, since an incompletely-built source tree reports every challenge as
  malformed (this understated hex-dev by 2,881 challenges).
- `./flake.nix`: the pipeline toolchain — a `cc` wrapper adding `-D_GNU_SOURCE` (Lean C FFI),
  elan/lake, s3cmd, and a uv2nix-built python env exposing `ablate-baseline` with no uv at
  runtime (`nix run .#ablate-baseline`).
- `./artifacts/`: mined eval datasets as versioned bundles — `<repo>-eval/<tag>-<hash>/{manifest.json, miner/profile.json, challenges.jsonl}` per `MANIFEST_SCHEMA.md`, indexed by `_index.json`. Each dataset owns exactly one profile (at `<version>/miner/profile.json`); the blessed `<repo>-eval/profile.json` that `mine-all` reads is a relative **symlink** into the canonical version's profile, not a copy. Bulk `*.jsonl`/`*.txt` payloads are sha256 blobs declared in manifests and gitignored.
- `./dashboard/`: Next.js app for exploring JSONL benchmark artifacts
- `./docs/`: changelog for the pattern detector (`PatternDetectorChanges.md`) + agent task template

### CLI tools

From `./scaffold/`:
- `uv run scaffold profile <repo> --tag <label> [--promote]` — Tier-1 calibration agent: synthesise a `RepoProfile`, mine, and write a versioned dataset bundle under `artifacts/<repo>-eval/<tag>-<hash>/`
- `uv run scaffold materialize <repo> -p <profile.json> --tag <label> [--kind handcrafted] [--promote]` — bundle an existing profile into a dataset version dir without the agent (used to migrate hand-authored profiles). `--promote` symlinks `<repo>-eval/profile.json` at the new version.
- `uv run scaffold calibrate --bundle <version-dir>` — calibrate a **repo-specific curation prompt** (issue #84): iteratively draws fresh random samples, labels them with the decision model, evaluates candidate prompts (tier-1 scoring + mechanical threshold sweep with a 10% defer-rate cap + tier-2 escalation), and lets a persistent decision-model "writer" analyze failures and propose variants. Writes `<version>/curation/{criteria.txt, tier1_prompt.txt, tier2_prompt.txt, calibration.json}`; consume via `scaffold curate <challenges.jsonl> --calibration <version>/curation`. Model roles (cheap/mid/decision — currently Haiku/Sonnet/Opus) are configured in `model-roles.json` at the monorepo root (see `scaffold/src/scaffold/model_roles.py`).
- `uv run scaffold` — Tier-2 deterministic mining: `mine`/`mine-all`/`dump-commits`/`enrich-commits`/`diff-enrich`/`stratify-tactics`/`group-tactics`, each taking `--profile/-p` (see `scaffold --help`)
- `uv run quali` — qualitative trajectory analysis via LLM (reads from artifacts, writes `*-quali.jsonl`)

From `./baselines/`:
- `uv run ablate-baseline <challenges.jsonl> <src> [--model … --max-turns N --timeout S --out F]` — run the pydantic-ai ReAct agent over ablator-generated challenges, scoring each by real compilation; reports PASS / trivial / malformed / turn-limit / harness-err. Run inside the relevant prover's nix shell (so `coqc`/`isabelle`/`lake` are on PATH) with `ANTHROPIC_API_KEY` in the repo `.env`. (For Coq, use the opam `coqc` that built the `.vo`, not the rocq-ablator nix shell's Rocq.)
- `uv run difficulty extract <challenges.jsonl> [--out-jsonl F --out-csv F]` / `uv run difficulty build-table <challenges.jsonl> <results.jsonl> [--out-* F]` — the **difficulty-classifier** feature/label layer (`apply_ablate.difficulty`). `extract` flattens each enriched ablator record into a fixed per-challenge feature vector (fan-in, sub-proof/tactic/cyclomatic aggregates over deleted lemmas + holes + corollary, sizes, closure_size); `build-table` joins those to `ablate-baseline` outcomes (PASS label + reconstructed outcome class) via the stable `challenge_id`. The proof-complexity metrics are computed *inside* the four ablators (visible in the JSONL); Python only aggregates. Model is deferred — see `apply_ablate.difficulty.model` and `docs/difficulty-features.md`.

From `./experiments/`:
- `uv run eval-baseline` — single-shot Claude baseline across slots; writes `results/<run_id>/baseline.jsonl`
- `uv run eval-agent` — pydantic-ai ReAct loop alternative; writes `results/<run_id>/agent.jsonl`
- `uv run python summary.py --inputs <glob>` — aggregate one or more JSONLs into summary.json + summary.md
- `./orchestrate/run-all.sh` — end-to-end: build images, gen compose, spawn tmux session per SHA
- `./orchestrate/aggregate.sh [<run_id>]` — post-run concat + summary + `latest` symlink
- `./orchestrate/attach.sh [<run_id>]` — attach to a running `proof-eval-<run_id>` tmux session

### source data repos
- https://github.com/seL4/l4v (Isabelle)
- https://github.com/mit-plv/fiat-crypto (Coq)
- https://github.com/AbsInt/CompCert (Coq)
- https://github.com/bluerock-io/BRiCk (Coq)
