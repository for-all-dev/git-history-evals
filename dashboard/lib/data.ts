import { createReadStream } from "fs";
import { readdir, stat } from "fs/promises";
import readline from "readline";
import path from "path";

export const ARTIFACTS_DIR = path.resolve(process.cwd(), "../artifacts");
export const TACTIC_DIR = path.join(ARTIFACTS_DIR, "tactic-subdatasets");

export type CommitRecord = {
  hash: string;
  parent_hashes: string[];
  author: string;
  author_email: string;
  date: string;
  message_subject: string;
  message_body: string;
  files_changed_count: number;
  insertions: number;
  deletions: number;
  changed_files: string[];
  coq_files_changed: string[];
  touches_proof_files: boolean;
  // labeled fields
  commit_class?: string;
  keywords?: string[];
  class_confidence?: string;
  // diff fields
  diff_sorry_removed?: boolean;
  diff_net_proof_lines?: number;
  tactic_tags?: string[];
  proof_style?: string[];
};

export type DatasetMeta = {
  id: string;
  filename: string;
  path: string;
  size: number;
  isSubdataset: boolean;
};

// module-level cache keyed by file path
const cache = new Map<string, { records: CommitRecord[]; mtime: number }>();

async function parseJsonl(filePath: string): Promise<CommitRecord[]> {
  const fileStat = await stat(filePath);
  const cached = cache.get(filePath);
  if (cached && cached.mtime === fileStat.mtimeMs) return cached.records;

  const records = await new Promise<CommitRecord[]>((resolve, reject) => {
    const results: CommitRecord[] = [];
    const rl = readline.createInterface({
      input: createReadStream(filePath),
      crlfDelay: Infinity,
    });
    rl.on("line", (line) => {
      if (line.trim()) {
        try {
          results.push(JSON.parse(line));
        } catch {}
      }
    });
    rl.on("close", () => resolve(results));
    rl.on("error", reject);
  });

  cache.set(filePath, { records, mtime: fileStat.mtimeMs });
  return records;
}

export async function listDatasets(): Promise<DatasetMeta[]> {
  const [mainFiles, tacticFiles] = await Promise.all([
    readdir(ARTIFACTS_DIR).then((files) =>
      files.filter((f) => f.endsWith(".jsonl"))
    ),
    readdir(TACTIC_DIR)
      .then((files) => files.filter((f) => f.endsWith(".jsonl")))
      .catch(() => [] as string[]),
  ]);

  const toMeta = async (
    filename: string,
    dir: string,
    isSubdataset: boolean
  ): Promise<DatasetMeta> => {
    const filePath = path.join(dir, filename);
    const fileStat = await stat(filePath).catch(() => ({ size: 0 }));
    return {
      id: filename.replace(".jsonl", ""),
      filename,
      path: filePath,
      size: (fileStat as { size: number }).size,
      isSubdataset,
    };
  };

  const [main, tactics] = await Promise.all([
    Promise.all(mainFiles.map((f) => toMeta(f, ARTIFACTS_DIR, false))),
    Promise.all(tacticFiles.map((f) => toMeta(f, TACTIC_DIR, true))),
  ]);

  return [...main, ...tactics];
}

export async function readDataset(datasetId: string): Promise<CommitRecord[]> {
  // check main dir first, then tactic subdir
  const mainPath = path.join(ARTIFACTS_DIR, `${datasetId}.jsonl`);
  const tacticPath = path.join(TACTIC_DIR, `${datasetId}.jsonl`);

  try {
    await stat(mainPath);
    return parseJsonl(mainPath);
  } catch {
    return parseJsonl(tacticPath);
  }
}

export type CommitStats = {
  total: number;
  dateRange: { earliest: string; latest: string };
  classCounts: Record<string, number>;
  topAuthors: { author: string; count: number }[];
  tacticCounts: Record<string, number>;
  sorryRemoved: number;
  totalInsertions: number;
  totalDeletions: number;
};

