# Miner: lean-ablator (syntactic)

Not a git-history miner — a syntactic proof ablator. Crop generated with:

```
ablate Cedar/Thm --corollary-delete-lemmas-leaves 1 --shrink-challenge-minimal --shrink-solution-minimal --seed 11 --compact
```

run from `data/cedar-spec/cedar-lean/` against cedar-spec @ `795ddccff61c83aeb5d6a7ccd22abf5e00164c73`, lean leanprover/lean4:v4.31.0. Each row is a `(challenge, solution)` pair per the shared `record.py`/`apply_ablate` schema. All 56 rows here passed the harness dry-run (challenge compiles with holes; ground-truth solution compiles hole-free).
