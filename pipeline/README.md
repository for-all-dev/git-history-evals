# The ablation pipeline

Mine → build → validate → publish, for corollary-ablation eval datasets.

The source repos are **not** submodules (they are multi-GB once built, and only their
URL+revision matters). `repos.tsv` pins them; `clone_repos.sh` materialises them under
`data/<language>/<name>/`. Every dataset manifest records the same repo+revision pair, so a
published dataset always traces back to an exact tree.

```bash
nix develop                                 # wrapped cc, elan/lake, ablate-baseline, s3cmd
bash pipeline/clone_repos.sh lean           # fetch the 50 Lean repos at their pinned revisions
bash pipeline/mine_all.sh        <scratch>  # ablate (syntactic — needs no build)
bash pipeline/health_probe.sh    <scratch> <repos.txt>          # is each tree really compilable?
bash pipeline/build_modules_tolerant.sh <scratch> <repos.txt>   # build EVERY module (see below)
bash pipeline/validate_whole.sh  <scratch>  # dry-run compile challenge + solution
bash pipeline/upload_ablations.sh           # publish (BUCKET_ACCESS_KEY / BUCKET_SECRET_KEY)
```

`ABLATE_MODE` selects the ablation: `--corollary-delete-lemmas-all` (whole-proof holing) or
`--corollary-delete-lemmas-leaves-all` (leaf-step holing).

## Validation is a build problem, not an ablation problem

A challenge is "valid" only if it really compiles, so validation is only as trustworthy as the
build environment. **A broken environment reports every challenge as `malformed`, which reads as bad
data.** Three bugs cost ~3,700 valid challenges before they were found — hex-dev alone went from
459 to 3,340 (12% → 87%) with no ablator change:

1. **`lake build` does not build every module.** A `lean_lib «Foo» {}` with no globs builds only
   the root module and its imports; siblings are never compiled, so any challenge importing one
   fails with `object file ... does not exist`. `build_modules_tolerant.sh` builds each mined
   module individually, per lake root (repos like lampe/lean-mlir/starkware have several) —
   *individually*, because a batched `lake build M1 M2 ...` aborts wholesale on the first unknown
   target (a stray `bench.*`/`docs.*` module belonging to no library).
2. **C FFI needs `-D_GNU_SOURCE`.** hex-dev's FFI calls `dlsym(RTLD_DEFAULT, ...)`, which glibc
   only declares under `_GNU_SOURCE`. Without it the FFI target fails and everything downstream
   goes unbuilt. The `cc` wrapper in `flake.nix` handles it.
3. **Vendored mathlib goes missing / half-cloned.** `lake exe cache get` restores it. Note that
   half-clones with an unresolvable `HEAD` are *self-inflicted*: the dry-run harness symlinks
   `.lake` from its work copy back into the source tree, so lake's git operations write through
   and can corrupt the source. `repair_repos.sh` drops and refetches those.

**Do not gate on a cheap probe.** `lake env true` only checks dependency *resolution* and happily
passes a tree whose mathlib has no `.olean` files. Probing "the first `.lean` file in the tree"
picks test files and never-built sub-libraries, so healthy repos get skipped. The trustworthy
signal is post-hoc: scan the dry-run results for environmental error signatures (`unknown module
prefix`, `object file ... does not exist`, `could not resolve 'HEAD'`, `cloning`) and re-validate
any repo that shows them. `health_probe.sh` compiles a file the miner actually produced records
for, which is the closest a pre-flight check can get.

## Isabelle / l4v

`mine_l4v.sh` + `dryrun_l4v.sh` + `build_l4v_heaps.sh`. Constraints:

- Use the **official Isabelle** from `ablators/isabelle/scala` (`nix develop .#isabelle-2025`) —
  nixpkgs' Isabelle has broken SMT reconstruction, and l4v @ 429d778 needs base 2025, not 2025-2.
- `L4V_ARCH` must be set (`ARM` for the flagship refinement proof).
- Heaps live in `ABLATE_ISABELLE_HOME`; Isabelle resolves them under `$HOME`, so the harness
  redirects `HOME` for the subprocess.
- **Unfinished:** the `ExecSpec`/`ASpec` sessions need `spec/design/*.thy`, which are *generated*
  from seL4's Haskell model (`make -C spec/design`) and are absent from a plain checkout. Until
  that runs, only sessions below the design spec (Lib, Word_Lib, Monads, …) can be validated. The
  bulk of l4v's challenges (14,594 of 19,018) sit under `Refine` and need it.

## Files

| script | what |
|---|---|
| `repos.tsv` / `clone_repos.sh` | the pinned source repos, and how to materialise them |
| `mine_repo_mode.sh` / `mine_all.sh` | ablate one repo / all repos (`$ABLATE_MODE`) |
| `mine_l4v.sh` | ablate l4v (Isabelle, prefix-cut apply-scripts) |
| `health_probe.sh` | compile a pristine mined file per repo — is the env real? |
| `build_modules_tolerant.sh` / `build_all_modules.sh` | build every mined module, per lake root |
| `repair_repos.sh` / `rebuild_repos.sh` | drop half-clones, refetch mathlib, clear stale lake config |
| `par_dryrun.sh` | shard a challenge set across parallel `ablate-baseline --dry-run` workers |
| `validate_whole.sh` / `revalidate_leaf.sh` | validate a batch and write the artifact bundle |
| `keep_good.py` / `finalize_mode.py` | filter to compile-validated records; write manifest |
| `upload_ablations.sh` | sync to `s3://forall-ablations/lean/<mode>/<repo>/` |
