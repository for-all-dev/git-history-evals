---
license: gpl-2.0
language:
- en
pretty_name: seL4/l4v Proof-Engineering Eval (git-history-mined)
tags:
- formal-verification
- theorem-proving
- proof-synthesis
- isabelle
- sel4
- l4v
- operating-systems
- git-history
task_categories:
- text-generation
size_categories:
- 10K<n<100K
configs:
- config_name: default
  data_files:
  - split: train
    path: train.jsonl
---

# seL4 / l4v Proof-Engineering Eval

Proof-synthesis challenges **mined from the git history** of
[seL4/l4v](https://github.com/seL4/l4v) — the Isabelle/HOL specifications and
proofs of the **seL4 microkernel**, the largest machine-checked functional-
correctness and security proof in existence (the kernel C code is proved to
refine an abstract spec, with integrity/confidentiality on top). Each challenge
is a real proof-engineering edit a human made in a single commit: the repository
state *before* the commit is the **challenge**, the state *after* is the
ground-truth **solution**, and the model must reconstruct the human's work.

This is the dataset Mike Dodds gestured at with "someone should build
seL4-ablate-bench": progressively remove proof work and measure how much an agent
can put back.

## How it was mined

One versioned cut produced by the
[git-history-evals](https://github.com/for-all-dev/git-history-evals) scaffold — a
profile-driven miner that walks a proof repo's history and extracts
`(commit, file)` challenges wherever a commit's diff to an Isabelle theory
(`.thy`) fills a "hole" (adds, optimises, or changes a proof or specification).

- **Source repo:** `seL4/l4v` (Isabelle/HOL)
- **Dataset version:** `l4v-curated-v1-50b089a6`
- **Miner:** agent-synthesised profile (`anthropic:claude-sonnet-4-6`)
- **Commits mined:** 3,624 (the SHA list in the manifest is the reproducibility source of truth)
- **Proof assistant:** Isabelle

### Curation

Every mined candidate passes an LLM curation gate (tiered cheap→decision models).
For l4v the gate is path-aware: it **rejects auto-generated executable spec**
(files directly under `spec/design/**`, mechanically translated from the Haskell
kernel) and pure tool-internal SML, while keeping proof-body edits, hand-written
spec/definition changes, and proof-automation machinery (simprocs, wp/crunch rule
sets, attribute declarations). **Only rows that passed curation
(`curation_verdict == "accept"`) are included here.** The verdict, deciding
model, and rationale are retained on each row for auditing.

## Statistics

| | |
|---|---|
| Challenges (rows) | **24,391** |
| Source commits | 3,624 |
| Distinct files | 2,039 |
| Curation verdict | 100% `accept` |

Challenge types: `proof_add` 14,105 · `proof_optimise` 9,963 · `spec_change` 323.

## Specs vs. proofs (read this before using the data)

There are **two orthogonal ways** to slice this dataset, and they disagree — be
deliberate about which you use.

**1. By `challenge_type` (nature of the diff):**

| type | count | what changed |
|---|---:|---|
| `proof_add` | 14,105 | a proof body / Isar script was added or extended |
| `proof_optimise` | 9,963 | an existing proof was modified/refactored |
| `spec_change` | 323 | a *statement* / definition / specification changed |

**2. By `file_path` (layer of the codebase):**

| layer | count | what lives there |
|---|---:|---|
| `proof/**` | 19,380 | refinement, invariant, crefine, access-control, infoflow proofs |
| `lib/**` | 2,136 | reusable Isabelle libraries + proof automation (wp, crunch, monads) |
| `spec/**` | 1,582 | **hand-written** specs: abstract spec, machine model, `design/skel` |
| other (`tools/`, `sys-init/`, `camkes/`, …) | 1,293 | parsers, capDL init, component glue |

These axes are nearly independent: most edits *inside* `spec/**` files are still
classified `proof_add`/`proof_optimise` (1,573 of 1,582) because l4v spec theories
contain lemmas and proofs too; conversely most `spec_change` rows (314 of 323)
live *outside* `spec/**`, in proofs/libs where a definition or lemma statement was
edited.

**Why it matters.**

- **Proof tasks are the well-posed, gradable core.** The statement is fixed and
  Isabelle is an exact oracle: a candidate either checks or it doesn't. This is
  the "reconstruct the proof" framing. Filter to `challenge_type != "spec_change"`
  (≈24,068 rows), and optionally to `proof/**` + `lib/**` paths.
- **Spec changes are a different, harder, partly ill-posed track.** Changing a
  spec changes *what is true* and what must be proved; "correctness" is a modeling
  judgement, not a checkable fact — a different-but-equivalent spec can be fine,
  and there's no oracle for "the right spec". Treat `spec_change` rows as a
  separate study (design-decision prediction), not as pass/fail proof problems.
- **Generated spec is already excluded.** Curation drops `spec/design/**` (the
  Haskell-translated executable spec), so the spec edits that remain are genuine
  human modeling work (`spec/abstract`, `spec/machine`, `spec/design/skel`).

```python
# the "reconstruct the proof" cut
proofs = ds.filter(lambda r: r["challenge_type"] != "spec_change")
# the spec-design cut
specs  = ds.filter(lambda r: r["challenge_type"] == "spec_change")
```

## Row schema

One JSON object per line in `train.jsonl`:

| field | description |
|---|---|
| `task_id` | stable id, `l4v_<commit8>_<file8>` |
| `repo` | `l4v` |
| `proof_assistant` | `isabelle` |
| `commit_hash` | the solving commit (state *after* = ground truth) |
| `parent_hash` | the parent commit (state *before* = challenge) |
| `commit_message` | upstream commit message (context; may describe a larger multi-file change) |
| `file_path` | path of the edited theory within the repo |
| `challenge_type` | `proof_add` \| `proof_optimise` \| `spec_change` |
| `challenge_file_content` | the file **before** the edit — what the model is given |
| `solution_file_content` | the file **after** the edit — ground truth |
| `holes_filled` | structured list of the hole(s) the commit filled (JSON string) |
| `diff` | unified diff from challenge → solution |
| `instructions` | natural-language task statement for the solver |
| `curation_verdict` | `accept` (all retained rows) |
| `curation_model` | model that produced the verdict |
| `curation_rationale` | one-line justification |

See the [manifest schema](https://github.com/for-all-dev/git-history-evals/blob/master/artifacts/MANIFEST_SCHEMA.md)
for the full contract.

## Loading

```python
from datasets import load_dataset

ds = load_dataset("for-all-dev/l4v-eval", split="train")
print(ds)
ex = ds[0]
print(ex["instructions"])
print(ex["challenge_file_content"])   # give this to the model
print(ex["solution_file_content"])    # ground truth
```

Or pull the raw file directly:

```python
from huggingface_hub import hf_hub_download
path = hf_hub_download("for-all-dev/l4v-eval", "train.jsonl", repo_type="dataset")
```

### Minimal solver sketch

```python
from datasets import load_dataset

ds = load_dataset("for-all-dev/l4v-eval", split="train")

def solve(example, model):
    prompt = f"{example['instructions']}\n\n--- file ---\n{example['challenge_file_content']}"
    candidate = model.complete(prompt)          # your model here
    return candidate                            # a full proposed file

# Faithful scoring is behavioural: splice `candidate` into an l4v checkout at
# `parent_hash` and run Isabelle (`isabelle build`) over the affected session to
# see whether the theory checks. Exact-match against solution_file_content is a
# cheap proxy. NOTE: a full l4v build is heavy (hours, lots of RAM); scope to the
# affected session/image rather than rebuilding the world.
```

## Limitations

- **Heuristic mining + LLM curation.** Challenges are found by diff heuristics
  and filtered by an LLM judge; both can mislabel. Verdicts are kept on-row so
  you can re-filter.
- **Whole-file granularity.** A row is a `(commit, file)` pair; a single commit
  touching several files becomes several rows that share a `commit_message`.
- **Heavy to grade.** Isabelle/l4v builds are large and slow; behavioural scoring
  needs real compute and a pinned environment.
- **Training-set contamination.** seL4/l4v is public OSS and very likely in
  frontier-model pretraining corpora — prefer relative/ablation comparisons over
  absolute scores.
- **Pre-canonical row shape** (`schema.row_version: 0`, `assistant: isabelle`):
  the `(commit, file)` + `holes_filled` layout predates the canonical per-theorem
  row; see the manifest.

## Attribution & citation

Derived from **seL4/l4v** © seL4 Project / Proofcraft / UNSW / Data61-CSIRO and
contributors. l4v is released under standard open-source licenses — proofs about
the kernel under **GPL-2.0**, general libraries/tools under **BSD-2-Clause**;
each upstream file carries its own SPDX tag. Upstream:
[seL4/l4v](https://github.com/seL4/l4v).

Mining scaffold: [for-all-dev/git-history-evals](https://github.com/for-all-dev/git-history-evals)
(Forall R&D).

```bibtex
@misc{l4v-eval-githistory,
  title  = {seL4/l4v Proof-Engineering Eval (git-history-mined)},
  author = {Dougherty, Quinn and Hoeppner, Ella and Abid, Taiba},
  year   = {2026},
  howpublished = {\url{https://huggingface.co/datasets/for-all-dev/l4v-eval}},
  note   = {Derived from seL4/l4v (GPL-2.0 / BSD-2-Clause)}
}
```
