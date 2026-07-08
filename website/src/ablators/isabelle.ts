// Isabelle/HOL adapter. Backend: the Rust ablator compiled with wasm-pack
// (`--target web`), an ES module exporting `ablate_theory(text, optsJson, seed)`
// and a default `init()` that self-locates its `_bg.wasm` via `import.meta.url`.
//
// Loading: we inject a `<script type="module">` whose source imports the wasm-
// pack glue and publishes it on a global, rather than a bundler-visible
// `import()`. Vite's dev import-analysis otherwise appends `?import` to a
// dynamic import of a public/ file and 500s trying to pull it into its module
// graph; a hand-written module script is never analysed, so the browser fetches
// the static file directly. Same rich JSON-opts ABI as Rocq (plus
// `ablate_scripts`); shares `richSpec`.

import type { Ablator, AblateOptions, AblateResult } from './types'
import { richSpec } from './types'
import { normalizeRaw, waitFor, wasmUrl } from './loader'

interface IsabelleExports {
  ablate_theory: (text: string, optsJson: string, seed: number) => string
  keyword_count: () => number
}

declare global {
  var __isabelleMod: IsabelleExports | undefined
  var __isabelleInjected: boolean | undefined
}

export const isabelleAblator: Ablator = {
  lang: 'isabelle',
  ready: false,
  async load() {
    if (this.ready) return
    if (!globalThis.__isabelleInjected) {
      globalThis.__isabelleInjected = true
      const url = wasmUrl('isabelle/pkg/isabelle_ablator.js')
      const s = document.createElement('script')
      s.type = 'module'
      // top-level await keeps __isabelleMod unset until init() resolves
      s.textContent =
        `import init, { ablate_theory, keyword_count } from ${JSON.stringify(url)};\n` +
        `await init();\n` +
        `globalThis.__isabelleMod = { ablate_theory, keyword_count };`
      document.head.appendChild(s)
    }
    await waitFor(() => typeof globalThis.__isabelleMod !== 'undefined')
    ;(this as { ready: boolean }).ready = true
  },
  ablate(source: string, opts: AblateOptions): AblateResult {
    const mod = globalThis.__isabelleMod
    if (!mod) throw new Error('isabelle wasm not loaded')
    const raw = JSON.parse(mod.ablate_theory(source, JSON.stringify(richSpec(opts)), opts.seed))
    const n = normalizeRaw(raw)
    return { ...n, raw }
  },
}
