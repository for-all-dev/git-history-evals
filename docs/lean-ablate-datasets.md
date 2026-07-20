# Lean ablation datasets (corollary-leaves, validated)

A batch of **validated corollary-ablation eval datasets** mined from real Lean 4
repositories with the Lean ablator (`ablators/lean`), then filtered to only the
records whose challenge and ground-truth solution both compile.

## What each challenge is

One ablation **per eligible corollary per file**, produced with:

```
ablate --corollary-delete-lemmas-leaves-all --shrink-solution-minimal --compact \
       --seed 42 -d <repo> <repo>
```

- `--corollary-delete-lemmas-leaves-all`: walk the file, and for each theorem that
  has an in-file dependency closure ("corollary"), delete one ancestor lemma from
  that closure and `sorry` the **leaf** proof steps that used it. One record per
  eligible corollary. (Files whose theorems only cite out-of-file lemmas produce
  nothing — there is nothing in-file to delete.)
- `--shrink-solution-minimal`: slice challenge + solution down to the holes' minimal
  dependency closure so the context stays small.
- The ablator stamps **provenance** on every record: `repo` (git remote) and
  `revision` (HEAD sha); the manifest also pins `lean_toolchain`.

## Validation ("keep the good ones")

Each mined record is put through `baselines/` `ablate-baseline --dry-run`, which
applies the challenge into a work copy of the built repo and pre-flight compiles it
with `lake env lean`:

- **good** — challenge compiles (holes allowed) **and** the ground-truth solution
  compiles hole-free. Kept.
- **malformed** — challenge doesn't compile. Dropped. (Residual after the slicer fix
  below is inherent to leaf-level holing: a `sorry` inside a rewrite-heavy dependent
  proof can carry a metavariable type, so the whole decl fails to elaborate.)
- **sol_BAD** — the ground truth already contained a `sorry`/axiom → unwinnable.
  Dropped.

Only the **good** records land in `artifacts/lean-ablate/<repo>/challenges.jsonl`
(all mined records are kept alongside in `challenges.all.jsonl`; `manifest.json` has
provenance + counts).

## Ablator changes made for this batch

1. **Git provenance metadata** — all three code ablators (Lean, Rocq, Isabelle-rust)
   now emit `repo` + `revision`, auto-detected from the enclosing git checkout and
   overridable with `--repo`/`--revision`. The Isabelle ablator also takes
   `--isabelle-version` (and `--repo`/`--revision`) for the packaged AFP, which has no
   local git — point it at the `isabelle-prover/mirror-afp-devel` mirror.
2. **Minimal-slice `@[simp]` fix** — `--shrink-solution-minimal` was silently dropping
   attribute-tagged lemmas (`@[simp]`, `@[grind]`, instances, …). A kept `simp` names
   none of its simp-set members, so the syntactic dependency closure couldn't see the
   edge, and dropping the lemma broke the kept proof with "unsolved goals". Fix: the
   minimal slice now keeps every attribute-tagged decl. On lean4lean's `VExpr.lean`
   this went 0/53 → 52/53 well-formed. (Parity port to the Rocq/Isabelle slicers is a
   TODO — targets here were all Lean.)

## Reproduce

Pipeline scripts live in `scratchpad_pilot/`:

- `mine_repo.sh <mine_dir> <strip> <out.jsonl> [seed] [timeout_s] [jobs]` — parallel
  per-file mining with a per-file timeout (pathological huge files are logged to
  `<out>.skipped`, not silently dropped).
- `par_dryrun.sh <challenges.jsonl> <src> <out.jsonl> [nshards]` — shard the JSONL and
  run `ablate-baseline --dry-run` across shards.
- `keep_good.py` / `finalize.py` — filter to good + write the artifact bundle.
- `registry.tsv` + `process_built.sh` — idempotently mine→validate→finalize every
  registered repo that is built but not yet finalized.

Each repo must be **built** first (`lake exe cache get` for mathlib deps, then
`lake build`) so `lake env lean` has the `.olean` closure.
