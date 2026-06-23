/-
A self-contained lexer for Lean 4 surface syntax. It does NOT use Lean's real
parser (`import Lean`): we want a small, dependency-free core that compiles to a
tiny binary and, ultimately, to WASM.

It is approximate where approximation is harmless (identifier boundaries,
operator runs) and exact where exactness matters:

  * every character is consumed by exactly one token, so concatenating sources
    round-trips byte-for-byte (the self-test's first invariant);
  * comments (`--`, nested `/- -/`, doc `/-- -/`/`/-! -/`), string and char
    literals are scanned by dedicated scanners that take priority, so a `:=` or
    a command keyword hiding inside a string/comment is never mistaken for code.
-/

import Ablator.Token

namespace Ablator
namespace Tokenize

/-- Codepoints Lean treats as letter-like inside identifiers (greek, letterlike,
    math alphanumerics). Mirrors `Lean.isLetterLike` closely enough for lexing. -/
def isLetterLike (n : Nat) : Bool :=
  (0x3b1 ≤ n && n ≤ 0x3c9 && n ≠ 0x3bb) ||                 -- α..ω except λ
  (0x391 ≤ n && n ≤ 0x3a9 && n ≠ 0x3a0 && n ≠ 0x3a3) ||   -- Α..Ω except Π,Σ
  (0x1f00 ≤ n && n ≤ 0x1fff) ||                            -- greek extended
  (0x2100 ≤ n && n ≤ 0x214f) ||                            -- letterlike symbols
  (0x1d49c ≤ n && n ≤ 0x1d59f)                             -- math script/fraktur

/-- Subscript letters/digits, valid in identifier continuations (`x₀`, `aₙ`). -/
def isSubScript (n : Nat) : Bool :=
  (0x2080 ≤ n && n ≤ 0x2089) || (0x2090 ≤ n && n ≤ 0x209c) || n == 0x2c7c

def isWS (c : Char) : Bool :=
  c == ' ' || c == '\t' || c == '\n' || c == '\r' ||
  c == Char.ofNat 0x0b || c == Char.ofNat 0x0c

def isIdentStart (c : Char) : Bool :=
  c == '_' || c.isAlpha || isLetterLike c.toNat

def isIdentCont (c : Char) : Bool :=
  isIdentStart c || c.isDigit || c == '\'' || c == '!' || c == '?' || isSubScript c.toNat

/-- Single-character delimiters that always stand alone (and several drive
    bracket depth in the ablator). -/
def isDelim (c : Char) : Bool :=
  c == '(' || c == ')' || c == '{' || c == '}' || c == '[' || c == ']' ||
  c == ',' || c == ';' ||
  c == '⟨' || c == '⟩' || c == '⦃' || c == '⦄' || c == '‹' || c == '›' ||
  c == '⌜' || c == '⌝'

/-- ASCII operator characters that coalesce into one symbol token (so `:=`,
    `=>`, `->` lex as single tokens). -/
def isAsciiSym (c : Char) : Bool :=
  "!#$%&*+-/:<=>?@\\^|~.".any (· == c)

def slice (cs : Array Char) (a b : Nat) : String :=
  String.mk (cs.extract a b).toList

/-- Length of an escape inside a string/char literal starting at the backslash
    `cs[i] = '\\'`; at least 2 (the `\` and the escaped char). -/
def escLen (cs : Array Char) (i : Nat) : Nat :=
  if i + 1 < cs.size then
    let c := cs[i+1]!
    if c == 'x' then 4          -- \xNN  (approx; over/under doesn't break round-trip)
    else if c == 'u' then 6     -- \uNNNN
    else 2
  else 1

/-- Scan a quoted literal beginning at `pos` (`cs[pos] == q`). Returns the index
    just past the closing quote, or past end if unterminated. -/
partial def scanQuoted (cs : Array Char) (pos : Nat) (q : Char) : Nat :=
  let n := cs.size
  let rec go (j : Nat) : Nat :=
    if j ≥ n then n
    else
      let c := cs[j]!
      if c == q then j + 1
      else if c == '\\' then go (j + escLen cs j)
      else go (j + 1)
  go (pos + 1)

/-- Scan a nested block comment opened by `/-` at `pos`. Returns the index just
    past the matching `-/` (or end of input). -/
partial def scanBlockComment (cs : Array Char) (pos : Nat) : Nat :=
  let n := cs.size
  let rec go (j depth : Nat) : Nat :=
    if j ≥ n then n
    else if j + 1 < n && cs[j]! == '/' && cs[j+1]! == '-' then go (j + 2) (depth + 1)
    else if j + 1 < n && cs[j]! == '-' && cs[j+1]! == '/' then
      if depth ≤ 1 then j + 2 else go (j + 2) (depth - 1)
    else go (j + 1) depth
  go pos 0

partial def scanWS (cs : Array Char) (pos : Nat) : Nat :=
  let n := cs.size
  let rec go (j : Nat) : Nat :=
    if j < n && isWS cs[j]! then go (j + 1) else j
  go pos

partial def scanLineComment (cs : Array Char) (pos : Nat) : Nat :=
  let n := cs.size
  let rec go (j : Nat) : Nat :=
    if j < n && cs[j]! != '\n' then go (j + 1) else j
  go pos

/-- Scan one identifier (possibly dotted, possibly `«escaped»`). Returns end. -/
partial def scanIdent (cs : Array Char) (pos : Nat) : Nat :=
  let n := cs.size
  -- one component: «...» escaped, or identStart identCont*
  let scanComponent (start : Nat) : Option Nat :=
    if start ≥ n then none
    else if cs[start]! == '«' then
      -- consume to matching »
      let rec esc (j : Nat) : Nat :=
        if j ≥ n then n
        else if cs[j]! == '»' then j + 1
        else esc (j + 1)
      some (esc (start + 1))
    else if isIdentStart cs[start]! then
      let rec cont (j : Nat) : Nat :=
        if j < n && isIdentCont cs[j]! then cont (j + 1) else j
      some (cont (start + 1))
    else none
  match scanComponent pos with
  | none => pos
  | some e0 =>
    -- continue across `.` + component (dotted name): Nat.succ, List.foo
    let rec dotted (e : Nat) : Nat :=
      if e + 1 < n && cs[e]! == '.' && (isIdentStart cs[e+1]! || cs[e+1]! == '«') then
        match scanComponent (e + 1) with
        | some e' => dotted e'
        | none => e
      else e
    dotted e0

/-- Scan a numeric literal (decimal/hex/float). Kept as `Kind.other` so it
    never pollutes the identifier set used for centrality. -/
partial def scanNumber (cs : Array Char) (pos : Nat) : Nat :=
  let n := cs.size
  let rec go (j : Nat) : Nat :=
    if j < n && (cs[j]!.isDigit || cs[j]!.isAlpha || cs[j]! == '.' && j + 1 < n && cs[j+1]!.isDigit)
    then go (j + 1) else j
  go pos

partial def scanAsciiSym (cs : Array Char) (pos : Nat) : Nat :=
  let n := cs.size
  let rec go (j : Nat) : Nat :=
    if j < n && isAsciiSym cs[j]! then go (j + 1) else j
  go pos

/-- Tokenize `text` into a lossless token stream (concatenated `src` == text). -/
def tokenize (text : String) : Array Token := Id.run do
  let cs : Array Char := text.toList.toArray
  let n := cs.size
  let mut out : Array Token := #[]
  let mut pos := 0
  let mut col := 0
  while pos < n do
    let c := cs[pos]!
    -- pick (kind, end)
    let (kind, e) : Kind × Nat :=
      if isWS c then (Kind.space, scanWS cs pos)
      else if c == '-' && pos + 1 < n && cs[pos+1]! == '-' then (Kind.lineComment, scanLineComment cs pos)
      else if c == '/' && pos + 1 < n && cs[pos+1]! == '-' then
        let e := scanBlockComment cs pos
        -- doc `/--` (but not `/---`... still a doc) or module doc `/-!`
        let isDoc := pos + 2 < n && (cs[pos+2]! == '-' || cs[pos+2]! == '!')
        (if isDoc then Kind.docComment else Kind.blockComment, e)
      else if c == '"' then (Kind.str, scanQuoted cs pos '"')
      else if c == '\'' then
        -- char literal `'x'` / `'\n'`; else a lone `'` (Kind.other)
        let e := scanQuoted cs pos '\''
        if e > pos + 1 && e ≤ pos + 8 then (Kind.char, e) else (Kind.other, pos + 1)
      else if isIdentStart c || c == '«' then (Kind.ident, scanIdent cs pos)
      else if c.isDigit then (Kind.other, scanNumber cs pos)
      else if isDelim c then (Kind.sym, pos + 1)
      else if isAsciiSym c then (Kind.sym, scanAsciiSym cs pos)
      else (Kind.sym, pos + 1)            -- a unicode operator char: stands alone
    let e := if e ≤ pos then pos + 1 else e   -- always make progress
    let src := slice cs pos e
    out := out.push { kind := kind, src := src, col := col }
    -- advance column over the consumed source
    for ch in src.toList do
      col := if ch == '\n' then 0 else col + 1
    pos := e
  -- second pass: mark the first proper token on each physical line
  let mut sawProper := false
  let mut res : Array Token := Array.mkEmpty out.size
  for t in out do
    let fol := t.isProper && !sawProper
    res := res.push { t with firstOnLine := fol }
    if t.src.any (· == '\n') then sawProper := false
    else if t.isProper then sawProper := true
  return res

end Tokenize

/-- Public entry: lossless tokenization of Lean source. -/
def tokenize (text : String) : Array Token := Tokenize.tokenize text

end Ablator
