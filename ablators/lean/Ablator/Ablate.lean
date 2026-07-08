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
import Std.Data.HashMap
import Ablator.Token
import Ablator.Keyword
import Ablator.Span
import Ablator.Uses
import Ablator.Metrics

namespace Ablator

open Std (HashSet HashMap)

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
  shrinkChallengeMinimal : Bool := false
  shrinkSolutionMinimal  : Bool := false
  deleteLemmas    : Bool := false
  deleteCount     : Option Nat := none
  deleteUniform   : Bool := false
  deleteLeaves    : Bool := false
  aggressive      : Bool := false
  -- corollary mode: restrict deletion candidates to one random theorem's dep closure
  corollary       : Bool := false
  deriving Inhabited

def Spec.usesCentrality (s : Spec) : Bool :=
  s.minCentrality > 0 || s.maxCentrality != INF || s.byCentrality

/-- A removed proof/body and the difficulty signals recorded for it. `metrics`
    covers the proof body (`proofText`) per spec §2; its `nLines` matches the
    separate `nLines` field. -/
structure Hole where
  theoremName : String
  depth       : Int
  nCommands   : Int
  nLines      : Int
  isLeaf      : Bool
  centrality  : Int
  method      : String
  proofText   : String
  metrics     : Metrics
  deriving Inhabited

/-- A lemma the ablator removed entirely (`--delete-lemmas` / corollary modes).
    `fanIn` is its in-file user count (how many proofs cited it); `metrics` covers
    the whole block for size and the proof body for the tactic heuristics. -/
structure DeletedLemma where
  name    : String
  text    : String  -- original block: statement + proof
  fanIn   : Int
  metrics : Metrics
  deriving Inhabited, BEq

/-- The theorem whose transitive in-file dependency closure seeded a corollary-mode
    deletion (empty outside corollary mode). It is typically also a holed theorem. -/
structure Corollary where
  name    : String
  fanIn   : Int
  metrics : Metrics
  deriving Inhabited, BEq

structure AblationResult where
  text     : String
  solution : String
  total    : Int
  ablated  : Int
  holes    : Array Hole
  deleted  : Array DeletedLemma := #[]     -- lemmas removed for --delete-lemmas
  corollaries : Array Corollary := #[]     -- seeds of corollary-mode deletions (else #[])
  closureSize : Int := 0                   -- distinct eligible closure members drawn from (else 0)
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

/-- Metrics for a holed proof, whose only available text is the proof body. -/
private def holeMetrics (proofText : String) : Metrics := Metrics.compute proofText proofText

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
            method := methodLabel st.toks contentLo contentHi kind, proofText := proofText,
            metrics := holeMetrics proofText } }
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
      match findDeclBody toks s.lo s.hi with
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
      -- keep block openers (`namespace`/`section`) too, not just the `end` closer,
      -- so shrink can't drop an opener while keeping its `end` -> unbalanced file.
      let closer := s.cmd == some "end" || s.cmd == some "namespace" || s.cmd == some "section"
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
def shrink (full : String) (segs : Array (Nat × Bool × Bool)) (count : Option Nat) : String := Id.run do
  let mut last : Option Nat := none
  let mut seen := 0
  for idx in [0:segs.size] do
    let (_, _isCloser, hadSorry) := segs[idx]!
    if hadSorry then
      seen := seen + 1
      match count with
      | some n => if seen ≤ n then last := some idx
      | none => last := some idx
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
      shrink full chalSegs spec.count
    else full
  let solution := if spec.shrinkSolution then shrink original solSegs spec.count else original
  ({ text := text, solution := solution, total := st.total, ablated := st.ablated, holes := st.holes },
   st.matchAcc)

/- ---------- leaf-level user ablation (--delete-lemmas-leaves) ----------
   Hole the smallest enclosing nested unit (have/bullet/arm/anon, via `detectUnit`)
   that cites a deleted name at its own level, keeping the surrounding proof skeleton;
   fall back to whole-proof when a deleted name is cited at the user's top level. The
   Lean analogue of the rust/scala Isar-keyword version (here nesting is layout-based). -/

def citesDeleted (toks : Array Token) (lo hi : Nat) (deleted : HashSet String) : Bool :=
  (Uses.namesIn toks lo hi).any deleted.contains

/-- Does `[lo,hi)` cite a deleted name at its OWN level (outside nested units deeper
    than `parentCol`)? -/
