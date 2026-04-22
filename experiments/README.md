# experiments/

Eval pipeline (baseline + agent) for proof-completion challenges mined from fiat-crypto's git history.

## Local dev

```
cd experiments
uv sync
uv run eval-baseline --max-challenges 1 --skip-a     # single-shot Claude baseline
uv run eval-agent    --max-challenges 1 --skip-a     # pydantic-ai ReAct agent
uv run python summary.py --inputs "results/**/*.jsonl" --markdown /tmp/summary.md
```

Both drivers write `ExperimentResult` rows (see `metrics.py`) with `mode="baseline"` / `mode="agent"` — same schema so they can be compared directly.

## End-to-end dockerized run

`./orchestrate/run-all.sh` is the one-liner entry point: it preflight-checks tools, builds `fc-base` / `fc-deps` / `fc-commit` images layered per Coq version, generates a per-run `compose.yml` via `gen-compose.py`, and spawns a detached tmux session `proof-eval-<RUN_ID>` with one window per SHA running `run-commit.sh`. Returns immediately.

```
export ANTHROPIC_API_KEY=...
./orchestrate/run-all.sh --mode both --max-parallel 4
./orchestrate/attach.sh <run_id>            # attach
./orchestrate/aggregate.sh <run_id>         # post-run: volumes → raw/ → concat → summary.md
```

Results live in docker named volumes (`results-<sha-prefix>`, source of truth) and are mirrored to `experiments/results/<run_id>/` by `aggregate.sh`. See `results/README.md`.

## Testing

```
uv run pytest -v                            # Python tests
bash orchestrate/test_*.sh                  # bash smoke tests (no docker required)
```

## Layout

- `run_experiment.py` / `run_agent_experiment.py` — top-level drivers (`eval-baseline`, `eval-agent`)
- `metrics.py` — `ExperimentResult` / `ProofMetrics` / `Summary` schemas
- `summary.py` — cross-run aggregator with per-metric drift ratios, baseline-vs-agent deltas, Pearson r faithfulness checks
- `proof_utils.py` — tactic edit-distance helpers
- `shared/` — pure helpers (splice, prompts, compile)
- `agent/` — pydantic-ai ReAct agent (`agent.py`, `tools.py`, `deps.py`, `runner.py`)
- `docker/` — layered Dockerfiles (`base`, `deps`, `commit`)
- `orchestrate/` — bash + `gen-compose.py` for tmux- and compose-driven per-SHA runs
- `tests/` + `test_summary.py` + `agent/test_*.py` + `orchestrate/test_*.sh` — full suite
- `results/` — per-run artifact mirror (see `results/README.md`)
- `admitted-proofs/` + `experiments3/` — per-slot challenge directories (fixtures)
