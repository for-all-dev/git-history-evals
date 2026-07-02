/-
Group the token stream into top-level command spans. Lean has no `qed`, so we
use the language's layout rule: a top-level command begins at a *command-leading
token in column 0* (a declaration/command keyword, a modifier, or an `@`
attribute marker). Each
span runs from one such boundary to the next.

This is a heuristic (a stray column-0 command keyword inside a proof would
mis-split), but it holds for well-formatted Lean and never corrupts source —
spans always concatenate back to the input.
-/

import Ablator.Token
import Ablator.Keyword

namespace Ablator

/-- A top-level command span: the half-open token range `[lo, hi)` plus the
    recognised command keyword (`none` for preamble / unrecognised). -/
structure Span where
  lo  : Nat
  hi  : Nat
  cmd : Option String
  deriving Repr, Inhabited

namespace Span

/-- Source text of the span. -/
def source (s : Span) (toks : Array Token) : String := Id.run do
  let mut acc := ""
  for i in [s.lo:s.hi] do
    acc := acc ++ toks[i]!.src
  return acc

/-- Is this span an ablatable declaration (its body after `:=` is a term/proof)? -/
def isDecl (s : Span) : Bool :=
  match s.cmd with
  | some k => Keyword.isDeclKind k
  | none => false

end Span

/-- Bracket-open token (drives the depth used to find the body `:=`). -/
def isOpenBracket (t : Token) : Bool :=
  t.isSym && (t.src == "(" || t.src == "[" || t.src == "{" ||
    t.src == "⟨" || t.src == "⦃" || t.src == "‹" || t.src == "⌜")

def isCloseBracket (t : Token) : Bool :=
  t.isSym && (t.src == ")" || t.src == "]" || t.src == "}" ||
    t.src == "⟩" || t.src == "⦄" || t.src == "›" || t.src == "⌝")

