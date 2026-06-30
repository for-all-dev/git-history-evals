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

-- delete-lemmas + --count / --truncate: aaa has 2 users (ua1, ua2); bbb has 3
-- (ub1..ub3). In delete mode --count is a target number of *ablations*.
def deleteCountTests : StateT Tally IO Unit := do
  let src := "theorem aaa : True := trivial\n\n\
    theorem bbb : True := trivial\n\n\
    theorem ua1 : True := aaa\n\n\
    theorem ua2 : True := aaa\n\n\
    theorem ub1 : True := bbb\n\n\
    theorem ub2 : True := bbb\n\n\
    theorem ub3 : True := bbb\n"
  let run := fun (k : Nat) (trunc uniform : Bool) (seed : Nat) =>
    ablate (tokenize src) { deleteLemmas := true, deleteUniform := uniform, count := some k, truncate := trunc }
      (Rng.mk (UInt64.ofNat seed)) (fun _ => (0:Int))
  -- count is a target reached by a weighted draw; aaa(2) or bbb(3) alone reaches
  -- ≥ 2, so a single deletion suffices and ablations are ≥ count, reproducible/seed
  let r2 := run 2 false false 1
  check "delete+count=2: ≥ 2 from one deletion" (r2.ablated ≥ 2 && r2.deleted.size == 1)
  check "delete+count=2: reproducible per seed" ((run 2 false false 1).deleted == r2.deleted)
  -- count = 4 needs both lemmas (2 + 3): always 5 ablations, both deleted
  let r4 := run 4 false false 0
  check "delete+count=4: needs both -> 5" (r4.ablated == 5 && r4.deleted.size == 2)
  -- --truncate caps ablations at exactly count, dropping the rest of the file
  check "delete+truncate+count=2: exactly 2" ((run 2 true false 3).ablated == 2)
  check "delete+truncate+count=1: exactly 1" ((run 1 true false 7).ablated == 1)
  -- tail reachability + weighting across seeds: the sole deletion at count=2 is
  -- sometimes the tail (aaa), sometimes the popular one (bbb); weighting favours bbb
  let firstDel := fun (uniform : Bool) (seed : Nat) =>
    match (run 2 false uniform seed).deleted.toList with | (n, _) :: _ => n | [] => ""
  let tally := fun (uniform : Bool) =>
    (List.range 100).foldl (fun (ab : Nat × Nat) s =>
      match firstDel uniform s with
      | "aaa" => (ab.1 + 1, ab.2) | "bbb" => (ab.1, ab.2 + 1) | _ => ab) (0, 0)
  let w := tally false
  let u := tally true
  check "delete: weighted reaches tail (aaa) and popular (bbb)" (w.1 > 0 && w.2 > 0)
  check "delete: weighting favours the more-used lemma (bbb)" (w.2 > w.1)
  check "delete-uniform: both reachable" (u.1 > 0 && u.2 > 0)

-- delete-lemmas + --shrink-* (prefix / minimal slice): base used by u1,u2,u3;
-- un1/un2/un3 unrelated; declare-before-use.
def deleteShrinkTests : StateT Tally IO Unit := do
  let src := "theorem base : True := trivial\n\n\
    theorem un1 : True := trivial\n\n\
    theorem u1 : True := base\n\n\
    theorem un2 : True := trivial\n\n\
    theorem u2 : True := base\n\n\
    theorem un3 : True := trivial\n\n\
    theorem u3 : True := base\n"
  let run := fun (mods : Spec → Spec) =>
    ablate (tokenize src) (mods { deleteLemmas := true, count := some 2 }) (Rng.mk 0) (fun _ => (0:Int))
  -- prefix: keep through 2nd hole (u2), drop u3; base deleted; context kept
  let p := run (fun s => { s with shrinkChallenge := true })
  check "shrink prefix: keeps u1,u2" (has p.text "theorem u1" && has p.text "theorem u2")
  check "shrink prefix: drops u3" (! has p.text "theorem u3")
  check "shrink prefix: base deleted" (! has p.text "theorem base")
  check "shrink prefix: context kept" (has p.text "theorem un1")
  -- minimal slice: only the 2 holes survive
  let m := run (fun s => { s with shrinkChallengeMinimal := true })
  check "slice: keeps u1,u2" (has m.text "theorem u1" && has m.text "theorem u2")
  check "slice: drops unrelated" (! has m.text "theorem un1" && ! has m.text "theorem un2")
  check "slice: drops base,u3" (! has m.text "theorem base" && ! has m.text "theorem u3")
  -- solution slice: base restored with its real proof
  let ms := run (fun s => { s with shrinkSolutionMinimal := true })
  check "solution slice: base restored" (has ms.solution "theorem base")
  check "solution slice: real proof present" (has ms.solution ":= base")

