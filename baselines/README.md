# apply-ablate

**Materialise an ablation challenge onto disk and verify it builds.** The four
ablators (`rocq-ablator`, `isabelle-ablator/{rust,scala}`, `lean-ablator`) each emit a
JSONL where every row is a self-contained **(challenge, solution)** pair:
`challenge_file_content` is the full ablated (holed) file text, and `solution_diff`
recovers the original. `apply-ablate` consumes one such row and writes the ablated file
into a copy of the source repo, optionally pre-building per-prover dependencies and
checking that the result compiles.

This is the prover-agnostic substrate for the upcoming pydantic-ai proving loop (where
a model repeatedly edits the holed file and recompiles). That loop is the next step and
is **not** in this package yet.

## Quickstart: load from HuggingFace + solve + score

`quickstart.py` is the runnable "download the data and make a solver" demo (issue
#141): `load_dataset("for-all-dev/ablation-eval", split="easy")` -> filter to one
repo via `manifest.repo` -> splice + solve + real-compile score via the existing
`ablate-baseline` driver (`--repo` slicing). See its module docstring for full
prerequisites (a **built** repo checkout + toolchain + `ANTHROPIC_API_KEY`) — a
`--dry-run` needs none of those except the checkout. For a small demo subset of repos,
`pipeline/fetch_closure.sh <repo>` downloads a prebuilt `.lake` closure instead of building
one yourself (`pipeline/README.md`'s "Prebuilt closures" section, #143).

```bash
uv run python quickstart.py --list-repos
uv run python quickstart.py --repo <name> --src ../data/lean/<name> --dry-run
uv run python quickstart.py --repo <name> --src ../data/lean/<name> --limit 3
```

## Usage

```bash
apply-ablate JSONL INDEX SRC DST [--prepare] [--check] [--full-check] \
                                 [--overwrite] [--solution] [--timeout N]
```

- **JSONL** — an ablator dataset file; **INDEX** — 0-based record index.
- **SRC** — a pristine checkout of the source repo; **DST** — the working copy to create.

Pipeline (each flag adds a stage; default = apply only):

| stage | what it does |
|-------|--------------|
| `--prepare` | build deps in **SRC** in place (Lean `lake build`, Coq `make`/`dune`, Isabelle `isabelle build -b HOL`) so the copy inherits warm artifacts |
| *(always)* | copy `SRC`→`DST` (skipping `.git`), write the challenge at `DST/<file_path>` |
| `--check` | holes-allowed compile of the applied challenge (Isabelle `quick_and_dirty`, Lean `sorry`, Coq `Admitted`) |
| `--full-check` | `--check`, then recover the original via `solution_diff` and compile it too; `DST` is left in challenge state |
| `--solution` | write the recovered original instead of the challenge (produce a ground-truth tree) |

The prover backend is selected automatically from the record's `proof_assistant`
(`coq` / `isabelle` / `lean`). Exit code is non-zero if any requested check fails.

### Example

```bash
apply-ablate data.jsonl 0 ./src ./work --prepare --full-check
```

## Layout

- `src/apply_ablate/record.py` — `AblationRecord` + `load_record`
- `src/apply_ablate/diff.py` — `apply(challenge, diff)` (the shared unified-diff dialect)
- `src/apply_ablate/apply.py` — copy SRC→DST, resolve `file_path`, write content
- `src/apply_ablate/provers/` — `coq.py` / `isabelle.py` / `lean.py` + registry
- `src/apply_ablate/cli.py` — the `apply-ablate` CLI
- `tests/` — fast unit tests (no toolchain) + opt-in `integration/` (real provers via nix)

## Development

```bash
uv run ruff format && uv run ruff check --fix && uv run ty check && uv run pytest
```

Integration tests are opt-in: for each prover they run the real ablator on a
synthetic project, then `apply-ablate --prepare --full-check`, asserting the holed
challenge builds and the recovered solution builds clean. They need the prover
toolchains, which each ablator's nix dev shell provides (the script enters them
automatically):

```bash
tests/integration/run.sh            # all provers (coq, isabelle, lean)
tests/integration/run.sh lean       # just one
```
