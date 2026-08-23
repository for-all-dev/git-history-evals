# Deletion-count sweep (#133)

Contamination instrument from `docs/contamination.agents.md`: deletion count separates
instance-level from knowledge-level memorization. Swept `--corollary-delete-lemmas-leaves-all N`
over N ∈ {1, 2, 3, 5}; `claude-sonnet-5`, 50-turn budget, leaf holing.

Machine-readable: `pipeline/deletion_curve.tsv`. Mining/validation/eval scripts:
`pipeline/experiments/133/`. Figure: `comms/vericode-workshop/figures/out/deletion-curve.pdf`
(renders from the TSV via `uv run figures`).

## Problem distribution held fixed across depths (AC-2), by construction

- The 101 unique files of the #129/#130 paired easy sample were mined at every depth with the
  same seed (42); `--corollary-delete-lemmas-leaves-all` walks every eligible corollary per
  file, so the same corollaries recur across depths.
- A (file, corollary) tuple enters the set only if it yields **exactly N deletions at every
  depth** (closure deep enough) *and* **compile-validates on both sides at every depth**
  (par_dryrun + keep_good). 457 tuples qualified pre-validation; 130 were drawn (seed 42);
  109 validated everywhere; the final set is a seeded 100 across 30 repos — identical at
  every depth.
- Depth-1 sanity: 24 of the paired sample's own (file, corollary) tuples survive the
  exact-N-at-all-depths filter, and for all 24 the depth-1 challenge_id is byte-identical to
  the paired sample's. Depth-1 macro (27.1%) is consistent with the grid's 29.2% on the
  paired sample.

## The curve (AC-1)

| depth | micro PASS | macro PASS [95% CI] | tampered | turn_limit | independence null p1^N |
|--:|--:|---|--:|--:|--:|
| 1 | 24% | 27.1% [23.2, 30.9] | 35 | 12 | 24% (by construction) |
| 2 | 8%  | 15.6% [13.7, 17.9] | 27 | 27 | 5.8% |
| 3 | 6%  | 15.1% [13.3, 17.6] | 20 | 28 | 1.4% |
| 5 | 5%  | 11.5% [10.0, 13.7] | 20 | 20 | 0.08% |

## Interpretation (AC-3): conjunction difficulty, not a memorization cliff

The sharp 1→2 drop looks like the "retrieval was carrying depth 1" signature — until it is
compared to the independence reference p1^N (a depth-N problem treated as N independent
single-deletion problems). The 1→2 drop **matches** independence (8% vs 5.8% predicted); the
tail sits far **above** it (depth 5: 5% observed vs 0.08% predicted). Interlocking lemmas from
one closure are much easier jointly than independent problems — shared context, statements,
and proof idiom; re-deriving one supplies material for the next.

Cross-referenced with the temporal holdout (#132, AC-4): `claude-sonnet-5` shows **no**
pre-cutoff advantage in any cell, so for this model both instruments agree — no memorization
signal. The natural follow-up is running this sweep on `leanstral-1-5`, whose temporal cells
are all pre-cutoff-positive after the clean re-run (see `pipeline/temporal_holdout.tsv`).

Secondary observation: tampering falls with depth (35/27/20/20) — deleting five theorem
statements at once is a less tempting exploit than deleting one.

## Provenance

- Mined 2026-08-22 (~1,600 records/depth over 101 files); the raw-ablator `repo` field is
  URL-form while sample manifests use short names — the selector maps explicitly (this
  silently produced a zero-size join on the first attempt).
- Evals ran 2026-08-22/23 with Anthropic prompt caching (merged #188), two depths at a time.
- Residual harness errors: ~6/400, background level.
