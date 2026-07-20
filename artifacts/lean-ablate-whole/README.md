# Lean corollary-ablation datasets

Syntactic proof-ablation challenges mined from **50 real Lean 4 repositories**. Each record
is a `(challenge, solution)` pair: the challenge is a real source file with one lemma
**deleted** and its in-file users **holed** (`sorry`); the solution is the original file. A
solver must re-derive the deleted lemma and close the holes so the file compiles.

Every record is **compile-validated**: the challenge compiles with its holes AND the
ground-truth solution compiles hole-free, against each repo's own built `.olean` closure.

| mode | what gets holed | validated / mined |
|---|---|---|
| `corollary-leaves` | only the **leaf tactic steps** citing the deleted lemma; the rest of each user's proof survives | **21,530 / 27,131** (79%) |
| `corollary-whole` | each user's **entire proof body** — solve from the statement alone | **23,383 / 28,607** (81%) |

Flags: `--corollary-delete-lemmas{-leaves,}-all --shrink-solution-minimal --seed 42`.
Both modes pick the same corollaries and delete the same lemmas (same seed); they differ
only in how the *users* of a deleted lemma are holed. `corollary-whole` is strictly harder.

## Layout

```
lean/<mode>/<repo>/challenges.jsonl       # the validated records
lean/<mode>/<repo>/challenges.all.jsonl   # all mined records (incl. rejects)
lean/<mode>/<repo>/manifest.json          # git repo, revision, lean toolchain, flags, counts
lean/_index.json                          # all of the above in one file, with sha256 per blob
```

## What the rejects are

A mined record is dropped if the challenge does not compile (`malformed`) or the ground
truth already contained a `sorry`/axiom (`sol_bad`). Residual malformed records are genuine
ablator limits: an in-file use of the deleted lemma that the syntactic scan misses (via
`simp only`, `.mp`, dot-notation, or elaboration order), leaving a challenge that will not
elaborate.

**Caveat for anyone re-running validation:** most apparent `malformed` challenges are an
artifact of an *incomplete build*, not the ablator. `lake build` only builds a lakefile's
default target, so a `lean_lib` declared with no globs leaves sibling modules uncompiled and
every challenge importing one fails with `object file ... does not exist`. Repos with C FFI
need `-D_GNU_SOURCE` (hex-dev's FFI calls `dlsym(RTLD_DEFAULT, ...)`). Getting this wrong
understated hex-dev by 2,881 challenges (459 -> 3,340) and verity by 651. The source repo's
`flake.nix` pins both fixes.

## Record schema

One JSON object per line: `proof_assistant`, `file_path`, `challenge_file_content`,
`solution_file_content`, `solution_diff`, `deleted_lemmas`, `holes_filled`, `challenge_id`
(stable join key), per-lemma proof-complexity metrics, and `repo`/`revision` provenance.
