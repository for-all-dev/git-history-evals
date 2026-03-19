# Pattern Detector Changes

Running log of improvements to `scaffold/src/scaffold/pattern_detector.py`.
Each entry documents what changed, why, and what edge cases remain.

---

## v0 — Original (baseline)

**Location:** `classify_commit_message(message, extra_keywords)`

**Approach:** Single-string keyword matching on the subject line only. Two buckets:
`proof_fill` or `other`, with weak `refactor` / `fix` passthrough.

**Problems:**
- Only 4 classes — too coarse for proof-relevance analysis.
- Subject-only — ignores commit body, file stats, insertion/deletion counts.
- `proof_fill` conflated proof completion with proof addition and spec changes.
- No keyword extraction for body-free commits.

---

## v1 — New taxonomy + heuristic classifier (2026-03-19)

### What changed

**New `CommitClass` enum** (8 classes):

| Class | Meaning |
|---|---|
| `proof_complete` | A `sorry`/`Admitted`/`oops` was fully removed (goal closed) |
| `proof_new` | A new lemma/theorem was added together with a proof |
| `proof_add` | Proof content added or extended but goals may still be open |
| `spec_change` | The *statement* of a theorem changed (affects provability) |
| `infra` | Dependency bumps, CI, build system noise |
| `refactor` | Reorganisation, rename, move — no proof content change |
| `fix` | Non-proof bug fix |
| `other` | Catch-all |

**`proof_add` motivation:** There is a meaningful difference between a commit that
*closes* a sorry and one that adds 50 lines of tactics but still leaves goals open.
Both are proof-relevant, but for eval purposes `proof_complete` commits are the
ground-truth signal (the "solution" exists in the next commit), while `proof_add`
commits are valuable training signal showing incremental proof development.

**New `classify_commit(record: CommitRecord)` function:**
- Priority-ordered rule chain (see docstring for order).
- Uses full record: subject + body + `touches_proof_files` + insertion/deletion counts.
- Structural heuristic: body-free commits that touch `.v` files with net deletions
  and proof-context in the subject are classified `proof_complete` (a sorry was
  likely removed without an explanatory message).

**New `extract_keywords(subject, body)` function:**
- Regex vocabulary of ~60 Coq tactics, proof-assistant keywords, and
  cryptographic domain terms (montgomery, weierstrass, p256, x25519, …).
- Always runs on subject; runs on body when present.
- Stored in `CommitRecord.keywords` for downstream retrieval.

**New `enrich_record(record)` function:**
- Returns a copy of the record with `commit_class`, `keywords`, and
  `class_confidence = "heuristic"` populated.
- Pure Python, no git calls — cheap to run over the full 8k-record dataset.

### Priority chain rationale

1. **infra first** — dependency bump commits are almost never proof-relevant.
   Checked on *subject only* to avoid false positives from merge commit bodies
   that contain sub-messages like `"* bump dependency X"`.

2. **proof_complete before proof_new/proof_add** — completing a proof is the
   most valuable signal; we want it classified correctly even when the subject
   also contains words that would match `proof_new`.

3. **spec_change before proof_new** — changing a theorem statement is logically
   prior to proving it; these commits break existing proofs and start new ones.

4. **proof_add as the final .v-file fallback** — any commit that touches `.v`
   files and matches nothing above is labelled `proof_add` rather than `other`,
   because even unclassified proof-file edits are more useful to keep than to
   discard.

### Known remaining issues

1. **Compat-removal misclassified as `proof_add`.**
   Commits like `"Remove 8.12 compat from String.v"` touch `.v` files and fall
   through to `proof_add`. They are really `refactor`. Fix: add `"compat"`,
   `"compatibility"`, `"8\.\d+"` version patterns to `_REFACTOR_SIGNALS` with
   the guard that the subject has no proof context.

2. **`proof_add` is over-broad.**
   Any `.v`-touching commit without a match lands here. This will over-count
   proof_add vs. refactor in practice. Plan: after running on the full dataset,
   sample 50–100 `proof_add` records manually and tighten the boundary.

3. **`spec_change` recall is low.**
   The keyword bank catches explicit phrasing (`"generalize"`, `"strengthen"`)
   but misses implicit spec changes like `"relax precondition of foo_lemma"`.
   Planned fix: LLM labelling pass on a random sample of `proof_add` to
   surface misclassified `spec_change` cases.