export function computeStats(records: CommitRecord[]): CommitStats {
  const classCounts: Record<string, number> = {};
  const authorCounts: Record<string, number> = {};
  const tacticCounts: Record<string, number> = {};
  let sorryRemoved = 0;
  let totalInsertions = 0;
  let totalDeletions = 0;
  let earliest = "";
  let latest = "";

  for (const r of records) {
    if (r.commit_class) classCounts[r.commit_class] = (classCounts[r.commit_class] ?? 0) + 1;
    authorCounts[r.author] = (authorCounts[r.author] ?? 0) + 1;
    if (r.tactic_tags) {
      for (const t of r.tactic_tags) {
        tacticCounts[t] = (tacticCounts[t] ?? 0) + 1;
      }
    }
    if (r.diff_sorry_removed) sorryRemoved++;
    totalInsertions += r.insertions ?? 0;
    totalDeletions += r.deletions ?? 0;
    if (r.date) {
      if (!earliest || r.date < earliest) earliest = r.date;
      if (!latest || r.date > latest) latest = r.date;
    }
  }

  const topAuthors = Object.entries(authorCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .map(([author, count]) => ({ author, count }));

  return {
    total: records.length,
    dateRange: { earliest, latest },
    classCounts,
    topAuthors,
    tacticCounts,
    sorryRemoved,
    totalInsertions,
    totalDeletions,
  };
}

// --- Qualitative study types ---

export type QualiObservation = {
  code: string;
  commit_hash: string;
  evidence: string;
};

export type QualiAnalysis = {
  declaration: string;
  file: string;
  observations: QualiObservation[];
  narrative: string;
  trajectory_signature: string;
  complexity: number;
  dominant_strategy: string;
};

export type QualiRecord = {
  source: {
    declaration: string;
    file: string;
    hole_kind: string;
    first_hole_date: string;
    proof_complete_date: string;
    days_to_prove: number;
    n_commits: number;
    top_tactics: string[];
  };
  analysis: QualiAnalysis;
};

const qualiCache = new Map<string, { records: QualiRecord[]; mtime: number }>();

async function parseQualiJsonl(filePath: string): Promise<QualiRecord[]> {
  const fileStat = await stat(filePath);
  const cached = qualiCache.get(filePath);
  if (cached && cached.mtime === fileStat.mtimeMs) return cached.records;

  const records = await new Promise<QualiRecord[]>((resolve, reject) => {
    const results: QualiRecord[] = [];
    const rl = readline.createInterface({
      input: createReadStream(filePath),
      crlfDelay: Infinity,
    });
    rl.on("line", (line) => {
      if (line.trim()) {
        try {
          results.push(JSON.parse(line));
        } catch {}
      }
    });
    rl.on("close", () => resolve(results));
    rl.on("error", reject);
  });

  qualiCache.set(filePath, { records, mtime: fileStat.mtimeMs });
  return records;
}

export async function readQualiStudy(
  filename = "fiat-crypto-quali.jsonl"
): Promise<QualiRecord[]> {
  const filePath = path.join(ARTIFACTS_DIR, filename);
  return parseQualiJsonl(filePath);
}

export type FilterParams = {
  dataset: string;
  page: number;
  pageSize: number;
  commitClass?: string;
  author?: string;
  search?: string;
  tactic?: string;
};

export type PagedResult = {
  items: CommitRecord[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
};

export async function queryCommits(params: FilterParams): Promise<PagedResult> {
  let records = await readDataset(params.dataset);

  if (params.commitClass) {
    records = records.filter((r) => r.commit_class === params.commitClass);
  }
  if (params.author) {
    records = records.filter((r) => r.author === params.author);
  }
  if (params.tactic) {
    records = records.filter((r) => r.tactic_tags?.includes(params.tactic!));
  }
  if (params.search) {
    const q = params.search.toLowerCase();
    records = records.filter(
      (r) =>
        r.message_subject?.toLowerCase().includes(q) ||
        r.author?.toLowerCase().includes(q) ||
        r.hash?.startsWith(q) ||
        r.keywords?.some((k) => k.toLowerCase().includes(q))
    );
  }

  // sort newest first
  records = [...records].sort((a, b) => (b.date > a.date ? 1 : -1));

  const total = records.length;
  const totalPages = Math.ceil(total / params.pageSize);
  const offset = (params.page - 1) * params.pageSize;
  const items = records.slice(offset, offset + params.pageSize);

  return { items, total, page: params.page, pageSize: params.pageSize, totalPages };
}
