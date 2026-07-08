// Lean 4 adapter. Backend: Lean-in-Lean ablator, emitted as portable C by the
// Lean compiler and relinked to WebAssembly with emscripten. It exposes a
// MODULARIZE factory `createAblator()` and a C entry `ablate_json` taking the
// source plus 15 numeric knobs (see `leanAblateTheory` in Ablator/Wasm.lean).
//
// ABI note: the Lean WASM entry now exposes the full knob set — corollary /
// delete-count / delete-uniform / delete-leaves / minimal-shrink — reaching
// parity with the Isabelle & Rocq JSON backends. Every knob maps 1:1 onto the
// numeric argument vector below (21 numbers, matching `ablate_json` in
// wasm/shim.c and `leanAblateTheory` in Ablator/Wasm.lean).

import type { Ablator, AblateOptions, AblateResult } from './types'
import { loadScript, normalizeRaw, wasmUrl } from './loader'

const INF = 0xffffffff

function u32(s: string): number {
  const t = String(s).trim()
  if (t === '' || t === 'inf' || t === 'infinity') return INF
  return parseInt(t, 10) >>> 0
}

interface EmModule {
  ccall: (name: string, ret: string, argTypes: string[], args: unknown[]) => number
  UTF8ToString: (ptr: number) => string
  _free: (ptr: number) => void
}

declare global {
  var createAblator: ((opts?: Record<string, unknown>) => Promise<EmModule>) | undefined
}

let mod: EmModule | null = null

// text + 22 numbers, matching leanAblateTheory's argument order (the final knob
// before seed is `corollaryAll`, which switches the return to a JSON array).
const SIG = ['string', ...Array<string>(22).fill('number')]

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
    const raw = callLean(source, opts, false) as Record<string, unknown>
    return { ...normalizeRaw(raw), raw }
  },
  ablateAll(source: string, opts: AblateOptions): AblateResult[] {
    // with corollaryAll=1 the wasm returns a JSON *array* (one per corollary)
    const raw = callLean(source, opts, true)
    const arr = (Array.isArray(raw) ? raw : [raw]) as Record<string, unknown>[]
    return arr.map((r) => ({ ...normalizeRaw(r), raw: r }))
  },
}

// build the 22-number arg vector + invoke `ablate_json`, returning the parsed JSON.
function callLean(source: string, opts: AblateOptions, corollaryAll: boolean): unknown {
  if (!mod) throw new Error('lean wasm not loaded')
  const count = opts.rateMode === 'count' ? opts.count : INF
  const permille = opts.rateMode === 'prob' ? Math.round(opts.prob * 1000) : 0
  // deleteCount: the `--delete-lemmas N` target; INF sentinel = unset.
  const deleteCount = opts.deleteLemmas && opts.deleteCount != null ? opts.deleteCount >>> 0 : INF
  const args = [
    source,
    u32(opts.minDepth),
    u32(opts.maxDepth),
    opts.leavesOnly ? 1 : 0,
    u32(opts.minSize),
    u32(opts.maxSize),
    u32(opts.minCentrality),
    u32(opts.maxCentrality),
    count,
    opts.byCentrality ? 1 : 0,
    permille,
    opts.truncate ? 1 : 0,
    opts.shrinkChallenge ? 1 : 0,
    opts.shrinkSolution ? 1 : 0,
    opts.deleteLemmas ? 1 : 0,
    opts.shrinkChallengeMinimal ? 1 : 0,
    opts.shrinkSolutionMinimal ? 1 : 0,
    deleteCount,
    opts.deleteUniform ? 1 : 0,
    opts.deleteLeaves ? 1 : 0,
    opts.corollary ? 1 : 0,
    corollaryAll ? 1 : 0,
    opts.seed,
  ]
  const ptr = mod.ccall('ablate_json', 'number', SIG, args)
  const json = mod.UTF8ToString(ptr)
  mod._free(ptr)
  return JSON.parse(json)
}
