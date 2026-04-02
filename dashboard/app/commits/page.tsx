import Link from "next/link";
import { Suspense } from "react";
import { queryCommits, readDataset, computeStats } from "@/lib/data";
import FilterForm from "./FilterForm";

const CLASS_BADGE: Record<string, string> = {
  proof_add:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  proof_new:
    "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  proof_complete:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  proof_optimise:
    "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300",
  spec_change:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
  infra: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  refactor:
    "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
  fix: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  other: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
};

function Badge({ cls }: { cls?: string }) {
  if (!cls) return null;
  const style =
    CLASS_BADGE[cls] ??
    "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400";
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-mono ${style}`}>
      {cls}
    </span>
  );
}

function fmtDate(iso: string) {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

type PageProps = {
  searchParams: Promise<{
    dataset?: string;
    page?: string;
    class?: string;
    author?: string;
    search?: string;
    tactic?: string;
  }>;
};

export default async function CommitsPage({ searchParams }: PageProps) {
  const sp = await searchParams;
  const dataset = sp.dataset ?? "fiat-crypto-commits-coq-diff";
  const page = Math.max(1, parseInt(sp.page ?? "1") || 1);
  const commitClass = sp.class ?? "";
  const author = sp.author ?? "";
  const search = sp.search ?? "";
  const tactic = sp.tactic ?? "";

  const [result, allRecords] = await Promise.all([
    queryCommits({
      dataset,
      page,
      pageSize: 50,
      commitClass: commitClass || undefined,
      author: author || undefined,
      search: search || undefined,
      tactic: tactic || undefined,
    }),
    readDataset(dataset),
  ]);

  const stats = computeStats(allRecords);
  const availableTactics = Object.keys(stats.tacticCounts).sort();

  const buildHref = (overrides: Record<string, string | number>) => {
    const params = new URLSearchParams();
    const values: Record<string, string> = {
      dataset,
      page: String(page),
      ...(commitClass && { class: commitClass }),
      ...(author && { author }),
      ...(search && { search }),
      ...(tactic && { tactic }),
      ...Object.fromEntries(
        Object.entries(overrides).map(([k, v]) => [k, String(v)])
      ),
    };
    for (const [k, v] of Object.entries(values)) {
      if (v) params.set(k, v);
    }
    return `/commits?${params.toString()}`;
  };

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
              className="font-medium text-foreground border-b border-foreground pb-0.5"
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
              className="text-zinc-500 dark:text-zinc-400 hover:text-foreground transition-colors"
            >
              Qualitative Study
            </Link>
          </nav>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-6 py-6 flex gap-6">
        {/* Sidebar */}
        <div className="w-52 shrink-0">
          <Suspense>
            <FilterForm
              dataset={dataset}
              commitClass={commitClass}
              author={author}
              search={search}
              tactic={tactic}
              availableTactics={availableTactics}
            />
          </Suspense>
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          {/* Result count + pagination */}
          <div className="flex items-center justify-between mb-4 text-sm text-zinc-500 dark:text-zinc-400">
            <span>
              {result.total.toLocaleString()} commit
              {result.total !== 1 ? "s" : ""}
              {commitClass && (
                <span>
                  {" "}
                  ·{" "}
                  <Link
                    href={buildHref({ class: "", page: 1 })}
                    className="text-blue-500 hover:text-blue-600"
                  >
                    {commitClass}
                  </Link>
                </span>
              )}
            </span>
            <div className="flex items-center gap-2">
              {page > 1 && (
                <Link
                  href={buildHref({ page: page - 1 })}
                  className="px-2 py-1 rounded border border-zinc-200 dark:border-zinc-700 hover:border-zinc-400 transition-colors"
                >
                  ← Prev
                </Link>
              )}
              <span className="tabular-nums">
                {page} / {result.totalPages}
              </span>
              {page < result.totalPages && (
                <Link
                  href={buildHref({ page: page + 1 })}
                  className="px-2 py-1 rounded border border-zinc-200 dark:border-zinc-700 hover:border-zinc-400 transition-colors"
                >
                  Next →
                </Link>
              )}
            </div>
          </div>

          {/* Commit list */}
          <div className="space-y-0 border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
            {result.items.length === 0 && (
              <div className="p-8 text-center text-zinc-400">
                No commits match the current filters.
              </div>
            )}
            {result.items.map((commit, i) => (
              <details
                key={commit.hash}
                className={`group border-zinc-200 dark:border-zinc-800 ${
                  i > 0 ? "border-t" : ""
                }`}
              >
                <summary className="flex items-start gap-3 px-4 py-3 cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-900/50 list-none select-none">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge cls={commit.commit_class} />
                      <span className="font-medium text-sm truncate">
                        {commit.message_subject}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-zinc-400">
                      <span className="font-mono">{commit.hash.slice(0, 8)}</span>
                      <span>{fmtDate(commit.date)}</span>
                      <Link
                        href={buildHref({ author: commit.author, page: 1 })}
                        className="hover:text-blue-500 transition-colors"
                      >
                        {commit.author}
                      </Link>
                      {typeof commit.insertions === "number" && (
                        <span className="text-green-600 dark:text-green-400">
                          +{commit.insertions}
                        </span>
                      )}
                      {typeof commit.deletions === "number" && (
                        <span className="text-red-500">
                          -{commit.deletions}
                        </span>
                      )}
                      {commit.diff_sorry_removed && (
                        <span className="text-emerald-500 font-medium">
                          sorry removed
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="text-zinc-300 dark:text-zinc-600 text-xs pt-0.5 shrink-0 group-open:rotate-90 transition-transform">
                    ▶
                  </div>
                </summary>

                {/* Expanded detail */}
                <div className="px-4 pb-4 bg-zinc-50 dark:bg-zinc-900/30 border-t border-zinc-100 dark:border-zinc-800 space-y-3">
                  {commit.message_body && (
                    <div>
                      <div className="text-xs font-medium text-zinc-400 mb-1">
                        Body
                      </div>
                      <pre className="text-xs text-zinc-600 dark:text-zinc-300 whitespace-pre-wrap font-mono">
                        {commit.message_body}
                      </pre>
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-4 text-xs">
                    <div>
                      <div className="font-medium text-zinc-400 mb-1">
                        Metadata
                      </div>
                      <dl className="space-y-1">
                        <div className="flex gap-2">
                          <dt className="text-zinc-400 w-24">Hash</dt>
                          <dd className="font-mono">{commit.hash}</dd>
                        </div>
                        <div className="flex gap-2">
                          <dt className="text-zinc-400 w-24">Date</dt>
                          <dd>{commit.date}</dd>
                        </div>
                        <div className="flex gap-2">
                          <dt className="text-zinc-400 w-24">Author</dt>
                          <dd>{commit.author}</dd>
                        </div>
                        <div className="flex gap-2">
                          <dt className="text-zinc-400 w-24">Files</dt>
                          <dd>{commit.files_changed_count}</dd>
                        </div>
                        {commit.class_confidence && (
                          <div className="flex gap-2">
                            <dt className="text-zinc-400 w-24">Confidence</dt>
                            <dd>{commit.class_confidence}</dd>
                          </div>
                        )}
                        {typeof commit.diff_net_proof_lines === "number" && (
                          <div className="flex gap-2">
                            <dt className="text-zinc-400 w-24">
                              Net proof Δ
                            </dt>
                            <dd
                              className={
                                commit.diff_net_proof_lines >= 0
                                  ? "text-green-600"
                                  : "text-red-500"
                              }
                            >
                              {commit.diff_net_proof_lines >= 0 ? "+" : ""}
                              {commit.diff_net_proof_lines}
                            </dd>
                          </div>
                        )}
                      </dl>
                    </div>

                    {commit.coq_files_changed &&
                      commit.coq_files_changed.length > 0 && (
                        <div>
                          <div className="font-medium text-zinc-400 mb-1">
                            Coq Files Changed
                          </div>
                          <ul className="space-y-0.5 font-mono text-zinc-600 dark:text-zinc-300">
                            {commit.coq_files_changed.map((f) => (
                              <li key={f} className="truncate" title={f}>
                                {f}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                  </div>

                  {commit.tactic_tags && commit.tactic_tags.length > 0 && (
                    <div>
                      <div className="text-xs font-medium text-zinc-400 mb-1">
                        Tactics
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {commit.tactic_tags.map((t) => (
                          <Link
                            key={t}
                            href={buildHref({ tactic: t, page: 1 })}
                            className="px-1.5 py-0.5 rounded bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 text-xs font-mono hover:bg-violet-200 dark:hover:bg-violet-900/50 transition-colors"
                          >
                            {t}
                          </Link>
                        ))}
                      </div>
                    </div>
                  )}

                  {commit.keywords && commit.keywords.length > 0 && (
                    <div>
                      <div className="text-xs font-medium text-zinc-400 mb-1">
                        Keywords
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {commit.keywords.map((k) => (
                          <span
                            key={k}
                            className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 text-xs font-mono"
                          >
                            {k}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {commit.proof_style && commit.proof_style.length > 0 && (
                    <div className="text-xs text-zinc-400">
                      Proof style:{" "}
                      <span className="text-foreground">
                        {commit.proof_style.join(", ")}
                      </span>
                    </div>
                  )}
                </div>
              </details>
            ))}
          </div>

          {/* Bottom pagination */}
          {result.totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-4 text-sm">
              {page > 1 && (
                <Link
                  href={buildHref({ page: page - 1 })}
                  className="px-3 py-1.5 rounded border border-zinc-200 dark:border-zinc-700 hover:border-zinc-400 transition-colors"
                >
                  ← Previous
                </Link>
              )}
              <span className="text-zinc-400 tabular-nums">
                Page {page} of {result.totalPages} ({result.total.toLocaleString()} results)
              </span>
              {page < result.totalPages && (
                <Link
                  href={buildHref({ page: page + 1 })}
                  className="px-3 py-1.5 rounded border border-zinc-200 dark:border-zinc-700 hover:border-zinc-400 transition-colors"
                >
                  Next →
                </Link>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
