// Lean 4 adapter. Backend: Lean-in-Lean ablator, emitted as portable C by the
// Lean compiler and relinked to WebAssembly with emscripten. It exposes a
// MODULARIZE factory `createAblator()` and a C entry `ablate_json` taking the
// source plus 15 numeric knobs (see `leanAblateTheory` in Ablator/Wasm.lean).
//
// ABI note: the Lean WASM entry predates the corollary / delete-count knobs the
// Isabelle & Rocq backends grew, so it cannot express "delete one whole lemma
// inside a random corollary's closure". The faithful analogue its ABI *can*
// express is to blank the `deleteCount` most fan-in-central top-level bodies
// (`--count N --by-centrality`) with `sorry`. The UI flags this approximation.

import type { Ablator, AblateOptions, AblateResult } from './types'
import { loadScript, normalizeRaw, wasmUrl } from './loader'

const INF = 0xffffffff

interface EmModule {
  ccall: (name: string, ret: string, argTypes: string[], args: unknown[]) => number
  UTF8ToString: (ptr: number) => string
  _free: (ptr: number) => void
}

declare global {
  var createAblator: ((opts?: Record<string, unknown>) => Promise<EmModule>) | undefined
}

let mod: EmModule | null = null

// text + 15 numbers, matching leanAblateTheory's argument order.
const SIG = ['string', ...Array<string>(15).fill('number')]

export const leanAblator: Ablator = {
  lang: 'lean',
  ready: false,
  async load() {
    if (this.ready) return
    await loadScript(wasmUrl('lean/ablator.js'))
    if (typeof globalThis.createAblator !== 'function') throw new Error('lean createAblator missing')
    mod = await globalThis.createAblator({
      locateFile: (p: string) => wasmUrl(`lean/${p}`),
    })
    ;(this as { ready: boolean }).ready = true
  },
  ablate(source: string, opts: AblateOptions): AblateResult {
    if (!mod) throw new Error('lean wasm not loaded')
    const sh = opts.contextMinimize ? 1 : 0
    const args = [
      source,
      1, // minDepth
      1, // maxDepth (top-level bodies)
      0, // leavesOnly
      0, // minSize
      INF, // maxSize
      0, // minCentrality
      INF, // maxCentrality
      opts.deleteCount, // count
      1, // byCentrality: pick the most fan-in-central
      0, // probPermille (unused when count is set)
      0, // truncate
      sh, // shrinkChallenge
      sh, // shrinkSolution
      0, // deleteLemmas
      opts.seed,
    ]
    const ptr = mod.ccall('ablate_json', 'number', SIG, args)
    const json = mod.UTF8ToString(ptr)
    mod._free(ptr)
    const raw = JSON.parse(json)
    const n = normalizeRaw(raw)
    return { ...n, raw }
  },
}
