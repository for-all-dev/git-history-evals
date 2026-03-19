import Link from "next/link";
import { readDataset, computeStats } from "@/lib/data";

const CLASS_COLORS: Record<string, string> = {
  proof_add: "bg-blue-500",
  proof_new: "bg-green-500",
  proof_complete: "bg-emerald-500",
  proof_optimise: "bg-cyan-500",
  spec_change: "bg-yellow-500",
  infra: "bg-zinc-400",
  refactor: "bg-purple-500",
  fix: "bg-red-500",
  other: "bg-zinc-500",
};

const CLASS_LABELS: Record<string, string> = {
  proof_add: "Proof Addition",
  proof_new: "New Proof",
  proof_complete: "Proof Completed",
  proof_optimise: "Proof Optimised",
  spec_change: "Spec Change",
  infra: "Infrastructure",
  refactor: "Refactor",
  fix: "Fix",
  other: "Other",
};

function fmt(n: number) {
  return n.toLocaleString();
}

function fmtDate(iso: string) {
  return iso ? iso.slice(0, 10) : "—";
}

function fmtBytes(bytes: number) {
  if (bytes > 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes > 1_000) return `${(bytes / 1_000).toFixed(1)} KB`;
  return `${bytes} B`;
}

export default async function Home() {
  const DEFAULT_DATASET = "fiat-crypto-commits-coq-diff";
  const records = await readDataset(DEFAULT_DATASET);
  const stats = computeStats(records);

  const sortedClasses = Object.entries(stats.classCounts).sort(
    (a, b) => b[1] - a[1]
  );
  const maxClassCount = Math.max(...sortedClasses.map(([, c]) => c));

  const topTactics = Object.entries(stats.tacticCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20);
  const maxTacticCount = topTactics[0]?.[1] ?? 1;

  return (
    <div className="min-h-screen bg-background text-foreground font-sans">
      {/* Header */}
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
              className="font-medium text-foreground border-b border-foreground pb-0.5"
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
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-8">
        {/* Summary stats */}
        <section>
          <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-3">
            {DEFAULT_DATASET}
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { label: "Total Commits", value: fmt(stats.total) },
              {
                label: "Date Range",
                value: `${fmtDate(stats.dateRange.earliest)} – ${fmtDate(stats.dateRange.latest)}`,
              },
              { label: "Lines Added", value: fmt(stats.totalInsertions) },
              { label: "Sorry Removed", value: fmt(stats.sorryRemoved) },
            ].map(({ label, value }) => (
              <div
                key={label}
                className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-4 bg-white dark:bg-zinc-900"
              >
                <div className="text-xs text-zinc-500 dark:text-zinc-400 mb-1">
                  {label}
                </div>
                <div className="text-2xl font-semibold tabular-nums">{value}</div>
              </div>
            ))}
          </div>
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Commit class distribution */}
          <section className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-5 bg-white dark:bg-zinc-900">
            <h2 className="text-sm font-semibold mb-4">Commit Class Distribution</h2>
            <div className="space-y-2">
              {sortedClasses.map(([cls, count]) => (
                <div key={cls} className="flex items-center gap-3">
                  <div className="w-28 text-xs text-right text-zinc-500 dark:text-zinc-400 shrink-0 truncate">
                    {CLASS_LABELS[cls] ?? cls}
                  </div>
                  <div className="flex-1 h-5 bg-zinc-100 dark:bg-zinc-800 rounded overflow-hidden">
                    <div
                      className={`h-full ${CLASS_COLORS[cls] ?? "bg-zinc-400"} rounded transition-all`}
                      style={{
                        width: `${(count / maxClassCount) * 100}%`,
                      }}
                    />
                  </div>
                  <div className="w-14 text-xs tabular-nums text-zinc-600 dark:text-zinc-300 text-right">
                    {fmt(count)}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4 pt-4 border-t border-zinc-100 dark:border-zinc-800">
              <Link
                href="/commits"
                className="text-xs text-blue-500 hover:text-blue-600 dark:text-blue-400"
              >
                Browse commits →
              </Link>
            </div>
          </section>

          {/* Top tactics */}
          <section className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-5 bg-white dark:bg-zinc-900">
            <h2 className="text-sm font-semibold mb-4">
              Top 20 Tactics (by commit frequency)
            </h2>
            <div className="space-y-1.5">
              {topTactics.map(([tactic, count]) => (
                <div key={tactic} className="flex items-center gap-3">
                  <div className="w-24 text-xs text-right font-mono text-zinc-500 dark:text-zinc-400 shrink-0 truncate">
                    {tactic}
                  </div>
                  <div className="flex-1 h-4 bg-zinc-100 dark:bg-zinc-800 rounded overflow-hidden">
                    <div
                      className="h-full bg-violet-500 rounded"
                      style={{
                        width: `${(count / maxTacticCount) * 100}%`,
                      }}
                    />
                  </div>
                  <div className="w-14 text-xs tabular-nums text-zinc-600 dark:text-zinc-300 text-right">
                    {fmt(count)}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4 pt-4 border-t border-zinc-100 dark:border-zinc-800">
              <Link
                href={`/commits?dataset=${DEFAULT_DATASET}`}
                className="text-xs text-blue-500 hover:text-blue-600 dark:text-blue-400"
              >
                Filter by tactic →
              </Link>
            </div>
          </section>
        </div>

        {/* Top contributors */}
        <section className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-5 bg-white dark:bg-zinc-900">
          <h2 className="text-sm font-semibold mb-4">Top Contributors</h2>
          <div className="flex flex-wrap gap-2">
            {stats.topAuthors.map(({ author, count }) => (
              <Link
                key={author}
                href={`/commits?dataset=${DEFAULT_DATASET}&author=${encodeURIComponent(author)}`}
                className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-zinc-200 dark:border-zinc-700 text-sm hover:border-zinc-400 dark:hover:border-zinc-500 transition-colors"
              >
                <span>{author}</span>
                <span className="text-xs text-zinc-400 tabular-nums">{fmt(count)}</span>
              </Link>
            ))}
          </div>
        </section>

        {/* Sorry-removed commits highlight */}
        {stats.sorryRemoved > 0 && (
          <section className="rounded-lg border border-emerald-200 dark:border-emerald-900 p-5 bg-emerald-50 dark:bg-emerald-950/30">
            <h2 className="text-sm font-semibold text-emerald-800 dark:text-emerald-300 mb-1">
              Proof Completions
            </h2>
            <p className="text-sm text-emerald-700 dark:text-emerald-400">
              <strong>{fmt(stats.sorryRemoved)}</strong> commits removed a{" "}
              <code className="font-mono text-xs bg-emerald-100 dark:bg-emerald-900 px-1 py-0.5 rounded">
                sorry
              </code>{" "}
              placeholder — these are the most valuable ground-truth eval
              examples.
            </p>
            <div className="mt-2">
              <Link
                href={`/commits?dataset=${DEFAULT_DATASET}&class=proof_complete`}
                className="text-xs text-emerald-600 dark:text-emerald-400 hover:text-emerald-700"
              >
                View proof_complete commits →
              </Link>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
