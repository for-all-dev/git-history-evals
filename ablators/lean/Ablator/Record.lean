/-
The (challenge, solution) JSON record. Its schema and field order match the
git-history datasets in `../../artifacts` (and the sibling Isabelle ablator), with
`proof_assistant: "lean"`, so a single dump can be stratified by difficulty
post-hoc and lines up field-for-field with the existing data.
-/

import Ablator.Ablate
import Ablator.Json
import Ablator.Hash
import Ablator.Diff

namespace Ablator

private def depthJson (d : Int) : Json :=
  if d == INF then Json.str "inf" else Json.num d

private def taskId (filePath : String) (variant : Option Nat) : String :=
  let base := "ablate_" ++ sha1Hex12 filePath
  match variant with
  | some v => base ++ "_" ++ toString v
  | none => base

private def theoryName (filePath : String) : String :=
  let base := (filePath.splitOn "/").getLastD filePath
  if base.endsWith ".lean" then String.mk (base.toList.take (base.length - 5)) else base

def holeJson (h : Hole) : Json :=
  Json.obj [
    ("theorem_name", Json.str h.theoremName),
    ("depth", Json.num h.depth),
    ("n_commands", Json.num h.nCommands),
    ("n_lines", Json.num h.nLines),
    ("is_leaf", Json.bool h.isLeaf),
    ("centrality", Json.num h.centrality),
    ("method", Json.str h.method),
    ("proof_text", Json.str h.proofText),
    -- proof-complexity metrics (spec §2); n_lines above matches metrics.nLines
    ("n_chars", Json.num h.metrics.nChars),
    ("n_subproofs", Json.num h.metrics.nSubproofs),
    ("n_tactics", Json.num h.metrics.nTactics),
    ("cyclomatic", Json.num h.metrics.cyclomatic) ]

def deletedJson (d : DeletedLemma) : Json :=
  Json.obj [
    ("name", Json.str d.name),
    ("text", Json.str d.text),
    ("fan_in", Json.num d.fanIn),
    ("n_lines", Json.num d.metrics.nLines),
    ("n_chars", Json.num d.metrics.nChars),
    ("n_subproofs", Json.num d.metrics.nSubproofs),
    ("n_tactics", Json.num d.metrics.nTactics),
    ("cyclomatic", Json.num d.metrics.cyclomatic) ]

def corollaryJson (c : Corollary) : Json :=
  Json.obj [
    ("name", Json.str c.name),
    ("fan_in", Json.num c.fanIn),
    ("n_lines", Json.num c.metrics.nLines),
    ("n_chars", Json.num c.metrics.nChars),
    ("n_subproofs", Json.num c.metrics.nSubproofs),
    ("n_tactics", Json.num c.metrics.nTactics),
    ("cyclomatic", Json.num c.metrics.cyclomatic) ]

/-- Stable, unique per-challenge id (so labels join to features exactly). Derived
    from the inputs that fully determine a challenge; unlike `task_id` it does not
    collide across challenges mined from the same file. See
    docs/difficulty-features.md §1. -/
private def challengeId (filePath : String) (seed : Int) (variant : Option Nat)
    (result : AblationResult) : String :=
  let sortJoin := fun (names : Array String) =>
    String.intercalate "," (names.qsort (· < ·)).toList
  let deleted := sortJoin (result.deleted.map (·.name))
  let holed := sortJoin (result.holes.map (·.theoremName))
  let variantStr := match variant with | some v => toString v | none => ""
  let key := String.intercalate "|" [filePath, toString seed, variantStr, deleted, holed]
  sha1Hex16 key

/-- The lightweight `{text, total, ablated, holes}` shape returned to the
    browser playground. -/
def resultJson (result : AblationResult) : Json :=
  Json.obj [
    ("text", Json.str result.text),
    ("solution_diff", Json.str (unifiedDiff result.text result.solution)),
    ("total", Json.num result.total),
    ("ablated", Json.num result.ablated),
    ("holes_filled", Json.arr (result.holes.map holeJson).toList),
    ("deleted_lemmas", Json.arr (result.deleted.map deletedJson).toList),
    ("corollaries", Json.arr (result.corollaries.map corollaryJson).toList),
    ("closure_size", Json.num result.closureSize) ]

def record (filePath session : String) (spec : Spec) (seed : Int) (variant : Option Nat)
    (difficulty : Option String) (repo revision : Option String) (result : AblationResult) : Json :=
  let optNat : Option Nat → Json := fun o => match o with | some n => Json.num (Int.ofNat n) | none => Json.null
  let optStr : Option String → Json := fun o => match o with | some s => Json.str s | none => Json.null
  Json.obj [
    ("task_id", Json.str (taskId filePath variant)),
    ("challenge_id", Json.str (challengeId filePath seed variant result)),
    ("proof_assistant", Json.str "lean"),
    ("session", Json.str session),
    -- provenance: the git repo + commit the source file was ablated from, so a
    -- consumer can reproduce/pin the exact ground truth. `null` when undetectable
    -- (e.g. a non-git source tree).
    ("repo", optStr repo),
    ("revision", optStr revision),
    ("file_path", Json.str filePath),
    ("theory", Json.str (theoryName filePath)),
    ("variant", optNat variant),
    ("challenge_type", Json.str (if result.deleted.isEmpty then "proof_ablate" else "lemma_delete")),
    ("difficulty", optStr difficulty),
    ("count", optNat spec.count),
    ("by_centrality", Json.bool spec.byCentrality),
    ("ablation_prob", if spec.count.isSome then Json.null else Json.float spec.prob),
    ("min_depth", Json.num spec.minDepth),
    ("max_depth", depthJson spec.maxDepth),
    ("leaves_only", Json.bool spec.leavesOnly),
    ("min_size", Json.num spec.minSize),
    ("max_size", depthJson spec.maxSize),
    ("min_centrality", Json.num spec.minCentrality),
    ("max_centrality", depthJson spec.maxCentrality),
    ("seed", Json.num seed),
    ("n_proofs", Json.num result.total),
    ("n_ablated", Json.num result.ablated),
    ("holes_filled", Json.arr (result.holes.map holeJson).toList),
    ("deleted_lemmas", Json.arr (result.deleted.map deletedJson).toList),
    ("corollaries", Json.arr (result.corollaries.map corollaryJson).toList),
    ("closure_size", Json.num result.closureSize),
    ("challenge_file_content", Json.str result.text),
    -- the answer as a whole (shrunk) file, plus the diff for back-compat. With
    -- --shrink-solution the solution is the shrunk file, so this stays manageable.
    ("solution_file_content", Json.str result.solution),
    ("solution_diff", Json.str (unifiedDiff result.text result.solution)) ]

end Ablator
