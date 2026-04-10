import Link from "next/link";
import { listDatasets } from "@/lib/data";

function fmtBytes(bytes: number) {
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(0)} KB`;
  return `${bytes} B`;
}

export default async function DatasetsPage() {
  const datasets = await listDatasets();
  const main = datasets.filter((d) => !d.isSubdataset);
  const tactics = datasets.filter((d) => d.isSubdataset);

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
              className="font-medium text-foreground border-b border-foreground pb-0.5"
            >
              Datasets
            </Link>
            <Link
              href="/quali"
              className="text-zinc-500 dark:text-zinc-400 hover:text-foreground transition-colors"
            >
              Qualitative Study
            </Link>
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-8">
        {/* Main datasets */}
        <section>
          <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-3">
            Main Datasets ({main.length})
          </h2>
          <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 dark:bg-zinc-900 text-xs text-zinc-500 dark:text-zinc-400">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Name</th>
                  <th className="text-right px-4 py-2 font-medium">Size</th>
                  <th className="text-right px-4 py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {main.map((d, i) => (
                  <tr
                    key={d.id}
                    className={`border-zinc-200 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-900/50 ${i > 0 ? "border-t" : ""}`}
                  >
                    <td className="px-4 py-3 font-mono text-xs">{d.id}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-zinc-400">
                      {fmtBytes(d.size)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link
                        href={`/commits?dataset=${encodeURIComponent(d.id)}`}
                        className="text-blue-500 hover:text-blue-600 text-xs"
                      >
                        Browse →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Tactic subdatasets */}
        {tactics.length > 0 && (
          <section>
            <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-3">
              Tactic Subdatasets ({tactics.length})
            </h2>
            <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-3">
              Per-tactic filtered views of commits where each tactic appears in
              the diff.
            </p>
            <div className="flex flex-wrap gap-2">
              {tactics.map((d) => {
                const tacticName = d.id.replace("tactic-", "");
                return (
                  <Link
                    key={d.id}
                    href={`/commits?dataset=${encodeURIComponent(d.id)}`}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded border border-zinc-200 dark:border-zinc-700 text-xs font-mono hover:border-violet-400 hover:text-violet-600 dark:hover:text-violet-300 transition-colors"
                  >
                    <span>{tacticName}</span>
                    <span className="text-zinc-400">{fmtBytes(d.size)}</span>
                  </Link>
                );
              })}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
