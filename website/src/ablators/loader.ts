// Small helpers shared by the adapters for loading assets out of `public/wasm/`.

/** Base URL the site is served under (respects Vite's `base`). Ends with '/'. */
export const BASE = import.meta.env.BASE_URL

export function wasmUrl(path: string): string {
  return `${BASE}wasm/${path}`.replace(/([^:])\/\//g, '$1/')
}

/** Inject a classic <script> once; resolve on load. Keyed so repeats are no-ops. */
export function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[data-wasm="${src}"]`,
    )
    if (existing) {
      if (existing.dataset.loaded === '1') resolve()
      else {
        existing.addEventListener('load', () => resolve())
        existing.addEventListener('error', () => reject(new Error(`failed to load ${src}`)))
      }
      return
    }
    const s = document.createElement('script')
    s.src = src
    s.async = true
    s.dataset.wasm = src
    s.addEventListener('load', () => {
      s.dataset.loaded = '1'
      resolve()
    })
    s.addEventListener('error', () => reject(new Error(`failed to load ${src}`)))
    document.head.appendChild(s)
  })
}

/** Poll for a condition (used for wasm_of_ocaml's async global publish). */
export function waitFor(pred: () => boolean, tries = 400, delayMs = 25): Promise<void> {
  return new Promise((resolve, reject) => {
    const tick = (n: number) => {
      if (pred()) resolve()
      else if (n <= 0) reject(new Error('timed out waiting for wasm module'))
      else setTimeout(() => tick(n - 1), delayMs)
    }
    tick(tries)
  })
}

/** Coerce whatever the WASM `result_json` returned into a normalised result. */
export function normalizeRaw(raw: Record<string, unknown>): {
  text: string
  total: number
  ablated: number
  holes: Record<string, unknown>[]
  deletedLemmas: { name: string; text?: string }[]
  solutionDiff?: string
} {
  const holes = (raw.holes ?? raw.holes_filled ?? []) as Record<string, unknown>[]
  const deleted = (raw.deleted_lemmas ?? []) as { name: string; text?: string }[]
  return {
    text: String(raw.text ?? ''),
    total: Number(raw.total ?? 0),
    ablated: Number(raw.ablated ?? 0),
    holes: Array.isArray(holes) ? holes : [],
    deletedLemmas: Array.isArray(deleted) ? deleted : [],
    solutionDiff: typeof raw.solution_diff === 'string' ? raw.solution_diff : undefined,
  }
}
