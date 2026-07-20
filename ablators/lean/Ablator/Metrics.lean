/-
Proof-complexity metrics, computed inside the ablator so they are visible in the
JSONL record (and any downstream website / difficulty classifier). These are
deliberately simple, tokenizer-driven heuristics — informative features, not
canonical semantics. See `docs/difficulty-features.md` §2 for the definitions,
which the rocq (reference) / Rust / Scala ablators mirror for their own provers.

Matches are whole identifier tokens only; occurrences inside comments or strings
are ignored by construction, because `tokenize` classifies those bytes as
comment/string tokens rather than `ident` tokens.
-/

import Ablator.Tokenize
import Ablator.Json

namespace Ablator

/-- Proof-complexity integers carried by every proof block.

    The first five are *size and shape*: how big and how branchy. They turned out to be weak
    predictors of whether a model can re-derive a lemma (ROC-AUC 0.61), because they cannot
    tell a `by simp` one-liner from a 40-line induction with the same step count. The rest
    describe what the proof actually *does* — which is the thing a solver has to reproduce. -/
structure Metrics where
  nLines     : Int
  nChars     : Int
  nSubproofs : Int  -- intermediate-assertion keywords: have/obtain/suffices
  nTactics   : Int  -- atomic proof steps: newline tactic lines + ';'/'<;>' separators
  cyclomatic : Int  -- 1 + #case-splitters + #alternation combinators
  -- what the proof DOES:
  nAutomation : Int  -- closing/automation tactics (simp, omega, aesop, decide, linarith, …)
  nRewrites   : Int  -- rewriting/unfolding steps (rw, simp only, unfold, conv, …)
  nStructural : Int  -- structural steps (induction, cases, refine, constructor, calc, …)
  automationOnly : Bool  -- EVERY step is automation: the proof is closable by a tactic call
  maxNesting  : Int  -- deepest indentation level of the body (proxy for proof structure)
  deriving Inhabited, BEq, Repr

namespace Metrics

/-- Lean keyword banks (see spec §2). -/
def subproofKw : List String := ["have", "obtain", "suffices"]

def caseSplitters : List String :=
  ["induction", "cases", "rcases", "rintro", "match", "split", "constructor"]

def alternationKw : List String := ["first", "try", "repeat"]

/-- Closing/automation tactics: a step that discharges a goal by search or decision procedure.
    A proof made only of these is one a model can often reproduce without understanding it. -/
def automationKw : List String :=
  ["simp", "simp_all", "simpa", "aesop", "omega", "decide", "norm_num", "linarith", "nlinarith",
   "positivity", "polyrith", "tauto", "trivial", "rfl", "ring", "ring_nf", "field_simp",
   "assumption", "exact?", "apply?", "bv_decide", "grind", "gcongr", "measurability", "fun_prop"]

/-- Rewriting / unfolding steps: manipulate the goal without deciding it. -/
def rewriteKw : List String :=
  ["rw", "rewrite", "unfold", "conv", "erw", "subst", "change", "show", "delta", "norm_cast",
   "push_cast", "abel", "congr"]

/-- Structural steps: introduce a proof skeleton the model must get right. -/
def structuralKw : List String :=
  ["induction", "cases", "rcases", "rintro", "refine", "constructor", "calc", "exact", "apply",
   "intro", "intros", "use", "exists", "obtain", "ext", "funext", "specialize", "interval_cases"]

private def nLinesOf (s : String) : Int :=
  if s.isEmpty then 0
  else Int.ofNat (s.toList.foldl (fun acc c => if c == '\n' then acc + 1 else acc) 1)

/-- Is the token at `i` the start of a `<;>` triple? The lexer splits `<;>` into
    three tokens (`<`, `;`, `>`) because `;` is a standalone delimiter, so we
    match the sequence rather than a single token. -/
private def isSemiAltAt (toks : Array Token) (i : Nat) : Bool :=
  i + 2 < toks.size
    && toks[i]!.isSym && toks[i]!.src == "<"
    && toks[i+1]!.src == ";"
    && toks[i+2]!.src == ">"

/-- Compute metrics. `block` is the whole source slice (statement + proof) used
    for line/char size; `body` is the proof body used for the tactic/branch
    heuristics. For holes the two coincide (only the proof body is available). -/
def compute (block body : String) : Metrics := Id.run do
  let toks := tokenize body
  let n := toks.size
  let mut subproofs := 0
  let mut branches := 0
  let mut tacticLines := 0   -- newline-separated tactic lines
  let mut separators := 0    -- ';' and '<;>' step separators
  let mut automation := 0
  let mut rewrites := 0
  let mut structural := 0
  let mut maxCol := 0
  let mut i := 0
  while i < n do
    let t := toks[i]!
    if t.firstOnLine && t.isProper then
      tacticLines := tacticLines + 1
      if t.col > maxCol then maxCol := t.col
    if t.isIdent then
      let s := t.src
      if subproofKw.contains s then subproofs := subproofs + 1
      if caseSplitters.contains s then branches := branches + 1
      if alternationKw.contains s then branches := branches + 1
      if automationKw.contains s then automation := automation + 1
      if rewriteKw.contains s then rewrites := rewrites + 1
      if structuralKw.contains s then structural := structural + 1
      i := i + 1
    else if isSemiAltAt toks i then
      -- `<;>` counts as both an alternation combinator and a step separator
      branches := branches + 1
      separators := separators + 1
      i := i + 3
    else if t.isSym then
      if t.src == "<|>" then branches := branches + 1
      else if t.src == ";" then separators := separators + 1
      i := i + 1
    else
      i := i + 1
  -- "automation only": at least one automation step, and no rewriting/structural/case work.
  -- This is the `by simp` / `by omega` class — the proofs a model reproduces without insight.
  let autoOnly := automation > 0 && rewrites == 0 && structural == 0 && subproofs == 0 && branches == 0
  return {
    nLines := nLinesOf block,
    nChars := Int.ofNat block.toUTF8.size,
    nSubproofs := Int.ofNat subproofs,
    nTactics := Int.ofNat (tacticLines + separators),
    cyclomatic := Int.ofNat (1 + branches),
    nAutomation := Int.ofNat automation,
    nRewrites := Int.ofNat rewrites,
    nStructural := Int.ofNat structural,
    automationOnly := autoOnly,
    maxNesting := Int.ofNat maxCol }

/-- JSON fields for a metrics object, inlined flat into the enclosing object so
    the website / classifier sees plain keys. Callers that also carry a separate
    `n_lines` (holes already do) should not double-emit it. -/
def toFields (m : Metrics) : List (String × Json) :=
  [ ("n_lines", Json.num m.nLines),
    ("n_chars", Json.num m.nChars),
    ("n_subproofs", Json.num m.nSubproofs),
    ("n_tactics", Json.num m.nTactics),
    ("cyclomatic", Json.num m.cyclomatic),
    ("n_automation", Json.num m.nAutomation),
    ("n_rewrites", Json.num m.nRewrites),
    ("n_structural", Json.num m.nStructural),
    ("automation_only", Json.bool m.automationOnly),
    ("max_nesting", Json.num m.maxNesting) ]

end Metrics
end Ablator
