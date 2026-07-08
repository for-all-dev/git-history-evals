/-
Proof-complexity metrics, computed inside the ablator so they are visible in the
JSONL record (and any downstream website / difficulty classifier). These are
deliberately simple, tokenizer-driven heuristics — informative features, not
canonical semantics. See `docs/difficulty-features.md` §2 for the definitions,
which the rocq (reference) / Rust / Scala ablators mirror for their own provers.

Matches are whole identifier tokens only; occurrences inside comments or strings
are ignored by construction, because `tokenize` classifies those bytes as
comment/string tokens rather than `ident` tokens.
-/

import Ablator.Tokenize
import Ablator.Json

namespace Ablator

/-- The five proof-complexity integers carried by every proof block. -/
structure Metrics where
  nLines     : Int
  nChars     : Int
  nSubproofs : Int  -- intermediate-assertion keywords: have/obtain/suffices
  nTactics   : Int  -- atomic proof steps: newline tactic lines + ';'/'<;>' separators
  cyclomatic : Int  -- 1 + #case-splitters + #alternation combinators
  deriving Inhabited, BEq, Repr

namespace Metrics

/-- Lean keyword banks (see spec §2). -/
def subproofKw : List String := ["have", "obtain", "suffices"]

def caseSplitters : List String :=
  ["induction", "cases", "rcases", "rintro", "match", "split", "constructor"]

def alternationKw : List String := ["first", "try", "repeat"]

private def nLinesOf (s : String) : Int :=
  if s.isEmpty then 0
  else Int.ofNat (s.toList.foldl (fun acc c => if c == '\n' then acc + 1 else acc) 1)

/-- Is the token at `i` the start of a `<;>` triple? The lexer splits `<;>` into
    three tokens (`<`, `;`, `>`) because `;` is a standalone delimiter, so we
    match the sequence rather than a single token. -/
private def isSemiAltAt (toks : Array Token) (i : Nat) : Bool :=
  i + 2 < toks.size
    && toks[i]!.isSym && toks[i]!.src == "<"
    && toks[i+1]!.src == ";"
    && toks[i+2]!.src == ">"

/-- Compute metrics. `block` is the whole source slice (statement + proof) used
    for line/char size; `body` is the proof body used for the tactic/branch
    heuristics. For holes the two coincide (only the proof body is available). -/
def compute (block body : String) : Metrics := Id.run do
  let toks := tokenize body
  let n := toks.size
  let mut subproofs := 0
  let mut branches := 0
  let mut tacticLines := 0   -- newline-separated tactic lines
  let mut separators := 0    -- ';' and '<;>' step separators
  let mut i := 0
  while i < n do
    let t := toks[i]!
    if t.firstOnLine && t.isProper then tacticLines := tacticLines + 1
    if t.isIdent then
      let s := t.src
      if subproofKw.contains s then subproofs := subproofs + 1
      if caseSplitters.contains s then branches := branches + 1
      if alternationKw.contains s then branches := branches + 1
      i := i + 1
    else if isSemiAltAt toks i then
      -- `<;>` counts as both an alternation combinator and a step separator
      branches := branches + 1
      separators := separators + 1
      i := i + 3
    else if t.isSym then
      if t.src == "<|>" then branches := branches + 1
      else if t.src == ";" then separators := separators + 1
      i := i + 1
    else
      i := i + 1
  return {
    nLines := nLinesOf block,
    nChars := Int.ofNat block.toUTF8.size,
    nSubproofs := Int.ofNat subproofs,
    nTactics := Int.ofNat (tacticLines + separators),
    cyclomatic := Int.ofNat (1 + branches) }

/-- JSON fields for a metrics object, inlined flat into the enclosing object so
    the website / classifier sees plain keys. Callers that also carry a separate
    `n_lines` (holes already do) should not double-emit it. -/
def toFields (m : Metrics) : List (String × Json) :=
  [ ("n_lines", Json.num m.nLines),
    ("n_chars", Json.num m.nChars),
    ("n_subproofs", Json.num m.nSubproofs),
    ("n_tactics", Json.num m.nTactics),
    ("cyclomatic", Json.num m.cyclomatic) ]

end Metrics
end Ablator
