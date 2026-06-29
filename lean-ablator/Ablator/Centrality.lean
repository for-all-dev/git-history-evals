/-
Corpus-wide proof-dependency centrality (fan-in by textual name citation). A
declared name `B` is "cited" by declaration `A` if `A`'s body mentions the
identifier `B`; fan-in of `B` is
the number of distinct other declarations that cite it. Removing a high-fan-in
lemma is a harder, more consequential ablation than removing a leaf.

Approximate (matched by name; we also project the last dotted component, so
`Nat.add_comm` cites `add_comm`), and short names (< 3 chars) are dropped to
avoid colliding with local variables (approximate, but no prover required).
-/

import Std.Data.HashMap
import Std.Data.HashSet
import Ablator.Token
import Ablator.Span

namespace Ablator

open Std (HashMap HashSet)

namespace Centrality

def minNameLen : Nat := 3

/-- Identifiers cited within a token range: each ident plus, for a dotted name,
    its last component. -/
def citedNames (toks : Array Token) (lo hi : Nat) (into : HashSet String) : HashSet String := Id.run do
  let mut acc := into
  for i in [lo:hi] do
    let t := toks[i]!
    if t.isIdent then
      acc := acc.insert t.src
      match t.src.splitOn "." |>.getLast? with
      | some last => if last != t.src then acc := acc.insert last
      | none => pure ()
  return acc

/-- For one theory's tokens, collect `(name, cited-set)` for every top-level
    declaration that has a body. -/
def goalsOf (toks : Array Token) (spans : Array Span) : Array (String × HashSet String) := Id.run do
  let mut goals : Array (String × HashSet String) := #[]
  for s in spans do
    if s.isDecl then
      match findDeclBody toks s.lo s.hi with
      | some a =>
        let name := declName toks s.lo s.hi
        let cited := citedNames toks (a + 1) s.hi {}
        goals := goals.push (name, cited)
      | none => pure ()
  return goals

end Centrality

/-- `name -> fan-in` over a whole corpus (each theory given as token+span pair). -/
def fanIn (corpus : Array (Array Token × Array Span)) : HashMap String Int := Id.run do
  let mut goals : Array (String × HashSet String) := #[]
  for (toks, spans) in corpus do
    goals := goals ++ Centrality.goalsOf toks spans
  -- declared names long enough to count
  let mut declared : HashSet String := {}
  for (name, _) in goals do
    if name.length ≥ Centrality.minNameLen then declared := declared.insert name
  let mut fan : HashMap String Int := {}
  for (name, cited) in goals do
    for d in cited do
      if d != name && declared.contains d then
        fan := fan.insert d ((fan.getD d 0) + 1)
  return fan

end Ablator
