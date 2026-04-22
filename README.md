# git-history-evals

Proof-engineering evals mined from real-world formal-methods git histories. Each challenge is a theorem whose proof was completed at commit `t+1`; the eval asks a language model to reproduce the proof given the state at commit `t`, and scores it on compile success plus drift metrics against the human reference.

Two Python projects live here:

- `scaffold/` — quantitative miner + qualitative study. Walks a target repo's git history, classifies commits, and extracts per-theorem challenge slots. See `scaffold/README.md`.
- `experiments/` — the eval *runner*. Baseline (single-shot Claude) and pydantic-ai ReAct agent drivers, plus a layered Docker + tmux + `docker compose` pipeline that runs one container per fiat-crypto SHA in parallel. See `experiments/README.md`.

Target repos (git submodules under `data/`): fiat-crypto (Coq), CompCert (Coq), BRiCk (Coq), l4v (Isabelle).

## Prereqs

- `docker` + `docker compose` (v2)
- `tmux`
- `jq`
- `uv` (Python package manager)
- `ANTHROPIC_API_KEY` — set in a `.env` at the repo root

## One-time setup

```bash
git submodule update --init --recursive data/fiat-crypto
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
cd experiments && uv sync && cd ..
```

## Mainline: dockerized end-to-end run

One command builds the images, generates a per-run `compose.yml`, and spawns a detached tmux session with one window per mined fiat-crypto SHA:

```bash
cd experiments
./orchestrate/run-all.sh --mode both --max-parallel 4
```

On success it prints a block like:

```
Started session: proof-eval-<run-id>
Results dir:    experiments/results/<run-id>/
Compose file:   experiments/results/<run-id>/compose.yml
Attach with:    ./attach.sh <run-id>
Aggregate with: ./aggregate.sh <run-id>
```

Useful flags:

- `--mode {baseline|agent|both}` — which driver(s) to run inside each container (default `both`)
- `--max-parallel N` — cap on concurrent per-SHA image builds and containers (default 4)
- `--shas <sha1,sha2,...>` — restrict to an explicit SHA list; otherwise all SHAs from `meta.json` are used
- `--skip-build` — assume images are already built
- `--run-id <id>` — override the timestamp-based run id
- `--dry-run` — print the plan without executing

Attach to the running session (each SHA has its own tmux window):

```bash
./orchestrate/attach.sh <run-id>
```

Once all windows have finished, aggregate:

```bash
./orchestrate/aggregate.sh <run-id>
```

This copies the per-SHA Docker named volumes (`results-<sha-prefix>`) into `experiments/results/<run-id>/raw/`, concatenates per-mode JSONLs, invokes `summary.py`, and updates the `experiments/results/latest` symlink.

## Inspecting results

```
experiments/results/<run-id>/
├── compose.yml              # snapshot of the generated compose file
├── run.log                  # per-run controller log
├── raw/<sha-prefix>/        # one dir per SHA, copied from the named volume
│   ├── agent.jsonl
│   ├── baseline.jsonl
│   └── transcripts/<slot>_d<size>.json
├── agent.jsonl              # concatenated across SHAs
├── baseline.jsonl
├── summary.json             # per-(mode, deletion_size) + drift + Pearson r
└── summary.md               # three tables: baseline, agent, baseline-vs-agent
```

The drift columns in `summary.md` (vo_bytes, compile_time, proof_chars/lines, tactic_count, n_assumptions) answer "is the LLM more/less X than the human reference?" directly. Per-metric Pearson r vs `deletion_size` is the faithfulness check.

Re-aggregating a prior run is safe and idempotent (named volumes persist):

```bash
./orchestrate/aggregate.sh <old-run-id>
```

## Single-slot local iteration (no Docker)

For fast iteration on prompt / agent changes against a host fiat-crypto checkout:

```bash
export FIAT_CRYPTO_DIR=/abs/path/to/fiat-crypto
cd experiments
uv run eval-baseline --max-challenges 1 --skip-a
uv run eval-agent    --max-challenges 1 --skip-a
uv run python summary.py --inputs "results/**/*.jsonl" --markdown /tmp/summary.md
```

## Testing

```bash
cd experiments
uv run pytest -v                   # Python tests
bash orchestrate/test_*.sh         # bash smoke tests (no Docker required)
```

## More depth

- `CLAUDE.md` — repo-wide agent/developer context
- `experiments/README.md` — pipeline internals and layout
- `experiments/results/README.md` — per-run artifact layout and two-layer persistence
- `scaffold/README.md` — mining + qualitative study pipelines
- GitHub epic [#27](https://github.com/for-all-dev/git-history-evals/issues/27) — history of how the `experiments/` pipeline was built