partial def ownCitesLean (toks : Array Token) (lo hi parentCol : Nat) (deleted : HashSet String) : Bool := Id.run do
  let mut i := lo
  while i < hi do
    match detectUnit toks i hi parentCol with
    | some u => i := u.blockEnd
    | none =>
      if citesDeleted toks i (i + 1) deleted then return true
      i := i + 1
  return false

/-- Render body `[lo,hi)` (opened at `parentCol`), holing the innermost units that
    own-level-cite a deleted name. `(text, all_covered)`: `all_covered=false` ⇒ a
    deleted name survives at this level (caller whole-proofs). -/
partial def leafRenderLean (toks : Array Token) (lo hi parentCol : Nat) (deleted : HashSet String) : String × Bool := Id.run do
  let mut out := ""
  let mut ok := true
  let mut i := lo
  while i < hi do
    match detectUnit toks i hi parentCol with
    | some u =>
      let contentHi := lastProperEnd toks u.contentLo u.blockEnd
      if citesDeleted toks i u.blockEnd deleted then
        if u.hasType && ownCitesLean toks u.contentLo contentHi u.unitCol deleted then
          out := out ++ implode (toks.extract i u.contentLo) ++ " sorry"
                     ++ implode (toks.extract contentHi u.blockEnd)
        else
          let (sub, sok) := leafRenderLean toks u.contentLo contentHi u.unitCol deleted
          out := out ++ implode (toks.extract i u.contentLo) ++ sub ++ implode (toks.extract contentHi u.blockEnd)
          ok := ok && sok
      else
        out := out ++ implode (toks.extract i u.blockEnd)
      i := u.blockEnd
    | none =>
      if citesDeleted toks i (i + 1) deleted then ok := false
      out := out ++ toks[i]!.src
      i := i + 1
  return (out, ok)

/-- Render one holed user. `[lo,a]` is the statement through `:=` (at `a`); the body
    is `[a+1, contentHi)`, trailing `[contentHi, hi)`. With `leaves`, hole the smallest
    citing steps (whole-proof fallback); otherwise whole-proof. -/
def renderUserLean (toks : Array Token) (lo a contentHi hi parentCol : Nat) (deleted : HashSet String) (leaves : Bool) : String :=
  let stmt := implode (toks.extract lo (a + 1))
  let trailing := implode (toks.extract contentHi hi)
  if leaves then
    let (body, ok) := leafRenderLean toks (a + 1) contentHi parentCol deleted
    if ok then stmt ++ body ++ trailing else stmt ++ " sorry" ++ trailing
  else stmt ++ " sorry" ++ trailing

/-- Minimal dependency-closed slice for `--shrink-*-minimal` (mirrors rocq
    `slice_delete`): keep the (first `count`) holes + the transitive closure of the
    goal-decls their statements reference; all non-goal items kept as glue. Challenge
    excludes the deleted lemma(s) and re-holes any kept goal still citing a deleted
    name; solution keeps everything real (restoring the deleted lemma + deps). -/
