# cedar-spec-eval (cedar-corollary-leaf-v1-9ab1ca6e)

Syntactic-ablation challenges over **cedar-policy/cedar-spec** (`cedar-lean`, Lean 4.31, AWS Cedar authorization-engine verification), from `Cedar/Thm/` (SymCC soundness, validation, TPE, data-structure lemmas).

- **56** well-posed challenges (of 60 ablated; the rest were ablator edge cases excluded by the dry-run well-posedness check).
- Source: cedar-spec @ `795ddccff61c`.
- Each challenge deletes one lemma + holes its in-file users; the solution restores it. Consume with `baselines/` (`ablate-baseline`), Lean backend.

`challenges.jsonl` is a sha256 blob (not git-tracked); see `manifest.json`.
