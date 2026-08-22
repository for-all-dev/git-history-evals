# VeriCodeGen workshop paper — outline, claim map, figure list

Companion to `neurips_2026_vericode_workshop.tex`. Deadlines: **abstract Sept 11, paper Sept 13
(2026)**. This file is the planning artifact; the `.tex` is the deliverable. Keep them in sync —
if a claim moves, move it in both.

**Rule for everyone working on this paper: never write a number that has not been produced by a
committed run.** Every unfilled results slot in the `.tex` is a `\todo{}` naming the issue that
will fill it. A `\todo{}` left in at submission time is a section we cut, not a section we guess
at.

To keep that rule enforceable now that results exist, every number in §5 traces to
`comms/vericode-workshop/results/` (committed alongside this file) or to
`pipeline/{temporal_holdout,membership,lemma_dates}.tsv` on master. The raw run trees are ~95 MB
and are not committed; `results/outcomes.tsv` is the 678-row compact projection of them that
`results/derive.py` recomputes `results/derived.md` from. If you change a number in the `.tex`,
it must be because one of those files changed.

---

## 1. The claims

Five claims, nothing beyond them. Each is stated in the abstract, restated as a contribution
bullet in §1, and discharged by exactly one experiment issue.

| # | Claim | Where it lands | Produced by | Status |
|---|---|---|---|---|
| C1 | A compile-validated proof-repair corpus at scale: 43,410 challenges from 57 real Lean 4 repos, **both** challenge and ground truth compiled against pinned `.olean` closures. The validation discipline is the contribution. | §3 Method, §4 Dataset, Table 1 | Already done — `artifacts/lean-ablate-whole/_index.json` | **Ready** |
| C2 | Reward hacking under a **verified** reward, measured: tamper rate ranks with capability and reaches ~50% of scorable attempts for the strongest model; a compile-only oracle would have reported 98.1% instead of 49.5%. | §5.3, Table 5 (was Fig. 8) | **#129/#130** grid (landed); **#131/#136** for the budget curve | **Landed at 50 turns**; budget curve pending |
| C3 | Two holing strategies, **measured** rather than assumed: leaf-step vs whole-body, on a matched problem set sharing `challenge_id`. Result is a **null** on pass rate for both generalists, a composition + turn-cost shift, and a (weak, confounded) reversal for the specialist. | §5.4, Fig. 3 | **#129** | **Landed** |
| C4 | A contamination argument that is **measured**, structured by the instance- vs knowledge-level memorization distinction. | §5.5, Tables 6–7, Figs. 4, 6 | **#137** membership (landed), **#132** temporal holdout (landed), **#133** deletion-count sweep, **#134** recall probe | **2 of 4 landed** |
| C5 | A **calibrated** difficulty model — held-out ROC-AUC, Brier, Murphy decomposition, reliability diagram, feature coefficients. Rare in benchmark papers. | §5.6, Fig. 7 | **#135** (labels now exist from #130) | Blocked on run |

Claim ordering changed once results landed: what was C5 (reward hacking) is now the paper's
strongest sentence and leads the results section, and what was C2 (the holing comparison) is a
null that must be reported as one. C1 is unchanged.

Claims we are **not** making, and must not drift into:

- Not "models cannot do proof engineering." Pass rates here are budget-dependent (see L2).
- Not "this corpus is contamination-free." Instance-level memorization is defeated by
  construction; knowledge-level is not, and §5.5 says so explicitly.
- Not "the temporal holdout proves no contamination." The pre-cutoff side is 10–19 problems.
  The finding is "we looked with the two instruments the data supports and found no signal",
  not "we excluded one" — §5.5 says this in the text, and it must survive editing.
- Not "tamper rate rises with turn budget." That was the hypothesis; the 15-turn data points the
  other way. §5.3 reports it as unconfirmed.
- Not "whole-body holing is harder." Measured, null at 50 turns. The published split names
  (`easy`/`hard`) overstate what we showed; the paper uses leaf / whole-body throughout.
- Not "this generalizes across proof assistants." The corpus is Lean-only (L1).
- Not "we beat baseline X." There is no competing repair benchmark on real-repo Lean to beat;
  the positioning in §2 is about *task shape and validation*, not leaderboard position.

---

## 2. Section-by-section outline

### §1 Introduction (~1 page)
- The gap: proof-synthesis evaluation is dominated by competition mathematics, where a problem is
  a self-contained statement with no surrounding library. Real proof engineering is *repair inside
  an existing development*.
- The instrument: syntactic ablation. Delete a lemma from a theorem's in-file dependency closure,
  hole its users, ask for re-derivation, score by the compiler.
- Why the validation discipline matters: an extracted benchmark whose ground truth was never
  compiled in the extracted context can ship unwinnable problems, and nobody notices because the
  scorer never runs on the gold answer.
