/-
WASM / FFI entry point. Lean compiles to C, and the C is built for `wasm32` with
emscripten; this module exposes a single C-callable function so a JS playground
can ablate a theory in the browser. It is pure (no IO), operating on a
corpus-of-one for centrality. See README.md / build-wasm.sh for the recipe.

The difficulty knobs are passed as primitive scalars (a clean C ABI) rather than
a JSON blob, so no JSON *parser* is needed — only the encoder. A `UInt32` knob of
`0xFFFFFFFF` is the "infinity"/"unset" sentinel (`max_*` bounds and `count`).
-/

import Ablator.Ablate
import Ablator.Centrality
import Ablator.Tokenize
import Ablator.Span
import Ablator.Record
import Ablator.Json

namespace Ablator

/-- `0xFFFFFFFF` is the sentinel a `UInt32` knob carries for "infinity" (upper
    bounds) or "unset" (`count`). -/
def u32Inf : UInt32 := 0xFFFFFFFF

/-- Ablate one Lean theory and return the browser-result JSON string
    `{text, total, ablated, holes_filled}`. Centrality fan-in is computed within
    this single theory (corpus-of-one). All the difficulty knobs are exposed so
    the browser playground has the same controls as the CLI. -/
def ablateTheoryFull
    (text : String)
    (minDepth maxDepth : Int) (leavesOnly : Bool)
    (minSize maxSize : Int) (minCent maxCent : Int)
    (count : Option Nat) (byCentrality : Bool)
    (prob : Float) (truncate shrinkChallenge shrinkSolution deleteLemmas : Bool)
    (shrinkChallengeMinimal shrinkSolutionMinimal : Bool)
    (deleteCount : Option Nat) (deleteUniform deleteLeaves corollary corollaryAll : Bool)
    (seed : UInt64) : String :=
  let toks := tokenize text
  let spans := parseSpans toks
  let spec : Spec := {
    prob := prob, count := count, byCentrality := byCentrality,
    minDepth := minDepth, maxDepth := maxDepth, leavesOnly := leavesOnly,
    minSize := minSize, maxSize := maxSize,
    minCentrality := minCent, maxCentrality := maxCent,
    truncate := truncate, shrinkChallenge := shrinkChallenge, shrinkSolution := shrinkSolution,
    shrinkChallengeMinimal := shrinkChallengeMinimal, shrinkSolutionMinimal := shrinkSolutionMinimal,
    deleteLemmas := deleteLemmas, deleteCount := deleteCount,
    deleteUniform := deleteUniform, deleteLeaves := deleteLeaves, corollary := corollary,
    corollaryAll := corollaryAll, aggressive := false }
  let fan := fanIn #[(toks, spans)]
  let centrality := fun name => fan.getD name 0
  -- corollaryAll: return a JSON ARRAY (one per eligible corollary); else a single object.
  if corollaryAll then
    (Json.arr ((ablateAll toks spec (Rng.mk seed) centrality).toList.map resultJson)).compact
  else
    (resultJson (ablate toks spec (Rng.mk seed) centrality)).compact

private def normU32 (x : UInt32) : Int :=
  if x == u32Inf then INF else Int.ofNat x.toNat

/-- C-callable export — the symbol the emscripten shim (`wasm/shim.c`) binds to.
    Knob semantics mirror the CLI; `probPermille` is `prob * 1000` (so JS passes
    an integer), and a `0xFFFFFFFF` `UInt32` means infinity / unset (`max_*`,
    `count`, `deleteCount`). Full lemma-delete + minimal-shrink knobs are exposed
    so the browser playground has the same controls as the CLI + the other
    backends (corollary / delete-count / uniform / leaves / minimal shrink).
    Returns the `{text, ..., holes_filled, deleted_lemmas, corollaries}` JSON. -/
@[export lean_ablate_theory]
def leanAblateTheory
    (text : String)
    (minDepth maxDepth : UInt32) (leavesOnly : Bool)
    (minSize maxSize : UInt32) (minCent maxCent : UInt32)
    (count : UInt32) (byCentrality : Bool)
    (probPermille : UInt32) (truncate shrinkChallenge shrinkSolution deleteLemmas : Bool)
    (shrinkChallengeMinimal shrinkSolutionMinimal : Bool)
    (deleteCount : UInt32) (deleteUniform deleteLeaves corollary corollaryAll : Bool)
    (seed : UInt64) : String :=
  ablateTheoryFull text
    (Int.ofNat minDepth.toNat) (normU32 maxDepth) leavesOnly
    (Int.ofNat minSize.toNat) (normU32 maxSize)
    (Int.ofNat minCent.toNat) (normU32 maxCent)
    (if count == u32Inf then none else some count.toNat) byCentrality
    (probPermille.toNat.toFloat / 1000.0) truncate shrinkChallenge shrinkSolution deleteLemmas
    shrinkChallengeMinimal shrinkSolutionMinimal
    (if deleteCount == u32Inf then none else some deleteCount.toNat) deleteUniform deleteLeaves corollary corollaryAll
    seed

end Ablator
