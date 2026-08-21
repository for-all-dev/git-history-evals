# The ablation pipeline

Mine → build → validate → publish → evaluate, for corollary-ablation eval datasets.

```bash
nix develop                                  # wrapped cc, elan/lake, ablate-baseline, s3cmd
bash pipeline/clone_repos.sh lean            # fetch the source repos at their pinned revisions
bash pipeline/run.sh all <scratch>           # mine -> validate -> index -> eval (both modes)
```

Or one stage at a time:

| command | what |
|---|---|
| `run.sh mine <scratch> [leaves\|whole]` | ablate every repo (syntactic — needs no build) |
| `run.sh validate <scratch> [leaves\|whole]` | really compile challenge + ground truth; write `artifacts/` |
| `run.sh index <scratch>` | regenerate `_index.json` (counts + sha256 per blob) |
| `run.sh eval <scratch> <n> [model]` | sample, solve, train the difficulty model, test on a disjoint sample |
| `upload_ablations.sh` | publish to `s3://forall-ablations/lean/<mode>/<repo>/` (needs `BUCKET_*_KEY`) |

`repos.tsv` (name, language, url, revision, path, toolchain) is the **single source of truth** for
the corpus. The source repos are not submodules — they are multi-GB once built, and only their
URL+revision matters; `clone_repos.sh` materialises them, and every dataset manifest records the
same repo+revision pair, so a published dataset always traces back to an exact tree.

## What a challenge is

Pick a theorem (the **corollary**), take its transitive in-file dependency closure, delete one lemma
from that closure, and `sorry` the proofs that used it. The solver must re-derive the deleted lemma
and close the holes so the file compiles.

Two modes, differing only in how the *users* of the deleted lemma are holed:

| mode | flags | what gets holed |
|---|---|---|
| `corollary-leaves` | `--corollary-delete-lemmas-leaves-all` | only the **leaf tactic steps** that cited the lemma — the rest of each user's proof survives |
| `corollary-whole` | `--corollary-delete-lemmas-all` | each user's **entire proof body** — solve from the statement alone (strictly harder) |

Both slice the file down to the corollary's dependency closure (`--shrink-solution-minimal`) so the
context stays small, and both dedup on the (challenge, solution) text.

**The slice is anchored on the corollary, not on the holes.** This matters more than it sounds. The
holes are the in-file *users* of the deleted lemma — a property of the *lemma*, not of the corollary
that motivated picking it. Anchoring the slice on the holes made the corollary invisible to the
output, so two corollaries whose closures both contained lemma `L` emitted a **byte-identical**
challenge: the same problem shipped twice under different `challenge_id`s (the id hashes the
corollary, so it could not detect the collision). That duplicated ~50% of the mined corpus.
Anchoring on the corollary makes each record the question it claims to be — *delete `L`, then rebuild
it so that **this** corollary still goes through* — with the context cut to what that corollary
actually depends on.

## Validation is a build problem, not an ablation problem

A challenge counts as valid only if it really compiles, so validation is only as trustworthy as the
build environment. **A broken environment reports every challenge as `malformed`, which reads as bad
data.** Three bugs cost ~3,700 valid challenges before they were found — hex-dev alone went from 459
to 3,340 (12% → 87%) with no ablator change at all:

1. **`lake build` does not build every module.** A `lean_lib «Foo» {}` with no globs builds only the
   root module and its imports; siblings are never compiled, so any challenge importing one fails
   with `object file ... does not exist`. `build_modules_tolerant.sh` builds each mined module
   individually, per lake root (lampe / lean-mlir / starkware / leanda have several) —
   *individually*, because a batched `lake build M1 M2 ...` aborts wholesale on the first unknown
   target (a stray `bench.*` / `docs.*` module belonging to no library).
2. **C FFI needs `-D_GNU_SOURCE`.** hex-dev's FFI calls `dlsym(RTLD_DEFAULT, ...)`, which glibc only
   declares under `_GNU_SOURCE`. Without it the FFI target fails and everything downstream goes
   unbuilt. The `cc` wrapper in `flake.nix` handles it.