- Contributions = the five claims above, each with a forward reference.

### §2 Related work (~0.75 page) — **drafted, no results needed**
Three groups:
1. **Competition-math benchmarks** — miniF2F, ProofNet, PutnamBench, FIMO. Self-contained
   statements, formalized from natural-language competition problems; no ambient repository, no
   downstream users of the theorem being proved. Our problems come with the library they live in.
2. **Repository-scale extraction** — LeanDojo/LeanStep, Mathlib-derived tactic-prediction corpora,
   CoqGym/PRISM on the Rocq side. These do operate on real developments, but the extracted
   *state–tactic* pairs are not validated as standalone compilable artifacts, and the task is
   next-tactic prediction rather than whole-lemma repair.
3. **Repair / agentic software benchmarks** — SWE-bench and descendants. Right task shape (repair
   inside a repository, execution-based scoring), wrong oracle: tests admit false positives, a
   kernel does not.
4. **Contamination methodology** — LiveCodeBench / GSM1K (temporal holdout), GSM-Symbolic
   (perturbation robustness), Carlini et al. (memorization scales with duplication), Golchin &
   Surdeanu (guided prompting / verbatim recall). §5.3 is built out of these instruments.

**The differentiator, in one sentence:** real-repository proofs, posed as a repair task, with the
compiler run on *both* the challenge and the ground truth before either ships.

### §3 Method (~1.5 pages) — **drafted, no results needed**
- §3.1 Ablation as proof repair. Corollary → in-file transitive dependency closure → delete one
  lemma → hole its in-file users. Solver must produce a hole-free file that compiles.
