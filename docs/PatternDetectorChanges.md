# Pattern Detector Changes

This document tracks incremental improvements to `scaffold/src/scaffold/pattern_detector.py` and the analyzer layer (`analyzers/`). Each entry describes the motivation, what changed, and what it enables.

---

## Change 1: Commit message sampling for dynamic hole-marker discovery

**File:** `pattern_detector.py`
**Status:** In progress

### Motivation
The original `classify_commit_message` used a fixed keyword list hardcoded for Coq/Lean/Isabelle conventions. Different repos use different vocabulary: fiat-crypto says "Complete proof", l4v might say "fill sorry", CompCert might use French commit messages. The miner misses candidates it shouldn't.

### What changed
- Added `sample_commit_messages(repo_path, n)` — pulls N recent commit messages from git log
- Added `infer_proof_fill_keywords(messages, client)` — sends a sample of commit messages to Claude and asks it to identify which keywords/phrases correlate with proof-filling activity in *this specific repo*
- `classify_commit_message` now accepts an optional `extra_keywords` argument so the inferred keywords can augment the static list without replacing it

### What it enables
The miner can now adapt its commit-message filter to each repo's conventions on the fly, catching more true positives.

---

## Change 2: Hole marker discovery via file sampling

**File:** `pattern_detector.py`, `analyzers/base.py`
**Status:** Planned

### Motivation
The current analyzers have hardcoded hole markers (`Admitted`, `sorry`, `oops`). Some repos introduce project-specific placeholder tactics (e.g., `todo`, `PLACEHOLDER`, custom Ltac stubs). These are invisible to the current detector.

### What changed (planned)
- `sample_proof_files(repo_path, analyzer, n)` — fetches N proof files from HEAD and scans for uncommon identifiers in proof positions
- Feed those candidates to Claude with a prompt: "which of these look like proof placeholders in this codebase?"
- Extend the analyzer's hole_markers at runtime with discovered patterns

### What it enables
Richer hole detection across codebases that don't strictly follow standard conventions.

---

## Change 3: Exclude-path refinement via build system inspection

**File:** `pattern_detector.py`
**Status:** Planned

### Motivation
`detect_exclude_paths` only checks top-level directory names against a static list. For fiat-crypto and CompCert, generated files live in nested paths (e.g., `src/Bedrock/End2End/*/`) that cause spurious challenges from auto-generated proofs being regenerated.

### What changed (planned)
- Parse `_CoqProject` / `Makefile` to extract `-R` and `-Q` path mappings
- Cross-reference with `git ls-files --others` (untracked generated files) at HEAD to detect generated output directories
- Add those to `exclude_paths`

### What it enables
Cleaner challenge extraction — fewer noisy challenges from auto-generated or vendored proof files.

---

## Change 4: Multi-parent commit handling

**File:** `git_walker.py`
**Status:** Planned

### Motivation
`iter_commits` currently takes only the first parent for merge commits (`parts[1].split()[0]`). Merge commits in collaborative repos (especially l4v) often represent the actual proof-filling event, where a feature branch containing `sorry` stubs gets merged with completions.

### What changed (planned)
- Parse all parents from the git log format string
- For merge commits (2+ parents), optionally diff against each parent and union the results
- Add a `is_merge_commit: bool` field to `RawCommit`

### What it enables
Mining merge-based workflows, which are common in larger proof engineering teams.
