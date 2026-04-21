# experiments/

Eval pipeline (baseline + agent) for proof-completion challenges.

## Local dev

cd experiments
uv sync
uv run eval-baseline --max-challenges 1 --skip-a

## Testing

uv run pytest -v

## Layout

- shared/     — pure helpers (splice, prompts, compile)
- agent/      — pydantic-ai ReAct agent (tools, deps, runner)
- docker/     — layered Dockerfiles (base, deps, per-commit)
- orchestrate/ — bash + docker-compose orchestration for tmux runs
- results/    — per-run artifact mirror (see results/README.md)
