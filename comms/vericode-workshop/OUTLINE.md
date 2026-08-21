# VeriCodeGen workshop paper — outline, claim map, figure list

Companion to `neurips_2026_vericode_workshop.tex`. Deadlines: **abstract Sept 11, paper Sept 13
(2026)**. This file is the planning artifact; the `.tex` is the deliverable. Keep them in sync —
if a claim moves, move it in both.

**Rule for everyone working on this paper: never write a number that has not been produced by a
committed run.** Every results slot in the `.tex` is a `\todo{}` naming the issue that will fill
it. A `\todo{}` left in at submission time is a section we cut, not a section we guess at.

---

## 1. The claims

Five claims, nothing beyond them. Each is stated in the abstract, restated as a contribution
bullet in §1, and discharged by exactly one experiment issue.

| # | Claim | Where it lands | Produced by | Status |
|---|---|---|---|---|
| C1 | A compile-validated proof-repair corpus at scale: 43,410 challenges from 57 real Lean 4 repos, **both** challenge and ground truth compiled against pinned `.olean` closures. The validation discipline is the contribution. | §3 Method, §4 Dataset, Table 1 | Already done — `artifacts/lean-ablate-whole/_index.json` | **Ready** |
| C2 | Two holing strategies, **measured** rather than assumed: leaf-step vs whole-body, on a matched problem set sharing `challenge_id`. | §5.2, Fig. 3 | **#129** (needs #126 sample, #127 aggregator, #119 harness) | Blocked on run |
| C3 | A contamination argument that is **measured**, structured by the instance- vs knowledge-level memorization distinction. | §5.3, Figs. 4–6 | **#132** temporal holdout, **#133** deletion-count sweep, **#134** recall probe | Blocked on runs |
| C4 | A **calibrated** difficulty model — held-out ROC-AUC, Brier, Murphy decomposition, reliability diagram, feature coefficients. Rare in benchmark papers. | §5.4, Fig. 7 | **#135** (needs #130 grid) | Blocked on run |
| C5 | Reward hacking under a **verified** reward: tamper rate rising with turn budget. | §5.5, Fig. 8 | **#136** (reuses #131 logs, no new spend) | Cheapest; blocked on #131 |

Claims we are **not** making, and must not drift into:

- Not "models cannot do proof engineering." Pass rates here are budget-dependent (see L2).
- Not "this corpus is contamination-free." Instance-level memorization is defeated by
  construction; knowledge-level is not, and §5.3 says so explicitly.
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

### §5 Experiments (~2 pages) — **results pending, all `\todo{}`**
- §5.1 Setup: ReAct agent with `list_proof_files` / `read_file` / `search` / `submit_solution` /
  `give_up`; compile feedback returned on each submission; fixed turn budget; the corpus's own
  prebuilt `.olean` closures. Reference run: Leanstral 1.5, 100-problem leaf sample, 38/91
  scorable (42%), modal outcome turn-limit (44). Reported as the *motivating* run, not a headline.
- §5.2 Easy vs hard (**#129**) — macro/micro PASS per strategy, bootstrap CIs, full outcome mix,
  McNemar on the paired sample.
- §5.3 Contamination (**#132/#133/#134**) — three instruments, framed by the instance- vs
  knowledge-level split.
- §5.4 Difficulty model (**#135**) — held-out AUC, Brier + Murphy decomposition, reliability
  diagram, feature coefficients; budget-dependence caveat inline.
- §5.5 Reward hacking (**#136**) — tamper rate vs budget, broken out by tamper reason.

### §6 Limitations (~0.5 page) — **drafted**
The five that must be stated, not buried:
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
  are systematically unmeasured.

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

| Float | Content | Produced by | Ready? |
|---|---|---|---|
| **Fig. 1** | Pipeline schematic: repo @ pinned revision → corollary closure → delete + hole → two-sided compile validation → split. | none (schematic) | Ready to draw |
| **Fig. 2** | Worked example: one file, the same deletion under leaf vs whole-body holing, side by side. Makes §3.2 concrete in one glance. | none (corpus excerpt) | Ready to draw |
| **Fig. 3** | Paired easy-vs-hard: PASS macro/micro with bootstrap CIs **plus the stacked outcome mix** — the hypothesis predicts mass shifting to turn-limit/fail, not just PASS dropping. | **#129** | Pending |
| **Fig. 4** | Deletion-count decay curve, `--count` ∈ {1,2,3,5}, CIs, problem distribution held fixed across depths. The shape is the finding. | **#133** | Pending |
| **Fig. 5** | Temporal holdout: pinned-corpus vs post-cutoff pass rate, macro-averaged per repo, per split, with CIs. | **#132** | Pending |
| **Fig. 6** | Recall-vs-solve: verbatim recall score against solve outcome on the same problems, per split. | **#134** | Pending |
| **Fig. 7** | Reliability diagram (deciles) + held-out AUC/Brier inset; companion bar of feature coefficients. | **#135** | Pending |
| **Fig. 8** | Tamper rate vs turn budget, stacked by tamper reason (statement deleted vs weakened). | **#136** | Pending |
| **Table 1** | Corpus composition: repos, mined, validated, per strategy; top-3 share. | none | **Ready** |
| **Table 2** | Outcome taxonomy: each outcome, its meaning, in/out of the PASS denominator. | none | **Ready** |
| **Table 3** | License summary: SPDX distribution across 57 repos + the 5 flagged. | none | **Ready** |
| **Table 4** | Main results table: per-strategy, per-model PASS with CIs. | **#129**, **#130** | Pending |

Priority if the page budget bites: Figs. 1, 3, 4, 8 and Tables 1, 2 are the core. Figs. 5–7 move
to the appendix before anything in §3 gets cut.

---

## 4. Open decisions before submission

1. **Page limit.** Confirm the VeriCodeGen workshop limit (the stock NeurIPS template says nine;
   workshops usually say four to eight). The section budgets above assume ~8 pages of content.
   **As drafted the paper runs ~10 content pages** (measured with a substitute font that sets
   wider than Times, so ~9 is the realistic figure) before any results text or figures are added.
   Assume it must shrink. The trim order is the float priority at the end of §3 above, then §3.3
   and §3.7 compress into a single "construction details" paragraph with the rest moved to the
   appendix; §5.3's three subsubsections merge into one running paragraph per instrument. §6
   does not get cut.
2. **Anonymization.** The Hub dataset id de-anonymizes the authors. The `.tex` currently uses an
   anonymized-URL placeholder; swap for camera-ready only.
3. **The 5 flagged repos** (`pipeline/LICENSE_SURVEY.md`): keep with justification or drop. This
   changes the headline row count, so it must be settled *before* Table 1 is final.
4. **Which models go in the grid** (#130) — determines Table 4 and the temporal-holdout cutoffs.
5. Whether §5.3's three instruments all land in time; if only one does, it is **#133**
   (deletion-count sweep), which is the distinctive one.
