import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  detectLanguage,
  getAblator,
  LANG_LABEL,
  LANGS,
  type AblateOptions,
  type AblateResult,
  type Lang,
} from './ablators'
import { SAMPLES } from './lib/samples'
import { toRecord, type EvalRecord } from './lib/record'
import { CodeView } from './components/CodeView'
import { JsonView } from './components/JsonView'

type Mode = 'challenge' | 'json'

function randSeed(): number {
  return Math.floor(Math.random() * 1_000_000)
}

export default function App() {
  const [source, setSource] = useState(SAMPLES.lean)
  const [debounced, setDebounced] = useState(source)
  const [override, setOverride] = useState<Lang | 'auto'>('auto')
  const [deleteCount, setDeleteCount] = useState(1)
  const [mode, setMode] = useState<Mode>('challenge')
  const [repeat, setRepeat] = useState(3)
  const [seed, setSeed] = useState(() => randSeed())
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [corollary, setCorollary] = useState(true)
  const [weightedByFanIn, setWeighted] = useState(true)
  const [contextMinimize, setContextMinimize] = useState(true)

  const [result, setResult] = useState<AblateResult | null>(null)
  const [records, setRecords] = useState<EvalRecord[] | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [error, setError] = useState('')

  const detected = useMemo(() => detectLanguage(debounced), [debounced])
  const lang: Lang = override === 'auto' ? detected : override

  // debounce source edits so typing doesn't thrash the wasm
  useEffect(() => {
    const t = setTimeout(() => setDebounced(source), 200)
    return () => clearTimeout(t)
  }, [source])

  const opts: AblateOptions = useMemo(
    () => ({ deleteCount, corollary, weightedByFanIn, contextMinimize, seed }),
    [deleteCount, corollary, weightedByFanIn, contextMinimize, seed],
  )

  const runToken = useRef(0)

  const run = useCallback(async () => {
    const token = ++runToken.current
    setStatus('loading')
    setError('')
    try {
      const ab = getAblator(lang)
      if (!ab.ready) await ab.load()
      if (token !== runToken.current) return
      if (mode === 'challenge') {
        setResult(ab.ablate(debounced, opts))
        setRecords(null)
      } else {
        const recs: EvalRecord[] = []
        const seen = new Set<string>()
        for (let k = 0; k < repeat; k++) {
          const r = ab.ablate(debounced, { ...opts, seed: opts.seed + k })
          if (seen.has(r.text)) continue
          seen.add(r.text)
          recs.push(toRecord(lang, r, { ...opts, seed: opts.seed + k }))
        }
        setRecords(recs)
        setResult(null)
      }
      if (token === runToken.current) setStatus('ready')
    } catch (e) {
      if (token !== runToken.current) return
      setError(String(e instanceof Error ? e.message : e))
      setStatus('error')
    }
  }, [lang, mode, debounced, opts, repeat])

  useEffect(() => {
    // defer so the effect body doesn't setState synchronously, and so a burst
    // of control changes coalesces into a single run
    const id = setTimeout(() => void run(), 0)
    return () => clearTimeout(id)
  }, [run])

  const loadSample = (l: Lang) => {
    setOverride(l)
    setSource(SAMPLES[l])
  }

  const stats = result
    ? { ablated: result.ablated, total: result.total }
    : records && records.length
      ? { ablated: records[0].n_ablated, total: records[0].n_total }
      : null
  const deletedNames = result
    ? result.deletedLemmas.map((d) => d.name)
    : (records?.[0]?.deleted_lemmas ?? []).map((d) => d.name)

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Proof Ablation Playground</h1>
          <p>
            One demo over three provers. Paste a <b>Lean</b>, <b>Isabelle/HOL</b>, or{' '}
            <b>Rocq/Coq</b> theory; the matching ablator — compiled to WebAssembly and running{' '}
            <b>entirely in your browser</b> — deletes a fan-in-weighted theorem from a random
            corollary and hands back the challenge file (or the JSON eval it becomes).
          </p>
        </div>
      </header>

      <div className="toolbar">
        <div className="field">
          <label>Language</label>
          <div className="lang-row">
            <select value={override} onChange={(e) => setOverride(e.target.value as Lang | 'auto')}>
              <option value="auto">auto-detect</option>
              {LANGS.map((l) => (
                <option key={l} value={l}>
                  {LANG_LABEL[l]}
                </option>
              ))}
            </select>
            <span className={`badge badge-${lang}`}>
              {override === 'auto' ? `detected: ${LANG_LABEL[lang]}` : LANG_LABEL[lang]}
            </span>
          </div>
        </div>

        <div className="field">
          <label>Theorems to delete: {deleteCount}</label>
          <input
            type="range"
            min={1}
            max={8}
            value={deleteCount}
            onChange={(e) => setDeleteCount(Number(e.target.value))}
          />
        </div>

        <div className="field">
          <label>Output</label>
          <div className="seg">
            <button className={mode === 'challenge' ? 'on' : ''} onClick={() => setMode('challenge')}>
              Ablated challenge
            </button>
            <button className={mode === 'json' ? 'on' : ''} onClick={() => setMode('json')}>
              JSON evals
            </button>
          </div>
        </div>

        {mode === 'json' && (
          <div className="field">
            <label>Repeat (variants): {repeat}</label>
            <input
              type="range"
              min={1}
              max={10}
              value={repeat}
              onChange={(e) => setRepeat(Number(e.target.value))}
            />
          </div>
        )}

        <div className="field">
          <label>&nbsp;</label>
          <button className="generate" onClick={() => setSeed(randSeed())}>
            ↻ Generate
          </button>
        </div>

        <div className="field samples">
          <label>Load sample</label>
          <div className="seg">
            {LANGS.map((l) => (
              <button key={l} onClick={() => loadSample(l)}>
                {l}
              </button>
            ))}
          </div>
        </div>
      </div>

      <details
        className="advanced"
        open={showAdvanced}
        onToggle={(e) => setShowAdvanced((e.target as HTMLDetailsElement).open)}
      >
        <summary>Advanced</summary>
        <div className="adv-grid">
          <label className="chk">
            <input type="checkbox" checked={corollary} onChange={(e) => setCorollary(e.target.checked)} />
            restrict to one random corollary's dependency closure
          </label>
          <label className="chk">
            <input
              type="checkbox"
              checked={weightedByFanIn}
              onChange={(e) => setWeighted(e.target.checked)}
            />
            weight selection by fan-in (else uniform)
          </label>
          <label className="chk">
            <input
              type="checkbox"
              checked={contextMinimize}
              onChange={(e) => setContextMinimize(e.target.checked)}
            />
            context-minimize the emitted file(s)
          </label>
          <label className="seed-field">
            seed
            <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value) || 0)} />
          </label>
        </div>
        {lang === 'lean' && (
          <p className="note">
            Note: the Lean WASM ABI predates the corollary / delete-count knobs, so it approximates
            the default by blanking the {deleteCount} most fan-in-central top-level bodies with{' '}
            <code>sorry</code> (no whole-lemma deletion).
          </p>
        )}
      </details>

      <main className="panes">
        <section className="pane">
          <div className="pane-head">
            <h2>Source</h2>
            <span className="muted">edit or paste any of the three provers</span>
          </div>
          <textarea
            className="source"
            spellCheck={false}
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
        </section>

        <section className="pane">
          <div className="pane-head">
            <h2>
              {mode === 'challenge'
                ? 'Ablated challenge'
                : `JSON evals${records ? ` (${records.length})` : ''}`}
            </h2>
            <span className="muted">
              {status === 'loading' && 'loading wasm…'}
              {status === 'error' && <span className="err">error</span>}
              {status === 'ready' && stats && (
                <>
                  deleted <b>{stats.ablated}</b> / {stats.total}
                  {deletedNames.length > 0 && <> · {deletedNames.join(', ')}</>}
                </>
              )}
            </span>
          </div>

          {status === 'error' && <pre className="error-box">{error}</pre>}

          {mode === 'challenge' && result && <CodeView lang={lang} code={result.text} />}

          {mode === 'json' && records && (
            <div className="records">
              {records.length === 0 && (
                <p className="muted">no ablations produced — try a higher count or a different seed.</p>
              )}
              {records.map((r, i) => (
                <div key={i} className="record">
                  <div className="record-head">
                    #{i + 1} · {r.task_id}
                  </div>
                  <JsonView data={r} />
                </div>
              ))}
            </div>
          )}
        </section>
      </main>

      <footer className="app-footer">
        <span>
          All computation is client-side WebAssembly — nothing is uploaded. Backends: Lean-in-Lean
          (emscripten), Isabelle-in-Rust (wasm-pack), Rocq-in-OCaml (wasm_of_ocaml).
        </span>
      </footer>
    </div>
  )
}
