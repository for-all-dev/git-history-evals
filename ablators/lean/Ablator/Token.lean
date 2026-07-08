/-
Outer tokens for Lean 4 source. A token carries its exact source slice plus its
starting column, so that:

  * concatenating every token's `src` reproduces the input byte-for-byte
    (the lossless round-trip invariant the self-test checks), and
  * we can tell top-level commands (column 0) from nested ones (column > 0)
    without re-scanning, which is how block extents are bounded.

This module is deliberately free of `import Lean` / IO so the whole core is a
pure `String -> String` library that compiles small (and to WASM).
-/

namespace Ablator

/-- Token classes we distinguish. Anything not lexically special is `ident`
    (a maximal identifier/keyword run) or `sym` (a punctuation/operator run). -/
inductive Kind where
  | space            -- whitespace incl. newlines
  | lineComment      -- `-- ...` to end of line
  | blockComment     -- `/- ... -/` (nested); ordinary, non-doc
  | docComment       -- `/-- ... -/` or `/-! ... -/`
  | str              -- `"..."` string literal
  | char             -- `'a'` char literal
  | ident            -- identifier or keyword (classified later via the keyword table)
  | sym              -- operator/punctuation token (`:=`, `(`, `=>`, ...)
  | other            -- a single char that fits nothing above
  deriving DecidableEq, Repr, Inhabited

/-- A lexed token: its kind, exact source text, and starting column
    (count of characters since the previous newline; 0 == start of line). -/
structure Token where
  kind : Kind
  src  : String
  col  : Nat
  /-- True iff this is the first *proper* (non-space, non-comment) token on
      its physical line — used to bound indentation-delimited blocks. -/
  firstOnLine : Bool := false
  deriving Repr, Inhabited

namespace Token

def isSpace (t : Token) : Bool := t.kind == Kind.space
def isLineComment (t : Token) : Bool := t.kind == Kind.lineComment
def isBlockComment (t : Token) : Bool := t.kind == Kind.blockComment
def isDocComment (t : Token) : Bool := t.kind == Kind.docComment
def isComment (t : Token) : Bool :=
  t.kind == Kind.lineComment || t.kind == Kind.blockComment || t.kind == Kind.docComment
def isIdent (t : Token) : Bool := t.kind == Kind.ident
def isSym (t : Token) : Bool := t.kind == Kind.sym

/-- A "proper" token contributes to the command grammar: not whitespace,
    not a comment. (Doc comments are comments here.) -/
def isProper (t : Token) : Bool := !t.isSpace && !t.isComment

/-- Whitespace or comment — the spans that may trail a proof body and that we
    must preserve verbatim when we splice in `sorry`. -/
def isIgnored (t : Token) : Bool := t.isSpace || t.isComment

def isIdentNamed (t : Token) (s : String) : Bool := t.isIdent && t.src == s
def isSymNamed (t : Token) (s : String) : Bool := t.isSym && t.src == s

end Token

/-- `Token.implode`: concatenate token sources (lossless). -/
def implode (ts : Array Token) : String :=
  ts.foldl (fun acc t => acc ++ t.src) ""

def implodeList (ts : List Token) : String :=
  ts.foldl (fun acc t => acc ++ t.src) ""

end Ablator
