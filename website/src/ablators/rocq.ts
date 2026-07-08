// Rocq (Coq) adapter. Backend: the OCaml ablator compiled with wasm_of_ocaml.
// It publishes a global `rocqAblate(text, optsJson, seed) -> jsonString` and
// loads its `.wasm` from `ablator.assets/` relative to the script URL.
//
// The rich JSON-opts ABI supports the issue's default natively:
//   delete_lemmas + corollary + delete_count + (weighted | uniform).

import type { Ablator, AblateOptions, AblateResult } from './types'
import { loadScript, normalizeRaw, waitFor, wasmUrl } from './loader'

declare global {
  var rocqAblate: ((text: string, optsJson: string, seed: number) => string) | undefined
}

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

export const rocqAblator: Ablator = {
  lang: 'rocq',
  ready: false,
  async load() {
    if (this.ready) return
    await loadScript(wasmUrl('rocq/ablator.js'))
    await waitFor(() => typeof globalThis.rocqAblate === 'function')
    ;(this as { ready: boolean }).ready = true
  },
  ablate(source: string, opts: AblateOptions): AblateResult {
    if (typeof globalThis.rocqAblate !== 'function') throw new Error('rocq wasm not loaded')
    const raw = JSON.parse(globalThis.rocqAblate(source, JSON.stringify(specOf(opts)), opts.seed))
    const n = normalizeRaw(raw)
    return { ...n, raw }
  },
}
