# Difficulty features & the enriched ablation record

This is the coordination contract for the difficulty-classifier work. It fixes:

1. the **new fields** every ablator must emit into its JSONL record (lockstep across
   `rocq-ablator` (OCaml, reference), `lean-ablator` (Lean), `isabelle-ablator/rust`
   (Rust), `isabelle-ablator/scala` (Scala));
2. the **per-prover definitions** of the proof-complexity metrics, computed *inside*
   each ablator (so they are visible in the JSONL and on downstream websites);
3. the **per-challenge feature vector** the Python extractor derives from a record;
4. the **label** and the **join key** used to build a training table.

The classifier itself (logistic regression / GBM) is intentionally deferred — this doc
covers feature extraction and label plumbing only.

## 1. Enriched record schema (additive, backward-compatible)

All new fields are *additions*; existing fields and their order are unchanged. Old
datasets simply lack the new fields and the Python side degrades gracefully (emits
`null`/`None` for anything missing). New field order:

- top level, new key `challenge_id` (string): a **stable, unique** id for the challenge,
  so labels join to features exactly. Defined as the first 16 hex chars of
  `sha1(file_path | seed | variant | sorted(deleted_lemma_names) | sorted(holed_names))`.
  (`task_id` is *not* unique — it is `sha1(file_path)[:12]` and collides across every
  challenge mined from the same file.)
- top level, new key `corollaries` (array, possibly empty): the theorem(s) whose
  dependency closure seeded the deletions in corollary mode (empty in non-corollary
  modes). Each entry is a **proof-metrics object** (below) plus `fan_in`.
- top level, new key `closure_size` (int, `0` when not applicable): number of distinct
  eligible closure members across the chosen corollaries — the size of the neighborhood
  deletions were drawn from.
- each `deleted_lemmas[]` entry gains: `fan_in` (int, in-file user count), and an
  inlined **proof-metrics object**.
- each `holes_filled[]` entry gains an inlined **proof-metrics object** (alongside the
  existing `n_lines`, `n_commands`, `centrality`).

### Proof-metrics object

Every proof (a deleted lemma's block, a holed theorem's original proof, a corollary's
proof) carries these five integers, computed from the proof **body** (the tokens
between the statement and the terminator):

| field | meaning |
|---|---|
| `n_lines` | number of source lines in the block (already present on holes; added to deleted lemmas) |
| `n_subproofs` | count of intermediate-assertion keywords (see §2) |
| `n_tactics` | count of atomic proof steps (see §2) |
| `cyclomatic` | `1 + (#case-splitters) + (#alternation combinators)` (see §2) |
| `n_chars` | byte length of the block |

`deleted_lemmas[]` and `corollaries[]` metrics are computed over the **whole block**
(statement + proof) for `n_lines`/`n_chars` and over the **proof body** for the tactic
metrics. Holes reuse their existing `n_lines`.

## 2. Per-prover metric definitions

These are deliberately simple, tokenizer-driven heuristics — informative features, not
canonical semantics. Each ablator implements them with its own lexer, so the two
Isabelle ablators (rust + scala) MUST agree token-for-token. Match keywords only as
whole identifier tokens (not substrings); ignore matches inside comments/strings.

**`n_subproofs`** — occurrences of any intermediate-assertion keyword:
- Coq: `assert`, `have`, `enough`, `cut`, `pose` (as in `pose proof`)
- Lean: `have`, `obtain`, `suffices`
- Isabelle: `have`, `obtain`, `hence`, `thus`

**`n_tactics`** — atomic proof steps:
- Coq: `#(sentence-terminating dots in the body)` + `#(`;` sequencing symbols)`
- Lean: `#(newline-separated tactic lines)` + `#(`;` and `<;>` separators)`
- Isabelle: `#(`apply` steps)` + `#(`by`/`..`/`.` closers)` + `#(structured `have`/`show`/`hence`/`thus`/`obtain` steps)`

**`cyclomatic`** = `1 + #case_splitters + #alternation`:
- Coq: case-splitters `{induction, destruct, case, inversion, elim, split, constructor, match}`; alternation `{try, first, solve, repeat}` and symbol `||`
- Lean: case-splitters `{induction, cases, rcases, rintro, match, split, constructor}`; alternation `{first, try, repeat}` and symbols `<;>`, `<|>`
- Isabelle: case-splitters `{cases, induct, induction, split}` and Isar `case`/`next`; alternation method separator `|`, plus `moreover`

## 3. Per-challenge feature vector (Python, `baselines/src/difficulty`)

The extractor reads a raw JSONL record and produces one flat dict. Metrics that come
from the record's per-lemma / per-hole objects are aggregated across the (possibly
several) deleted lemmas and holes with `sum` / `max` / `mean`. Feature groups:

- **counts**: `n_proofs`, `n_ablated`, `n_holes`, `n_deleted_lemmas`, `challenge_n_declarations`
- **sizes**: `challenge_n_lines/chars`, `solution_n_lines/chars`, `solution_minus_challenge_lines`
- **deleted-lemma aggregates** (over `deleted_lemmas[]`): `del_{sum,max,mean}_{lines,chars,fan_in,subproofs,tactics,cyclomatic}`
- **hole aggregates** (over `holes_filled[]`): `hole_{sum,max,mean}_{lines,commands,subproofs,tactics,cyclomatic,centrality,depth}`, `hole_n_leaves`
- **corollary aggregates** (over `corollaries[]`): `cor_{sum,max,mean}_{lines,subproofs,tactics,cyclomatic,fan_in}`, `closure_size`, `n_corollaries`
- **knobs / metadata** (not necessarily predictive, kept for slicing): `challenge_type`, `by_centrality`, `leaves_only`, `ablation_prob`, size/depth/centrality windows, `seed`

Missing emitter fields (legacy datasets) yield `None`; aggregation over an empty list
yields `None`, over a list with values ignores `None` entries.

## 4. Label & join

- **Label**: `SolveResult.succeeded` (bool PASS) from the `ablate-baseline` harness. The
  human-readable outcome class is reconstructed from the boolean flags in precedence
  order: `pass, dry_run, trivial, malformed, tampered, gave_up, turn_limit, error, fail`
  (mirrors `baselines/src/apply_ablate/baseline.py`). `trivial`, `malformed`, and
  `dry_run` rows are excluded from the trainable set.
- **Join key**: `challenge_id` (added to both the record and `SolveResult`). Fallback for
  legacy runs: positional line index, cross-checked against `task_id` + `file_path` +
  deleted-lemma names to detect misalignment.

The joiner (`difficulty build-table`) emits a features+label table as JSONL and CSV.
