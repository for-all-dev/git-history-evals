# scaffold

Python tooling for mining proof engineering git histories and analyzing proof evolution.

## Setup

```
uv sync
```

Requires Python >= 3.14. An `ANTHROPIC_API_KEY` in a `.env` file at the repo root is needed for the `quali` tool.

## Tools

### `scaffold` — quantitative mining pipeline

```
uv run scaffold --help
```

Key commands:
- `scaffold mine <repo_path>` — mine eval challenges from a proof repo
- `scaffold dump-commits <repo_path>` — export all commits to JSONL
- `scaffold enrich-commits <input.jsonl>` — add heuristic commit classes and keywords
- `scaffold diff-enrich <input.jsonl> <repo_path>` — second-pass diff-based enrichment (tactic tags, proof style, sorry detection)
- `scaffold stratify-tactics <input.jsonl>` — split into per-tactic subdatasets
- `scaffold group-tactics <input.jsonl>` — map tactics to behavioural groups

### `quali` — qualitative trajectory analysis

```
uv run quali --help
```

Uses pydantic-ai to produce structured observations and interpretive narratives for per-theorem proof evolution trajectories. Reads from the lifecycle and grouped-commit artifacts produced by `scaffold`.

```
uv run quali -n 10 --min-commits 3 \
  -l ../artifacts/fiat-crypto-lifecycle.jsonl \
  -g ../artifacts/fiat-crypto-commits-coq-grouped.jsonl
```

Output: `artifacts/fiat-crypto-quali.jsonl` (one JSON object per analyzed theorem).

## Packages

- `src/scaffold/` — git walker, pattern detector, commit classifier, analyzers (Coq, Lean 4, Isabelle), output utilities
- `src/quali/` — pydantic-ai qualitative study (models, study logic, CLI)
- `src/scripts/analysis/` — proof lifecycle reporting