/-- Index of the first bracket-depth-0 `:=` token in `[lo, hi)` (the separator
    between a declaration/binder's signature and its body). Binder defaults like
    `(x := 0)` sit at depth > 0, so they are correctly skipped. -/
def findAssign (toks : Array Token) (lo hi : Nat) : Option Nat := Id.run do
  let mut i := lo
  let mut depth := 0
  while i < hi do
    let t := toks[i]!
    if isOpenBracket t then depth := depth + 1
    else if isCloseBracket t then depth := if depth == 0 then 0 else depth - 1
    else if depth == 0 && t.isSymNamed ":=" then return some i
    i := i + 1
  return none

/-- Index of the depth-0 `:=` that delimits a *declaration's* signature from its
    proof body. Unlike `findAssign`, this skips the `:=` introduced by `let`/`have`
    binders inside a dependent type, e.g.
    `theorem foo : let x := e; P x := by …` — the `let`'s `:=` is not the body
    delimiter. Each depth-0 `let`/`have` keyword "consumes" one subsequent depth-0
    `:=` before we accept one as the body separator. -/
def findDeclBody (toks : Array Token) (lo hi : Nat) : Option Nat := Id.run do
  let mut i := lo
  let mut depth := 0
  let mut pending := 0
  while i < hi do
    let t := toks[i]!
    if isOpenBracket t then depth := depth + 1
    else if isCloseBracket t then depth := if depth == 0 then 0 else depth - 1
    else if depth == 0 && t.isProper && t.isIdent && (t.src == "let" || t.src == "have") then
      pending := pending + 1
    else if depth == 0 && t.isSymNamed ":=" then
      if pending == 0 then return some i
      else pending := pending - 1
    i := i + 1
  return none

/-- Declared name of a decl span: the identifier right after the command
    keyword, skipping modifiers and `@[ ... ]`. `""` when anonymous (`example`,
    anonymous `instance`). -/
def declName (toks : Array Token) (lo hi : Nat) : String := Id.run do
  let mut i := lo
  let mut depth := 0
  let mut afterKw := false
  while i < hi do
    let t := toks[i]!
    if isOpenBracket t then depth := depth + 1
    else if isCloseBracket t then depth := if depth == 0 then 0 else depth - 1
    else if depth == 0 && t.isProper && t.isIdent then
      if afterKw then return t.src
      else if Keyword.modifiers.contains t.src then pure ()
      else if Keyword.isCommandStarter t.src then afterKw := true
      else return ""
    i := i + 1
  return ""

/-- A column-0 proper token that begins a new top-level command. -/
def isCommandLeading (t : Token) : Bool :=
  t.col == 0 && t.isProper &&
    ((t.isIdent && Keyword.isCommandStarter t.src) || (t.isSym && t.src == "@"))

/-- The command keyword of a span: first proper identifier that is a command
    starter, skipping leading modifiers and `@[ ... ]` attribute groups. -/
def classifySpan (toks : Array Token) (lo hi : Nat) : Option String := Id.run do
  let mut i := lo
  let mut depth := 0
  while i < hi do
    let t := toks[i]!
    if t.isSym then
      if t.src == "[" || t.src == "(" then depth := depth + 1
      else if t.src == "]" || t.src == ")" then depth := if depth == 0 then 0 else depth - 1
    else if depth == 0 && t.isIdent then
      if Keyword.modifiers.contains t.src then
        pure ()   -- skip modifier, keep scanning
      else if Keyword.isCommandStarter t.src then
        return some t.src
      else
        return none   -- first real ident isn't a command head
    i := i + 1
  return none

/-- Number of newlines in a token's source (a "blank line" between a doc comment
    and a command — two or more — breaks the doc's attachment to that command). -/
private def newlineCount (t : Token) : Nat :=
  t.src.foldl (fun acc c => if c == '\n' then acc + 1 else acc) 0

/-- Move a command boundary at `cmdIdx` earlier to absorb a doc-comment block that
    sits directly above it (only whitespace, no blank line, in between). Lean attaches
    such `/-- … -/` to the *following* declaration; without this they would trail the
    previous span and be orphaned when the documented decl is deleted/ablated. Never
    crosses below `lower` (the previous boundary) or a proper (code) token. -/
private def attachedStart (toks : Array Token) (cmdIdx lower : Nat) : Nat := Id.run do
  let mut start := cmdIdx
  let mut k := cmdIdx
  while k > lower do
    let prev := toks[k-1]!
    if prev.isSpace then
      if newlineCount prev ≥ 2 then return start  -- blank line: not attached
      k := k - 1
    else if prev.isDocComment then
      start := k - 1
      k := k - 1
    else
      return start  -- code token or ordinary comment: stop
  return start

/-- Are tokens `[lo, hi)` only whitespace / doc comments / `@[ … ]` attribute groups /
    modifiers — i.e. an attribute-or-modifier PREFIX of the command that starts at `hi`,
    with no blank-line break? Then the boundary at `hi` is a continuation of the command
    begun at `lo`, not a new command: a col-0 `@[simp]` (or `public`) opens its own
    boundary but belongs to the decl below it, and without merging, deleting that decl
    would orphan the leading `@[simp]` (→ `@[simp] end`, a syntax error). -/
private def isAttrPrefix (toks : Array Token) (lo hi : Nat) : Bool := Id.run do
  let mut i := lo
  let mut depth := 0
  let mut sawAttrMod := false
  while i < hi do
    let t := toks[i]!
    if t.isSym && t.src == "[" then depth := depth + 1
    else if t.isSym && t.src == "]" then depth := if depth == 0 then 0 else depth - 1
    else if depth > 0 then pure ()  -- inside @[ … ]
    else if t.isSpace then
      if newlineCount t ≥ 2 then return false
    else if t.isDocComment then pure ()
    else if t.isSym && t.src == "@" then sawAttrMod := true
    else if t.isIdent && Keyword.modifiers.contains t.src then sawAttrMod := true
    else return false  -- a decl keyword or code token → a genuine new command
    i := i + 1
  return sawAttrMod

/-- Split tokens into top-level command spans. -/
def parseSpans (toks : Array Token) : Array Span := Id.run do
  let n := toks.size
  if n == 0 then return #[]
  -- boundary indices: where each command starts (absorbing any attached doc comment)
  let mut bounds : Array Nat := #[]
  for i in [0:n] do
    if isCommandLeading toks[i]! then
      let lower := if bounds.isEmpty then 0 else bounds[bounds.size - 1]!
      bounds := bounds.push (attachedStart toks i lower)
  -- Merge attribute/modifier-prefix boundaries into the command they annotate, so a decl
  -- and its leading `@[…]`/`public`/… stay ONE span (deleting the decl takes them all).
  let mut merged : Array Nat := #[]
  for b in bounds do
    if (!merged.isEmpty) && isAttrPrefix toks merged[merged.size - 1]! b then
      pure ()  -- drop b: it continues the attribute-led command above
    else
      merged := merged.push b
  bounds := merged
  -- assemble spans between boundaries; tokens before the first boundary are preamble
  let mut spans : Array Span := #[]
  -- always cover [0, n): if there is no column-0 command (e.g. an import-only
  -- file), the whole file is one preamble span; otherwise prepend 0 if needed.
  let starts :=
    if bounds.isEmpty then #[0]
    else if bounds[0]! == 0 then bounds
    else #[0] ++ bounds
  for j in [0:starts.size] do
    let lo := starts[j]!
    let hi := if j + 1 < starts.size then starts[j+1]! else n
    spans := spans.push { lo := lo, hi := hi, cmd := classifySpan toks lo hi }
  return spans

end Ablator
