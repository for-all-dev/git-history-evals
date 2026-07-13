# Dataset issues found by `baselines/` inspection

Surfaced by `uv run baseline inspect` (see `baselines/`) over a streamed sample
of each published HuggingFace cut on 2026-06-24. The inspector is static — no
model, no API key — so these are properties of the serialised data, not of any
baseline run. Counts are from the sample sizes noted per dataset; treat
percentages as estimates, not exact full-dataset rates.

Samples: `for-all-dev/fiat-crypto-eval` (400 rows), `for-all-dev/CompCert-eval`
(300), `for-all-dev/l4v-eval` (300).

Filed upstream (`for-all-dev/git-history-evals`): ISSUE 1 → #102, ISSUE 2 → #103,
ISSUE 3 → #104, ISSUE 4 → #105, ISSUE 5 → #106, ISSUE 6 → #107.

---

## ISSUE 1 — `holes_filled` is empty in 100% of rows (all three datasets)

**Severity: high (consumer-facing).** Every sampled row across fiat-crypto,
CompCert, and l4v has `holes_filled == []`. The published cuts are
`schema.row_version: 0`, which predates the canonical per-hole metadata, so a
consumer that keys on `holes_filled` (line/column/kind/enclosing_decl) to locate
or describe the hole gets nothing. The information is only recoverable by diffing
`challenge_file_content` vs `solution_file_content`.

- Evidence: `fiat-crypto_cf28fc70_f69a180a`, `l4v_429d778e_84dc7533`,
  `CompCert_618d523d_4c575778`.
- Fix: re-materialize/republish the canonical row shape with `holes_filled`
  populated, or document prominently on each dataset card that v0 omits it.

## ISSUE 2 — Most challenges contain no literal placeholder marker

**Severity: medium (framing / documentation).** `no_marker_in_challenge`:
~82% (fiat-crypto), ~98% (l4v), 100% (CompCert sample). The `challenge_file_content`
has no `Admitted`/`admit`/`sorry`/`oops`. These challenges are not
"fill the `Admitted`" tasks — they ask the model to reproduce the proof/spec the
human *added* in the diff (`challenge_type` add/optimise/spec_change). The dataset
card's "fill a hole" language oversells the `proof_complete` subset.

- Evidence: `fiat-crypto_cf28fc70_f33a0be4`, `l4v_429d778e_0d68a44b`.
- Fix: clarify on the card that only a minority are placeholder completions, and
  expose `challenge_type` distribution so consumers can filter. (The whole-file
  baseline in `baselines/` handles both shapes; a marker-fill solver would not.)

## ISSUE 3 — Ground truth still contains a placeholder

**Severity: medium (label quality).** `marker_in_solution`: ~11% (fiat-crypto),
~1.3% (l4v). The `solution_file_content` still has `Admitted`/`sorry`, i.e. the
"ground truth" did not actually close the goal — the mined child commit left the
proof open, or the chosen commit isn't the one that finished it.

- Evidence: `fiat-crypto_71d6c520_4008d9b7`, `l4v_429d778e_2d759703`.
- Fix: either drop rows whose solution retains a marker, or flag them so scorers
  can exclude them from "did the model close it" metrics.

## ISSUE 4 — Degenerate new-file-addition rows (empty `challenge_file_content`)

**Severity: medium (task design, not data corruption).** `empty_challenge`:
~7% (fiat-crypto), ~1% (l4v). These rows have an empty `challenge_file_content`,
but that is *faithful*, not malformed: confirmed examples are all
`challenge_type: proof_add` of brand-new files (e.g. "Add P256 curve file"), and
the challenge is the repo state *before* the commit — before a file is added it
does not exist, so "" correctly represents "the parent had nothing here".

The problem is that such a row is degenerate **for a whole-file completion
task**: there is nothing to complete, so the model would have to author the
entire file (3k–16k chars in the confirmed examples) from only the commit
message. The `baselines/` runner detects the empty input and skips it before
spending a model call.

- Evidence: `fiat-crypto_cf28fc70_ac5dddcc` (new file `JacobianAffine.v`, solution
  16275 chars), `fiat-crypto_71d6c520_4008d9b7` (new file `P256.v`, solution 3373
  chars), `l4v_3f194296_19a6d32f`.
- Fix: flag new-file additions distinctly (e.g. an `is_new_file` field) so
  consumers can route them to a "write from spec" task or exclude them, rather
  than each consumer having to special-case `challenge_file_content == ""`.

## ISSUE 5 — `challenge == solution` (no-op challenge)

**Severity: low (rare).** `challenge_equals_solution`: 1 row in each of the
fiat-crypto and l4v samples. Challenge and solution are byte-identical after
stripping — the mined commit made no net change to this file, so there is nothing
to solve.

- Evidence: `fiat-crypto_2ece1e73_335ba93f`, `l4v_15832558_e456a220`.
- Fix: drop rows where `challenge_file_content.strip() == solution_file_content.strip()`
  at materialization time.

## ISSUE 6 — File sizes make whole-file challenges intractable (esp. l4v)

**Severity: medium (eval design).** Sampled `challenge_file_content` sizes:

| dataset | median chars | p90 | max |
|---------|-------------|-----|-----|
| fiat-crypto | ~16k | ~51k | ~98k |
| compcert | ~27k | ~63k | ~186k |
| l4v | **~139k** | ~322k | ~369k |

A `(commit, file)` challenge asks for the whole file, but an l4v theory file is
routinely 100k+ chars — too large to regenerate within any model output-token
budget (the `baselines/` run skipped 100% of sampled l4v challenges as
`TOO_LARGE`). This is the strongest argument for **hole-localized** challenges:
serialize the changed declaration/region (the miner already finds it) so a
challenge is the proof to write, not the file to retype. Until then, whole-file
consumers should expect to cover only the small-file tail of l4v.

- Fix: emit a per-declaration / per-hole challenge variant (changed span +
  enough surrounding context), in addition to or instead of whole-file rows.

---

### Reproduce

```bash
cd baselines && uv sync
uv run baseline inspect --limit 400          # all three datasets
uv run baseline inspect -d l4v               # full stream of one
```
