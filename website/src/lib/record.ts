// Assemble a HuggingFace-style eval record from a browser ablation result.
//
// The WASM demo entry points return the lightweight `{text, total, ablated,
// holes, deleted_lemmas, solution_diff}` shape rather than the full mined
// record. We reconstruct a record whose field names mirror the mined dataset
// schema (challenge_file_content / solution_diff / holes_filled / task_id …) so
// the "JSON evals" view shows what an actual dataset row looks like.

import type { AblateOptions, AblateResult, Lang } from '../ablators/types'

/** Tiny, stable, non-cryptographic content hash for a demo task_id. */
function shortHash(s: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

export interface EvalRecord {
  task_id: string
  proof_assistant: Lang
  challenge_file_content: string
  solution_diff: string
  holes_filled: unknown[]
  deleted_lemmas: { name: string; text?: string }[]
  challenge_type: string
  n_total: number
  n_ablated: number
  spec: Record<string, unknown>
  seed: number
}

/** The knobs actually in effect, for the record's `spec` field. */
function specSummary(o: AblateOptions): Record<string, unknown> {
  const s: Record<string, unknown> = {
    rate: o.rateMode === 'count' ? { count: o.count, by_centrality: o.byCentrality } : { prob: o.prob },
    depth: [o.minDepth, o.maxDepth],
    leaves_only: o.leavesOnly,
    size: [o.minSize, o.maxSize],
    centrality: [o.minCentrality, o.maxCentrality],
  }
  if (o.deleteLemmas) {
    s.delete_lemmas = {
      corollary: o.corollary,
      count: o.deleteCount,
      weighted_by: o.deleteUniform ? 'uniform' : 'fan_in',
      leaves_only: o.deleteLeaves,
    }
  }
  const shaping: string[] = []
  if (o.truncate) shaping.push('truncate')
  if (o.shrinkChallengeMinimal) shaping.push('shrink_challenge_minimal')
  else if (o.shrinkChallenge) shaping.push('shrink_challenge')
  if (o.shrinkSolutionMinimal) shaping.push('shrink_solution_minimal')
  else if (o.shrinkSolution) shaping.push('shrink_solution')
  if (shaping.length) s.context_shaping = shaping
  return s
}

export function toRecord(lang: Lang, result: AblateResult, opts: AblateOptions): EvalRecord {
  const id = shortHash(`${lang}:${opts.seed}:${result.text}`)
  return {
    task_id: `${lang}-demo:${id}`,
    proof_assistant: lang,
    challenge_file_content: result.text,
    solution_diff: result.solutionDiff ?? '',
    holes_filled: result.holes,
    deleted_lemmas: result.deletedLemmas,
    challenge_type: result.deletedLemmas.length > 0 ? 'lemma_delete' : 'proof_ablate',
    n_total: result.total,
    n_ablated: result.ablated,
    spec: specSummary(opts),
    seed: opts.seed,
  }
}
