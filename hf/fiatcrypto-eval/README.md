---
license: mit
language:
- en
pretty_name: fiat-crypto Proof-Engineering Eval (git-history-mined)
tags:
- formal-verification
- theorem-proving
- proof-synthesis
- coq
- fiat-crypto
- cryptography
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

# fiat-crypto Proof-Engineering Eval

Proof-synthesis challenges **mined from the git history** of
[mit-plv/fiat-crypto](https://github.com/mit-plv/fiat-crypto), the Coq framework
that synthesises *correct-by-construction* field-arithmetic code for elliptic
curves — the verified crypto primitives behind Curve25519 and friends, shipped in
production (BoringSSL, Signal). Each challenge is a real proof-engineering edit a
human made in a single commit: the repository state *before* the commit is the
**challenge**, the state *after* is the ground-truth **solution**, and the model
must reconstruct the human's proof/spec work.

## How it was mined

One versioned cut produced by the
[git-history-evals](https://github.com/for-all-dev/git-history-evals) scaffold — a
profile-driven miner that walks a proof repo's history and extracts
`(commit, file)` challenges wherever a commit's diff to a Coq (`.v`) file fills a
"hole" (adds or changes a proof or specification).

- **Source repo:** `mit-plv/fiat-crypto` (Coq)
- **Dataset version:** `fiat-crypto-curated-v3-dc0abd63`
- **Miner:** agent-synthesised profile (`anthropic:claude-sonnet-4-6`)
- **Commits mined:** 4,798 (the SHA list in the manifest is the reproducibility source of truth)
- **Proof assistant:** Coq

### Curation

Every mined candidate passes an LLM curation gate (tiered cheap→decision models)
that asks *"is this a substantive proof-engineering edit, or noise?"* — rejecting
whitespace, comment, import-only, and copyright-header diffs while keeping
tactic-body edits, definition/lemma/theorem changes, and spec changes. **Only
rows that passed curation (`curation_verdict == "accept"`) are included here.**
The verdict, deciding model, and rationale are retained on each row for auditing.

## Statistics

| | |
|---|---|
| Challenges (rows) | **11,131** |
| Source commits | 4,798 |
| Distinct files | 2,152 |
| Curation verdict | 100% `accept` |

Challenge types: `proof_add` 11,125 · `spec_change` 6.

## Row schema

One JSON object per line in `train.jsonl`:

| field | description |
|---|---|
| `task_id` | stable id, `fiat-crypto_<commit8>_<file8>` |
| `repo` | `fiat-crypto` |
| `proof_assistant` | `coq` |
| `commit_hash` | the solving commit (state *after* = ground truth) |
| `parent_hash` | the parent commit (state *before* = challenge) |
| `commit_message` | upstream commit message (context; may describe a larger multi-file change) |
| `file_path` | path of the edited file within the repo |
| `challenge_type` | `proof_add` \| `spec_change` |
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

ds = load_dataset("for-all-dev/fiat-crypto-eval", split="train")
print(ds)
ex = ds[0]
print(ex["instructions"])
print(ex["challenge_file_content"])   # give this to the model
print(ex["solution_file_content"])    # ground truth
```

Or pull the raw file directly:

```python
from huggingface_hub import hf_hub_download
path = hf_hub_download("for-all-dev/fiat-crypto-eval", "train.jsonl", repo_type="dataset")
```

### Minimal solver sketch

```python
from datasets import load_dataset

ds = load_dataset("for-all-dev/fiat-crypto-eval", split="train")

def solve(example, model):
    prompt = f"{example['instructions']}\n\n--- file ---\n{example['challenge_file_content']}"
    candidate = model.complete(prompt)          # your model here
    return candidate                            # a full proposed file

# Ground-truth scoring is exact (match against solution_file_content) or, better,
# behavioural: splice `candidate` into a fiat-crypto checkout at `parent_hash`
# and run `coqc`/`make` to see whether the proof compiles.
```

The behavioural scorer (splice → compile) is the faithful one; exact-match is a
cheap proxy. The
[`experiments/`](https://github.com/for-all-dev/git-history-evals/tree/master/experiments)
runner implements exactly this for fiat-crypto (layered Docker per-commit Coq
builds).

## Limitations

- **Heuristic mining + LLM curation.** Challenges are found by diff heuristics
  and filtered by an LLM judge; both can mislabel. Verdicts are kept on-row so
  you can re-filter.
- **Whole-file granularity.** A row is a `(commit, file)` pair; a single commit
  touching several files becomes several rows that share a `commit_message`.
- **Training-set contamination.** fiat-crypto is public OSS and likely in
  frontier-model pretraining corpora — prefer relative/ablation comparisons over
  absolute scores.
- **Pre-canonical row shape** (`schema.row_version: 0`): the `(commit, file)` +
  `holes_filled` layout predates the canonical per-theorem row; see the manifest.

## Attribution & citation

Derived from **fiat-crypto** © the fiat-crypto authors, which upstream is offered
under the **MIT**, **Apache-2.0**, and **BSD-1-Clause** licenses (your choice);
this dataset redistributes excerpts under MIT. Upstream:
[mit-plv/fiat-crypto](https://github.com/mit-plv/fiat-crypto).

Mining scaffold: [for-all-dev/git-history-evals](https://github.com/for-all-dev/git-history-evals)
(Forall R&D).

```bibtex
@misc{fiatcrypto-eval-githistory,
  title  = {fiat-crypto Proof-Engineering Eval (git-history-mined)},
  author = {Dougherty, Quinn and Hoeppner, Ella and Abid, Taiba},
  year   = {2026},
  howpublished = {\url{https://huggingface.co/datasets/for-all-dev/fiat-crypto-eval}},
  note   = {Derived from mit-plv/fiat-crypto (MIT/Apache-2.0/BSD-1-Clause)}
}
```
