# Figure input data — provenance

Inputs for `comms/vericode-workshop/figures`. The three per-model aggregates
below are **relative symlinks** into `scratch-wave3/` (a gitignored scratch
tree at the repo root, not a copy) — the same pattern
`artifacts/<repo>-eval/profile.json` uses to point at a versioned bundle
without duplicating bytes. `scratch-wave3/` is untracked, so **these
symlinks dangle on a fresh clone or in a worktree that never ran the evals**;
that's expected. To regenerate the target, rerun the sample described below,
or fetch `scratch-wave3/` from wherever the run artifacts are mirrored.
`uv run figures` detects a dangling symlink and fails with a clear
"aggregate missing — run the evals or fetch results" message rather than
silently skipping a figure.

Two TSVs used by `temporal-holdout` (and, in the future, a
contamination-conditioned cut) already live on `master` under `pipeline/`
and are read in place — see below.

## The three per-model aggregates (symlinked, not copied)

| symlink | -> target | model |
|---|---|---|
| `aggregate-claude-sonnet-5.json` | `../../../scratch-wave3/paired/aggregate.json` | `claude-sonnet-5` |
| `aggregate-gpt-5.6-sol.json` | `../../../scratch-wave3/paired-openai/aggregate.json` | `openai:gpt-5.6-sol` |
| `aggregate-leanstral-1-5.json` | `../../../scratch-wave3/paired-leanstral/aggregate.json` | `mistral:labs-leanstral-1-5` |

### Blob declaration (manifest-declares-blob pattern, à la `artifacts/MANIFEST_SCHEMA.md`)

Content-addressed as of the run described below — if a regenerated
`scratch-wave3/*/aggregate.json` doesn't hash to these values, the
provenance below no longer describes what's on disk and this table should
be refreshed:

| model | sha256 | modes (n) | macro PASS [CI] |
|---|---|---|---|
| claude-sonnet-5 | `0a8137f490a6ecc7c2abe8dc8b38e75d3ad8aca9096473116501187b2ee664a` | leaves 113/103 scorable, whole 113/103 scorable | leaves 29.25% [23.58,34.91]; whole 28.30% [22.64,33.96] |
| gpt-5.6-sol | `c4eb622a723e015f9e8d9e2f0eb7134dc26a80711c4db1a737fc9b18b2dc583` | leaves 113/103 scorable, whole 113/103 scorable | leaves 49.06% [43.40,54.72]; whole 47.17% [42.45,51.89] |
| leanstral-1-5 | `80bdae2249bb0a71db84326f73f4359ea1f21e71382b9c3bb6371d900fdb8a` | leaves 113/103 scorable, whole 113/100 scorable | leaves 17.92% [13.21,22.64]; whole 24.04% [19.23,28.85] |

(macro_n_repos = 53 for every row except leanstral/whole, which is 52 — one
repo dropped entirely out of the whole-mode scorable set. Full field list:
`model`, `mode` (`leaves`|`whole` = paper's easy|hard), `max_turns`,
`total`, `outcomes` (pass/dry_run/trivial/malformed/context_exceeded/
tampered/gave_up/turn_limit/error/fail), `scorable`, `micro_pass`,
`micro_rate` (+CI), `macro_n_repos`, `macro_rate` (+CI), `per_repo`.)

Each aggregate is a 2-element JSON list, one record per mode. Schema and
bootstrap-CI methodology: `ablate-aggregate`
(`baselines/src/apply_ablate/aggregate.py`).

**Run context** (see `scratch-wave3/GRID.md` for the full writeup):
- Sample: 113 paired easy/hard challenges (shared `challenge_id`) across 53
  scoring repos, seed 42, 50-turn budget, `pipeline/eval_sample.sh` driving
  `baselines/` (`uv run ablate-baseline` under the hood) per repo, then
  `uv run ablate-aggregate` over the concatenated per-repo JSONL to produce
  each `aggregate.json`/`aggregate.md`.
- Target file mtimes at hash time (local clock, `America/New_York`): sonnet
  2026-08-21 11:42, gpt-5.6-sol 2026-08-21 16:58, leanstral-1-5 2026-08-22
  03:28.
- Repo tree when these symlinks were made: branch `dispatch/147-figures`
  off `master` at `9745ce0` (Merge PR #182, `fix/132-holdout-schema`); the
  eval runs themselves predate that merge and were driven from a
  `baselines/` checkout around `6f038da` (comms/vericode draft) onward —
  see `scratch-wave3/runlogs/run_130_openai.log` and the sibling `.sh`
  driver for the exact commands.
- Known gap: 10 pairs per mode are `malformed` from repo-environment gaps
  (lampe, lean-mlir, verity, starkware-formal-proofs, sparkle, LNSym) and
  are excluded from every denominator in these aggregates; see GRID.md
  finding #4 for the breakdown. `outcome-composition` renders `malformed`
  hatched precisely because of this.

Un-symlinked siblings of these files (`aggregate.md`, `agg_manifest.json`,
the per-repo `res_*.jsonl`, `runlogs/`) stay in `scratch-wave3/` — they're
either derivable from the linked JSON or are bulk per-challenge payloads out
of scope for these summary figures.

## TSVs read in place (not copied, not symlinked — already on `master`)

- `pipeline/temporal_holdout.tsv` — pre/post-cutoff macro PASS per
  model/mode/cutoff. Read directly by `temporal-holdout`; `figures`
  resolves the path via `pipeline/temporal_holdout.tsv` relative to the
  repo root it's run from.
- `pipeline/membership.tsv` — per-repo Software Heritage / infinigram
  contamination membership signals. Not consumed by any figure yet; kept
  available for a future contamination-conditioned cut.

## Not yet available

- `pipeline/budget_curve.tsv` — issue #131, in flight. `budget-curve` in
  `figures` checks for this file and skips with a message when it's absent;
  no data file to document here until it lands.