3. **Vendored mathlib goes missing / half-cloned.** `lake exe cache get` restores it. Historically the
   half-clones were *self-inflicted* (#119): the dry-run harness symlinks `.lake` from its work copy
   back into the source tree (deliberately — it is a heavy prebuilt dep dir, never copied), and
   `check` used to run `lake env lean` with `cwd` inside that work copy, so lake's own workspace
   resolution — git re-clones, the compiled-lakefile-config cache — wrote through the symlink and
   corrupted the shared source. `check` no longer invokes `lake` at all: it compiles via bare `lean`
   against a `LEAN_PATH` reconstructed from `.lake/build/lib`, with the one remaining `lake env`
   call (an FFI-environment snapshot for packages like hex-dev) taken once per repo in `prepare`,
   against the pristine root, never the work copy. `rebuild_repos.sh` still exists to repair
   pre-existing damage from before the fix, but a validation run should no longer produce any.

**Do not gate on a cheap probe.** `lake env true` only checks dependency *resolution* and happily
passes a tree whose mathlib has no `.olean` files. Probing "the first `.lean` file in the tree" picks
test files and never-built sub-libraries, so healthy repos get skipped. The trustworthy signal is
post-hoc: scan the dry-run results for environmental error signatures (`unknown module prefix`,
`object file ... does not exist`, `could not resolve 'HEAD'`, `cloning`) and re-validate any repo that
shows them. `health_probe.sh` compiles a file the miner actually produced records for, which is the
closest a pre-flight check can get.

## Evaluating, and the difficulty model

`run.sh eval` samples 2 problems per repo (making up any shortfall from the repos with the fewest
remaining problems), runs the solver, trains a P(pass) model on the outcomes, then draws a
**disjoint** second sample, **scores it before the outcomes exist**, and reports ROC-AUC
(discrimination) and Brier (calibration, with the Murphy decomposition and a reliability diagram).

Two things the harness must get right or the numbers lie:

- **Context overflows are not failures.** ~10% of challenges exceed a 262k-token window even after
  minimal-slicing. The provider rejects the prompt outright, so the model never sees the problem;
  scoring it 0 blames the solver for a property of the (challenge, model) pair. They are flagged
  `context_exceeded` and excluded from the PASS denominator, like `malformed` / `trivial`.
- **Samples must be deduplicated by challenge TEXT**, not `challenge_id` — see the slicing note above.

The features come from the ablators (so they are visible in the JSONL and on the website): size and
shape (`n_lines`, `n_tactics`, `cyclomatic`, fan-in), what the proof *does* (`n_automation` /
`n_rewrites` / `n_structural` / `automation_only` / `max_nesting`), and what the corollary rests on
(`n_deps_direct` / `n_deps_transitive`). Size alone cannot tell a `by simp` one-liner from a 40-line
induction with the same step count, and that is most of what decides whether a model can re-derive a
lemma.

## Isabelle / l4v

`mine_l4v.sh` + `dryrun_l4v.sh` + `build_l4v_heaps.sh`. Constraints:

- Use the **official Isabelle** pinned in `ablators/isabelle/flake.nix` — nixpkgs' Isabelle swaps the
  bundled veriT for a generic build, which breaks `smt` proof reconstruction. **The version must match
  the corpus**: `nix develop ablators/isabelle#isabelle-2025` for **l4v** (@429d778 needs the base
  release), `#isabelle-2025-2` for the **AFP**.
- `L4V_ARCH` must be set (`ARM` for the flagship refinement proof).
- Heaps live under `ABLATE_ISABELLE_HOME`; Isabelle resolves them below `$HOME`, so the harness
  redirects `HOME` for the subprocess.
- **Unfinished:** the `ExecSpec` / `ASpec` sessions need `spec/design/*.thy`, which are *generated*
  from seL4's Haskell model (`make -C spec/design`) and are absent from a plain checkout. Until that
  runs, only sessions below the design spec (Lib, Word_Lib, Monads, …) can be validated — and 14,594
  of l4v's 19,018 challenges sit under `Refine`, which needs it.

## Files

| | |
|---|---|
| `repos.tsv`, `clone_repos.sh`, `repo_deps.tsv` | the pinned corpus, how to materialise it, and out-of-band deps (unpinnable path-requires) |
| `registry_all.tsv` | a **derived** `name<TAB>path` projection of `repos.tsv` that the helper scripts read. Do NOT hand-edit — regenerate with `tail -n+2 repos.tsv \| awk -F'\t' '$2=="lean"{print $1"\t"$6}' > registry_all.tsv` |
| `run.sh` | the pipeline: `mine` / `validate` / `index` / `eval` / `all` |
| `mine_repo_mode.sh`, `mine_all.sh` | ablate one repo / all repos (`$ABLATE_MODE`) |
| `mine_l4v.sh`, `dryrun_l4v.sh`, `build_l4v_heaps.sh` | the Isabelle / l4v path |
| `par_dryrun.sh`, `eval_one_repo.sh` | shard a challenge set across parallel `--dry-run` / solve workers |
| `keep_good.py`, `finalize_mode.py`, `write_index.py` | filter to compile-validated records; write manifests + index |
| `health_probe.sh`, `rebuild_repos.sh`, `build_modules_tolerant.sh` | is the environment real, and fix it when it isn't |
| `eval_sample.sh`, `sample_disjoint.py`, `score_predictions.py` | solve a sample; draw a disjoint one; measure AUC / Brier |
| `validate_whole.sh`, `revalidate_leaf.sh` | standalone re-validation of an existing batch |
| `upload_ablations.sh` | publish to the Space |
| `licenses.tsv`, `survey_licenses.py`, `LICENSE_SURVEY.md` | per-repo license survey at each repo's **pinned revision** (source of truth for a `license`/`license_url` field and the dataset-card license table; see #118) |

## TODO

- **`apply.py` symlinks `.lake` from the work copy back into the source tree.** Single-file checks
  only *read* it — except lake will re-clone through the symlink and corrupt the source. Give the work
  copy a scratch package dir, or mount `.lake` read-only.
- l4v's generated design spec (above).
- Resolve the 5 flagged repos in `pipeline/LICENSE_SURVEY.md` (no license file at the pinned revision, or a non-commercial-only custom license) before camera-ready: keep with justification or drop from the corpus.
- The Rocq corpus: `docs/rocq-ablate-candidates.md`. Filesystems (FSCQ, Perennial) and Raft/Paxos
  (verdi-raft) exist only there — Lean has neither.
