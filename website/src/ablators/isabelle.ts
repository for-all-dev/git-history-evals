// Isabelle/HOL adapter. Backend: the Rust ablator compiled with wasm-pack
// (`--target web`), an ES module exporting `ablate_theory(text, optsJson, seed)`
// and a default `init()` that self-locates its `_bg.wasm` via `import.meta.url`.
//
// Same rich JSON-opts ABI as Rocq, so the issue's default maps natively.

import type { Ablator, AblateOptions, AblateResult } from './types'
import { normalizeRaw, wasmUrl } from './loader'

interface IsabelleModule {
  default: (input?: unknown) => Promise<unknown>
  ablate_theory: (text: string, optsJson: string, seed: number) => string
  keyword_count: () => number
}

let mod: IsabelleModule | null = null

function specOf(opts: AblateOptions): Record<string, unknown> {
  return {
    delete_lemmas: true,
    corollary: opts.corollary,
    delete_count: opts.deleteCount,
    delete_uniform: !opts.weightedByFanIn,
    shrink_challenge_minimal: opts.contextMinimize,
    shrink_solution_minimal: opts.contextMinimize,
  }
}

export const isabelleAblator: Ablator = {
  lang: 'isabelle',
  ready: false,
  async load() {
    if (this.ready) return
    // Runtime import of a file in public/ — bypass Vite's module graph.
    const m = (await import(/* @vite-ignore */ wasmUrl('isabelle/pkg/isabelle_ablator.js'))) as IsabelleModule
    await m.default()
    mod = m
    ;(this as { ready: boolean }).ready = true
  },
  ablate(source: string, opts: AblateOptions): AblateResult {
    if (!mod) throw new Error('isabelle wasm not loaded')
    const raw = JSON.parse(mod.ablate_theory(source, JSON.stringify(specOf(opts)), opts.seed))
    const n = normalizeRaw(raw)
    return { ...n, raw }
  },
}
