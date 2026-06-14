---
license: other
license_name: inria-non-commercial
license_link: https://github.com/AbsInt/CompCert/blob/master/LICENSE
language:
- en
pretty_name: CompCert Proof-Engineering Eval (git-history-mined)
tags:
- formal-verification
- theorem-proving
- proof-synthesis
- coq
- compcert
- git-history
task_categories:
- text-generation
size_categories:
- 1K<n<10K
configs:
- config_name: default
  data_files:
  - split: train
    path: train.jsonl
---

# CompCert Proof-Engineering Eval

Proof-synthesis challenges **mined from the git history** of
[AbsInt/CompCert](https://github.com/AbsInt/CompCert), the formally verified C
compiler. Each challenge is a real proof-engineering edit that a human made in a
single commit: we take the repository state *before* the commit (the
**challenge**) and treat the state *after* the commit (the **solution**) as
ground truth. The model's job is to reconstruct the proof/spec work the human
did.

> ⚠️ **License notice.** CompCert is distributed under the **INRIA
> Non-Commercial License Agreement** — a non-free license that permits
> educational, research, and evaluation use only and **prohibits commercial
> use**. This dataset redistributes excerpts of CompCert source and is therefore
> bound by the same terms. Use for research/evaluation only. See
> [the upstream LICENSE](https://github.com/AbsInt/CompCert/blob/master/LICENSE).

## How it was mined

This is one versioned cut produced by the
[git-history-evals](https://github.com/for-all-dev/git-history-evals) scaffold —
a profile-driven miner that walks a proof repo's history and extracts
`(commit, file)` challenges wherever a commit's diff to a Coq (`.v`/`.vp`) file
fills a "hole" (adds, optimises, or changes a proof or specification).

- **Source repo:** `AbsInt/CompCert` (Coq)
- **Dataset version:** `compcert-curated-v6-b8d9e1fd`
- **Commits mined:** 757 (the SHA list in the manifest is the reproducibility source of truth)
- **Proof assistant:** Coq

### Curation

Every mined candidate is passed through an LLM curation gate (tiered
cheap→decision models) that asks *"is this a substantive proof-engineering edit,
or noise?"* — rejecting whitespace, comment, import-only, and copyright-header
diffs while keeping tactic-body edits (including `omega`→`lia` style
modernisations), definition/lemma/theorem changes, and spec changes. **Only rows
that passed curation (`curation_verdict == "accept"`) are included here.** The
verdict, deciding model, and rationale are retained on each row for auditing.

## Statistics

| | |
|---|---|
| Challenges (rows) | **4,194** |
| Source commits | 757 |
| Distinct files | 379 |
| Curation verdict | 100% `accept` |

Challenge types: `proof_add` 3,201 · `proof_optimise` 793 · `spec_change` 200.

## Row schema

One JSON object per line in `train.jsonl`:

| field | description |
|---|---|
| `task_id` | stable id, `compcert_<commit8>_<file8>` |
| `repo` | `compcert` |
| `proof_assistant` | `coq` |
| `commit_hash` | the solving commit (state *after* = ground truth) |
| `parent_hash` | the parent commit (state *before* = challenge) |
| `commit_message` | upstream commit message (context; may describe a larger multi-file change) |
| `file_path` | path of the edited file within the repo |
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

ds = load_dataset("for-all-dev/CompCert-eval", split="train")
print(ds)
ex = ds[0]
print(ex["instructions"])
print(ex["challenge_file_content"])   # give this to the model
print(ex["solution_file_content"])    # ground truth
```

Or pull the raw file directly:

```python
from huggingface_hub import hf_hub_download
path = hf_hub_download("for-all-dev/CompCert-eval", "train.jsonl", repo_type="dataset")
```

### Minimal solver sketch

```python
from datasets import load_dataset

ds = load_dataset("for-all-dev/CompCert-eval", split="train")

def solve(example, model):
    prompt = f"{example['instructions']}\n\n--- file ---\n{example['challenge_file_content']}"
    candidate = model.complete(prompt)          # your model here
    return candidate                            # a full proposed file

# Ground-truth scoring is exact (does it match solution_file_content?) or,
# better, behavioural: splice `candidate` into a CompCert checkout at
# `parent_hash` and run `coqc`/`make` to see whether the proof compiles.
```

The behavioural scorer (splice → compile) is the faithful one; exact-match is a
cheap proxy. See the
[`experiments/`](https://github.com/for-all-dev/git-history-evals/tree/master/experiments)
runner for a worked per-commit Coq harness.

## Limitations

- **Heuristic mining + LLM curation.** Challenges are found by diff heuristics
  and filtered by an LLM judge; both can mislabel. Verdicts are kept on-row so
  you can re-filter.
- **Whole-file granularity.** A row is a `(commit, file)` pair; a single commit
  touching several files becomes several rows that share a `commit_message`, so
  the message may overstate any one row.
- **Training-set contamination.** CompCert is long-standing public OSS and is
  almost certainly in frontier-model pretraining corpora — treat absolute scores
  with suspicion and prefer relative/ablation comparisons.
- **Pre-canonical row shape** (`schema.row_version: 0`): the `(commit, file)` +
  `holes_filled` layout predates the canonical per-theorem row; see the manifest.

## Attribution & citation

This dataset is derived from **CompCert** © INRIA and AbsInt Angewandte
Informatik GmbH, redistributed under the INRIA Non-Commercial License for
research/evaluation use. Upstream:
[AbsInt/CompCert](https://github.com/AbsInt/CompCert).

Mining scaffold: [for-all-dev/git-history-evals](https://github.com/for-all-dev/git-history-evals)
(Forall R&D).

```bibtex
@misc{compcert-eval-githistory,
  title  = {CompCert Proof-Engineering Eval (git-history-mined)},
  author = {Dougherty, Quinn and Hoeppner, Ella and Abid, Taiba},
  year   = {2026},
  howpublished = {\url{https://huggingface.co/datasets/for-all-dev/CompCert-eval}},
  note   = {Derived from AbsInt/CompCert under the INRIA Non-Commercial License}
}
```
