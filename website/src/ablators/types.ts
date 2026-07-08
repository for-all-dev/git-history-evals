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

/**
 * The knobs the demo exposes. The default challenge — "delete one theorem,
 * weighted by fan-in, for some randomly selected corollary" (issue #112) — is
 * `{ deleteCount: 1, corollary: true, weightedByFanIn: true }`. Each adapter
 * translates these into its backend's native option encoding as faithfully as
 * that backend's ABI allows (see the per-adapter notes).
 */
export interface AblateOptions {
  /** how many theorems/lemmas to remove (the deletion slider) */
  deleteCount: number
  /** restrict deletions to one random corollary's dependency closure */
  corollary: boolean
  /** weight the random pick by corpus fan-in (vs. uniform) */
  weightedByFanIn: boolean
  /** trim the emitted files to the minimal slice around the holes */
  contextMinimize: boolean
  seed: number
}

export interface Ablator {
  readonly lang: Lang
  /** idempotent; resolves once the WASM module is ready */
  load(): Promise<void>
  ablate(source: string, opts: AblateOptions): AblateResult
  /** true once `load()` has resolved */
  readonly ready: boolean
}