- §3.2 The two holing strategies. `corollary-leaves` (only the leaf tactic steps citing the lemma
  are `sorry`ed; the rest of each user's proof survives as scaffolding) vs `corollary-whole` (each
  user's entire proof body is replaced). Same deletion, same slice, different amount of surviving
  structure — the reason a matched comparison is possible at all.
- §3.3 Corollary-anchored slicing. Why the slice is anchored on the corollary and not on the
  holes (anchoring on holes made the corollary invisible and shipped byte-identical challenges
  under distinct ids — ~50% duplication). Dedup is on `(challenge, solution)` **text**.
- §3.4 `challenge_id` and matched selections. `sha1_16(file | seed | variant | deleted names |
  holed names)` — invariant to the holing strategy, which is exactly what makes the paired design
  in §5.2 valid.
- §3.5 Two-sided compile validation. Challenge must compile *with* holes; ground truth must
  compile *hole-free*. Drop `malformed` and `sol_BAD`. Note that validation is a **build** problem
  — an incompletely-built tree reports everything malformed (this understated one repo by 2,881
  challenges before it was caught).
- §3.6 Scoring and the outcome taxonomy. PASS / fail / turn-limit / gave-up / tampered /
  malformed / trivial / context-exceeded, and which are excluded from the denominator and why.
- §3.7 The tamper check. Statement-preservation guard; Lean compares exact statement text up to
  `:=`; Coq/Isabelle degrade to name-presence. Our corpus is Lean, so the strong version applies.

### §4 Dataset (~1 page) — **drafted, no results needed**
- Composition: Table 1. 57 repos, 26,157 mined candidates per strategy, 21,692 / 21,718 validated.
- Provenance and pinning: `repos.tsv` (url + revision + toolchain); every manifest records the
  repo+revision pair.
- Licensing: survey at each repo's *pinned revision*, not default-branch HEAD; mixed corpus →
  aggregate `license: other` with a per-repo table; 5 of 57 flagged and pending an explicit
  keep/drop decision.
- Skew: `evm-asm` 33.8% of leaf rows, top three 53.4%. **All headline numbers macro-averaged per
  repo.** Stated as a dataset property, and again in Limitations.
- Splits and distribution: `easy` / `hard` on the Hub; scoring requires a built checkout.

### §5 Experiments (~4 pages) — **mostly landed**
- §5.1 Setup: ReAct agent with `list_proof_files` / `read_file` / `search` / `submit_solution` /
  `give_up`; compile feedback returned on each submission; fixed turn budget; the corpus's own
  prebuilt `.olean` closures. **The grid**: 3 models × 2 strategies × 113 matched pairs at 50
  turns, seed 42, 103 scorable, 53 repos, 678 attempts, 407.6M/8.9M tokens, 117.5 h. Reference
  run (Leanstral, 38/91, modal outcome turn-limit) retained only as motivation.
- §5.2 Main results — Table 4 (macro/micro PASS + CIs + tamper share + compile-only rate),
  Table 5 (outcome composition), Table 6 (cost). Leads with the reward-hacking sentence, then
  discrimination (49.1 / 29.2 / 17.9 macro leaf).
- §5.3 Reward hacking (**#136**) — tamper rate by model and reason; the 15-turn preliminary
  point; the hypothesis (rises with budget) reported as **unconfirmed**. `\todo{#131/#136}` for
  the full curve.
- §5.4 Leaf vs whole-body (**#129**) — null for both generalists (p = 0.754, 1.000), composition
  + turn-cost shift, weak reversal for the specialist (p = 0.118, confounded by transport errors;
  no-error subset p = 0.625).
- §5.5 Contamination — §5.5.1 membership vs the dated SWH/Stack v2 snapshot (**#137**, landed:
  2/57 `likely_in`); §5.5.2 temporal holdout (**#132**, landed: 21,692/21,692 lemmas dated,
  no systematic pre-cutoff advantage, pre-side n = 10–19); §5.5.3 deletion-count sweep
  (**#133**, `\todo{}`); §5.5.4 recall probe (**#134**, `\todo{}`); §5.5.5 what we do not use
  (popularity — now dropped rather than footnoted, since #137 supersedes it).
- §5.6 Difficulty model (**#135**, `\todo{}`) — labels now exist from the grid; caveats extended
  to include model-dependence of difficulty and the fact that ~half of one model's labels are
  tampers.

### §6 Limitations (~0.5 page) — **drafted**
Eight now, the first six as originally planned plus two the grid forced:
- **L1 Lean-only.** l4v is partially mined (19,018 challenges) but the flagship `Refine` sessions —
  14,594 of them — are blocked on seL4's *generated* design spec. The Rocq corpus is unmined.
- **L2 Budget-dependent pass rates.** Turn-limit was the modal outcome in the first baseline
  (44 vs 38 PASS); raising 30→50 turns moved the rate and the curve had not flattened. Every
  reported pass rate is a pass rate *at budget B*, including the difficulty model's labels.
- **L3 Scoring requires a built checkout.** Only a subset of prebuilt closures is published (#143);
  ~5–7 GB per mathlib-dependent repo, ~1 TB for the full corpus.
- **L4 Corpus skew.** One repo is a third of the rows; three are half. Macro-averaging mitigates
  but does not remove this.
- **L5 Context.** ~10% of challenges exceed a 262k-token window even after minimal slicing; they
  are excluded from the denominator, which is honest but also means the hardest-to-fit problems
  are systematically unmeasured. (The grid's macro-balanced sample saw only 3/678.)
- **L6 Licensing.** 5 of 57 repos flagged pending a keep/drop decision.
- **L7 Transport faults (new).** The leanstral arm lost 60/103 leaf and 44/100 whole-body
  attempts to provider TLS/read failures, which the aggregator counts as non-passes. Its row is a
  lower bound; error-excluded macro is 40.3% / 43.8% over 31 / 40 repos. Also confounds the
  §5.4 reversal. **A clean re-run of this arm is the cheapest outstanding result item.**
- **L8 One seed, one budget, one run per cell (new).** The bootstrap CIs cover problem/repo
  sampling, not solver run-to-run variance.

### §7 Conclusion (~0.2 page)

### Appendix (no page limit)
- A: outcome taxonomy in full, with the exact exclusion rules.
- B: per-repo license table (from `pipeline/LICENSE_SURVEY.md`).
- C: per-repo challenge counts.
- D: agent system prompt and tool schemas.
- E: NeurIPS checklist (`checklist.tex`, already `\input`).

---

## 3. Figure and table list

Every float is mapped to the issue that produces it. Floats with no issue are already
producible from committed artifacts.

Source data for everything marked **Ready (data committed)** lives in
`comms/vericode-workshop/results/`: `outcomes.tsv` (678 rows, one per solve attempt),
`grid-<model>.{md,json}` (verbatim aggregator output, incl. per-repo rates and bootstrap CIs),
`derived.md` (McNemar, tamper-reason split, transport-error sensitivity, cost), and
`derive.py` which regenerates `derived.md` from `outcomes.tsv`. Contamination floats read
`pipeline/{temporal_holdout,membership,lemma_dates}.tsv` on master.

| Float | Content | Produced by | Ready? |
|---|---|---|---|
| **Fig. 1** | Pipeline schematic: repo @ pinned revision → corollary closure → delete + hole → two-sided compile validation → split. | none (schematic) | Ready to draw |
| **Fig. 2** | Worked example: one file, the same deletion under leaf vs whole-body holing, side by side. Makes §3.2 concrete in one glance. | none (corpus excerpt) | Ready to draw |
| **Fig. 3** | Stacked outcome mix per (model, strategy) — 6 bars, PASS / tampered / fail / turn-limit / gave-up / harness. This is the figure that *shows* the reward-hacking finding, and it is now the single highest-value float in the paper. Currently in the text as Table 5. | **#129/#130** | **Ready (data committed)** — `results/outcomes.tsv` |
| **Fig. 4** | Deletion-count decay curve, `--count` ∈ {1,2,3,5}, CIs, problem distribution held fixed across depths. The shape is the finding. | **#133** | Pending |
| ~~**Fig. 5**~~ | Temporal holdout as a figure. **Demoted to Table 7**: with a 10–19-problem pre-side, a bar chart with error bars would imply precision the data does not have. | **#132** | **Landed as a table** — `pipeline/temporal_holdout.tsv` |
| **Fig. 6** | Recall-vs-solve: verbatim recall score against solve outcome on the same problems, per split. | **#134** | Pending |
| **Fig. 7** | Reliability diagram (deciles) + held-out AUC/Brier inset; companion bar of feature coefficients. | **#135** | Pending |
| **Fig. 8** | Tamper rate vs turn budget, stacked by tamper reason (declaration removed vs statement weakened). | **#131/#136** | **Partial** — the 50-turn point and its reason split are committed (`results/derived.md`); 15-turn verified but not committed here; 100-turn in flight |
| **Table 1** | Corpus composition: repos, mined, validated, per strategy; top-3 share. | none | **Ready** |
| **Table 2** | Outcome taxonomy: each outcome, its meaning, in/out of the PASS denominator. **Amended**: `harness_err` is *in* the denominator, which the original table had backwards relative to the aggregator. | none | **Ready** |
| **Table 3** | License summary: SPDX distribution across 57 repos + the 5 flagged. | none | **Ready** |
| **Table 4** | Main results: per-strategy, per-model macro/micro PASS with CIs, tamper share, compile-only rate. | **#129/#130** | **Ready (data committed)** — in the `.tex` |
| **Table 5** | Outcome composition, counts out of 113 per (model, strategy). Candidate to become Fig. 3. | **#129/#130** | **Ready (data committed)** — in the `.tex` |
| **Table 6** | Cost: avg turns, input/output Mtok, solver hours per model. | **#129/#130** | **Ready (data committed)** — in the `.tex` |
| **Table 7** | Temporal holdout: pre/post macro PASS at two cutoffs × 3 models × 2 strategies. | **#132** | **Ready (data committed)** — in the `.tex` |
| **Table 8** | Corpus membership vs the SWH/Stack v2 snapshot: 2 `likely_in`, 9 `too_recent`, 46 `not_in_swh`. Currently prose in §5.5.1; promote to a table only if space allows. | **#137** | Ready — `pipeline/membership.tsv` |

Priority if the page budget bites: Figs. 1, 3, 4 and Tables 1, 2, 4 are the core. Table 6 (cost)
and Table 7 (temporal) move to the appendix first; Figs. 6–7 next; nothing in §3 gets cut before
those.

---

## 4. Open decisions before submission

1. **Page limit.** Confirm the VeriCodeGen workshop limit (the stock NeurIPS template says nine;
   workshops usually say four to eight). The section budgets above assume ~8 pages of content.
   **With the results sections in, the paper runs 13 content pages** (references start on p. 14),
   measured with a substitute font that sets wider than Times — call it ~11–12 realistic. It was
   ~10 before results. It must shrink, and the results section is *not* where to cut first: the
   three new tables are the paper. The trim order is the float priority at the end of §3 above, then §3.3
   and §3.7 compress into a single "construction details" paragraph with the rest moved to the
   appendix; §5.3's three subsubsections merge into one running paragraph per instrument. §6
   does not get cut.
2. **Anonymization.** The Hub dataset id de-anonymizes the authors. The `.tex` currently uses an
   anonymized-URL placeholder; swap for camera-ready only.
3. **The 5 flagged repos** (`pipeline/LICENSE_SURVEY.md`): keep with justification or drop. This
   changes the headline row count, so it must be settled *before* Table 1 is final.
4. ~~**Which models go in the grid** (#130)~~ — **settled**: `gpt-5.6-sol`, `claude-sonnet-5`,
   `leanstral-1-5`; cutoffs 2024-06 and 2025-01.
5. Whether §5.5's remaining instruments land in time. If only one does, it is **#133**
   (deletion-count sweep), which is the distinctive one — and it is now *more* important than
   planned, because the temporal axis turned out to be nearly degenerate (98% of deleted lemmas
   postdate 2024-06), so depth is the only dimension left along which instance- and
   knowledge-level memorization visibly separate.
6. **Re-run the leanstral arm** (L7). 60/103 attempts lost to provider transport faults is the
   one number in the results section that a reviewer can dismiss the whole row over, and it costs
   one run to fix.
7. **Whether to rename the published splits.** §5.4 concludes the `easy`/`hard` labels are not
   supported. The paper already uses leaf / whole-body; the Hub split names still say
   easy/hard, and the mismatch will be noticed.
