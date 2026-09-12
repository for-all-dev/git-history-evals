// AFP entry importer (issue #113). The full Archive of Formal Proofs is
// pre-mirrored into a world-readable DO Space (see `website/scripts/mirror-afp.py`)
// because isa-afp.org sends no permissive CORS. The manifest is split so the
// site loads fast: a lightweight `index.json` (entry names + counts) up front,
// then a per-entry `<Entry>/theories.json` fetched lazily on selection.

/** Base URL of the mirror prefix (no trailing slash). Overridable for a
 *  different bucket/CDN via a *non-secret* build env var; reads are anonymous. */
export const AFP_BASE: string =
  import.meta.env.VITE_AFP_BASE_URL ??
  'https://forall-evals.nyc3.digitaloceanspaces.com/ablations/isabelle/_data/afp'

export interface AfpTheory {
  file: string // e.g. "Regular_Set.thy"
  key: string // object key within the bucket
  url: string // absolute public URL
  bytes: number
}

/** Lightweight per-entry record in index.json (no theory list — that's lazy). */
export interface AfpEntry {
  name: string // AFP entry short name, e.g. "Regular-Sets"
  n_theories: number
  afp_url: string // human-facing entry page on isa-afp.org
}

export interface AfpIndex {
  schema: string
  base_url: string
  source: string
  release: string
  entries: AfpEntry[]
}

async function getJson<T>(url: string, what: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, { mode: 'cors' })
  } catch (e) {
    throw new Error(`Could not reach the AFP mirror (${url}). ${(e as Error).message}`, { cause: e })
  }
  if (!res.ok) throw new Error(`${what} HTTP ${res.status} (${url})`)
  return (await res.json()) as T
}

/** Fetch the lightweight manifest listing all mirrored entries. */
export async function fetchAfpIndex(): Promise<AfpIndex> {
  const idx = await getJson<AfpIndex>(`${AFP_BASE}/index.json`, 'AFP mirror index')
  if (!idx || !Array.isArray(idx.entries)) throw new Error('AFP mirror index is malformed')
  return idx
}

/** Fetch one entry's theory list (lazy shard). */
export async function fetchAfpEntryTheories(entryName: string): Promise<AfpTheory[]> {
  const url = `${AFP_BASE}/${encodeURIComponent(entryName)}/theories.json`
  const shard = await getJson<{ name: string; theories: AfpTheory[] }>(url, 'AFP entry')
  return Array.isArray(shard?.theories) ? shard.theories : []
}

/** Fetch one theory's raw `.thy` source. */
export async function fetchAfpTheory(theory: AfpTheory): Promise<string> {
  const res = await fetch(theory.url, { mode: 'cors' })
  if (!res.ok) throw new Error(`AFP theory HTTP ${res.status} (${theory.file})`)
  return res.text()
}
