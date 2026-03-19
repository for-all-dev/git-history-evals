"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useTransition } from "react";

const COMMIT_CLASSES = [
  "proof_add",
  "proof_new",
  "proof_complete",
  "proof_optimise",
  "spec_change",
  "infra",
  "refactor",
  "fix",
  "other",
];

const MAIN_DATASETS = [
  "fiat-crypto-commits-coq-diff",
  "fiat-crypto-commits-coq-labeled",
  "fiat-crypto-commits-coq",
  "fiat-crypto-commits-all-labeled",
  "fiat-crypto-commits-all",
];

type Props = {
  dataset: string;
  commitClass: string;
  author: string;
  search: string;
  tactic: string;
  availableTactics: string[];
};

export default function FilterForm({
  dataset,
  commitClass,
  author,
  search,
  tactic,
  availableTactics,
}: Props) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isPending, startTransition] = useTransition();

  const update = useCallback(
    (key: string, value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
      params.delete("page"); // reset pagination
      startTransition(() => router.push(`/commits?${params.toString()}`));
    },
    [router, searchParams]
  );

  return (
    <aside
      className={`space-y-5 text-sm ${isPending ? "opacity-60 pointer-events-none" : ""}`}
    >
      <div>
        <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1.5">
          Dataset
        </label>
        <select
          value={dataset}
          onChange={(e) => update("dataset", e.target.value)}
          className="w-full rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          {MAIN_DATASETS.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1.5">
          Search
        </label>
        <input
          type="search"
          defaultValue={search}
          placeholder="hash, author, keyword…"
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              update("search", (e.target as HTMLInputElement).value);
            }
          }}
          onBlur={(e) => update("search", e.target.value)}
          className="w-full rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1.5">
          Commit Class
        </label>
        <div className="space-y-1">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="class"
              value=""
              checked={!commitClass}
              onChange={() => update("class", "")}
              className="accent-blue-500"
            />
            <span>All</span>
          </label>
          {COMMIT_CLASSES.map((cls) => (
            <label key={cls} className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="class"
                value={cls}
                checked={commitClass === cls}
                onChange={() => update("class", cls)}
                className="accent-blue-500"
              />
              <span className="font-mono text-xs">{cls}</span>
            </label>
          ))}
        </div>
      </div>

      {availableTactics.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1.5">
            Tactic
          </label>
          <select
            value={tactic}
            onChange={(e) => update("tactic", e.target.value)}
            className="w-full rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="">All tactics</option>
            {availableTactics.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      )}

      {author && (
        <div>
          <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1.5">
            Author
          </label>
          <div className="flex items-center gap-2">
            <span className="text-xs truncate">{author}</span>
            <button
              onClick={() => update("author", "")}
              className="text-xs text-zinc-400 hover:text-red-500 ml-auto"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {(commitClass || search || tactic || author) && (
        <button
          onClick={() => {
            const params = new URLSearchParams(searchParams.toString());
            params.delete("class");
            params.delete("search");
            params.delete("tactic");
            params.delete("author");
            params.delete("page");
            startTransition(() => router.push(`/commits?${params.toString()}`));
          }}
          className="text-xs text-red-500 hover:text-red-600"
        >
          Clear all filters
        </button>
      )}
    </aside>
  );
}
