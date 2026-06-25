/-
Proof/term ablation for Lean. We replace the body of a selected declaration (or
nested binder) with `sorry`, preserving everything else byte-for-byte.

The unit of ablation is a `name := body` binding. Bindings nest:

    theorem foo : P := by        depth 1   (a top-level declaration)
      have h : Q := by simp      depth 2   (a nested `have`)
      exact h

Top-level depth-1 ablation is exact (split a file into command spans, find the
bracket-depth-0 `:=`, replace the body). Nested ablation (depth >= 2) is
best-effort: Lean has no `qed`, so we bound a `have`/`let`/`suffices` block by
the layout rule — it runs until the next line indented no deeper than the
binder. Single-line proofs and term-mode `have ... ; ...` are not descended into
(documented limitation), but the engine never corrupts source: it only ever
deletes a cleanly-delimited body and splices ` sorry`. `sorry` inhabits any
type, so non-`Prop` `def`/`abbrev` bodies ablate too.
-/

import Std.Data.HashSet
import Ablator.Token
import Ablator.Keyword
import Ablator.Span
import Ablator.Uses

namespace Ablator

open Std (HashSet)

def INF : Int := 9223372036854775807   -- 2^63 - 1, the "infinity" sentinel

/-- Which bindings to ablate. `count` (when set) overrides `prob`. -/
structure Spec where
  prob          : Float := 0.5
  count         : Option Nat := none
  byCentrality  : Bool := false
  minDepth      : Int := 1
  maxDepth      : Int := 1
  leavesOnly    : Bool := false
  minSize       : Int := 0
  maxSize       : Int := INF
  minCentrality : Int := 0
  maxCentrality : Int := INF
  truncate       : Bool := false
  shrinkChallenge : Bool := false
  shrinkSolution  : Bool := false
  deleteLemmas    : Bool := false
  aggressive      : Bool := false
  deriving Inhabited

def Spec.usesCentrality (s : Spec) : Bool :=
  s.minCentrality > 0 || s.maxCentrality != INF || s.byCentrality

/-- A removed proof/body and the difficulty signals recorded for it. -/
structure Hole where
  theoremName : String
  depth       : Int
  nCommands   : Int
  nLines      : Int
  isLeaf      : Bool
  centrality  : Int
  method      : String
  proofText   : String
  deriving Inhabited

structure AblationResult where
  text     : String
  solution : String
  total    : Int
  ablated  : Int
  holes    : Array Hole
  deleted  : Array (String × String) := #[] -- (name, original block) for --delete-lemmas
  deriving Inhabited

/-- How a candidate is chosen for ablation. -/
inductive Selector where
  | prob (p : Float)
  | set  (s : HashSet Nat)
  | never

/- ---------- SplitMix64 PRNG (seedable, reproducible) ---------- -/

structure Rng where
  state : UInt64

