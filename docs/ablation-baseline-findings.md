# Ablation-baseline findings (agentic harness on real repos)

This covers the **agentic** baseline in `baselines/` (`ablate-baseline`) — distinct from
the retired single-shot whole-file baseline that ran over the git-history HuggingFace
cuts (`docs/dataset-issues.md`). Here challenges come from the ablators' *leaf*
deletion (`--delete-lemmas-leaves --count 5 --shrink-challenge-minimal
--shrink-solution-minimal`); a pydantic-ai ReAct agent re-derives the deleted lemma(s)
into a compiling, hole-free file, and we score by real compilation.

## Targets

| repo | prover | status |
|------|--------|--------|
| [ryu-lean4](https://github.com/lexicone42/ryu-lean4) (Ryu float→string, complete roundtrip proof; mathlib) | Lean 4 | **baselined** — 8/13 (Sonnet, max_turns=40) |
| fiat-crypto (`src/Util/*`) | Coq | **baselined** — 1/5 (Sonnet, max_turns=40) |
| AFP [Isabelle-Solidity](https://isa-afp.org/entries/Isabelle-Solidity.html) | Isabelle | challenges valid; baseline slow (smt-heavy), see below |

## Coq baseline — fiat-crypto (Sonnet 4.6)

5 challenges from `src/Util/{Decidable,NatUtil,NumTheoryUtil,Option,Tuple}.v` (ryu config:
`--delete-lemmas-leaves --count 5 --shrink-*-minimal`), scored by real `coqc`.

| outcome | n |
|---------|---|
| PASS | 1/5 (`Decidable`) |
| turn-limit (40) | 3/5 (`NumTheoryUtil`, `Option`, `Tuple`) |
| harness error | 1/5 (`NatUtil` — tool-arg retry; since hardened with `retries=3`) |
| malformed | 0/5 |

fiat-crypto's number-theory lemmas are hard; 1/5 with budget the dominant limiter again.
0 malformed confirms the Rocq-ablator fixes below.

### Environment gotcha (coqc version)

The `.vo` deps must be built and checked with the **same** coqc. fiat-crypto's `make`
uses the opam **Coq 8.20.0** on PATH; the `ablators/rocq` nix shell ships **Rocq 9.1.1**
(whose split-out `Stdlib` isn't even present → `From Coq Require …` fails). Run the Coq
baseline with the opam coqc (default PATH), *not* inside the `ablators/rocq` shell. The
ablator binary is built in its shell once; the baseline only needs `coqc` on PATH.
Build deps via `make -f Makefile.coq <targets>` (the top `Makefile` drags in uninited
`bedrock2`/`coqutil` submodules; `Makefile.coq` builds only the Coq closure).

## Rocq-ablator bugs surfaced by fiat-crypto

1. **Slice dropped a decl still cited by a kept structural command (FIXED).** With
   `--shrink-*-minimal`, `slice_delete` always kept structural items (`Hint Resolve`,
   `Ltac`, `Notation`, type-class `Instance`…) but only pulled *goal-decl* dependencies
   into the keep-set — so e.g. `Global Hint Resolve mod_bound_nonneg` survived while
   `Lemma mod_bound_nonneg` was sliced out (`reference … not found`). Unlike Lean's
   decl-local `@[simp]`, Coq's commands are separate and non-local. Fix
   (`lib/ablate.ml`): seed the keep-closure with every in-file name a kept structural
   item cites, and drop (challenge-side only) structural items that cite the *deleted*
   lemma. Took fiat-crypto Util slices from **1/5 → 5/5** valid.

2. **Leaf-holing a mid-sequence tactic → "No such goal" (known, not yet fixed).** In
   non-slice `--delete-lemmas-leaves`, a tactic citing the deleted lemma is replaced by
   `admit.` mid-proof; but `admit` discharges the goal while following tactics still
   expect it. This is the Coq analog of the Lean smallest-enclosing-block fix (hole the
   enclosing bullet/brace/proof, not a flat-sequence tactic). The **slice path**
   whole-proof-holes (`Proof. Admitted.`), so it is unaffected — which is why the Coq
   baseline uses the slice config.

## Harness robustness: pre-flight challenge validation

`solve_one` now compiles the challenge **with holes** before invoking the agent. A
challenge that does not compile is recorded `malformed_challenge=True` and excluded
from the PASS rate — so an ablator bug is never miscounted as a model failure. The
baseline summary reports `malformed` separately. This was essential: the first ryu
pass had **6/13 malformed challenges** from two ablator bugs (below).

`apply.py` now symlinks heavy prebuilt-dependency dirs (`.lake`, `lake-packages`,
`_build`, `build`) back to the pristine source instead of copying them — ryu's `.lake`
(all of mathlib's `.olean`) is **7.1 GB**, so per-challenge copying was infeasible.
Single-file checks (`lake env lean`, `coqc`) only read them, so the symlink is safe.

## Lean-ablator bugs surfaced by ryu (both fixed)

Real Lean (mathlib-style) exercised two span/boundary bugs the synthetic fixtures
never hit. Both are fixed in `ablators/lean/Ablator/Span.lean`, with regressions in
`Tests.lean` (`letDocTests`); malformed dropped **6/13 → 0/13** (all challenges *and*
ground-truth solutions now compile).

1. **`let`/`have` `:=` in a dependent type mistaken for the proof delimiter.**
   `findAssign` returned the first depth-0 `:=`. For
   `theorem foo : let x := e; P x := by …` that is the `let`'s `:=`, so the holer
   truncated the declaration mid-type (`let x : Int := sorry` then a dangling next
   command). New `findDeclBody` skips one depth-0 `:=` per preceding depth-0
   `let`/`have`. Used at all decl-level call sites (Ablate/Uses/Centrality);
   `findAssign` is kept for nested binder detection where the first `:=` is correct.
   Example: `RyuLean4/IEEE754/RoundProof.lean` (`sigExact_eq` has a `let binExp := …`
   in its goal and cites the deleted `toRat_abs` only in its proof).

2. **A deleted lemma's leading `/-- doc -/` was orphaned.** Doc comments are
   non-`proper` tokens, so span boundaries (at col-0 command keywords) left a
   documented decl's doc comment at the *tail of the previous span*. Deleting the decl
   then stranded its doc comment above the next command (`/-- … -/ end` →
   `unexpected token 'end'`). New `attachedStart` backs a command boundary up over an
   attached doc-comment block (whitespace only, no blank line) so the doc travels with
   its decl. Hit `Classify`, `Value`, `FullRoundtrip`, and (downstream) `Interval`,
   `ShortestRep`.

**Parity TODO (scoped).** The two fixes do *not* port symmetrically; only one of them
crosses provers at all.

- **`attachedStart` ports to neither.** Both other ablators already attach leading
  comments to the declaration that follows them, so bug 2 cannot occur there. The Rocq
  ablator accumulates space and comment tokens into `cur` and flushes them with the next
  sentence (`ablators/rocq/lib/span.ml:106`), so a doc comment is part of its decl's span.
  The Isabelle ablator collects non-command tokens as `Ignored` spans and folds them into
  the following command (`ablators/isabelle/rust/src/span.rs:114`). Nothing to port.
- **`findDeclBody` ports to Rocq only.** Isabelle theory syntax has no `:=` proof
  delimiter, so bug 1 has no analogue there. Rocq does, and its `has_assign`
  (`ablators/rocq/lib/span.ml:80`) is `List.exists (Token.is_symbol_named ":=")` over the
  span — *no depth tracking at all*, so it is strictly weaker than the pre-fix Lean
  `findAssign`, which at least required depth 0. A `Definition`/`Let` with a `:=` nested
  inside a type will confuse it the same way. This is the one genuine port.
- **The real cross-prover gap is attribute retention**, which is missing in *both* the
  Rocq and Isabelle ablators: attributes/annotations preceding a declaration are not
  carried with it when the declaration is deleted or holed. That, not the two Lean span
  bugs, is what should be fixed in lockstep once a real Coq/Isabelle corpus is buildable
  here.

## Isabelle session-aware checking + the SMT-solver wall

`baselines/src/apply_ablate/provers/isabelle.py` now auto-discovers a target theory's
enclosing AFP/l4v `ROOT`, computes its in-session import closure, and checks just that closure as one
throwaway session (cross-session deps pruned to only those the closure imports; heavy
`thys/` never registered wholesale). Validated end-to-end on a synthetic multi-theory
session (valid → ok; `oops` → caught). Unit tests in `baselines/tests/test_prover_cmds.py`
cover the ROOT parser, import closure, dep-session pruning, and discovery.

**But** real AFP Solidity / l4v proofs replay `smt`/`sledgehammer` tactics, which need
external solvers (z3 / veriT / cvc) on PATH. The nix Isabelle ships none, so those
proofs hang (observed: a single `smt (verit, …)` in `Solidity.ReadShow` running >130 s).
Real Isabelle baselines need the solver-equipped toolchain (add z3/veriT/cvc to the
flake); the session machinery itself is ready.

## Baseline results — ryu-lean4 (Sonnet 4.6)

13 challenges, one per RyuLean4 source file (seed 7, `--count 5`), all well-formed
(0 malformed after the fixes above). Agent: pydantic-ai ReAct loop, may read other
`.thy/.lean/.v` in the repo, no internet/git, no `sorry`/`admit`/`axiom`; scored by a
real hole-free `lake env lean` compile.

**Run A — `--max-turns 12` (request budget incl. tool turns):**

| outcome | n |
|---------|---|
| PASS (re-derived, compiles, hole-free) | 2/13 |
| turn-limit (ran out of request budget) | 11/13 |
| malformed challenge | 0/13 |

PASS: `Decimal/Format.lean`, `Decimal/Parse.lean`. The dominant failure mode was the
**request budget**, not wrong proofs: every non-pass hit `UsageLimitExceeded` before
finishing. pydantic-ai's `request_limit` counts *every* model call (each `read_file`,
`submit_solution`, etc.), so 12 requests ≈ 5–6 real attempts — too few for these
goals. This motivated three harness changes: default budget 12 → 30; turn-limit
recorded as a distinct outcome; and on turn-limit the agent's **last on-disk attempt is
still scored** (running out of budget ≠ a wrong proof).

**Run B — `--max-turns 40`:**

| outcome | n |
|---------|---|
| PASS (re-derived, compiles, hole-free) | **8/13 (62%)** |
| turn-limit (ran out of request budget) | 5/13 |
| malformed challenge | 0/13 |

PASS: `Decimal/{Decimal,Format,Parse}`, `IEEE754/{Classify,Float64,RoundToNearest,Value}`,
`Roundtrip/FullRoundtrip`. Still turn-limited (the harder/larger files):
`IEEE754/RoundProof`, `Roundtrip/FormatParse`, `Ryu/{Interval,Shortest,ShortestRep}`.
Raising the budget 12 → 40 moved 6 challenges from turn-limit to PASS, confirming the
budget — not model capability — was the dominant constraint at 12.

**Takeaways.** (1) Real-prover scoring + pre-flight validation make the harness
trustworthy: PASS is over *well-formed, really-compiled, hole-free* challenges, with
ablator bugs and budget exhaustion broken out separately rather than silently inflating
the failure count. (2) Budget is the first knob to tune: 2/13 → 8/13 from 12 → 40 turns
alone. (3) The remaining 5 failures are the largest proofs (`Interval` ~1150 lines,
`ShortestRep` ~710) — re-deriving a deleted lemma + its leaf use-sites in a big file is
genuinely hard for a single agent, consistent with the project thesis that proof
reconstruction is beyond current agents even at one-lemma granularity. Next levers:
higher budget, a stronger model, or finer-grained (per-hole) editing instead of
whole-file resubmission.
