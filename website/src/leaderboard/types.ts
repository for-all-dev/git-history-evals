// Mirrors `apply_ablate.aggregate.GroupResult` (baselines/src/apply_ablate/aggregate.py) —
// keep in sync with that module's field names (it's a `pydantic.BaseModel.model_dump()`, so
// the JSON is exactly these keys).

export const OUTCOME_ORDER = [
  'pass',
  'dry_run',
  'trivial',
  'malformed',
  'context_exceeded',
  'tampered',
  'gave_up',
  'turn_limit',
  'error',
  'fail',
] as const

export type Outcome = (typeof OUTCOME_ORDER)[number]

// Outcomes excluded from the scorable denominator — the challenge was broken (malformed),
// empty (trivial), or the model never saw it (context_exceeded). Mirrors NON_SCORABLE.
export const NON_SCORABLE: ReadonlySet<Outcome> = new Set(['malformed', 'trivial', 'context_exceeded'])

export interface RepoStat {
  total: number
  scorable: number
  pass: number
  rate: number
}

export interface GroupResult {
  model: string
  mode: string
  max_turns: number | null
  total: number
  outcomes: Record<Outcome, number>
  scorable: number
  micro_pass: number
  micro_rate: number
  micro_ci_lo: number | null
  micro_ci_hi: number | null
  macro_n_repos: number
  macro_rate: number | null
  macro_ci_lo: number | null
  macro_ci_hi: number | null
  per_repo: Record<string, RepoStat>
}

export interface LeaderboardIndex {
  status: 'sample' | 'real'
  note: string
  file: string
  generated_by?: string
}
