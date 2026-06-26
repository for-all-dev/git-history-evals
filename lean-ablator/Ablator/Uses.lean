/-
Single-file usage analysis for `--delete-lemmas` (mirrors the rocq ablator's
`uses.ml`). For each top-level `theorem`/`lemma` we record its span range,
whether its name is ever used *outside a proof body*, whether it carries an
attribute (`@[…]`), and the set of in-file proofs that cite it.

Correct-by-construction: deletable only when every occurrence of the name
outside its own declaration sits inside a theorem/lemma body (so deleting it +
whole-proof-ablating its users leaves a well-formed file). A `def`/`abbrev`
body is term-level context (non-proof), and `@[simp]`-style attributes are
rejected — together these close the simp/automation hole without a prover.
-/

import Ablator.Token
import Ablator.Span
import Ablator.Keyword
import Ablator.Centrality
import Std.Data.HashSet

namespace Ablator

open Std (HashSet)

structure DeletableLemma where
  name    : String
  spanIdx : Nat
  users   : Array Nat -- span indices of theorem/lemma decls whose body cites this
  stmtNames : Array String -- names referenced in the statement (slice closure)
  bodyNames : Array String -- names cited in the proof body (slice closure)
  eligible : Bool
  deriving Inhabited

namespace Uses

/-- Only opaque proof lemmas are deletable (a `def`/`abbrev` is referenced in
    terms/types and can't be deleted safely). -/
def deletableKinds : List String := ["theorem", "lemma"]

/-- Idents in `[lo, hi)`, each plus the last component of a dotted name. -/
def namesIn (toks : Array Token) (lo hi : Nat) : Array String := Id.run do
  let mut acc := #[]
  for i in [lo:hi] do
    let t := toks[i]!
    if t.isIdent then
      acc := acc.push t.src
      match t.src.splitOn "." |>.getLast? with
      | some last => if last != t.src then acc := acc.push last
      | none => pure ()
  return acc

end Uses

/-- `aggressive` relaxes the no-attribute guard (BE only; the caller must
    validate with `--check-build`). The non-proof-use guard is never relaxed. -/
def analyzeUses (toks : Array Token) (spans : Array Span) (aggressive : Bool) :
    Array DeletableLemma := Id.run do
  let n := spans.size
  -- per lemma: (spanIdx, name, cited-set in its body, statement names)
  let mut goals : Array (Nat × String × HashSet String × Array String) := #[]
  -- non-proof occurrences: (name, spanIdx) from statements / non-lemma bodies / other spans
  let mut nonproof : Array (String × Nat) := #[]
  let pushNon (arr : Array (String × Nat)) (names : Array String) (si : Nat) : Array (String × Nat) :=
    names.foldl (fun a x => a.push (x, si)) arr
  for si in [0:n] do
    let s := spans[si]!
    if s.isDecl then
      match findAssign toks s.lo s.hi with
      | some a =>
        let nm := declName toks s.lo s.hi
        let isLemma := match s.cmd with | some k => Uses.deletableKinds.contains k | none => false
        -- the statement [lo, a+1) is non-proof context
        nonproof := pushNon nonproof (Uses.namesIn toks s.lo (a + 1)) si
        if isLemma then
          let cited := Centrality.citedNames toks (a + 1) s.hi {}
          let stmtNames := Uses.namesIn toks s.lo (a + 1)
          goals := goals.push (si, nm, cited, stmtNames)
        else
          -- a def/abbrev body is term-level, also non-proof
          nonproof := pushNon nonproof (Uses.namesIn toks (a + 1) s.hi) si
      | none => nonproof := pushNon nonproof (Uses.namesIn toks s.lo s.hi) si
    else nonproof := pushNon nonproof (Uses.namesIn toks s.lo s.hi) si
  let mut out := #[]
  for (si, nm, cited, stmtNames) in goals do
    let mut users := #[]
    for (si2, _, cited2, _) in goals do
      if si2 != si && cited2.contains nm then users := users.push si2
    let onlyProofs := nonproof.all (fun p => p.1 != nm || p.2 == si)
    let hasAttr := (spans[si]!.lo < toks.size) && (toks[spans[si]!.lo]!).isSymNamed "@"
    let eligible :=
      (aggressive || !hasAttr) && nm.length ≥ Centrality.minNameLen && users.size > 0 && onlyProofs
    out := out.push { name := nm, spanIdx := si, users := users,
                      stmtNames := stmtNames, bodyNames := cited.toArray, eligible := eligible }
  return out

end Ablator