4. **No body-based structural signals yet for `proof_new`.**
   If a commit adds a new `.v` file (detected via `changed_files`) that didn't
   exist in the parent, it is almost certainly `proof_new`. Plan: add a
   new-file heuristic using `git diff --diff-filter=A`.

---

---

## v2 — Diff-based reclassification + tactic stratification (2026-03-19)

### What changed

**New `CommitClass` value: `proof_optimise`**
A commit where the proof content shrank (net deleted lines in `.v` files, no
sorry removed). Distinct from `proof_add` because the developer is making the
proof *shorter or cleaner*, not extending it. Semantically closer to refactor
but entirely within proof content — hence its own class.

**New `CommitRecord` fields:**
| Field | Type | Source |
|---|---|---|
| `diff_sorry_removed` | bool | git diff — holes net-removed |
| `diff_net_proof_lines` | int | added - removed lines in `.v` diffs |
| `tactic_tags` | list[str] | tactics found in `+` lines of `.v` diffs |
| `proof_style` | list[str] | `tactic_mode / term_mode / ssreflect / mixed` |

**New `enrich_record_with_diff(record, repo_path)` function:**
Runs `git diff parent child -- *.v` for every commit that touches `.v` files.
Parses added (`+`) and removed (`-`) lines and applies:
1. `sorry_removed = True` if hole keywords net-removed → `proof_complete`
2. `net_proof_lines < 0` → `proof_optimise`
3. `net_proof_lines >= 0` and no sorry removed → `proof_add`

`proof_new` and `spec_change` from the message pass are preserved — the diff
pass cannot detect new declarations or statement changes without a full parser.

**Tactic extraction from diff lines (`_TACTIC_RE`):**
~50-pattern regex that matches tactic names at tactic positions (start of line,
after `;`, `|`, `{`, `(`). Covers: core Coq, ssreflect, Ltac2, arithmetic
solvers (omega/lia/ring/field/norm_num), rewriting family, automation.

**`rapply` and the vocabulary gap:**
The message-level `_PROOF_TERMS` vocabulary was expanded to ~100 terms
including ssreflect tactics and modern Coq (cbn, norm_cast, push_cast, zify,
norm_num, setoid_rewrite, etc.) and the full fiat-crypto/bedrock2 domain.
The diff-level `_TACTIC_RE` is even broader: it fires on actual code lines
so it catches tactics regardless of whether the commit message mentions them.

**`fix` and `refactor` on `.v` files collapsed into `proof_add`:**
Any structural change to proof files is proof-relevant. `fix` and `refactor`
are only retained for commits that do NOT touch `.v` files.

**New CLI commands:**
- `scaffold diff-enrich <labeled.jsonl> <repo_path>` — second-pass diff
  enrichment, parallelised with 8 threads
- `scaffold stratify-tactics <diff-enriched.jsonl>` — splits `proof_add`
  records into per-tactic JSONL files (`tactic-rewrite.jsonl`, etc.)

### New classification pipeline (two passes)

```
git log --numstat         →  CommitRecord (metadata + file stats)
      ↓
enrich_record()           →  message-heuristic class + keywords
      ↓
enrich_record_with_diff() →  diff-based class + tactic_tags + proof_style
      ↓
stratify-tactics          →  per-tactic subdataset files
```

### Known remaining issues

1. **`proof_new` recall still message-dependent.**
   If a commit adds a new `.v` file (`diff-filter=A`) it is almost certainly
   `proof_new`, but this is not yet detected. Plan: add new-file heuristic.

2. **Tactic regex can over-fire on comments.**
   Lines like `(* apply foo *)` will match. Fix: strip Coq comments before
   applying `_TACTIC_RE`.

3. **`proof_optimise` may include compat-removal.**
   "Remove 8.12 compat from String.v" deletes lines → `proof_optimise`.
   This is arguably `refactor`. Distinguishing requires knowing whether the
   removed lines contained tactic/proof content or just compatibility shims.

---

## Planned (v3)

- [ ] Add new-file heuristic for `proof_new` (diff-filter=A on .v files).
- [ ] Tighten `refactor` vs `proof_add` boundary for compat-removal commits.
- [ ] LLM labelling pass (`class_confidence = "llm"`) on commits with body text.
- [ ] Sample 100 records per class and compute precision by hand to set a baseline.