/-
Unit tests for the Lean ablator (`lake exe ablate-test`). Exits non-zero on any
failure — a CI gate complementing `ablate --check` (which validates over a real
corpus). Kept dependency-free; no test framework, just `IO` assertions.
-/

import Ablator

open Ablator

def SAMPLE : String :=
"namespace Demo

theorem add_zero (n : Nat) : n + 0 = n := by
  simp

def double (n : Nat) : Nat := n + n

theorem nested : True ∧ True := by
  have h1 : True := by
    trivial
  have h2 : True := trivial
  exact ⟨h1, h2⟩

end Demo
"

structure Tally where
  passed : Nat := 0
  failed : Nat := 0

abbrev T := StateM Tally

def check (name : String) (cond : Bool) : StateT Tally IO Unit := do
  if cond then
    modify fun t => { t with passed := t.passed + 1 }
  else
    modify fun t => { t with failed := t.failed + 1 }
    IO.eprintln s!"  FAIL: {name}"

/-- Ablate `SAMPLE` with a spec (no centrality), returning the result. -/
def run (spec : Spec) : AblationResult :=
  let toks := tokenize SAMPLE
  ablate toks spec (Rng.mk 0) (fun _ => (0 : Int))

/-- Does `hay` contain `needle` as a substring? -/
def has (hay needle : String) : Bool := (hay.splitOn needle).length > 1

def main : IO UInt32 := do
  let (_, tally) ← (do
    let toks := tokenize SAMPLE

    -- 1. lossless tokenization round-trip
    check "tokenize round-trips" (implode toks == SAMPLE)

    -- 2. identity at prob 0
    let id := run { prob := 0.0 }
    check "prob 0 is identity" (id.text == SAMPLE)

    -- 3. top-level --all replaces all three bodies, statements kept
    let all := run { prob := 1.0 }
    check "all: 3 top-level bodies" (all.total == 3 && all.ablated == 3)
    check "all: theorem stmt kept" (has all.text "theorem add_zero (n : Nat) : n + 0 = n := sorry")
    check "all: def body ablated" (has all.text "def double (n : Nat) : Nat := sorry")
    check "all: no leftover `by simp`" (! has all.text "by\n  simp")

    -- 4. nested depth-2 ablation: the two inner `have`s, skeleton kept
    let nested := run { prob := 1.0, minDepth := 2, maxDepth := INF }
    check "nested: 2 inner goals" (nested.total == 2 && nested.ablated == 2)
    check "nested: h1 ablated" (has nested.text "have h1 : True := sorry")
    check "nested: h2 ablated" (has nested.text "have h2 : True := sorry")
    check "nested: outer skeleton kept" (has nested.text "exact ⟨h1, h2⟩")

    -- 5. leaves-only keeps non-leaf top-level skeleton, ablates its leaves
    let l1 := run { prob := 1.0, minDepth := 1, maxDepth := INF, leavesOnly := true }
    check "L1: add_zero (leaf) ablated" (has l1.text "add_zero (n : Nat) : n + 0 = n := sorry")
    check "L1: nested skeleton kept" (has l1.text "exact ⟨h1, h2⟩")

    -- 6. method classification
    let methods := all.holes.toList.map (fun h : Hole => h.method)
    check "method by:simp present" (methods.contains "by:simp")
    check "method term present" (methods.contains "term")

    -- 7. centrality fan-in
    let cToks := tokenize "namespace C\ntheorem base (n : Nat) : n = n := rfl\ntheorem u1 : True := by exact base 0 ▸ trivial\ntheorem u2 : True := by exact base 1 ▸ trivial\nend C\n"
    let fan := fanIn #[(cToks, parseSpans cToks)]
    check "centrality: base cited twice" (fan.getD "base" 0 == 2)

    -- 8. bullets (· and .) ablate to `· sorry` at depth 2
    let bulletSrc := "theorem b (p q : Prop) (hp : p) (hq : q) : p ∧ q := by\n  apply And.intro\n  · exact hp\n  . exact hq\n"
    let bul := ablate (tokenize bulletSrc) { prob := 1.0, minDepth := 2, maxDepth := INF } (Rng.mk 0) (fun _ => (0:Int))
    check "bullet: 2 bullets ablated" (bul.ablated == 2)
    check "bullet: · sorry" (has bul.text "· sorry")
    check "bullet: . sorry" (has bul.text ". sorry")
    check "bullet: method=bullet" (bul.holes.toList.all (fun h : Hole => h.method == "bullet"))

    -- 9. match arms ablate to `| pat => sorry`
    let armSrc := "def f (n : Nat) : Nat :=\n  match n with\n  | 0 => 100\n  | k + 1 => k\n"
    let arm := ablate (tokenize armSrc) { prob := 1.0, minDepth := 2, maxDepth := INF } (Rng.mk 0) (fun _ => (0:Int))
    check "arm: 2 arms ablated" (arm.ablated == 2)
    check "arm: | 0 => sorry" (has arm.text "| 0 => sorry")
    check "arm: method=arm" (arm.holes.toList.all (fun h : Hole => h.method == "arm"))

    -- 10. type-ascription safety: untyped `have :=`/`let :=`/`def :=` are NOT ablated
    let utSrc := "theorem t : True := by\n  have h := trivial\n  let x := 5\n  exact h\n"
    let ut := ablate (tokenize utSrc) { prob := 1.0, minDepth := 2, maxDepth := INF } (Rng.mk 0) (fun _ => (0:Int))
    check "untyped have/let not ablated" (ut.ablated == 0)
    let defSrc := "def foo := 5\ndef bar : Nat := 7\n"
    let dab := ablate (tokenize defSrc) { prob := 1.0, minDepth := 1, maxDepth := 1 } (Rng.mk 0) (fun _ => (0:Int))
    check "untyped def kept, typed def ablated" (dab.ablated == 1 && has dab.text "def foo := 5" && has dab.text "def bar : Nat := sorry")
  ).run {}
  let total := tally.passed + tally.failed
  IO.println s!"ablate-test: {tally.passed}/{total} passed"
  if tally.failed == 0 then
    return 0
  else
    IO.eprintln s!"{tally.failed} test(s) failed"
    return 1