namespace Rng
/-- (The structure's auto-generated `Rng.mk : UInt64 → Rng` is the constructor.) -/

def nextU64 (r : Rng) : UInt64 × Rng :=
  let s := r.state + 0x9E3779B97F4A7C15
  let z0 := s
  let z1 := (z0 ^^^ (z0 >>> 30)) * 0xBF58476D1CE4E5B9
  let z2 := (z1 ^^^ (z1 >>> 27)) * 0x94D049BB133111EB
  let z3 := z2 ^^^ (z2 >>> 31)
  (z3, ⟨s⟩)

def nextF64 (r : Rng) : Float × Rng :=
  let (u, r') := r.nextU64
  let m := (u >>> 11).toNat
  (m.toFloat / (Nat.pow 2 53).toFloat, r')

/-- Fisher–Yates shuffle. -/
def shuffle (r : Rng) (xs : Array Nat) : Array Nat × Rng := Id.run do
  let mut a := xs
  let mut rng := r
  let mut i := a.size
  while i > 1 do
    i := i - 1
    let (u, rng') := rng.nextU64
    rng := rng'
    let j := (u.toNat) % (i + 1)
    let tmp := a[i]!
    a := a.set! i a[j]!
    a := a.set! j tmp
  return (a, rng)
end Rng

/- ---------- pure measurements + ablation-unit detection ---------- -/

private def firstProperIdx (toks : Array Token) (lo hi : Nat) : Option Nat := Id.run do
  for i in [lo:hi] do
    if toks[i]!.isProper then return some i
  return none

/-- Index just after the last *proper* token in `[lo, hi)` (so trailing
    whitespace/comments can be preserved verbatim). -/
def lastProperEnd (toks : Array Token) (lo hi : Nat) : Nat := Id.run do
  let mut e := lo
  for i in [lo:hi] do
    if toks[i]!.isProper then e := i + 1
  return e

private def nLinesOf (s : String) : Int :=
  if s.isEmpty then 0 else Int.ofNat (s.toList.foldl (fun acc c => if c == '\n' then acc + 1 else acc) 1)

/-- End of the indentation-delimited block opened at column `col`: the first
    later line at column ≤ `col`, else `hi`. -/
def blockEnd (toks : Array Token) (start hi col : Nat) : Nat := Id.run do
  for j in [start:hi] do
    let t := toks[j]!
    if t.firstOnLine && t.col ≤ col then return j
  return hi

/-- Is there a bracket-depth-0 type ascription `:` in `[lo, hi)`? A binder/decl
    whose value we replace by `sorry` MUST have one — `sorry` has no inferable
    type otherwise (`have := sorry` / `def f := sorry` fail to elaborate). -/
def hasTypeColon (toks : Array Token) (lo hi : Nat) : Bool := Id.run do
  let mut depth := 0
  for i in [lo:hi] do
    let t := toks[i]!
    if isOpenBracket t then depth := depth + 1
    else if isCloseBracket t then depth := if depth == 0 then 0 else depth - 1
    else if depth == 0 && t.isSymNamed ":" then return true
  return false

/-- Index of the first bracket-depth-0 `=>` in `[lo, hi)` (a match/`fun` arm). -/
def findArrow (toks : Array Token) (lo hi : Nat) : Option Nat := Id.run do
  let mut depth := 0
  for i in [lo:hi] do
    let t := toks[i]!
    if isOpenBracket t then depth := depth + 1
    else if isCloseBracket t then depth := if depth == 0 then 0 else depth - 1
    else if depth == 0 && t.isSymNamed "=>" then return some i
  return none

/-- Name bound by a nested `have h`/`let h`/`suffices h` (`""` if anonymous). -/
def nestedBinderName (toks : Array Token) (binderIdx assignIdx : Nat) : String := Id.run do
  for i in [binderIdx+1:assignIdx] do
    let t := toks[i]!
    if t.isProper then
      if t.isIdent then return t.src else return ""
  return ""

/-- Innermost still-open bracket char enclosing token `i` (a backward scan),
    or `none` at top level. Used to spot anonymous-constructor components. -/
def enclosingBracket (toks : Array Token) (i : Nat) : Option Char := Id.run do
  let mut depth := 0
  let mut j := i
  while j > 0 do
    j := j - 1
    let t := toks[j]!
    if isCloseBracket t then depth := depth + 1
    else if isOpenBracket t then
      if depth == 0 then return t.src.get 0 else depth := depth - 1
  return none

/-- Source of the proper token immediately before `i` (skipping whitespace). -/
def prevProperSrc (toks : Array Token) (i : Nat) : Option String := Id.run do
  let mut j := i
  while j > 0 do
    j := j - 1
    if toks[j]!.isProper then return toks[j]!.src
  return none

/-- End of an anonymous-constructor component starting at `start`: the first
    bracket-depth-0 `,` (next component) or closing bracket (end of `⟨…⟩`). -/
def componentEnd (toks : Array Token) (start hi : Nat) : Nat := Id.run do
  let mut depth := 0
  for j in [start:hi] do
    let t := toks[j]!
    if isOpenBracket t then depth := depth + 1
    else if isCloseBracket t then
      if depth == 0 then return j else depth := depth - 1
    else if depth == 0 && t.isSymNamed "," then return j
  return hi

/-- A focusing bullet token: `·`, or ASCII `.` *followed by space* (so the
    leading-dot of `.gate`/`.map` projections is never mistaken for a bullet). -/
def isBulletLead (toks : Array Token) (i : Nat) : Bool :=
  let t := toks[i]!
  t.isSym && (t.src == "·" || (t.src == "." && i + 1 < toks.size && toks[i+1]!.isSpace))

/-- The kinds of body we can replace with `sorry`. -/
inductive UnitKind where
  | binder   -- `have`/`let`/… `x : T := <value>`
  | bullet   -- `·` / `.` focused tactic block
  | arm      -- `| pat => <rhs>` match/`fun` arm
  | anon     -- `by <tac>` component of an anonymous constructor `⟨…, by …⟩`
  deriving DecidableEq, Inhabited

/-- A detected ablation unit: its content range, the column it opened at, an
    optional bound name, and whether it carries a type (so `sorry` is well-typed). -/
structure FoundUnit where
  kind      : UnitKind
  contentLo : Nat   -- first token of the replaceable value (after `:=` / `·` / `=>`)
  blockEnd  : Nat   -- end of this unit's indentation block
  unitCol   : Nat
  name      : String
  hasType   : Bool
  deriving Inhabited

/-- Detect an ablatable unit opening at token `i`: an anonymous-constructor
    `by`-component (bracket-position based, any column), or a line-leading
    binder / bullet / match-arm indented deeper than `parentCol`. -/
def detectUnit (toks : Array Token) (i outerHi parentCol : Nat) : Option FoundUnit :=
  let t := toks[i]!
  -- `⟨…, by …⟩` / `⟨by …⟩`: a tactic proof in constructor-component position.
  -- Replacing yields `by sorry` (header = the `by`), which is type-safe because
  -- the component elaborates against the field's expected type.
  if t.isIdentNamed "by" && enclosingBracket toks i == some '⟨'
      && (prevProperSrc toks i == some "⟨" || prevProperSrc toks i == some ",") then
    some { kind := .anon, contentLo := i+1, blockEnd := componentEnd toks (i+1) outerHi,
           unitCol := t.col, name := "", hasType := true }
  else if !(t.firstOnLine && t.isProper && t.col > parentCol) then none
  else
    let bEnd := blockEnd toks (i+1) outerHi t.col
    if t.isIdent && Keyword.isBinder t.src then
      match findAssign toks (i+1) bEnd with
      | some a => some { kind := .binder, contentLo := a+1, blockEnd := bEnd, unitCol := t.col,
                         name := nestedBinderName toks i a, hasType := hasTypeColon toks (i+1) a }
      | none => none
    else if isBulletLead toks i then
      some { kind := .bullet, contentLo := i+1, blockEnd := bEnd, unitCol := t.col, name := "", hasType := true }
    else if t.isSymNamed "|" then
      match findArrow toks (i+1) bEnd with
      | some ar => some { kind := .arm, contentLo := ar+1, blockEnd := bEnd, unitCol := t.col, name := "", hasType := true }
      | none => none
    else none

/-- Does `[lo, hi)` contain any nested ablatable unit deeper than `parentCol`? -/
def hasNestedUnit (toks : Array Token) (lo hi parentCol : Nat) : Bool := Id.run do
  for i in [lo:hi] do
    if (detectUnit toks i hi parentCol).isSome then return true
  return false

/-- A short label for a binder body's method, for difficulty stratification:
    `by:<tac>`, `by`, `calc`, `trivial` (`rfl`), or `term`. -/
def classifyMethod (toks : Array Token) (lo hi : Nat) : String := Id.run do
  match firstProperIdx toks lo hi with
  | none => return "?"
  | some fp =>
    let ft := toks[fp]!
    if ft.isIdentNamed "by" then
      for j in [fp+1:hi] do
        if toks[j]!.isProper && toks[j]!.isIdent then return "by:" ++ toks[j]!.src
      return "by"
    else if ft.isIdentNamed "calc" then
      return "calc"
    else
      let mut nProper := 0
      let mut onlySrc := ""
      for j in [lo:hi] do
        if toks[j]!.isProper then
          nProper := nProper + 1
          onlySrc := toks[j]!.src
      if nProper == 1 && (onlySrc == "rfl" || onlySrc == "trivial") then return "trivial"
      else return "term"

structure Body where
  nCommands : Int
  isLeaf    : Bool
  method    : String

def measureBody (toks : Array Token) (lo hi parentCol : Nat) : Body := Id.run do
  let mut firstOnLineCount := 0
  for i in [lo:hi] do
    let t := toks[i]!
    if t.firstOnLine && t.isProper then firstOnLineCount := firstOnLineCount + 1
  -- +1 if the body shares the header line (its first proper token isn't line-leading)
  let headerLineExtra :=
    match firstProperIdx toks lo hi with
    | some fp => if toks[fp]!.firstOnLine then 0 else 1
    | none => 0
  let nCmds := Nat.max 1 (firstOnLineCount + headerLineExtra)
  return { nCommands := Int.ofNat nCmds, isLeaf := !hasNestedUnit toks lo hi parentCol,
           method := classifyMethod toks lo hi }

/- ---------- the walk (StateM over WState) ---------- -/

structure WState where
  toks       : Array Token
  spec       : Spec
  centrality : String → Int
  selector   : Selector
  rng        : Rng
  out        : String := ""
  outLen     : Nat := 0
  holes      : Array Hole := #[]
  matchAcc    : Array (Nat × Int) := #[]
  total      : Int := 0
  ablated    : Int := 0
  lastSorryEnd : Int := -1
  origLen    : Nat := 0
  -- (challenge-offset-end, original-offset-end, isGoal, hadSorry)
  topSegs    : Array (Nat × Nat × Bool × Bool) := #[]

abbrev W := StateM WState

private def emit (s : String) : W Unit :=
  modify fun st => { st with out := st.out ++ s, outLen := st.outLen + s.length }

private def emitTokens (lo hi : Nat) : W Unit := do
  let toks := (← get).toks
  for i in [lo:hi] do
    emit toks[i]!.src

private def decideAblate (stmtIdx : Nat) : W Bool := do
  let st ← get
  match st.selector with
  | .never => return false
  | .set s => return s.contains stmtIdx
  | .prob p =>
    let (x, r') := st.rng.nextF64
    set { st with rng := r' }
    return x < p

/-- The method label recorded for a hole: bullets/arms are tagged by kind,
    binders (and top-level decls) by their proof method. -/
private def methodLabel (toks : Array Token) (contentLo contentHi : Nat) : UnitKind → String
  | .bullet => "bullet"
  | .arm    => "arm"
  | .anon   => "anon"
  | .binder => classifyMethod toks contentLo contentHi

mutual
  /-- A unit whose header (through `:=` / `·` / `=>`) is already emitted.
      `contentLo..contentHi` is the replaceable value (proper tokens),
      `trailingHi` bounds the trailing whitespace, `unitCol` is the column the
      unit opened at, `hasType` says `sorry` is well-typed here. -/
  partial def handleGoal (stmtIdx contentLo contentHi trailingHi unitCol : Nat)
      (depth : Int) (name : String) (hasType : Bool) (kind : UnitKind) : W Unit := do
    let st ← get
    let spec := st.spec
    let m := measureBody st.toks contentLo contentHi unitCol
    let cent := st.centrality name
    let candidate :=
      hasType &&
      depth ≥ spec.minDepth && depth ≤ spec.maxDepth &&
      (!spec.leavesOnly || m.isLeaf) &&
      m.nCommands ≥ spec.minSize && m.nCommands ≤ spec.maxSize &&
      cent ≥ spec.minCentrality && cent ≤ spec.maxCentrality
    if candidate then
      modify fun s => { s with total := s.total + 1, matchAcc := s.matchAcc.push (stmtIdx, cent) }
      if ← decideAblate stmtIdx then
        emit " sorry"
        let proofText := implode (st.toks.extract contentLo contentHi)
        modify fun s => { s with
          lastSorryEnd := Int.ofNat s.outLen,
          ablated := s.ablated + 1,
          holes := s.holes.push {
            theoremName := name, depth := depth, nCommands := m.nCommands,
            nLines := nLinesOf proofText, isLeaf := m.isLeaf, centrality := cent,
            method := methodLabel st.toks contentLo contentHi kind, proofText := proofText } }
        emitTokens contentHi trailingHi
      else
        walkBody contentLo contentHi trailingHi unitCol depth
    else if depth > spec.maxDepth then
      emitTokens contentLo trailingHi              -- too deep: verbatim
    else
      walkBody contentLo contentHi trailingHi unitCol depth

  /-- Emit a body verbatim, descending into nested units (the "keep" path). -/
  partial def walkBody (lo hi trailingHi parentCol : Nat) (depth : Int) : W Unit := do
    let toks := (← get).toks
    let mut i := lo
    while i < hi do
      match detectUnit toks i hi parentCol with
      | some u =>
        emitTokens i u.contentLo
        let contentHi := lastProperEnd toks u.contentLo u.blockEnd
        handleGoal i u.contentLo contentHi u.blockEnd u.unitCol (depth + 1) u.name u.hasType u.kind
        i := u.blockEnd
      | none =>
        emit toks[i]!.src
        i := i + 1
    emitTokens hi trailingHi
end

/-- Top-level pass: walk command spans, ablating decl bodies. -/
def runWalk : W Unit := do
  let st ← get
  let toks := st.toks
  let spans := parseSpans toks
  for s in spans do
    -- the original length this span contributes (statement + proof, verbatim)
    let srcLen := (s.source toks).length
    if s.isDecl then
      match findAssign toks s.lo s.hi with
      | some a =>
        let ablated0 := (← get).ablated
        let binderCol := match firstProperIdx toks s.lo s.hi with
          | some fp => toks[fp]!.col
          | none => 0
        let name := declName toks s.lo s.hi
        let hasTy := hasTypeColon toks s.lo a
        let contentHi := lastProperEnd toks (a+1) s.hi
        emitTokens s.lo (a+1)
        handleGoal s.lo (a+1) contentHi s.hi binderCol 1 name hasTy .binder
        let now ← get
        let orig := now.origLen + srcLen
        modify fun z => { z with origLen := orig }
        -- a goal is never a structural closer
        modify fun z => { z with topSegs := z.topSegs.push (now.outLen, orig, false, now.ablated > ablated0) }
      | none =>
        emit (s.source toks)
        let orig := (← get).origLen + srcLen
        modify fun z => { z with origLen := orig }
        modify fun z => { z with topSegs := z.topSegs.push (z.outLen, orig, false, false) }
    else
      emit (s.source toks)
      let orig := (← get).origLen + srcLen
      let closer := s.cmd == some "end"   -- `end` closes a namespace/section
      modify fun z => { z with origLen := orig }
      modify fun z => { z with topSegs := z.topSegs.push (z.outLen, orig, closer, false) }

/- ---------- context shaping (truncate / shrink) ---------- -/

/-- Collapse runs of >=2 blank lines into a single blank line (tidies up the
    gaps left behind by `shrink`). -/
def collapseBlankLines (s : String) : String := Id.run do
  let cs := s.toList.toArray
  let n := cs.size
  let mut out := ""
  let mut i := 0
  while i < n do
    if cs[i]! == '\n' then
      -- count following [ \t]*\n groups
      let mut j := i + 1
      let mut groups := 0
      let mut cont := true
      while cont do
        let mut k := j
        while k < n && (cs[k]! == ' ' || cs[k]! == '\t') do k := k + 1
        if k < n && cs[k]! == '\n' then
          j := k + 1
          groups := groups + 1
        else
          cont := false
      if groups ≥ 2 then
        out := out ++ "\n\n"
        i := j
      else
        out := out ++ "\n"
        i := i + 1
    else
      out := out.push cs[i]!
      i := i + 1
  return out

/-- Drop all top-level segments after the last ablated one, keeping structural
    closers (`end`) so namespaces/sections still close. `segs` are
    `(char-offset-end, isCloser, hadSorry)`. The same operation shrinks either
    the challenge or the solution — only the offsets differ. -/
def shrink (full : String) (segs : Array (Nat × Bool × Bool)) : String := Id.run do
  let mut last : Option Nat := none
  for idx in [0:segs.size] do
    let (_, _isCloser, hadSorry) := segs[idx]!
    if hadSorry then last := some idx
  match last with
  | none => return full
  | some lastIdx =>
    let cs := full.toList.toArray
    let mut out := ""
    let mut prev := 0
    let mut gap := false      -- dropped a segment since the last kept one
    let mut endsNl := true    -- does `out` currently end in a newline?
    for idx in [0:segs.size] do
      let (endo, isCloser, _) := segs[idx]!
      if idx ≤ lastIdx || isCloser then
        -- a kept closer after a gap (e.g. `end`) must not glue onto the
        -- previous token — ensure a newline separates them.
        if gap && !endsNl then
          out := out.push '\n'
          endsNl := true
        if endo > prev then
          out := out ++ String.mk (cs.extract prev endo).toList
          endsNl := (cs[endo-1]! == '\n')
        gap := false
      else
        gap := true
      prev := endo
    return collapseBlankLines out

/-- Run one ablation pass with a given selector; returns the result and the list
    of `(stmtIdx, centrality)` matches (used by count-mode). -/
def walkAll (toks : Array Token) (spec : Spec) (centrality : String → Int)
    (selector : Selector) (rng : Rng) : AblationResult × Array (Nat × Int) :=
  let init : WState := { toks := toks, spec := spec, centrality := centrality,
                         selector := selector, rng := rng }
  let (_, st) := runWalk.run init
  let full := st.out
  let original := implode toks
  let chalSegs := st.topSegs.map (fun (c, _, g, a) => (c, g, a))
  let solSegs := st.topSegs.map (fun (_, o, g, a) => (o, g, a))
  let text :=
    if spec.truncate && st.lastSorryEnd ≥ 0 then
      String.mk (full.toList.take st.lastSorryEnd.toNat)
    else if spec.shrinkChallenge then
      shrink full chalSegs
    else full
  let solution := if spec.shrinkSolution then shrink original solSegs else original
  ({ text := text, solution := solution, total := st.total, ablated := st.ablated, holes := st.holes },
   st.matchAcc)

/-- `--delete-lemmas`: delete eligible used lemmas + whole-proof-ablate users. -/
def ablateDelete (toks : Array Token) (spec : Spec) (rng : Rng) : AblationResult := Id.run do
  let spans := parseSpans toks
  let lemmas := analyzeUses toks spans spec.aggressive
  let totalEligible := (lemmas.filter (·.eligible)).size
  let cands := lemmas.filter (fun l =>
    l.eligible && Int.ofNat l.users.size ≥ spec.minCentrality
    && Int.ofNat l.users.size ≤ spec.maxCentrality)
  let mut selected : Array DeletableLemma := #[]
  let mut r := rng
  match spec.count with
  | some k =>
    if cands.size ≤ k then selected := cands
    else if spec.byCentrality then
      let sorted := cands.qsort (fun a b =>
        a.users.size > b.users.size || (a.users.size == b.users.size && a.spanIdx < b.spanIdx))
      selected := sorted.extract 0 k
    else
      let (sh, r') := r.shuffle (Array.range cands.size)
      r := r'
      selected := (sh.extract 0 k).map (fun i => cands[i]!)
  | none =>
    for l in cands do
      let (x, r') := r.nextF64
      r := r'
      if x < spec.prob then selected := selected.push l
  let delSet : HashSet Nat := HashSet.ofArray (selected.map (·.spanIdx))
  let mut userSet : HashSet Nat := {}
  for l in selected do
    for u in l.users do
      if !delSet.contains u then userSet := userSet.insert u
  let nameOf (si : Nat) : String :=
    match lemmas.find? (fun l => l.spanIdx == si) with | some l => l.name | none => ""
  let mut out := ""
  let mut holes : Array Hole := #[]
  let mut deleted : Array (String × String) := #[]
  let mut ablated : Int := 0
  for si in [0:spans.size] do
    let s := spans[si]!
    if delSet.contains si then
      deleted := deleted.push (nameOf si, s.source toks)
    else if userSet.contains si then
      match findAssign toks s.lo s.hi with
      | some a =>
        let contentHi := lastProperEnd toks (a + 1) s.hi
        out := out ++ implode (toks.extract s.lo (a + 1)) ++ " sorry"
                   ++ implode (toks.extract contentHi s.hi)
        let proofText := implode (toks.extract (a + 1) contentHi)
        holes := holes.push {
          theoremName := nameOf si, depth := 1, nCommands := 0, nLines := nLinesOf proofText,
          isLeaf := true, centrality := 0, method := "deleted-dep", proofText := proofText }
        ablated := ablated + 1
      | none => out := out ++ s.source toks
    else out := out ++ s.source toks
  return {
    text := collapseBlankLines out, solution := implode toks,
    total := Int.ofNat totalEligible, ablated := ablated, holes := holes, deleted := deleted }

/-- Public entry. `centrality` maps a name to its corpus fan-in (0 if unused). -/
def ablate (toks : Array Token) (spec : Spec) (rng : Rng) (centrality : String → Int) : AblationResult :=
  if spec.deleteLemmas then ablateDelete toks spec rng
  else
  match spec.count with
  | some target =>
    -- enumerate matchAcc (never ablate), select a subset, then ablate exactly those
    let (_, cands) := walkAll toks spec centrality .never rng
    let selected : HashSet Nat :=
      if cands.size ≤ target then
        HashSet.ofArray (cands.map (·.1))
      else if spec.byCentrality then
        let sorted := cands.qsort (fun a b => a.2 > b.2 || (a.2 == b.2 && a.1 < b.1))
        HashSet.ofArray ((sorted.extract 0 target).map (·.1))
      else
        let (idxShuffled, _) := rng.shuffle (cands.map (·.1))
        HashSet.ofArray (idxShuffled.extract 0 target)
    let (r, _) := walkAll toks spec centrality (.set selected) rng
    { r with total := Int.ofNat cands.size }
  | none =>
    (walkAll toks spec centrality (.prob spec.prob) rng).1

/- ---------- difficulty preset ladder (easy -> hard) ---------- -/

structure Preset where
  prob       : Float
  minDepth   : Int
  maxDepth   : Int
  leavesOnly : Bool
  deriving Inhabited

def ladder : Array Preset := #[
  { prob := 0.3, minDepth := 1, maxDepth := INF, leavesOnly := true },   -- L0
  { prob := 1.0, minDepth := 1, maxDepth := INF, leavesOnly := true },   -- L1
  { prob := 1.0, minDepth := 2, maxDepth := INF, leavesOnly := false },  -- L2
  { prob := 0.5, minDepth := 1, maxDepth := 1, leavesOnly := false },    -- L3
  { prob := 1.0, minDepth := 1, maxDepth := 1, leavesOnly := false } ]   -- L4

def presetOf (s : String) : Option Preset :=
  let t := s.toLower.dropWhile (· == 'l')
  match t.toNat? with
  | some i => if i < ladder.size then some ladder[i]! else none
  | none => none

end Ablator