def sliceDelete (toks : Array Token) (spans : Array Span) (spec : Spec)
    (lemmas : Array DeletableLemma) (delSet userSet : HashSet Nat) (solution : Bool) : String := Id.run do
  let bySpan := fun (si : Nat) => lemmas.find? (fun l => l.spanIdx == si)
  -- name -> ALL openers that define it. A name may be declared more than once (e.g. the
  -- same lemma name inside different namespaces/sections); keeping only one — worse, an
  -- arbitrary `find?` match — can drop the definition a kept reference resolves to,
  -- leaving a dangling reference in the slice. (Mirrors rocq `openers_of_name`.)
  -- Resolve a cited name to lemma decls by full name OR last dotted component. A
  -- dot-notation call `x.foo` (with `x : T`, resolving to `T.foo`) or an open-namespace
  -- reference cites only `foo`, which cannot be tied to the fully-qualified `T.foo`
  -- declaration syntactically — matching on the last component recovers it. This
  -- conservatively over-keeps same-suffix lemmas, which is safe: an extra kept lemma
  -- never breaks compilation, whereas a dropped-but-needed one does (the over-cut bug
  -- that made `--shrink-*-minimal` corrupt dot-notation-heavy files, e.g. lean4lean).
  let lastComp := fun (s : String) => (s.splitOn ".").getLastD s
  let openersOfName := fun (nm : String) =>
    let nc := lastComp nm
    lemmas.filterMap (fun l => if l.name == nm || lastComp l.name == nc then some l.spanIdx else none)
  let mut deletedNames : HashSet String := {}
  for si in delSet.toList do
    match bySpan si with | some l => deletedNames := deletedNames.insert l.name | none => pure ()
  let mustHole := fun (si : Nat) =>
    match bySpan si with | some l => l.bodyNames.any (fun nm => deletedNames.contains nm) | none => false
  let seedAll := userSet.toList.toArray.qsort (· < ·)
  let seed := match spec.count with | some k => seedAll.extract 0 (min k seedAll.size) | none => seedAll
  -- Keep-set computed BEFORE ablating, shared by challenge & solution: the full
  -- statement+body closure of the target holes over the original. Keeps every lemma
  -- the real proofs need (never throws away more than the deleted lemma) and gives
  -- both sides the same context. The deleted lemma stays in the set (restored in the
  -- solution, omitted from the challenge).
  let mut keep : HashSet Nat := {}
  let mut q : Array Nat := #[]
  for o in seed do
    if !keep.contains o && (bySpan o).isSome then
      keep := keep.insert o; q := q.push o
  -- Structural (non-goal) items are always kept, so any in-file decl they cite must be
  -- kept too — else it dangles. Seed the closure with those references. (rocq parity.)
  for si in [0:spans.size] do
    if (bySpan si).isNone then
      let s := spans[si]!
      for nm in Uses.namesIn toks s.lo s.hi do
        for o2 in openersOfName nm do
          if !keep.contains o2 && (bySpan o2).isSome then
            keep := keep.insert o2; q := q.push o2
  let mut qi := 0
  while qi < q.size do
    let o := q[qi]!
    qi := qi + 1
    match bySpan o with
    | none => pure ()
    | some l =>
      for nm in l.stmtNames ++ l.bodyNames do
        for o2 in openersOfName nm do
          if !keep.contains o2 && (bySpan o2).isSome then
            keep := keep.insert o2; q := q.push o2
  let mut buf := ""
  for si in [0:spans.size] do
    let s := spans[si]!
    match bySpan si with
    | some _ =>
      if keep.contains si then
        if !solution && delSet.contains si then
          pure () -- deleted lemma: omitted from the challenge
        else if !solution && (mustHole si || userSet.contains si) then
          match findDeclBody toks s.lo s.hi with
          | some a =>
            let contentHi := lastProperEnd toks (a + 1) s.hi
            let binderCol := (firstProperIdx toks s.lo s.hi).map (fun fp => toks[fp]!.col) |>.getD 0
            buf := buf ++ renderUserLean toks s.lo a contentHi s.hi binderCol deletedNames spec.deleteLeaves
          | none => buf := buf ++ s.source toks
        else buf := buf ++ s.source toks
    | none =>
      -- structural item (e.g. `attribute`/`open`): drop from the challenge if it cites a
      -- deleted lemma (else it dangles); the solution restores the lemma. (rocq struct_ok.)
      let citesDeleted := !solution && (Uses.namesIn toks s.lo s.hi).any deletedNames.contains
      if !citesDeleted then buf := buf ++ s.source toks
  return collapseBlankLines buf

