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
import { fetchAfpIndex, fetchAfpEntryTheories, fetchAfpTheory, type AfpIndex, type AfpTheory } from './lib/afp'
import { toRecord, type EvalRecord } from './lib/record'
import { CodeView } from './components/CodeView'
import { CodeEditor } from './components/CodeEditor'
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

  // AFP importer (issue #113): pull a real Archive of Formal Proofs theory from
  // our world-readable mirror into the source pane. Isabelle-only.
  const [afp, setAfp] = useState<AfpIndex | null>(null) // lightweight entry list
  const [afpOpen, setAfpOpen] = useState(false)
  const [afpEntry, setAfpEntry] = useState('')
  const [afpTheories, setAfpTheories] = useState<AfpTheory[] | null>(null) // lazy shard for afpEntry
  const [afpFile, setAfpFile] = useState('') // currently-loaded theory (persists across setting tweaks)
  const [afpBusy, setAfpBusy] = useState(false)
  const [afpErr, setAfpErr] = useState('')
  const afpCache = useRef<Map<string, AfpTheory[]>>(new Map()) // entry -> theories.json

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
        // corollary-all yields many challenges; the single-challenge view shows the first
        setResult(opts.corollaryAll ? (ab.ablateAll(debounced, opts)[0] ?? null) : ab.ablate(debounced, opts))
        setRecords(null)
      } else {
        const recs: EvalRecord[] = []
        const seen = new Set<string>()
        if (opts.corollaryAll) {
          // one ablation per eligible corollary; --repeat is ignored (as in the CLI).
          // dedup by the (deleted lemma(s), corollary) PAIR — the same lemma deleted for
          // different corollaries is kept; only an identical pair is dropped.
          for (const r of ab.ablateAll(debounced, opts)) {
            const dels = r.deletedLemmas.map((d) => d.name).sort().join(',')
            const cors = ((r.raw as { corollaries?: { name: string }[] }).corollaries ?? [])
              .map((c) => c.name).sort().join(',')
            const key = `${dels}\x00${cors}`
            if (seen.has(key)) continue
            seen.add(key)
            recs.push(toRecord(lang, r, opts))
          }
        } else {
          for (let k = 0; k < repeat; k++) {
            const r = ab.ablate(debounced, { ...opts, seed: opts.seed + k })
            if (seen.has(r.text)) continue
            seen.add(r.text)
            recs.push(toRecord(lang, r, { ...opts, seed: opts.seed + k }))
          }
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
    setAfpFile('') // a built-in sample is now in the pane, not an AFP theory
  }

  const openAfp = useCallback(async () => {
    setAfpOpen((o) => !o)
    if (afp) return
    setAfpBusy(true)
    setAfpErr('')
    try {
      setAfp(await fetchAfpIndex())
    } catch (e) {
      setAfpErr(String(e instanceof Error ? e.message : e))
    } finally {
      setAfpBusy(false)
    }
  }, [afp])

  // Pick a specific theory from the currently-loaded entry shard.
  const pickAfpTheory = useCallback(
    async (file: string) => {
      const theory = afpTheories?.find((t) => t.file === file)
      if (!theory) return
      setAfpFile(file) // reflect the selection immediately; survives setting tweaks
      setAfpBusy(true)
      setAfpErr('')
      try {
        const text = await fetchAfpTheory(theory)
        setOverride('isabelle')
        setSource(text)
      } catch (e) {
        setAfpErr(String(e instanceof Error ? e.message : e))
      } finally {
        setAfpBusy(false)
      }
    },
    [afpTheories],
  )

  // Selecting an entry (typed/picked in the combobox) lazily fetches its theory
  // shard and loads the first theory — keeps the flow live and the theory
  // dropdown always pointing at real, loaded source. Ignores partial typing.
  const selectAfpEntry = useCallback(
    async (name: string) => {
      setAfpEntry(name)
      if (!afp?.entries.some((e) => e.name === name)) {
        setAfpTheories(null)
        setAfpFile('')
        return
      }
      setAfpBusy(true)
      setAfpErr('')
      try {
        let ths = afpCache.current.get(name)
        if (!ths) {
          ths = await fetchAfpEntryTheories(name)
          afpCache.current.set(name, ths)
        }
        setAfpTheories(ths)
        const first = ths[0]
        setAfpFile(first?.file ?? '')
        if (first) {
          const text = await fetchAfpTheory(first)
          setOverride('isabelle')
          setSource(text)
        }
      } catch (e) {
        setAfpErr(String(e instanceof Error ? e.message : e))
        setAfpTheories(null)
      } finally {
        setAfpBusy(false)
      }
    },
    [afp],
  )

  // The L0–L4 presets are probability/depth (selection-mode) difficulty knobs, so
  // a preset must (a) switch off corollary mode and (b) clear deleteCount —
  // otherwise the corollary/delete-count path pins the ablation to a fixed count
  // (`delete_count` is emitted whenever it's non-null) and every preset yields the
  // identical result regardless of prob/depth.
  const applyPreset = (i: number) => {
    const p = PRESETS[i]
    up({
      corollary: false,
      deleteCount: null,
      rateMode: 'prob',
      prob: p.prob,
      minDepth: p.minDepth,
      maxDepth: p.maxDepth,
      leavesOnly: p.leavesOnly,
    })
  }
  const activePreset = useMemo(
    () =>
      opts.corollary || opts.rateMode !== 'prob'
        ? -1
        : PRESETS.findIndex(
            (p) =>
              p.prob === opts.prob &&
              p.minDepth === opts.minDepth &&
              p.maxDepth === opts.maxDepth &&
              p.leavesOnly === opts.leavesOnly,
          ),
    [opts.corollary, opts.rateMode, opts.prob, opts.minDepth, opts.maxDepth, opts.leavesOnly],
  )

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
        <svg className="app-logo" viewBox="0 0 640 640" role="img" aria-label="Forall R&D" xmlns="http://www.w3.org/2000/svg">
          <g transform="translate(-81, 151)">
            <text x="455" y="112" fontFamily="monospace" fontSize="52" fontWeight="500" fill="currentColor">
              R&amp;D
            </text>
            <g stroke="currentColor" strokeWidth="17" strokeLinecap="butt" strokeLinejoin="miter" fill="none">
              <path d="M250 60 L340 260 L430 60" />
              <line x1="285" y1="140" x2="560" y2="140" />
            </g>
          </g>
        </svg>
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
          <label>New variant</label>
          <button
            className="generate"
            title="Everything else updates live; this re-rolls the random seed for a different ablation with the current settings."
            onClick={() => up({ seed: randSeed() })}
          >
            ↻ Random
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

        <div className="field afp">
          <label>Import AFP entry</label>
          <button className={`afp-toggle${afpOpen ? ' on' : ''}`} onClick={() => void openAfp()}>
            {afpOpen ? '▾' : '▸'} isabelle · AFP
          </button>
          {afpOpen && (
            <div className="afp-pickers">
              {afpBusy && !afp ? (
                <span className="afp-note">loading catalog…</span>
              ) : afp ? (
                <>
                  <input
                    className="afp-entry-input"
                    list="afp-entries"
                    value={afpEntry}
                    placeholder={`search ${afp.entries.length} entries…`}
                    onChange={(e) => void selectAfpEntry(e.target.value)}
                    aria-label="AFP entry"
                  />
                  <datalist id="afp-entries">
                    {afp.entries.map((en) => (
                      <option key={en.name} value={en.name}>
                        {en.n_theories} thy
                      </option>
                    ))}
                  </datalist>
                  <select
                    value={afpFile}
                    disabled={afpBusy || !afpTheories}
                    onChange={(e) => void pickAfpTheory(e.target.value)}
                    aria-label="AFP theory file"
                  >
                    {afpFile === '' && (
                      <option value="" disabled>
                        {afpBusy ? 'fetching…' : afpTheories ? 'pick a theory…' : 'pick an entry first'}
                      </option>
                    )}
                    {afpTheories?.map((t) => (
                      <option key={t.file} value={t.file}>
                        {t.file} ({Math.ceil(t.bytes / 1024)}KB)
                      </option>
                    ))}
                  </select>
                  {(() => {
                    const en = afp.entries.find((e) => e.name === afpEntry)
                    return en ? (
                      <a className="afp-note" href={en.afp_url} target="_blank" rel="noreferrer">
                        entry page ↗
                      </a>
                    ) : null
                  })()}
                </>
              ) : null}
              {afpErr && <span className="afp-err">{afpErr}</span>}
            </div>
          )}
        </div>
      </div>

      <main className="panes">
        <aside className="controls">
          <div className="ctrl-sec">
            <h3>Difficulty preset</h3>
            <div className="presets">
              {['L0', 'L1', 'L2', 'L3', 'L4'].map((l, i) => (
                <button key={l} className={activePreset === i ? 'on' : ''} onClick={() => applyPreset(i)}>
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
            <Check
              label="All eligible corollaries (one ablation each; ignores repeat)"
              checked={opts.corollaryAll}
              onChange={(v) => up({ corollaryAll: v })}
              disabled={!caps.lemmaDelete || !opts.corollary}
              hint="walk the file, deleting one ancestor lemma per corollary"
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
          <CodeEditor lang={lang} value={source} onChange={setSource} />
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
