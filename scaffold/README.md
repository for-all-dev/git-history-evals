# scaffold

Python tooling for mining proof engineering git histories and analyzing proof evolution.

## Setup

```
uv sync
```

Requires Python >= 3.14. An `ANTHROPIC_API_KEY` is needed for the `quali` tool and the `scaffold profile` calibration agent; put it in a `.env` at the monorepo root (one dir above `scaffold/`) — both walk up from the CWD to find it.

## Tools

### `scaffold` — quantitative mining pipeline (profile-driven)

```
uv run scaffold --help
```

The engine is **repo-agnostic**: every mining command is parameterised by a `RepoProfile` (see `src/scaffold/profile.py`) — a declarative spec of one repo's conventions (proof-file globs, hole markers, declaration patterns, commit-message signal banks, tactic vocabulary/groups). Profiles are passed with `--profile/-p`; nothing about Coq/fiat-crypto is hardcoded in the engine.

**Tier 1 — calibration (synthesise a profile from a repo):**
- `scaffold profile <repo_path> --tag <label>` — run the CodeMode calibration agent to explore a repo + its git history and emit a `RepoProfile`, then mine and write a versioned dataset bundle under `artifacts/<repo>-eval/<tag>-<hash>/` (`manifest.json` + `miner/profile.json` + `challenges.jsonl`).
- `scaffold materialize <repo_path> -p <profile.json> --tag <label> [--kind handcrafted]` — bundle an *existing* profile (e.g. a hand-authored one) into the same versioned dataset layout, without the agent.

Each dataset owns exactly one profile, at `<version>/miner/profile.json` (co-located with its manifest and challenges). `--promote` blesses a dataset by making `artifacts/<repo>-eval/profile.json` a relative **symlink** into that version's `miner/profile.json` — a pointer, not a copy — which is what `mine-all` reads. `_index.json` maps manifest hash → version path. See `../artifacts/MANIFEST_SCHEMA.md`.

**Tier 2 — deterministic mining (profile-driven, no LLM in the loop):**
- `scaffold mine <repo_path> -p <profile.json>` — mine eval challenges from a proof repo
- `scaffold mine-all` — mine every repo in `./data` that has a blessed `<repo>-eval/profile.json`
- `scaffold dump-commits <repo_path> -p <profile.json>` — export all commits to JSONL
- `scaffold enrich-commits <input.jsonl> -p <profile.json>` — add heuristic commit classes and keywords
- `scaffold diff-enrich <input.jsonl> <repo_path> -p <profile.json>` — second-pass diff-based enrichment (tactic tags, proof style, hole-removal detection)
- `scaffold stratify-tactics <input.jsonl>` — split into per-tactic subdatasets
- `scaffold group-tactics <input.jsonl> -p <profile.json>` — map tactics to behavioural groups

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

- `src/scaffold/` — the profile-driven engine:
  - `profile.py` — the `RepoProfile` contract + `CompiledProfile` (pre-compiled regexes); the central data spec the whole engine consumes.
  - `analyzers/` — one `ProfileAnalyzer(compiled)` (finds holes/declarations per the profile; replaced the former per-language Coq/Lean/Isabelle subclasses).
  - `git_walker.py`, `pattern_detector.py` — commit walking + classification/enrichment, all parameterised by a `CompiledProfile`.
  - `profiler/` — the Tier-1 calibration **agent** (`agent.py`/`tools.py`/`deps.py`/`prompts.py`/`runner.py`): a pydantic-ai + `pydantic-ai-harness` CodeMode agent that synthesises a `RepoProfile` from a repo.
  - `dataset.py` — manifest-schema dataset versioning (`build_manifest`, `materialize_dataset_version`, `_index.json` upsert); see `../artifacts/MANIFEST_SCHEMA.md`.
  - `models.py`, `output.py` — core data models + JSONL IO.
- `src/quali/` — pydantic-ai qualitative study (models, study logic, CLI)
- `src/scripts/analysis/` — proof lifecycle reporting
