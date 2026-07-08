import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  CAPS,
  DEFAULT_OPTIONS,
  detectLanguage,
  getAblator,
  LANG_LABEL,
  LANGS,
  PRESETS,
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

// ---- small control primitives -------------------------------------------------

function Check({
  label,
  checked,
  onChange,
  disabled,
  hint,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  hint?: string
}) {
  return (
    <label className={`chk${disabled ? ' gated' : ''}`} title={disabled ? hint : undefined}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

function TextIn({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  return (
    <label className="ti">
      <span>{label}</span>
      <input type="text" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
    </label>
  )
}

// ------------------------------------------------------------------------------

export default function App() {
  const [source, setSource] = useState(SAMPLES.lean)
  const [debounced, setDebounced] = useState(source)
  const [override, setOverride] = useState<Lang | 'auto'>('auto')
  const [mode, setMode] = useState<Mode>('challenge')
  const [repeat, setRepeat] = useState(3)
  const [opts, setOpts] = useState<AblateOptions>(() => ({ ...DEFAULT_OPTIONS, seed: randSeed() }))
  const up = useCallback((patch: Partial<AblateOptions>) => setOpts((o) => ({ ...o, ...patch })), [])

  const [result, setResult] = useState<AblateResult | null>(null)
  const [records, setRecords] = useState<EvalRecord[] | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [error, setError] = useState('')

  const detected = useMemo(() => detectLanguage(debounced), [debounced])
  const lang: Lang = override === 'auto' ? detected : override
  const caps = CAPS[lang]

  useEffect(() => {
    const t = setTimeout(() => setDebounced(source), 200)
    return () => clearTimeout(t)
  }, [source])

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
    const id = setTimeout(() => void run(), 0)
    return () => clearTimeout(id)
  }, [run])

  const loadSample = (l: Lang) => {
    setOverride(l)
    setSource(SAMPLES[l])
  }
  const applyPreset = (i: number) => {
    const p = PRESETS[i]
    up({ rateMode: 'prob', prob: p.prob, minDepth: p.minDepth, maxDepth: p.maxDepth, leavesOnly: p.leavesOnly })
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
            <b>entirely in your browser</b> — removes selected proofs and hands back the challenge
            file (or the JSON eval it becomes). Every knob the standalone demos expose is here;
            controls a backend's WASM ABI can't honour are greyed out.
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
            <input type="range" min={1} max={10} value={repeat} onChange={(e) => setRepeat(Number(e.target.value))} />
          </div>
        )}

        <div className="field">
          <label>&nbsp;</label>
          <button className="generate" onClick={() => up({ seed: randSeed() })}>
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

      <main className="panes">
        <aside className="controls">
          <div className="ctrl-sec">
            <h3>Difficulty preset</h3>
            <div className="presets">
              {['L0', 'L1', 'L2', 'L3', 'L4'].map((l, i) => (
                <button key={l} onClick={() => applyPreset(i)}>
                  {l}
                </button>
              ))}
            </div>
          </div>

          <div className="ctrl-sec">
            <h3>Rate</h3>
            <select value={opts.rateMode} onChange={(e) => up({ rateMode: e.target.value as 'prob' | 'count' })}>
              <option value="prob">probability</option>
              <option value="count">exact count</option>
            </select>
            {opts.rateMode === 'prob' ? (
              <label className="ti">
                <span>probability {opts.prob.toFixed(2)}</span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={opts.prob}
                  onChange={(e) => up({ prob: Number(e.target.value) })}
                />
              </label>
            ) : (
              <>
                <label className="ti">
                  <span>count (per file)</span>
                  <input type="number" min={0} value={opts.count} onChange={(e) => up({ count: Number(e.target.value) || 0 })} />
                </label>
                <Check label="by centrality (most-cited)" checked={opts.byCentrality} onChange={(v) => up({ byCentrality: v })} />
              </>
            )}
          </div>

          <div className="ctrl-sec">
            <h3>Selection</h3>
            <div className="two">
              <TextIn label="min depth" value={opts.minDepth} onChange={(v) => up({ minDepth: v })} />
              <TextIn label="max depth" value={opts.maxDepth} onChange={(v) => up({ maxDepth: v })} />
            </div>
            <Check label="leaves only" checked={opts.leavesOnly} onChange={(v) => up({ leavesOnly: v })} />
            <div className="two">
              <TextIn label="min size" value={opts.minSize} onChange={(v) => up({ minSize: v })} />
              <TextIn label="max size" value={opts.maxSize} onChange={(v) => up({ maxSize: v })} />
            </div>
            <div className="two">
              <TextIn label="min centrality" value={opts.minCentrality} onChange={(v) => up({ minCentrality: v })} />
              <TextIn label="max centrality" value={opts.maxCentrality} onChange={(v) => up({ maxCentrality: v })} />
            </div>
          </div>

          <div className="ctrl-sec">
            <h3>Lemma deletion</h3>
            <Check label="delete used lemmas (+ ablate their users)" checked={opts.deleteLemmas} onChange={(v) => up({ deleteLemmas: v })} />
            <Check
              label="restrict to one random corollary's closure"
              checked={opts.corollary}
              onChange={(v) => up({ corollary: v })}
              disabled={!caps.lemmaDelete}
              hint={`${LANG_LABEL[lang]} WASM ABI: whole-lemma deletion only`}
            />
            <label className={`ti${!caps.lemmaDelete ? ' gated' : ''}`} title={!caps.lemmaDelete ? `${LANG_LABEL[lang]} WASM ABI has no delete-count` : undefined}>
              <span>delete count</span>
              <input
                type="number"
                min={1}
                value={opts.deleteCount ?? 1}
                disabled={!caps.lemmaDelete}
                onChange={(e) => up({ deleteCount: Number(e.target.value) || 1 })}
              />
            </label>
            <Check
              label="uniform pick (else fan-in weighted)"
              checked={opts.deleteUniform}
              onChange={(v) => up({ deleteUniform: v })}
              disabled={!caps.lemmaDelete}
              hint={`${LANG_LABEL[lang]} WASM ABI has no weighting knob`}
            />
            <Check
              label="hole only leaf steps citing the lemma"
              checked={opts.deleteLeaves}
              onChange={(v) => up({ deleteLeaves: v })}
              disabled={!caps.lemmaDelete}
              hint={`${LANG_LABEL[lang]} WASM ABI has no delete-leaves`}
            />
            {!caps.lemmaDelete && opts.deleteLemmas && (
              <p className="mini-note">
                {LANG_LABEL[lang]} deletes all eligible used lemmas (its ABI predates corollary /
                count restriction).
              </p>
            )}
          </div>

          <div className="ctrl-sec">
            <h3>Language-specific</h3>
            <Check
              label="ablate Defined. proofs (Rocq)"
              checked={opts.allowDefined}
              onChange={(v) => up({ allowDefined: v })}
              disabled={!caps.allowDefined}
              hint="only Rocq distinguishes Qed vs Defined"
            />
            <Check
              label="ablate apply-scripts (Isabelle)"
              checked={opts.ablateScripts}
              onChange={(v) => up({ ablateScripts: v })}
              disabled={!caps.ablateScripts}
              hint="apply-script ablation is Isabelle-specific"
            />
          </div>

          <div className="ctrl-sec">
            <h3>Context shaping</h3>
            <Check label="truncate after last hole" checked={opts.truncate} onChange={(v) => up({ truncate: v })} />
            <Check label="shrink challenge (prefix)" checked={opts.shrinkChallenge} onChange={(v) => up({ shrinkChallenge: v })} />
            <Check label="shrink solution (prefix)" checked={opts.shrinkSolution} onChange={(v) => up({ shrinkSolution: v })} />
            <Check
              label="shrink challenge — minimal slice"
              checked={opts.shrinkChallengeMinimal}
              onChange={(v) => up({ shrinkChallengeMinimal: v })}
              disabled={!caps.minimalShrink}
              hint={`${LANG_LABEL[lang]} WASM ABI has no minimal-slice shrink`}
            />
            <Check
              label="shrink solution — minimal slice"
              checked={opts.shrinkSolutionMinimal}
              onChange={(v) => up({ shrinkSolutionMinimal: v })}
              disabled={!caps.minimalShrink}
              hint={`${LANG_LABEL[lang]} WASM ABI has no minimal-slice shrink`}
            />
          </div>

          <div className="ctrl-sec">
            <h3>Seed</h3>
            <input type="number" value={opts.seed} onChange={(e) => up({ seed: Number(e.target.value) || 0 })} />
          </div>
        </aside>

        <section className="pane">
          <div className="pane-head">
            <h2>Source</h2>
            <span className="muted">edit or paste any of the three provers</span>
          </div>
          <textarea className="source" spellCheck={false} value={source} onChange={(e) => setSource(e.target.value)} />
        </section>

        <section className="pane">
          <div className="pane-head">
            <h2>{mode === 'challenge' ? 'Ablated challenge' : `JSON evals${records ? ` (${records.length})` : ''}`}</h2>
            <span className="muted">
              {status === 'loading' && 'loading wasm…'}
              {status === 'error' && <span className="err">error</span>}
              {status === 'ready' && stats && (
                <>
                  ablated <b>{stats.ablated}</b> / {stats.total}
                  {deletedNames.length > 0 && <> · deleted {deletedNames.join(', ')}</>}
                </>
              )}
            </span>
          </div>

          {status === 'error' && <pre className="error-box">{error}</pre>}
          {mode === 'challenge' && result && <CodeView lang={lang} code={result.text} />}
          {mode === 'json' && records && (
            <div className="records">
              {records.length === 0 && <p className="muted">no ablations produced — adjust the knobs or re-generate.</p>}
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
