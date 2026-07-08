// AFP entry importer (issue #113). Fetches a curated, version-pinned set of
// Archive of Formal Proofs theories that we pre-mirror into a world-readable DO
// Space (see `website/scripts/mirror-afp.py`). isa-afp.org itself sends no
// permissive CORS, so a pure-static site can't read it directly — the mirror
// bucket is CORS-enabled and controlled by us, keeping this a static deploy.

/** Base URL of the mirror prefix (no trailing slash). Overridable for a
 *  different bucket/CDN via a *non-secret* build env var; reads are anonymous. */
export const AFP_BASE: string =
  import.meta.env.VITE_AFP_BASE_URL ??
  'https://forall-git-evals.nyc3.digitaloceanspaces.com/afp'

export interface AfpTheory {
  file: string // e.g. "Regular_Set.thy"
  key: string // object key within the bucket
  url: string // absolute public URL
  bytes: number
}

export interface AfpEntry {
  name: string // AFP entry short name, e.g. "Regular-Sets"
  afp_url: string // human-facing entry page on isa-afp.org
  theories: AfpTheory[]
}

export interface AfpIndex {
  schema: string
  base_url: string
  source: string
  entries: AfpEntry[]
}

/** Fetch the mirror manifest. Throws with a friendly message on failure. */
export async function fetchAfpIndex(): Promise<AfpIndex> {
  const url = `${AFP_BASE}/index.json`
  let res: Response
  try {
    res = await fetch(url, { mode: 'cors' })
  } catch (e) {
    throw new Error(`Could not reach the AFP mirror (${url}). ${(e as Error).message}`, { cause: e })
  }
  if (!res.ok) throw new Error(`AFP mirror index HTTP ${res.status} (${url})`)
  const idx = (await res.json()) as AfpIndex
  if (!idx || !Array.isArray(idx.entries)) throw new Error('AFP mirror index is malformed')
  return idx
}

/** Fetch one theory's raw `.thy` source. */
export async function fetchAfpTheory(theory: AfpTheory): Promise<string> {
  const res = await fetch(theory.url, { mode: 'cors' })
  if (!res.ok) throw new Error(`AFP theory HTTP ${res.status} (${theory.file})`)
  return res.text()
}