/-- Corollary selection: pick a random theorem, take its transitive in-file
dependency closure, and draw deletions from the *eligible* members of that closure
(fan-in weighted, or uniform). One corollary is exhausted before a fresh one is drawn,
so deletions stay concentrated in a single proof's subtree unless the target forces
spilling. Mirrors the rust ablator's `corollary_select`. Returns the chosen lemmas,
the corollary seed(s) deletions were drawn from, the distinct eligible closure members
they were drawn from, and the advanced RNG (the latter two feed the emitted record). -/
def corollarySelect (lemmas : Array DeletableLemma) (cands : Array DeletableLemma)
    (spec : Spec) (rng : Rng) :
    Array DeletableLemma × Array DeletableLemma × HashSet Nat × Rng := Id.run do
  if cands.isEmpty then return (#[], #[], {}, rng)
  -- name -> first lemma position
  let mut byNamePos : HashMap String Nat := {}
  for i in [0:lemmas.size] do
    let l := lemmas[i]!
    if !byNamePos.contains l.name then byNamePos := byNamePos.insert l.name i
  let depsOf := fun (l : DeletableLemma) =>
    (l.stmtNames ++ l.bodyNames).foldl (fun acc nm =>
      if byNamePos.contains nm then
        let d := lemmas[byNamePos.getD nm 0]!
        if d.spanIdx != l.spanIdx then acc.push d else acc
      else acc) (#[] : Array DeletableLemma)
  let candSet : HashSet Nat := HashSet.ofArray (cands.map (·.spanIdx))
  let closureCands := fun (start : DeletableLemma) => Id.run do
    let mut seen : HashSet Nat := {}
    let mut acc : Array DeletableLemma := #[]
    let mut stack : Array DeletableLemma := depsOf start
    while stack.size > 0 do
      let l := stack.back!
      stack := stack.pop
      if l.spanIdx != start.spanIdx && !seen.contains l.spanIdx then
        seen := seen.insert l.spanIdx
        acc := acc.push l
        for d in depsOf l do
          if !seen.contains d.spanIdx then stack := stack.push d
    return (acc.filter (fun l => candSet.contains l.spanIdx)).qsort (fun a b => a.spanIdx < b.spanIdx)
  let weight := fun (l : DeletableLemma) => if spec.deleteUniform then 1.0 else Float.ofNat l.users.size
  let pickIdx := fun (remaining : Array DeletableLemma) (x : Float) => Id.run do
    let total := remaining.foldl (fun a l => a + weight l) 0.0
    let target := x * total
    let mut acc := 0.0
    let mut idx := remaining.size - 1
    let mut found := false
    for j in [0:remaining.size] do
      if !found then
        acc := acc + weight remaining[j]!
        if acc > target then idx := j; found := true
    return idx
  let (orderIdx, rngAfter) := rng.shuffle (List.range lemmas.size).toArray
  let order := orderIdx.map (fun i => lemmas[i]!)
  let targetDeletions := spec.deleteCount
  let targetAblations := if spec.deleteCount.isNone then spec.count else none
  let useProb := spec.deleteCount.isNone && spec.count.isNone
  let coveredCount := fun (chosen : Array DeletableLemma) (del : HashSet Nat) => Id.run do
    let mut s : HashSet Nat := {}
    for l in chosen do
      for u in l.users do
        if !del.contains u then s := s.insert u
    return s.size
  let reached : Array DeletableLemma → HashSet Nat → Bool := fun chosen del =>
    match targetDeletions, targetAblations with
    | some nd, _ => decide (chosen.size ≥ nd)
    | _, some k => decide (coveredCount chosen del ≥ k)
    | _, _ => false
  let mut r := rngAfter
  let mut del : HashSet Nat := {}
  let mut chosen : Array DeletableLemma := #[]
  let mut seeds : Array DeletableLemma := #[]
  let mut closure : HashSet Nat := {}
  let mut stop := false
  -- record `cor` as a corollary seed and its closure as the draw neighborhood
  let noteCorollary := fun (cor : DeletableLemma) (cc : Array DeletableLemma)
      (seeds : Array DeletableLemma) (closure : HashSet Nat) =>
    (seeds.push cor, cc.foldl (fun (s : HashSet Nat) l => s.insert l.spanIdx) closure)
  for cor in order do
    if !stop then
      let cc := closureCands cor
      if useProb then
        let pool := cc.filter (fun l => !del.contains l.spanIdx)
        if !pool.isEmpty then
          let before := chosen.size
          for pp in pool do
            let (x, r') := r.nextF64
            r := r'
            if x < spec.prob then
              del := del.insert pp.spanIdx
              chosen := chosen.push pp
          if chosen.size > before then
            let (s', c') := noteCorollary cor cc seeds closure
            seeds := s'; closure := c'
          stop := true
      else if reached chosen del then
        stop := true
      else
        let mut pool := cc.filter (fun l => !del.contains l.spanIdx)
        let before := chosen.size
        while pool.size > 0 && !(reached chosen del) do
          let (x, r') := r.nextF64
          r := r'
          let l := pool[pickIdx pool x]!
          del := del.insert l.spanIdx
          chosen := chosen.push l
          pool := pool.filter (fun y => y.spanIdx != l.spanIdx)
        if chosen.size > before then
          let (s', c') := noteCorollary cor cc seeds closure
          seeds := s'; closure := c'
  return (chosen, seeds, closure, r)

/-- `--delete-lemmas`: delete eligible used lemmas + whole-proof-ablate users. -/
def ablateDelete (toks : Array Token) (spec : Spec) (rng : Rng) : AblationResult := Id.run do
  let spans := parseSpans toks
  let lemmas := analyzeUses toks spans spec.aggressive
  let totalEligible := (lemmas.filter (·.eligible)).size
  let cands := lemmas.filter (fun l =>
    l.eligible && Int.ofNat l.users.size ≥ spec.minCentrality
    && Int.ofNat l.users.size ≤ spec.maxCentrality)
  -- `--count k` is a target number of *ablations* (holed users), not deletions:
  -- deleting a lemma forces all its users to be ablated, so ablations arrive in
  -- chunks. We draw deletions at random *without replacement*, each with
  -- probability proportional to its weight (user count — or 1 under deleteUniform),
  -- accumulating their forced ablations until the distinct total reaches ≥ k, then
  -- stop. Seed-driven (diverse evals), favours popular lemmas, yet keeps a non-zero
  -- chance on the tail. Without --count the per-lemma `prob` coin decides. (With
  -- --truncate we later keep only the first k.)
  let weight := fun (l : DeletableLemma) => if spec.deleteUniform then 1.0 else Float.ofNat l.users.size
  -- index of one weighted pick (proportional to `weight`) given a random draw `x`
  let pickIdx := fun (remaining : Array DeletableLemma) (x : Float) => Id.run do
    let total := remaining.foldl (fun a l => a + weight l) 0.0
    let target := x * total
    let mut acc := 0.0
    let mut idx := remaining.size - 1
    let mut found := false
    for j in [0:remaining.size] do
      if !found then
        acc := acc + weight remaining[j]!
        if acc > target then idx := j; found := true
    return idx
  let mut selected : Array DeletableLemma := #[]
  let mut r := rng
  let mut corollarySeeds : Array DeletableLemma := #[]
  let mut closureOpeners : HashSet Nat := {}
  if spec.corollary then
    -- Corollary mode restricts the candidate pool to one random theorem's dependency
    -- closure; everything downstream (user ablation, shrink, --count) is shared.
    let (sel, seeds, closure, r') := corollarySelect lemmas cands spec r
    selected := sel
    corollarySeeds := seeds
    closureOpeners := closure
    r := r'
  else match spec.deleteCount with
  | some kd =>
    -- --delete-lemmas N: delete exactly N lemmas (weighted draw), any ablation count
    let mut remaining := cands
    let target := min kd cands.size
    while selected.size < target && remaining.size > 0 do
      let (x, r') := r.nextF64
      r := r'
      let l := remaining[pickIdx remaining x]!
      selected := selected.push l
      remaining := remaining.filter (fun y => y.spanIdx != l.spanIdx)
  | none =>
    match spec.count with
    | none =>
      for l in cands do
        let (x, r') := r.nextF64
        r := r'
        if x < spec.prob then selected := selected.push l
    | some 0 => pure ()
    | some k =>
      let mut covered : HashSet Nat := {}
      let mut remaining := cands
      while covered.size < k && remaining.size > 0 do
        let (x, r') := r.nextF64
        r := r'
        let l := remaining[pickIdx remaining x]!
        for u in l.users do covered := covered.insert u
        selected := selected.push l
        remaining := remaining.filter (fun y => y.spanIdx != l.spanIdx)
  let delSet : HashSet Nat := HashSet.ofArray (selected.map (·.spanIdx))
  let deletedNamesSet : HashSet String := selected.foldl (fun s l => s.insert l.name) {}
  -- distinct forced ablations (users not themselves deleted), in file order
  let mut usersArr : Array Nat := #[]
  let mut seen : HashSet Nat := {}
  for l in selected do
    for u in l.users do
      if !delSet.contains u && !seen.contains u then
        seen := seen.insert u
        usersArr := usersArr.push u
  let usersSorted := usersArr.qsort (· < ·)
  -- with --truncate + --count, ablate EXACTLY the first k and cut the rest;
  -- otherwise every user must be ablated (a dangling reference would not compile).
  let ablateArr :=
    match spec.truncate, spec.count with
    | true, some k => usersSorted.extract 0 (min k usersSorted.size)
    | _, _ => usersSorted
  let userSet : HashSet Nat := HashSet.ofArray ablateArr
  let nameOf (si : Nat) : String :=
    match lemmas.find? (fun l => l.spanIdx == si) with | some l => l.name | none => ""
  -- Emit the challenge while recording per-item challenge/solution segments (codepoint
  -- offsets, isCloser, hadHole) so --shrink-* can trim each side to the first N holes.
  -- (--delete-lemmas-leaves: leaf-level holing is deferred to the heavyweight semantic
  -- ablator; here it falls back to whole-proof ablation, always correct.)
  let mut out := ""
  let mut holes : Array Hole := #[]
  let mut deleted : Array DeletedLemma := #[]
  let mut ablated : Int := 0
  let mut lastSorryEnd : Int := -1
  let mut chalSegs : Array (Nat × Bool × Bool) := #[]
  let mut solSegs : Array (Nat × Bool × Bool) := #[]
  let mut origLen := 0
  for si in [0:spans.size] do
    let s := spans[si]!
    -- keep block openers (`namespace`/`section`) too (see shrink); else a kept `end`
    -- could dangle after its opener was trimmed.
    let closer := s.cmd == some "end" || s.cmd == some "namespace" || s.cmd == some "section"
    let itemLen := (s.source toks).length
    if delSet.contains si then
      let block := s.source toks
      let body := match findDeclBody toks s.lo s.hi with
        | some a => implode (toks.extract (a + 1) (lastProperEnd toks (a + 1) s.hi))
        | none => ""
      let fanIn := match lemmas.find? (fun l => l.spanIdx == si) with
        | some l => Int.ofNat l.users.size | none => 0
      deleted := deleted.push {
        name := nameOf si, text := block, fanIn := fanIn, metrics := Metrics.compute block body }
      origLen := origLen + itemLen
      solSegs := solSegs.push (origLen, false, false)
    else if userSet.contains si then
      match findDeclBody toks s.lo s.hi with
      | some a =>
        let contentHi := lastProperEnd toks (a + 1) s.hi
        let binderCol := (firstProperIdx toks s.lo s.hi).map (fun fp => toks[fp]!.col) |>.getD 0
        out := out ++ renderUserLean toks s.lo a contentHi s.hi binderCol deletedNamesSet spec.deleteLeaves
        lastSorryEnd := Int.ofNat out.length
        let proofText := implode (toks.extract (a + 1) contentHi)
        holes := holes.push {
          theoremName := nameOf si, depth := 1, nCommands := 0, nLines := nLinesOf proofText,
          isLeaf := true, centrality := 0, method := "deleted-dep", proofText := proofText,
          metrics := holeMetrics proofText }
        ablated := ablated + 1
        chalSegs := chalSegs.push (out.length, false, true)
        origLen := origLen + itemLen
        solSegs := solSegs.push (origLen, false, true)
      | none =>
        out := out ++ s.source toks
        chalSegs := chalSegs.push (out.length, closer, false)
        origLen := origLen + itemLen
        solSegs := solSegs.push (origLen, closer, false)
    else
      out := out ++ s.source toks
      chalSegs := chalSegs.push (out.length, closer, false)
      origLen := origLen + itemLen
      solSegs := solSegs.push (origLen, closer, false)
  let original := implode toks
  let text :=
    if spec.truncate && lastSorryEnd ≥ 0 then
      collapseBlankLines (String.mk (out.toList.take lastSorryEnd.toNat))
    else if spec.shrinkChallengeMinimal then sliceDelete toks spans spec lemmas delSet userSet false
    else if spec.shrinkChallenge then shrink out chalSegs spec.count
    else collapseBlankLines out
  let solution :=
    if spec.shrinkSolutionMinimal then sliceDelete toks spans spec lemmas delSet userSet true
    else if spec.shrinkSolution then shrink original solSegs spec.count
    else original
  let corollaries : Array Corollary := corollarySeeds.map (fun c =>
    let s := spans[c.spanIdx]!
    let block := s.source toks
    let body := match findDeclBody toks s.lo s.hi with
      | some a => implode (toks.extract (a + 1) (lastProperEnd toks (a + 1) s.hi))
      | none => ""
    { name := c.name, fanIn := Int.ofNat c.users.size, metrics := Metrics.compute block body })
  return {
    text := text, solution := solution,
    total := Int.ofNat totalEligible, ablated := ablated, holes := holes, deleted := deleted,
    corollaries := corollaries, closureSize := Int.ofNat closureOpeners.size }

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
