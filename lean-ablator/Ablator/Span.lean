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

/-- Split tokens into top-level command spans. -/
def parseSpans (toks : Array Token) : Array Span := Id.run do
  let n := toks.size
  if n == 0 then return #[]
  -- boundary indices: where each command starts
  let mut bounds : Array Nat := #[]
  for i in [0:n] do
    if isCommandLeading toks[i]! then bounds := bounds.push i
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