-- --delete-lemmas N: delete exactly N lemmas (regardless of ablation count)
def deleteCountArgTests : StateT Tally IO Unit := do
  let src := "theorem aaa : True := trivial\n\n\
    theorem bbb : True := trivial\n\n\
    theorem ccc : True := trivial\n\n\
    theorem ua : True := aaa\n\n\
    theorem ub : True := bbb\n\n\
    theorem uc : True := ccc\n"
  let ndel := fun (n : Nat) =>
    (ablate (tokenize src) { deleteLemmas := true, deleteCount := some n } (Rng.mk 0) (fun _ => (0:Int))).deleted.size
  check "delete-lemmas 2: exactly 2" (ndel 2 == 2)
  check "delete-lemmas 1: exactly 1" (ndel 1 == 1)
  check "delete-lemmas 9: capped at 3 candidates" (ndel 9 == 3)

-- corollary-delete-lemmas: a chain top -> mid -> base, plus isolated `lonely`. Only
-- `top` ({mid,base}) and `mid` ({base}) have non-empty eligible closures, so whichever
-- corollary the draw lands on, every deletion is in {mid, base} -- never `top`
-- (nothing uses it) or `lonely`.
def corollaryTests : StateT Tally IO Unit := do
  let src := "theorem base : True := trivial\n\n\
    theorem mid : True := base\n\n\
    theorem top : True := mid\n\n\
    theorem lonely : True := trivial\n"
  let del1 := fun (seed : Nat) =>
    (ablate (tokenize src) { deleteLemmas := true, corollary := true, deleteCount := some 1 }
      (Rng.mk (UInt64.ofNat seed)) (fun _ => (0:Int))).deleted.toList.map Prod.fst
  let mut sawBase := false
  let mut sawMid := false
  let mut bad := false
  for s in [0:40] do
    match del1 s with
    | [n] => if n == "base" then sawBase := true
             else if n == "mid" then sawMid := true
             else bad := true
    | _ => bad := true
  check "corollary: =1 deletes one within a corollary closure (never top/lonely)" (! bad)
  check "corollary: both closure members reachable across seeds" (sawBase && sawMid)
  check "corollary: reproducible per seed" (del1 7 == del1 7)
  -- wholesale holes the user; full solution preserved; uniform variant draws from closure
  let rw := ablate (tokenize src) { deleteLemmas := true, corollary := true, deleteCount := some 1 }
    (Rng.mk 2) (fun _ => (0:Int))
  check "corollary: user proof holed; full solution preserved"
    (has rw.text "sorry" && rw.solution == src && rw.deleted.size == 1)
  let du := (ablate (tokenize src) { deleteLemmas := true, corollary := true, deleteUniform := true, deleteCount := some 1 }
    (Rng.mk 5) (fun _ => (0:Int))).deleted.toList.map Prod.fst
  check "corollary-uniform: deletion within the closure" (du.all (fun n => n == "base" || n == "mid"))

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

-- Regressions from the ryu-lean4 real-data baseline:
--  (a) a dependent `let … :=` in a theorem's *type* must not be mistaken for the
--      proof-body `:=` (would truncate the decl); the proof, not the type, is holed.
--  (b) a deleted lemma's leading `/-- doc -/` must travel with it (else orphaned
--      above the next command, e.g. `/-- … -/ end`).
def letDocTests : StateT Tally IO Unit := do
  -- (a) let-in-type: helper is cited in user's proof; user's type carries a `let`.
  let letSrc := "theorem helper : True := trivial\n\n\
    theorem user : let x : Nat := 5\n    x = 5 := by\n  have := helper\n  rfl\n"
  let lt := ablate (tokenize letSrc) { prob := 1.0, deleteLemmas := true } (Rng.mk 0) (fun _ => (0:Int))
  check "let-in-type: helper deleted" ((lt.deleted.toList.map Prod.fst).contains "helper")
  check "let-in-type: type's let value kept (not holed)" (has lt.text "let x : Nat := 5")
  check "let-in-type: proof holed" (has lt.text "x = 5 := sorry")
  check "let-in-type: not truncated at let" (! has lt.text "let x : Nat := sorry")
  check "let-in-type: solution restores original" (lt.solution == letSrc)
  -- (b) doc comment attaches to the documented decl, so deletion removes it too.
  let docSrc := "/-- helper doc -/\ntheorem helper : True := trivial\n\n\
    theorem user : True := helper\n"
  let dc := ablate (tokenize docSrc) { prob := 1.0, deleteLemmas := true } (Rng.mk 0) (fun _ => (0:Int))
  check "doc: helper deleted" ((dc.deleted.toList.map Prod.fst).contains "helper")
  check "doc: orphaned doc comment removed" (! has dc.text "helper doc")
  check "doc: helper statement gone" (! has dc.text "theorem helper")
  check "doc: solution restores doc comment" (has dc.solution "/-- helper doc -/")

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
    deleteCountTests
    deleteShrinkTests
    deleteCountArgTests
    corollaryTests
    letDocTests
    diffTests
  ).run {}
  let total := tally.passed + tally.failed
  IO.println s!"ablate-test: {tally.passed}/{total} passed"
  if tally.failed == 0 then
    return 0
  else
    IO.eprintln s!"{tally.failed} test(s) failed"
    return 1
