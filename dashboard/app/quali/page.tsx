import Link from "next/link";
import { readQualiStudy } from "@/lib/data";
import type { QualiRecord } from "@/lib/data";

const CODE_COLORS: Record<string, string> = {
  strategy_shift: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  collaboration_handoff: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  backtrack: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  incremental_progress: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  sorry_introduced: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  sorry_resolved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  tactic_style_change: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
  proof_compression: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/40 dark:text-cyan-300",
  specification_change: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
  blocked_period: "bg-zinc-200 text-zinc-700 dark:bg-zinc-700/40 dark:text-zinc-300",
  exploratory_phase: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  breakthrough: "bg-lime-100 text-lime-800 dark:bg-lime-900/40 dark:text-lime-300",
};

function complexityDots(n: number) {
  return Array.from({ length: 5 }, (_, i) =>
    i < n ? "\u25cf" : "\u25cb"
  ).join(" ");
}

function fmtDate(iso: string) {
  return iso ? iso.slice(0, 10) : "\u2014";
}

function ObservationBadge({ code }: { code: string }) {
  const colors = CODE_COLORS[code] ?? "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400";
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${colors}`}>
      {code.replace(/_/g, " ")}
    </span>
  );
}

function TrajectoryCard({ record }: { record: QualiRecord }) {
  const { source: s, analysis: a } = record;

  // Count observation types
  const codeCounts: Record<string, number> = {};
  for (const o of a.observations) {
    codeCounts[o.code] = (codeCounts[o.code] ?? 0) + 1;
  }

  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-semibold font-mono text-base">{a.declaration}</h3>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">
              {a.file}
            </p>
          </div>
          <div className="text-right shrink-0">
            <div className="text-sm font-medium tracking-wide" title={`Complexity: ${a.complexity}/5`}>
              {complexityDots(a.complexity)}
            </div>
            <div className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
              complexity
            </div>
          </div>
        </div>

        {/* Summary stats row */}
        <div className="flex flex-wrap gap-x-5 gap-y-1 mt-3 text-xs text-zinc-500 dark:text-zinc-400">
          <span>{s.n_commits} commits</span>
          <span>{s.days_to_prove} days</span>
          <span>{s.hole_kind}</span>
          <span>{fmtDate(s.first_hole_date)} &rarr; {fmtDate(s.proof_complete_date)}</span>
        </div>
      </div>

      {/* Trajectory signature */}
      <div className="px-5 py-3 border-b border-zinc-100 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/50">
        <p className="text-sm italic text-zinc-600 dark:text-zinc-300">
          &ldquo;{a.trajectory_signature}&rdquo;
        </p>
        <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
          Strategy: <span className="font-medium">{a.dominant_strategy}</span>
        </p>
      </div>

      {/* Observations timeline */}
      <div className="px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
        <h4 className="text-xs font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-3">
          Observations ({a.observations.length})
        </h4>
        <div className="space-y-2">
          {a.observations.map((obs, i) => (
            <div key={i} className="flex items-start gap-2">
              <div className="shrink-0 mt-0.5">
                <ObservationBadge code={obs.code} />
              </div>
              <div className="text-sm text-zinc-600 dark:text-zinc-300 leading-snug">
                <code className="text-xs text-zinc-400 font-mono mr-1">{obs.commit_hash}</code>
                {obs.evidence}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Narrative */}
      <div className="px-5 py-4">
        <h4 className="text-xs font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-2">
          Narrative
        </h4>
        <div className="text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed space-y-2">
          {a.narrative.split("\n\n").map((para, i) => (
            <p key={i}>{para}</p>
          ))}
        </div>
      </div>

      {/* Tactics footer */}
      {s.top_tactics.length > 0 && (
        <div className="px-5 py-3 border-t border-zinc-100 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/50">
          <div className="flex flex-wrap gap-1">
            {s.top_tactics.slice(0, 12).map((t) => (
              <span
                key={t}
                className="px-1.5 py-0.5 rounded text-xs font-mono bg-violet-50 text-violet-600 dark:bg-violet-900/30 dark:text-violet-300"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default async function QualiPage() {
  let records: QualiRecord[] = [];
  try {
    records = await readQualiStudy();
  } catch {
    // file may not exist yet
  }

  // Aggregate stats across all records
  const allCodes: Record<string, number> = {};
  let totalObs = 0;
  const complexities = records.map((r) => r.analysis.complexity);
  const avgComplexity = complexities.length
    ? (complexities.reduce((a, b) => a + b, 0) / complexities.length).toFixed(1)
    : "\u2014";
  for (const r of records) {
    for (const o of r.analysis.observations) {
      allCodes[o.code] = (allCodes[o.code] ?? 0) + 1;
      totalObs++;
    }
  }
  const sortedCodes = Object.entries(allCodes).sort((a, b) => b[1] - a[1]);
  const maxCode = sortedCodes[0]?.[1] ?? 1;

  return (
    <div className="min-h-screen bg-background text-foreground font-sans">
      <header className="border-b border-zinc-200 dark:border-zinc-800 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">
              git history evals
            </h1>
            <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-0.5">
              proof engineering benchmark data explorer
            </p>
          </div>
          <nav className="flex gap-4 text-sm">
            <Link
              href="/"
              className="text-zinc-500 dark:text-zinc-400 hover:text-foreground transition-colors"
            >
              Overview
            </Link>
            <Link
              href="/commits"
              className="text-zinc-500 dark:text-zinc-400 hover:text-foreground transition-colors"
            >
              Browse Commits
            </Link>
            <Link
              href="/datasets"
              className="text-zinc-500 dark:text-zinc-400 hover:text-foreground transition-colors"
            >
              Datasets
            </Link>
            <Link
              href="/quali"
              className="font-medium text-foreground border-b border-foreground pb-0.5"
            >
              Qualitative Study
            </Link>
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-8">
        {records.length === 0 ? (
          <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-8 text-center">
            <p className="text-zinc-500 dark:text-zinc-400">
              No qualitative study data yet. Run{" "}
              <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded">
                uv run quali
              </code>{" "}
              from the scaffold directory to generate analyses.
            </p>
          </div>
        ) : (
          <>
            {/* Summary */}
            <section>
              <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-3">
                Human Proof Trajectories &mdash; fiat-crypto
              </h2>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                {[
                  { label: "Theorems Analyzed", value: String(records.length) },
                  { label: "Total Observations", value: String(totalObs) },
                  { label: "Avg Complexity", value: `${avgComplexity} / 5` },
                  {
                    label: "Observation Types",
                    value: String(sortedCodes.length),
                  },
                ].map(({ label, value }) => (
                  <div
                    key={label}
                    className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-4 bg-white dark:bg-zinc-900"
                  >
                    <div className="text-xs text-zinc-500 dark:text-zinc-400 mb-1">
                      {label}
                    </div>
                    <div className="text-2xl font-semibold tabular-nums">
                      {value}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* Observation code distribution */}
            <section className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-5 bg-white dark:bg-zinc-900">
              <h2 className="text-sm font-semibold mb-4">
                Observation Code Distribution
              </h2>
              <div className="space-y-2">
                {sortedCodes.map(([code, count]) => (
                  <div key={code} className="flex items-center gap-3">
                    <div className="w-40 shrink-0">
                      <ObservationBadge code={code} />
                    </div>
                    <div className="flex-1 h-5 bg-zinc-100 dark:bg-zinc-800 rounded overflow-hidden">
                      <div
                        className="h-full bg-indigo-500 rounded transition-all"
                        style={{ width: `${(count / maxCode) * 100}%` }}
                      />
                    </div>
                    <div className="w-10 text-xs tabular-nums text-zinc-600 dark:text-zinc-300 text-right">
                      {count}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* Trajectory cards */}
            <section>
              <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-3">
                Proof Trajectories ({records.length})
              </h2>
              <div className="space-y-6">
                {records
                  .sort((a, b) => b.analysis.complexity - a.analysis.complexity)
                  .map((r, i) => (
                    <TrajectoryCard key={i} record={r} />
                  ))}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
