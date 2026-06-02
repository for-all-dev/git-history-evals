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
- `./data/`: source repos as git submodules
- `./artifacts/`: mined eval datasets as versioned bundles — `<repo>-eval/<tag>-<hash>/{manifest.json, miner/profile.json, challenges.jsonl}` per `MANIFEST_SCHEMA.md`, indexed by `_index.json`. Each dataset owns exactly one profile (at `<version>/miner/profile.json`); the blessed `<repo>-eval/profile.json` that `mine-all` reads is a relative **symlink** into the canonical version's profile, not a copy. Bulk `*.jsonl`/`*.txt` payloads are sha256 blobs declared in manifests and gitignored.
- `./dashboard/`: Next.js app for exploring JSONL benchmark artifacts
- `./docs/`: changelog for the pattern detector (`PatternDetectorChanges.md`) + agent task template

### CLI tools

From `./scaffold/`:
- `uv run scaffold profile <repo> --tag <label> [--promote]` — Tier-1 calibration agent: synthesise a `RepoProfile`, mine, and write a versioned dataset bundle under `artifacts/<repo>-eval/<tag>-<hash>/`
- `uv run scaffold materialize <repo> -p <profile.json> --tag <label> [--kind handcrafted] [--promote]` — bundle an existing profile into a dataset version dir without the agent (used to migrate hand-authored profiles). `--promote` symlinks `<repo>-eval/profile.json` at the new version.
- `uv run scaffold` — Tier-2 deterministic mining: `mine`/`mine-all`/`dump-commits`/`enrich-commits`/`diff-enrich`/`stratify-tactics`/`group-tactics`, each taking `--profile/-p` (see `scaffold --help`)
- `uv run quali` — qualitative trajectory analysis via LLM (reads from artifacts, writes `*-quali.jsonl`)

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
