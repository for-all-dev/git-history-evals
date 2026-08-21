---
license: other
license_name: mixed-corpus-see-license-section
language:
- en
pretty_name: Ablation Eval (Lean proof-engineering corpus)
tags:
- formal-verification
- theorem-proving
- proof-synthesis
- lean
- lean4
- ablation
task_categories:
- text-generation
size_categories:
- 10K<n<100K
configs:
- config_name: default
  data_files:
  - split: easy
    path: easy.jsonl
  - split: hard
    path: hard.jsonl
---

# Ablation Eval

Proof-reconstruction challenges **syntactically ablated** from 57 Lean 4 proof-engineering
repositories (formal-verification libraries, provers, and specs — see the corpus table below).
Unlike a git-history-mined eval, a challenge here is not "reconstruct the commit that came
next" — it is *"delete this lemma and hole its in-file users; put it back so the file compiles
again."* The deletion is chosen and applied by a deterministic ablator, not by waiting for a
human to happen to touch that lemma, so the corpus can be as large and as evenly distributed
across repos as the source material allows.

- **Repos in the corpus:** 57 (Lean 4)
- **Total rows:** 21,692 (both splits combined)
- **Mining tool:** the four ablators in
  [for-all-dev/git-history-evals](https://github.com/for-all-dev/git-history-evals)
  (`lean-ablator`, plus `rocq-ablator` / `isabelle-ablator` for the sibling Coq/Isabelle
  corpora, which are published as separate datasets)
- **Corpus + toolchain pins:** `pipeline/repos.tsv` in the scaffold repo (url + revision per
  repo) — every row's embedded `manifest` traces back to an exact upstream commit

## What a challenge is

Pick a theorem (the **corollary**), take its transitive in-file dependency closure, delete one
lemma from that closure, and hole the proofs that used it. The solver must re-derive the
deleted lemma and close the holes so the file compiles again. The file is sliced down to the
corollary's dependency closure first, so the context stays small.

**This is not "reconstruct one commit's diff."** The ablation is entirely synthetic and
repeatable: the same (repo, corollary, deleted lemma) triple can be re-ablated at will, which
is also why this corpus can be orders of magnitude larger than a git-history-mined one (a git
history only has as many usable commits as it has).

## Split semantics: `easy` and `hard` are two HOLING STRATEGIES, not two difficulty tiers

**This is the single most important thing to get right about this dataset and the most common
way to misread it.** `easy` and `hard` do not partition the corpus into "easier problems" and
"harder problems" drawn from different pools. They are the **same set of (repo, corollary,
deleted-lemma) selections**, ablated two different ways:

| split | ablator mode | flag | what gets holed |
|---|---|---|---|
| `easy` | `corollary-leaves` | `--corollary-delete-lemmas-leaves-all` | only the **leaf tactic steps** that cited the deleted lemma — the rest of each user's proof survives untouched |
| `hard` | `corollary-whole` | `--corollary-delete-lemmas-all` | each user's **entire proof body** is holed — the solver must re-derive it from the statement alone |

A row's `challenge_id` is a hash of the *(repo, corollary, deleted lemma)* selection, not of
the holing strategy — so **a row in `easy` and the row in `hard` that share a `challenge_id`
are the matched pair for the same underlying deletion**, holed two different ways. Compare
outcomes on matched `challenge_id`s across splits to measure how much of the difficulty comes
from "know what the missing lemma should say" (present in both) versus "reconstruct the whole
downstream proof, not just the citing step" (`hard` only). Do **not** treat `easy` PASS rate
and `hard` PASS rate as scores on different populations of problems — join on `challenge_id`
first if you want a paired comparison, or report both splits' aggregate rates as what they are:
the same selections stressed two different ways.

Not every `challenge_id` necessarily has a row in both splits — a mode-specific dedup on
(challenge, solution) *text* can drop a row from one split but not the other (see `pipeline/README.md`'s
mining notes). Absence of a partner row is not itself informative about difficulty.

## Row schema

One JSON object per line in `easy.jsonl` / `hard.jsonl`:

| field | description |
|---|---|
| `proof_assistant` | `lean` for every row in this dataset |
| `file_path` | path of the ablated file within its source repo |
| `challenge_file_content` | the file **with holes** — what the model is given |
| `solution_file_content` | the file with holes filled — ground truth |
| `holes_filled` | list of `{theorem_name}` — the in-file users the ablator holed |
| `deleted_lemmas` | list of `{name, text}` — the lemma(s) the ablator removed entirely; `text` is the crux the model must re-derive |
| `task_id` | informational id |
| `challenge_id` | **stable id shared by a row's `easy`/`hard` matched pair** (see above); `None` on legacy pre-enrichment rows |
| `theory` / `session` | proof-assistant-specific grouping (may be null) |
| `manifest` | the source repo's `manifest.json` embedded verbatim — carries `manifest.repo` (the name to filter on with `--repo`) and `manifest.revision` (the pinned upstream commit this row was mined at) |

See `apply_ablate.record.AblationRecord` in
[baselines/src/apply_ablate/record.py](https://github.com/for-all-dev/git-history-evals/blob/master/baselines/src/apply_ablate/record.py)
for the authoritative pydantic model this file validates against, and
`artifacts/MANIFEST_SCHEMA.md` for the manifest contract.

## Outcome taxonomy and the scorable denominator

Scoring a challenge means splicing it into a **built** checkout of its source repo at the
pinned `manifest.revision`, handing it to a model, and recompiling. The harness
(`ablate-baseline` / `apply_ablate.baseline.run`) classifies every row into exactly one of:

| outcome | meaning | counts toward PASS rate? |
|---|---|---|
| `pass` (`succeeded`) | model's edit compiles hole-free and preserves every holed theorem | numerator |
| `trivial` | empty diff — the ablator found nothing to hole for this row; excluded, never reaches a real baseline | excluded from denominator |
| `malformed` | the **challenge itself** does not compile even before the model touches it — almost always a build-environment problem, not a bad ablation (see Known limitations) | excluded from denominator |
| `context_exceeded` | the challenge alone exceeds the model's context window — the provider rejected the prompt outright, so the model never saw the problem | excluded from denominator |
| `tampered` | compiled, but the model deleted or weakened a holed theorem's statement instead of proving it | in denominator, not in numerator |
| `gave_up` | model explicitly gave up before running out of turns | in denominator, not in numerator |
| `turn_limit` | model ran out of its request budget without a compiling result | in denominator, not in numerator |
| `error` | harness-side failure unrelated to the model (e.g. an unsupported repo layout) | in denominator, not in numerator |
| `fail` | compiled with remaining holes / didn't compile, none of the above | in denominator, not in numerator |

**The comparable statistic is PASS **rate over the scorable denominator**:**

```
scorable = total - malformed - trivial - context_exceeded
PASS %   = 100 * passed / scorable
```

`malformed`, `trivial`, and `context_exceeded` are excluded because each one is a property of
the (challenge, environment) or (challenge, model) pair, not a wrong answer from the model —
scoring them 0 blames the solver for something outside its control. Report `scorable` alongside
any PASS rate so a third party can tell how much of the split you actually threw at the model
(`baseline.run`'s own summary output prints this breakdown for you; do not recompute it by hand).

## Known limitations

- **Scoring requires a BUILT repo checkout, not just a clone.** `challenge_file_content` alone
  is not enough to compile — you need the repo's full dependency tree built (`lake build` for
  Lean) at the pinned `manifest.revision`. An unbuilt or partially-built checkout reports every
  row as `malformed`, which reads as "the model is bad" when it is really "the checkout is
  cold." See the scaffold repo's `pipeline/README.md` ("Validation is a build problem, not an
  ablation problem") — three build-environment bugs there cost ~3,700 valid challenges before
  being found (one repo alone went from 12% to 87% non-malformed with zero ablator change).
- **~10% of rows exceed a 262k-token context even after minimal slicing.** These are marked
  `context_exceeded` and excluded from the scorable denominator (see above) — they are not
  failures, but they also mean the effective sample size for a large-context evaluation is
  smaller than the raw row count.
- **`easy`/`hard` are holing strategies, not difficulty tiers** — see the Split semantics
  section. Reporting a single "easy vs hard" PASS-rate delta without joining on `challenge_id`
  conflates two different things.
- **Training-set contamination.** Every repo in this corpus is public OSS and some are likely
  in frontier-model pretraining corpora. The ablation itself (which lemma is deleted, how the
  file is sliced) is synthetic and not literally present upstream, but the surrounding file text
  is. Prefer relative/ablation comparisons (e.g. `easy` vs `hard` on matched `challenge_id`s)
  over absolute PASS rates as a contamination-robustness measure.
- **Heuristic ablation.** The ablator's choice of "eligible" lemma and closure computation is a
  static-analysis heuristic over the Lean source; it can occasionally hole or fail to hole
  something a human reviewer would call wrong. `holes_filled` / `deleted_lemmas` are kept
  on-row so a consumer can audit or re-filter.

## Loading

```python
from datasets import load_dataset

ds = load_dataset("for-all-dev/ablation-eval", split="easy")   # or split="hard"
print(ds)
ex = ds[0]
print(ex["manifest"]["repo"], ex["challenge_id"])
print(ex["challenge_file_content"])   # give this to the model
print(ex["solution_file_content"])    # ground truth
```

Filter to one repo (every row carries the source repo's manifest under `manifest`):

```python
rows = [r for r in ds if r["manifest"]["repo"] == "lean-zip"]
```

## Running a baseline

The dataset multiplexes 57 repos into each split, so scoring needs the matching repo checkout
— select one repo's rows before pointing a real checkout at them.

Prerequisites: a **built** checkout of the target repo (see Known limitations above), the
matching toolchain on `PATH` (`elan`/`lake` for Lean), and `ANTHROPIC_API_KEY` set (skip this
last one only for `--dry-run`, which splices and pre-flight-compiles every row without ever
calling a model).

```bash
git clone https://github.com/for-all-dev/git-history-evals
cd git-history-evals/baselines
uv sync

# see every manifest.repo value present in a split
uv run python quickstart.py --list-repos

# splice + preflight-compile only (no model call, no API key needed) — use this
# first to confirm the checkout is actually built before spending a model budget
uv run python quickstart.py --repo <name> --src ../data/lean/<name> --dry-run

# run the model
uv run python quickstart.py --repo <name> --src ../data/lean/<name> \
    --model anthropic:claude-sonnet-4-6 --limit 3
```

`quickstart.py` (added in
[baselines/quickstart.py](https://github.com/for-all-dev/git-history-evals/blob/master/baselines/quickstart.py))
is a thin wrapper: it loads the split, filters to `--repo`, and hands the filtered rows to the
same `apply_ablate.baseline.run` driver documented below — it does not reimplement any of the
splice/solve/score logic.

For direct control (e.g. scripting multiple repos, or from a checkout that already has
`baselines/` set up) the underlying CLI takes the same `--repo` flag directly against a raw
JSONL file:

```bash
# from ./baselines/, inside the target repo's toolchain shell
uv run ablate-baseline easy.jsonl ../data/lean/<name> \
    --repo <name> \
    --model anthropic:claude-sonnet-4-6 \
    --limit 10 \
    --max-turns 30 \
    --timeout 600 \
    --out results.jsonl
```

`--repo <name>` selects only the rows whose `manifest.repo` matches `<name>` — required
whenever the input JSONL (like `easy.jsonl` / `hard.jsonl` here) mixes rows from more than one
repo; omitting it on a mixed file is a hard error naming the repos present, not a run against
the wrong checkout. Add `--dry-run` to splice + preflight-compile without calling a model.

Full flag reference: `uv run ablate-baseline --help`, or
[baselines/src/apply_ablate/baseline.py](https://github.com/for-all-dev/git-history-evals/blob/master/baselines/src/apply_ablate/baseline.py).

## Attribution & citation

This is a **mixed-license corpus of 57 upstream repositories** — no single SPDX license id is
accurate for the aggregate, hence `license: other` above. The full per-repo license survey,
performed at each repo's **pinned mining revision** (not its current default branch — licenses
change over time), is the source of truth:
[`pipeline/LICENSE_SURVEY.md`](https://github.com/for-all-dev/git-history-evals/blob/master/pipeline/LICENSE_SURVEY.md)
(human-readable table + methodology) and
[`pipeline/licenses.tsv`](https://github.com/for-all-dev/git-history-evals/blob/master/pipeline/licenses.tsv)
(machine-readable, one row per repo: name, SPDX id, license file URL at the pinned commit).

Most of the corpus is Apache-2.0 or MIT, with single repos under BSD-3-Clause, LGPL-2.1,
LGPL-3.0, and Zlib. **5 of 57 repos (8.8%) are flagged** and should be checked before relying
on this dataset for anything license-sensitive:

- `Clear` (NethermindEth/Clear) — a custom non-commercial-only license (research/education/
  nonprofit use only; commercial use, resale, and third-party verification-as-a-service are
  explicitly prohibited).
- `starkware-formal-proofs`, `verified-consensus`, `posix-parsing`, `posix-submatching` — no
  LICENSE-like file found at the pinned revision (confirmed by a full tree listing, not just
  candidate-path misses); default copyright applies absent an explicit grant.

See `pipeline/LICENSE_SURVEY.md`'s "Flagged repos" section for per-repo recommendations. Each
upstream repository retains its own copyright; this dataset redistributes excerpts (ablated
files derived from the pinned revision) under the terms above.

Mining scaffold:
[for-all-dev/git-history-evals](https://github.com/for-all-dev/git-history-evals) (Forall R&D).

```bibtex
@misc{ablation-eval,
  title  = {Ablation Eval: A Lean 4 Proof-Reconstruction Corpus via Synthetic Ablation},
  author = {Dougherty, Quinn and Hoeppner, Ella and Abid, Taiba},
  year   = {2026},
  howpublished = {\url{https://huggingface.co/datasets/for-all-dev/ablation-eval}},
  note   = {Mixed-license corpus of 57 upstream Lean repositories; see the dataset card's
            Attribution section and pipeline/LICENSE_SURVEY.md for per-repo terms.}
}
```
