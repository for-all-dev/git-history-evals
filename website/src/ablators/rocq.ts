// Rocq (Coq) adapter. Backend: the OCaml ablator compiled with wasm_of_ocaml.
// It publishes a global `rocqAblate(text, optsJson, seed) -> jsonString` and
// loads its `.wasm` from `ablator.assets/` relative to the script URL.
//
// Takes the full rich JSON-opts ABI (rate, selection window, lemma deletion
// incl. corollary/delete_count/allow_defined, context shaping).

import type { Ablator, AblateOptions, AblateResult } from './types'
import { richSpec } from './types'
import { loadScript, normalizeRaw, waitFor, wasmUrl } from './loader'

declare global {
  var rocqAblate: ((text: string, optsJson: string, seed: number) => string) | undefined
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
    const raw = JSON.parse(globalThis.rocqAblate(source, JSON.stringify(richSpec(opts)), opts.seed))
    const n = normalizeRaw(raw)
    return { ...n, raw }
  },
}
