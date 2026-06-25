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

-- delete-lemmas: `helper` (used by `main`) deletable; `unused` (no user) and
-- `keydef` (a def, referenced in a later statement) are not. Extracted into its
-- own function so Lean emits a separate (smaller) C function — a single
-- monolithic `main` makes gcc -O3 OOM on the generated C.
def deleteTests : StateT Tally IO Unit := do
  let dlSrc := "theorem helper : True := trivial\n\n\
    theorem unused : True := trivial\n\n\
    def keydef : Nat := 0\n\n\
    theorem main : True := helper\n\n\
    theorem about : keydef = keydef := rfl\n"
  let dl := ablate (tokenize dlSrc) { prob := 1.0, deleteLemmas := true } (Rng.mk 0) (fun _ => (0:Int))
  let delNames := dl.deleted.toList.map Prod.fst
  check "delete: helper deleted" (delNames.contains "helper")
  check "delete: unused (no user) not deleted" (! delNames.contains "unused")
  check "delete: helper's statement gone" (! has dl.text "theorem helper")
  check "delete: main's statement kept, proof holed" (has dl.text "theorem main : True :=" && has dl.text "sorry" && ! has dl.text ":= helper")
  check "delete: solution is full original" (dl.solution == dlSrc)

-- solution_diff: apply(challenge, unifiedDiff challenge solution) = solution.
-- Also extracted to keep the generated C functions small (see `deleteTests`).
def diffTests : StateT Tally IO Unit := do
  let rtCases := [("a\nb\nc\n", "a\nB\nc\n"), ("l1\nl2\nl3\nl4\n", "l1\nX\nl3\nY\n"),
                  ("a\nb", "a\nc"), ("same\n", "same\n"), ("", "x\ny\n")]
  for (ca, so) in rtCases do
    check s!"diff round-trip {ca} -> {so}" (applyDiff ca (unifiedDiff ca so) == so)
  -- ablation result round-trips through the diff
  let allr := run { prob := 1.0 }
  check "diff: ablation round-trip" (applyDiff allr.text (unifiedDiff allr.text allr.solution) == allr.solution)
  -- large file + localized change -> tiny diff
  let big := String.join ((List.range 300).map (fun i => s!"def d{i} : Nat := {i}\n"))
  let solB := big ++ "theorem foo : True := by trivial\n"
  let chalB := big ++ "theorem foo : True := sorry\n"
  let dB := unifiedDiff chalB solB
  check "diff: large localized round-trip" (applyDiff chalB dB == solB)
  check "diff: large localized is tiny" (dB.length < solB.length / 4)

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

    -- 11. anonymous-constructor `by`-components: ⟨…, by …⟩ -> ⟨…, by sorry⟩
    let anonSrc := "def m (n : Nat) : { k : Nat // 0 ≤ k } := ⟨n, by omega⟩\n"
    let an := ablate (tokenize anonSrc) { prob := 1.0, minDepth := 2, maxDepth := INF } (Rng.mk 0) (fun _ => (0:Int))
    check "anon: ⟨n, by sorry⟩" (an.ablated == 1 && has an.text "⟨n, by sorry⟩")
    check "anon: method=anon" (an.holes.toList.all (fun h : Hole => h.method == "anon"))
    -- two components both ablate
    let an2 := ablate (tokenize "theorem t (n : Nat) : 0 ≤ n ∧ 0 ≤ n := ⟨by omega, by omega⟩\n")
                 { prob := 1.0, minDepth := 2, maxDepth := INF } (Rng.mk 0) (fun _ => (0:Int))
    check "anon: both components ablated" (an2.ablated == 2)
    -- a `by` nested in parens is NOT a component, so it is kept
    let anP := ablate (tokenize "def v (n : Nat) : { k : Nat // 0 ≤ k } := ⟨id (by exact n), by omega⟩\n")
                 { prob := 1.0, minDepth := 2, maxDepth := INF } (Rng.mk 0) (fun _ => (0:Int))
    check "anon: paren-nested by kept" (anP.ablated == 1 && has anP.text "id (by exact n)")

    deleteTests
    diffTests
  ).run {}
  let total := tally.passed + tally.failed
  IO.println s!"ablate-test: {tally.passed}/{total} passed"
  if tally.failed == 0 then
    return 0
  else
    IO.eprintln s!"{tally.failed} test(s) failed"
    return 1
