import { useEffect, useState } from 'react'
import './Leaderboard.css'
import { OUTCOME_ORDER, type GroupResult, type LeaderboardIndex } from './types'

function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? '—' : `${(100 * x).toFixed(1)}%`
}

function ci(lo: number | null, hi: number | null): string {
  return lo === null || hi === null ? 'n/a' : `[${pct(lo)}, ${pct(hi)}]`
}

type Loaded =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  | { state: 'ready'; index: LeaderboardIndex; groups: GroupResult[] }

const base = import.meta.env.BASE_URL

export function Leaderboard() {
  const [loaded, setLoaded] = useState<Loaded>({ state: 'loading' })

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const idxRes = await fetch(`${base}leaderboard/index.json`)
        if (!idxRes.ok) throw new Error(`index.json: HTTP ${idxRes.status}`)
        const index = (await idxRes.json()) as LeaderboardIndex
        const dataRes = await fetch(`${base}leaderboard/${index.file}`)
        if (!dataRes.ok) throw new Error(`${index.file}: HTTP ${dataRes.status}`)
        const groups = (await dataRes.json()) as GroupResult[]
        if (!cancelled) setLoaded({ state: 'ready', index, groups })
      } catch (e) {
        if (!cancelled) setLoaded({ state: 'error', message: String(e instanceof Error ? e.message : e) })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const groups =
    loaded.state === 'ready'
      ? [...loaded.groups].sort((a, b) => (b.macro_rate ?? -1) - (a.macro_rate ?? -1))
      : []

  return (
    <div className="lb">
      <header className="lb-header">
        <h1>Leaderboard</h1>
        <p>
          Agentic ReAct-loop reconstructions of ablated lemmas/theorems, scored by real
          compilation (<code>ablate-baseline</code>) and aggregated with <b>macro</b> (mean of
          per-repo pass rate — required whenever repos differ hugely in row count) and{' '}
          <b>micro</b> (pooled) PASS, both with 95% bootstrap CIs. See{' '}
          <code>baselines/src/apply_ablate/aggregate.py</code>.
        </p>
      </header>

      {loaded.state === 'loading' && <p className="lb-muted">loading…</p>}
      {loaded.state === 'error' && <p className="lb-err">failed to load leaderboard data: {loaded.message}</p>}

      {loaded.state === 'ready' && (
        <>
          {loaded.index.status === 'sample' && (
            <div className="lb-banner">
              <strong>Awaiting model grid.</strong> {loaded.index.note}
            </div>
          )}

          <div className="lb-table-wrap">
            <table className="lb-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Split</th>
                  <th>Turn budget</th>
                  <th>Macro PASS</th>
                  <th>Micro PASS</th>
                  <th>Scorable / total</th>
                  <th>Repos</th>
                  {OUTCOME_ORDER.map((o) => (
                    <th key={o} className="lb-outcome-h" title={o}>
                      {o}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {groups.map((g, i) => (
                  <tr key={i}>
                    <td className="lb-model">{g.model}</td>
                    <td>{g.mode}</td>
                    <td className="lb-num">{g.max_turns ?? 'n/a'}</td>
                    <td className="lb-num">
                      <b>{pct(g.macro_rate)}</b>
                      <span className="lb-ci">{ci(g.macro_ci_lo, g.macro_ci_hi)}</span>
                    </td>
                    <td className="lb-num">
                      <b>{pct(g.micro_rate)}</b>
                      <span className="lb-ci">
                        {g.micro_pass}/{g.scorable} · {ci(g.micro_ci_lo, g.micro_ci_hi)}
                      </span>
                    </td>
                    <td className="lb-num">
                      {g.scorable} / {g.total}
                    </td>
                    <td className="lb-num">{g.macro_n_repos}</td>
                    {OUTCOME_ORDER.map((o) => (
                      <td key={o} className={`lb-num lb-outcome${o === 'malformed' || o === 'context_exceeded' ? ' lb-outcome-flag' : ''}`}>
                        {g.outcomes[o] ?? 0}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="lb-note">
            "Scorable / total" is the denominator PASS is computed over — total rows minus{' '}
            <code>malformed</code> (broken challenge), <code>trivial</code> (empty diff), and{' '}
            <code>context_exceeded</code> (model never saw the challenge), highlighted above.
            Those are harness/dataset artifacts, not wrong answers, so they're excluded from PASS
            rather than scored 0 — but a row with a large gap between "total" and "scorable" is
            worth a second look before comparing it to others.
          </p>
        </>
      )}

      <section className="lb-submit">
        <h2>Submitting a result</h2>
        <ol>
          <li>
            Run <code>ablate-baseline</code> for the (model, mode, turn budget) cell you want,
            once per repo — see the root <code>CLAUDE.md</code> and <code>baselines/README.md</code>.
            Each run writes a <code>res_&lt;repo&gt;.jsonl</code>.
          </li>
          <li>
            Build a manifest (<code>[{'{'}path, model, mode, max_turns, repo{'}'}, …]</code>) over
            those files and aggregate:
            <pre className="lb-code">
              {'cd baselines\nuv run ablate-aggregate manifest.json --out-json results.json'}
            </pre>
          </li>
          <li>
            Copy <code>results.json</code> (plus the manifest and the <code>res_*.jsonl</code>{' '}
            files it references, for reproducibility) into{' '}
            <code>website/public/leaderboard/</code>.
          </li>
          <li>
            Edit <code>website/public/leaderboard/index.json</code>: set{' '}
            <code>"status": "real"</code> and <code>"file": "results.json"</code>.
          </li>
          <li>Open a PR against <code>website/</code>. No code changes needed — the page renders whatever <code>index.json</code> points at.</li>
        </ol>
        <p className="lb-muted">Full instructions: <code>website/public/leaderboard/README.md</code>.</p>
      </section>
    </div>
  )
}
