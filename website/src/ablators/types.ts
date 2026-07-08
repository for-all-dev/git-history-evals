// Uniform contract over the three WASM ablators (Lean / Isabelle / Rocq).
//
// Each backend was compiled to WebAssembly from a *different* toolchain and so
// exposes a *different* JS ABI (emscripten `ccall`, wasm-pack ESM, and
// wasm_of_ocaml global). The adapters in this folder hide that behind one
// `Ablator` interface so the UI never has to care which prover it is driving.

export type Lang = 'lean' | 'isabelle' | 'rocq'

export const LANGS: readonly Lang[] = ['lean', 'isabelle', 'rocq'] as const

export const LANG_LABEL: Record<Lang, string> = {
  lean: 'Lean 4',
  isabelle: 'Isabelle/HOL',
  rocq: 'Rocq (Coq)',
}

/** The marker each prover splices in place of a removed body. */
export const HOLE_MARKER: Record<Lang, string> = {
  lean: 'sorry',
  isabelle: 'sorry',
  rocq: 'Admitted',
}

export interface Hole {
  theorem_name?: string
  depth?: number
  n_lines?: number
  method?: string
  centrality?: number
  is_leaf?: boolean
  proof_text?: string
  [k: string]: unknown
}

/** Normalised result, unified across the three backends' JSON shapes. */
export interface AblateResult {
  /** the ablated challenge file content */
  text: string
  /** total ablatable bodies considered */
  total: number
  /** how many were actually removed */
  ablated: number
  holes: Hole[]
  deletedLemmas: { name: string; text?: string }[]
  /** unified diff turning `text` back into the ground-truth solution */
  solutionDiff?: string
  /** the full JSON object the WASM module returned (for the JSON-evals view) */
  raw: unknown
}

export type RateMode = 'prob' | 'count'

/**
 * The full knob set the demo exposes — the union of what the three standalone
 * playgrounds expose. `depth`/`size`/`centrality` bounds are strings so `inf`
 * round-trips (both JSON backends and the Lean numeric ABI accept the sentinel).
 * Each adapter maps these into its backend's native encoding, and knobs a
 * backend's ABI can't express (see `CAPS`) are dropped for that backend.
 */
export interface AblateOptions {
  // rate
  rateMode: RateMode
  prob: number
  count: number
  byCentrality: boolean
  // selection window
  minDepth: string
  maxDepth: string
  leavesOnly: boolean
  minSize: string
  maxSize: string
  minCentrality: string
  maxCentrality: string
  // lemma deletion
  deleteLemmas: boolean
  corollary: boolean
  deleteCount: number | null
  deleteUniform: boolean
  deleteLeaves: boolean
  // context shaping
  truncate: boolean
  shrinkChallenge: boolean
  shrinkSolution: boolean
  shrinkChallengeMinimal: boolean
  shrinkSolutionMinimal: boolean
  // language-specific
  allowDefined: boolean // rocq: also ablate `Defined.` proofs
  ablateScripts: boolean // isabelle: ablate apply-scripts
  seed: number
}

/** Which knobs each backend's WASM ABI can actually honour. */
export interface Caps {
  minimalShrink: boolean
  lemmaDelete: boolean // corollary / delete_count / delete_uniform / delete_leaves
  allowDefined: boolean
  ablateScripts: boolean
}

export const CAPS: Record<Lang, Caps> = {
  // All three backends now honour the full lemma-delete + minimal-shrink knob
  // set (Lean's numeric ABI was extended to parity). `allowDefined` is Rocq-only
  // and `ablateScripts` is Isabelle-only (genuine language-specific knobs).
  lean: { minimalShrink: true, lemmaDelete: true, allowDefined: false, ablateScripts: false },
  isabelle: { minimalShrink: true, lemmaDelete: true, allowDefined: false, ablateScripts: true },
  rocq: { minimalShrink: true, lemmaDelete: true, allowDefined: true, ablateScripts: false },
}

/**
 * Difficulty ladder L0..L4, identical across the three standalone demos. Each
 * entry overrides the rate + depth window; other knobs are left as-is.
 */
export const PRESETS: { prob: number; minDepth: string; maxDepth: string; leavesOnly: boolean }[] = [
  { prob: 0.3, minDepth: '1', maxDepth: 'inf', leavesOnly: true },
  { prob: 1.0, minDepth: '1', maxDepth: 'inf', leavesOnly: true },
  { prob: 1.0, minDepth: '2', maxDepth: 'inf', leavesOnly: false },
  { prob: 0.5, minDepth: '1', maxDepth: '1', leavesOnly: false },
  { prob: 1.0, minDepth: '1', maxDepth: '1', leavesOnly: false },
]

/**
 * Defaults are the issue-#112 default experience: delete one fan-in-weighted
 * theorem from one random corollary's dependency closure, context-minimized.
 * (`seed` is replaced with a random value by the app on mount.)
 */
export const DEFAULT_OPTIONS: AblateOptions = {
  rateMode: 'prob',
  prob: 0.5,
  count: 3,
  byCentrality: false,
  minDepth: '1',
  maxDepth: '1',
  leavesOnly: false,
  minSize: '0',
  maxSize: 'inf',
  minCentrality: '0',
  maxCentrality: 'inf',
  deleteLemmas: true,
  corollary: true,
  deleteCount: 1,
  deleteUniform: false,
  deleteLeaves: false,
  truncate: false,
  shrinkChallenge: false,
  shrinkSolution: false,
  shrinkChallengeMinimal: true,
  shrinkSolutionMinimal: true,
  allowDefined: false,
  ablateScripts: false,
  seed: 0,
}

/** JSON-opts object shared by the Isabelle & Rocq backends (both ignore keys
 *  their ABI doesn't know, so one builder serves both). */
export function richSpec(o: AblateOptions): Record<string, unknown> {
  const s: Record<string, unknown> = {
    min_depth: o.minDepth,
    max_depth: o.maxDepth,
    leaves_only: o.leavesOnly,
    min_size: o.minSize,
    max_size: o.maxSize,
    min_centrality: o.minCentrality,
    max_centrality: o.maxCentrality,
    truncate: o.truncate,
    shrink_challenge: o.shrinkChallenge,
    shrink_solution: o.shrinkSolution,
    shrink_challenge_minimal: o.shrinkChallengeMinimal,
    shrink_solution_minimal: o.shrinkSolutionMinimal,
    delete_lemmas: o.deleteLemmas,
    delete_uniform: o.deleteUniform,
    delete_leaves: o.deleteLeaves,
    corollary: o.corollary,
    allow_defined: o.allowDefined,
    ablate_scripts: o.ablateScripts,
  }
  if (o.rateMode === 'count') {
    s.count = o.count
    s.by_centrality = o.byCentrality
  } else {
    s.prob = o.prob
  }
  if (o.deleteLemmas && o.deleteCount != null) s.delete_count = o.deleteCount
  return s
}

export interface Ablator {
  readonly lang: Lang
  /** idempotent; resolves once the WASM module is ready */
  load(): Promise<void>
  ablate(source: string, opts: AblateOptions): AblateResult
  /** true once `load()` has resolved */
  readonly ready: boolean
}
