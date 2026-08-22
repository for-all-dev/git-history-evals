# Committed source data for the paper's results section

Every number in §5 of `../neurips_2026_vericode_workshop.tex` traces to a file here or to
`pipeline/{temporal_holdout,membership,lemma_dates}.tsv` at the repo root. Nothing in §5 may cite
a number that is not reproducible from one of them.

| file | what it is |
|---|---|
| `GRID.md` | The run notes for the #129/#130 three-model grid: sample construction, the headline table, and the four findings, as written when the runs finished. |
| `grid-<model>.md` | Verbatim `ablate-aggregate` output per run tree — macro/micro PASS, bootstrap CIs, outcome breakdown. **The CIs in Table 4 come from here**, not from `derive.py`. |
| `grid-<model>.json` | Same, machine-readable, plus per-repository counts and rates (53 repos with at least one scorable problem, of 57 sampled). |
| `outcomes.tsv` | 678 rows, one per solve attempt: model, strategy, repo, `challenge_id`, outcome, tamper class, turns, tokens, elapsed. The compact projection of the ~95 MB of run trees, which are not committed. |
| `derived.md` | Generated from `outcomes.tsv`: outcome composition, tamper-reason split, compile-only-oracle rates, exact McNemar tests, transport-error sensitivity, cost roll-up. |
| `derive.py` | Produces both of the above. |

```bash
# from the run trees (not committed; ~95 MB each)
python3 derive.py extract <tree>/paired-openai <tree>/paired <tree>/paired-leanstral
# from the committed table
python3 derive.py report
```

`derive.py`'s outcome precedence mirrors `apply_ablate.aggregate`, and its macro rates reproduce
the aggregator's to the reported precision — that agreement is the check that the projection did
not lose anything load-bearing.

Two gotchas, both of which cost time once:

- The `assistant` field in a result row is the **proof assistant** (`lean`), not the model. The
  model id lives in the run tree's `agg_manifest.json`.
- When an attempt is `tampered`, the tamper reason is stored in the row's `error` field, not in
  `reason` (which stays null). That is where the declaration-removed / statement-weakened split
  comes from.
