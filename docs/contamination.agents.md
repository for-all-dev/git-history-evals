# Contamination: verdict, evidence, and the instrument stack

Notes on whether training-data contamination undermines `for-all-dev/ablation-eval`
(43,410 compile-validated Lean ablation challenges over 57 public GitHub repos), and
what to actually run about it. Written 2026-08-19.

## Bottom line

Contamination is **not** an existential risk to this dataset, for two different reasons
on the two use cases. It is, however, the single section a reviewer will attack hardest,
and it is currently argued rather than measured. Turning it into a measurement is the
highest-value experimental work available — and either outcome is publishable.

| use case | is contamination fatal? | why |
|---|---|---|
| RL environment | **No — the worry is close to backwards** | Verified reward has no false positives; memorized problems self-filter out of the gradient |
| Eval / benchmark | **No, but must be measured** | Instance-level memorization is defeated by construction; knowledge-level is not |

## Why RL is the *strong* case, not the weak one

1. **A verified reward is contamination-immune in the way that matters.** The
   catastrophic contamination story (GSM8K, MMLU) is that the model emits a memorized
   answer string and the scorer cannot distinguish recall from reasoning. Here the reward
   is: `lake env lean` exits clean, hole-free, tamper-check passed. **A memorized proof
   that compiles is a correct proof.** There is no false positive. Contamination biases
   the *difficulty estimate*, not the *gradient*.
2. **Contamination is self-limiting under any sane curriculum.** Standard practice keeps
   problems in a useful pass-rate band (not 0%, not 100%). A memorized problem passes
   ~always → advantage ≈ 0 → no gradient → filtered automatically. Contamination costs
   **corpus size**, not **corpus validity**. Losing some fraction of 43,410 is an
   inconvenience.
3. **The real threat to a proof-RL env is a hackable reward, and the defense already
   exists.** The Leanstral run recorded 4 `tampered` outcomes — "solving" by weakening or
   deleting the theorem. Tamper rate rose 2 → 4 as turns went 30 → 50. The harness catches
   these and excludes them from PASS. That matters more than contamination purity.
4. **The scarce asset is not novel problems.** It is 43k problems with a compiler in the
   loop across 57 real repos with pinned reproducible builds. Most Lean RL runs on
   miniF2F-scale competition math.

## The distinction that should structure the paper

Two things the literature merges and we should not:

- **Instance-level memorization** — the model saw *this exact problem*. **Defeated by
  construction.** The sliced file with these specific lemmas deleted and these specific
  downstream uses holed never existed as a document. This is a defensible claim and it is
  the thing git-history mining categorically *cannot* claim (there the challenge is a real
  historical commit and the solution is the literal next commit — both in training data,
  as a pair).
- **Knowledge-level memorization** — the model knows *this lemma*. **Not defeated.** Do
  not claim otherwise.

**The deletion-count sweep measures exactly where the first stops mattering and the second
takes over.** Reconstructing one lemma is plausibly retrieval; reconstructing five
interlocking lemmas that were never simultaneously absent is not. That curve is a
methodological contribution in the GSM-Symbolic tradition, not merely a defense. The knob
already exists (`--count N` over the corollary closure).

## Direct measurement: The Stack / Software Heritage membership check (issue #137)

This replaces the stars/age proxy below as the lead instrument. Ran
`pipeline/membership_check.py` over all 57 repos in `pipeline/repos.tsv`; output committed at
`pipeline/membership.tsv`.

**Method.** The Stack v1/v2 aren't full-text searchable without downloading terabytes of
parquet (v2 is also gated on HuggingFace, so `datasets-server` search 404s without an accepted
license click-through). But **The Stack v2 is built directly from a dated Software Heritage
(SWH) graph snapshot** — "3.28B files belonging to 104.2M GitHub repositories were collected by
traversing the Software Heritage 2023-09-06 graph dataset" (the-stack-v2 dataset card) — and
SWH's public API records, per origin, the dated history of every crawl ("visit") it has made. So
instead of a popularity proxy, the script queries SWH directly for each repo's origin and visit
history and compares the **earliest visit date** against the documented snapshot cutoffs:

- Stack v1 cutoff: **2022-06** (files "downloaded from public GitHub repositories between
  November 2021 and June 2022" — the-stack v1 dataset card)
- Stack v2 cutoff: **2023-09-06** (SWH graph snapshot date)

`earliest_visit <= cutoff` is necessary but not sufficient for actual dataset inclusion (SWH
crawling by that date doesn't guarantee the-stack's own license/dedup filtering kept the repo),
so the table reports `likely_in` / `too_recent` / `not_in_swh` / `UNKNOWN`, not a hard
yes/no — still strictly more direct than a variable (stars) that only proxies duplication count
through a broken chain (see below). `--infinigram` optionally cross-checks the repo's
`owner/name` slug against AI2's infini-gram API (`v4_dolma-v1_7_llama` index) as a secondary,
noisier signal (a slug can appear in link lists without the code being duplicated).

**Result, run 2026-08-21 (57/57 repos resolved, zero UNKNOWN):**

| Stack v2 status (SWH visit <= 2023-09-06) | count |
|---|---|
| `likely_in` | **2** (`starkware-formal-proofs`, `symcrust`) |
| `too_recent` (SWH has visited, but only after the cutoff) | 9 |
| `not_in_swh` (no SWH visit found at all) | 46 |
| `UNKNOWN` (API failure) | 0 |

Only `symcrust` (the vendored `microsoft/SymCrypt`, earliest visit 2022-03) clears the *Stack
v1* cutoff too. This corroborates the "Corpus facts" recency skew below through a direct
instrument rather than an inferred one: **the overwhelming majority of this corpus (55/57 by
this measure) was never crawled by Software Heritage before either Stack snapshot**, so
knowledge-level memorization of these specific repos' code from Stack-derived pretraining is
implausible for all but two of them. infini-gram slug counts (Dolma v1.7) were 0 for every repo
except `lean-mlir` (2) and `symcrust` (3) — consistent with the SWH picture and too sparse to
say more than "consistent."

**Correlation with pass rate: not yet run.** The join key is `repo name` (this table's `name`
column = `challenge["repo"]` in ablator output = the `repo` field in
`baselines/apply_ablate` result JSONL) against a macro-averaged per-repo pass rate. No baseline
run currently covers enough of the 57-repo corpus to compute that — see
`macro_pass_rate_join_hook()` in `pipeline/membership_check.py` for the documented hook
(raises `NotImplementedError` rather than fabricating numbers) and implementation sketch. Wire
it up once a corpus-wide baseline exists.

### Reproduce

```bash
python3 pipeline/membership_check.py --infinigram --out pipeline/membership.tsv
```
Network access required; every API call is wrapped so a transient failure records `UNKNOWN`
rather than crashing the run (SWH unauthenticated rate limit is 120 req/hr — the script
throttles with `--sleep`, default 0.6s).

## Direct measurement: verbatim recall probe on deleted lemmas (issue #134)

The membership check above says whether these *repos* could plausibly be in a training
corpus. This says whether the models actually **know the proofs** — the knowledge-level
half of the distinction this document is built on.

Recall the split. **Instance-level** memorization (the model saw *this exact challenge
document*) is defeated by construction: the sliced file with these specific lemmas deleted
and these specific downstream uses holed never existed. **Knowledge-level** memorization
(the model knows *this lemma*) is not defeated by construction and cannot be argued away —
it has to be measured. The verbatim recall probe is the only instrument in the stack that
can produce *positive* evidence of it, and it is the standard black-box test
(Golchin & Surdeanu, *Time Travel in LLMs* — guided prompting / verbatim completion).

**Method.** For each of the 113 deleted lemmas in the paired sample (`scratch-wave3`, the
#129/#130 grid — see `scratch-wave3/GRID.md`), the model is given the repo identity, the
pinned revision, the file path and the full declaration header (docstring, attributes,
binders, statement) with the proof body replaced by `<PROOF>`, and asked to reproduce the
body **exactly as it appears in that file**. No tools, no repo access, single shot — so
anything it produces comes from its weights. `easy` and `hard` delete the *same* lemma
(verified: all 113 pairs have byte-identical lemma text), so each lemma is probed once per
model and the score joined to both splits' outcomes on `challenge_id`. Scoring is exact
match after whitespace normalization, normalized Levenshtein similarity, and token-level
F1; bands are `high` >= 0.9, `mid` >= 0.6, `low` otherwise. 339 calls total.

**One stratification is load-bearing.** A one-line `by simp [foo]` is reproduced by any
model that knows Lean, with zero memorization, so a corpus-wide exact-match rate is really
a measure of how many stock one-liners the sample contains. Every table is therefore also
reported over the **non-trivial** subset (proof bodies of more than one line, 96/113),
where reproducing the token sequence by guessing is implausible.

### Recall scores (run 2026-08-22, 339/339 responses, zero errors)

| model | n | exact | mean lev | mean tokF1 | high band | n non-triv | exact (nt) | mean lev (nt) | high band (nt) |
|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5 | 113 | 0/113 | 0.286 | 0.327 | 0.9% | 96 | **0/96** | 0.284 | 0.0% |
| openai:gpt-5.6-sol | 113 | 2/113 | 0.269 | 0.330 | 1.8% | 96 | **0/96** | 0.238 | 0.0% |
| mistral:labs-leanstral-1-5 | 113 | 0/113 | 0.189 | 0.218 | 0.0% | 96 | **0/96** | 0.200 | 0.0% |

**Verbatim recall is at the floor.** Not one model reproduced a single multi-line proof
body from this corpus. The only two exact matches in 339 probes — both by gpt-5.6-sol,
`round_zero` (`by simp [roundToNearestEven]`, ryu-lean4) and `to_MvPolynomial_Option_X`
(`by simp [to_MvPolynomial_Option]`, formal-snarks-project) — are one-line `simp` calls
whose argument is the definition named in the statement. That is competent Lean, not
recall. The single high-band non-exact hit (sonnet, `IsHermitian₁₁_of_IsHermitian_toBlock`
in CvxLean, lev 0.986) is likewise a one-liner. Mean similarity sits at 0.19–0.29, which is
what surface overlap of *any* two proofs of the same statement looks like.

This is the result the SWH membership check predicts: 55 of 57 repos were never crawled
before either Stack snapshot, so there is little for the models to have memorized.
Two independent instruments, one direct on the corpus and one direct on the models, agree.

### Correlation with solve outcome — the payoff

Point-biserial correlation between recall similarity (lev) and the PASS outcome *of the
same model on the same challenge* (n = 103 scorable per cell; 10 pairs per mode excluded as
`malformed`, `tampered` counted as fail). CI is 5,000-sample bootstrap; p is a 10,000-draw
permutation test. The last column controls for log proof length, since short proofs are both
easier to guess and easier to solve.

| model | split | n scorable | pass | r(lev, PASS) | 95% CI | perm p | partial r (size-controlled) |
|---|---|---|---|---|---|---|---|
| claude-sonnet-5 | easy | 103 | 30/103 | +0.184 | [-0.009, +0.375] | 0.061 | +0.171 |
| claude-sonnet-5 | hard | 103 | 30/103 | +0.257 | [+0.056, +0.451] | 0.007 | +0.235 |
| openai:gpt-5.6-sol | easy | 103 | 51/103 | +0.033 | [-0.156, +0.241] | 0.738 | +0.097 |
| openai:gpt-5.6-sol | hard | 103 | 49/103 | +0.015 | [-0.172, +0.219] | 0.880 | +0.111 |
| mistral:labs-leanstral-1-5 | easy | 103 | 18/103 | +0.034 | [-0.220, +0.290] | 0.728 | +0.041 |
| mistral:labs-leanstral-1-5 | hard | 103 | 25/103 | +0.020 | [-0.221, +0.247] | 0.840 | +0.028 |

Pass rate by recall band (Wilson 95%) tells the same story from the other side — the `low`
band, which is where 97 of 103 problems sit for every model, has essentially the model's
overall pass rate:

| model | split | low band | mid | high |
|---|---|---|---|---|
| claude-sonnet-5 | easy | 28.9% (n=97) | 20.0% (n=5) | 100% (n=1) |
| claude-sonnet-5 | hard | 28.9% (n=97) | 20.0% (n=5) | 100% (n=1) |
| openai:gpt-5.6-sol | easy | 49.5% (n=97) | 75.0% (n=4) | 0% (n=2) |
| openai:gpt-5.6-sol | hard | 47.4% (n=97) | 75.0% (n=4) | 0% (n=2) |
| mistral:labs-leanstral-1-5 | easy | 17.5% (n=103) | — | — |
| mistral:labs-leanstral-1-5 | hard | 24.3% (n=103) | — | — |

**Reading.** For gpt-5.6-sol and Leanstral, recall and solve success are uncorrelated
(|r| <= 0.03, p >= 0.73): the ~49% and ~18–24% pass rates are not retrieval. For sonnet
there is a small positive association, significant on `hard` (r = +0.26, p = 0.007;
+0.24 after controlling for proof size) and marginal on `easy` (r = +0.18, p = 0.06). Six
cells were tested, so the one surviving cell is p ≈ 0.04 Bonferroni-corrected — real but
weak, and r = 0.26 is ~7% of variance. Note also the direction it points: the *hard*
(whole-body) split is the one where recall matters more, exactly as the open question at the
end of this document hypothesised — with no proof skeleton left to constrain the
reconstruction, whatever the model knows about the lemma has more work to do.

Crucially, an r of +0.26 against a **floor-level** recall distribution is not a
contamination finding. Nothing was recalled verbatim; the association is between *slightly
less bad* reconstructions and solving, which is what you expect if both are downstream of
"this model has a good idea how this proof goes" — i.e. ability, not memorization. To call
it contamination you would need recall mass at the top of the range, and there is none.

### Limits

- Absence of verbatim recall does not prove absence of knowledge-level memorization: a model
  can know a lemma's *content* without reproducing its *surface form*, and RLHF'd chat models
  are trained away from long verbatim emission. This bounds the effect; it does not zero it.
- The probe prompts for a best reconstruction when the model does not recall the file, which
  mixes ability into the similarity score. That is deliberate — it is why the trivial-body
  stratum and the size-controlled partial correlation are reported — but it means mean lev
  should be read as an upper bound on recall, never a lower one.
- n = 113 lemmas, 3 models, one sample. The correlation cells are powered to detect
  r ≈ 0.28 at 80%, so a genuinely small memorization effect would be missed.
- Outcomes inherit the 50-turn budget of the #129/#130 grid, and gpt-5.6-sol's non-passes
  are almost entirely `tampered` (`scratch-wave3/GRID.md`); its PASS column is therefore a
  strict measure and its `fail` column heterogeneous.

### Reproduce

```bash
set -a; . .env; set +a
uv run --project baselines --no-sync python3 pipeline/recall_probe.py probe \
    --sample scratch-wave3/paired/easy/sample.jsonl \
    --out pipeline/recall_probe_responses.jsonl        # 339 calls, resumable

python3 pipeline/recall_probe.py score \
    --responses pipeline/recall_probe_responses.jsonl \
    --sample scratch-wave3/paired/easy/sample.jsonl \
    --results claude-sonnet-5=scratch-wave3/paired \
    --results openai:gpt-5.6-sol=scratch-wave3/paired-openai \
    --results mistral:labs-leanstral-1-5=scratch-wave3/paired-leanstral \
    --out pipeline/recall_probe.tsv --report -
```

Per-challenge scores are committed at `pipeline/recall_probe.tsv` (one row per model x
lemma, with both splits' outcomes) and the raw model responses at
`pipeline/recall_probe_responses.jsonl`. `score` is stdlib-only and offline; unit tests are
in `baselines/tests/test_recall_probe.py`.

## Robustness footnote: repo popularity (stars/age) — weak instrument, do not lead with it

Superseded by the direct SWH/Stack measurement above; kept as a secondary robustness check
because it was the first thing tried and its qualitative direction (no evidence memorization
drives pass rate) agrees with the direct measurement.

Joined the per-repo pass rates in `docs/leanstral-baseline-100.md` against GitHub
popularity/age metadata. If memorization drove performance, pass rate should rise with how
public and how old a repo is.

| correlate | Pearson r |
|---|---|
| pass rate vs. log(stars) | **−0.07** |
| pass rate vs. repo age (months) | **−0.08** |
| pass rate vs. log(repo size KB) | +0.06 |

| bucket | pass rate |
|---|---|
| ≥100 stars (18 repos) | 14/35 = **40%** |
| <100 stars (29 repos) | 24/54 = **44%** |
| all matched repos | 38/89 = 43% |

Individual cases sharpen it: `iris-lean` (2022, 213★, heavily mirrored and discussed) 0/2;
`lean4lean` (225★) 0/2; `lean-mlir` (258★) 0/2. Meanwhile `nickelean` and
`shortest-decimal`, both created **2026-03** with 0 stars, score **2/2** each.

**Caveats — this is a hint, not a result.** n=47 repos at 2 problems each; one small model
(Leanstral 1.5); enormous noise; and stars are a poor instrument (next section). The
correct use of this table is a robustness footnote, not a headline.

### Reproduce

```bash
# fetch popularity metadata for the pinned corpus
tail -n+2 pipeline/repos.tsv | awk -F'\t' '$2=="lean"{print $1"\t"$3}' | while IFS=$'\t' read -r name url; do
  slug=$(echo "$url" | sed -E 's#https://github.com/##; s#/$##')
  line=$(gh api "repos/$slug" --jq '[.stargazers_count,(.created_at[0:7]),(.pushed_at[0:7]),.size] | @tsv' \
         2>/dev/null | grep -E '^[0-9]' | head -1)
  printf '%s\t%s\n' "$name" "${line:--1\t?\t?\t-1}"
done > stars.tsv
# then join against the per-repo table in docs/leanstral-baseline-100.md
```
Note: `gh` prints a `mise` banner to stdout in this environment — the `grep -E '^[0-9]'`
filter above is load-bearing.

### Why repo popularity is a WEAK instrument (do not publish on it)

Stars proxy contamination only insofar as they proxy **duplication count in the training
corpus**, which is the variable that actually drives memorization (Carlini et al.,
*Quantifying Memorization Across Neural Language Models*: memorization scales with model
size, duplicate count, and prefix length). In this corpus that chain is broken:

- `r(log stars, repo age) = +0.55` — real, but loose
- **41% of the 57 repos were created after 2025-06; 29% after 2026-01**
- 9 of the 19 repos with ≥100 stars postdate mid-2024 (`cslib` 656★ created 2025-06;
  `veil` 283★ created 2025-02; `ArkLib` 327★ created 2024-12)

So "popular" and "plausibly in the training set" are substantially decoupled here.
Popularity is also **not a standard instrument** in the contamination literature — it shows
up in code-LLM papers as a *sampling/quality* criterion, not a contamination probe.

**Corollary, and it is good news:** that same recency skew makes the temporal holdout
nearly free. See below.

## Standard methods (what the literature and labs actually use)

| method | representative work | usable by us? |
|---|---|---|
| **Temporal holdout / live benchmarks** | LiveCodeBench, LiveBench, GSM1K (*A Careful Examination of LLM Performance on Grade School Arithmetic*) | **Yes — cheapest for us of anyone.** Gold standard; needs no corpus access, no logprobs |
| **N-gram / substring overlap vs. training corpus** | GPT-3 and Llama papers; standard in model cards | Not against closed corpora. Open approximation: AI2 **infini-gram**, **WIMBD** |
| **Corpus membership check** | The Stack / Software Heritage (documented snapshot dates + inclusion lists) | **Done — see "Direct measurement" above.** `pipeline/membership_check.py` / `pipeline/membership.tsv` |
| **Membership inference from logprobs** | Min-K% Prob (Shi et al.), Min-K%++, reference-model perplexity | Open-weight models only; most frontier APIs withhold logprobs |
| **Exchangeability / ordering test** | Oren et al., *Proving Test Set Contamination in Black Box Language Models* | Needs logprobs |
| **Verbatim-completion / guided prompting** | Golchin & Surdeanu, *Time Travel in LLMs* | **Done (#134)** — `pipeline/recall_probe.py` / `pipeline/recall_probe.tsv` |
| **Perturbation robustness** | GSM-Symbolic (Apple) | **Yes — this is what the ablator already is** |

## Recommended instrument stack, ranked

1. **Temporal holdout.** Re-mine at today's HEAD; partition by lemma *introduction date*;
   keep only post-cutoff lemmas. Directly measures the contamination gap instead of arguing
   about it. Every repo is revision-pinned in `pipeline/repos.tsv` and most are still active
   (`pushed_at` 2026-08), so the slice is cheap to construct. **Either result is a good
   result:** small gap ⇒ the whole 43k corpus is trustworthy; large gap ⇒ a real finding,
   and the post-cutoff slice becomes the headline eval with the rest as a training pool.
   **Prep done (#132 DATA-PREP half):** `pipeline/lemma_dates.py mine` walks the pinned
   `data/lean/<repo>` checkouts (`git log -S`/`--reverse`, verified against an actual
   declaration line, not just a name mention) to date every deleted lemma in
   `artifacts/lean-ablate/*/challenges.jsonl`, writing `pipeline/lemma_dates.tsv`
   (`challenge_id, repo, lemma, introduction_date, introduction_sha, depth_limited`),
   joined on the same `challenge_id` convention as `membership.tsv`/the difficulty layer.
   The pass-rate correlation itself (`lemma_dates.py temporal-holdout` /
   `temporal_holdout_split`) is implemented and unit-tested against synthetic rows but not
   yet run for real — same "documented hook, no fabricated numbers" status as item 4 below,
   blocked on #129/#130 producing real `ablate-baseline` results.
2. **Deletion-count sweep** (`--count` 1/2/3/5). The memorization-decay curve. Distinctive
   to this project; likely the paper's most interesting figure.
3. ~~**Verbatim recall probe.**~~ **Done (#134)** — see "verbatim recall probe" above:
   verbatim recall is at the floor (0/96 non-trivial bodies reproduced by any of the three
   grid models) and recall is uncorrelated with solve success except for a small
   sonnet-on-`hard` association (r = +0.26). Original description: given the lemma name +
   surrounding context but not the body,
   can the model state it? Decorrelates memorization from ability.
4. ~~**The Stack / infini-gram membership check** per repo.~~ **Done** — see "Direct
   measurement" above (`pipeline/membership_check.py`, `pipeline/membership.tsv`). Replaced
   the stars proxy with a direct measurement; the pass-rate correlation step is still blocked
   on a corpus-wide baseline run (documented hook, no fabricated numbers).
5. **Min-K%** on whichever open-weight models are in the grid. Secondary signal.
6. **Stars / age** → robustness footnote (done, see above), included only for agreement-check.

Items 1–3 are ~3 weeks of runs with tooling that already exists. Item 4 took an afternoon.

## Corpus facts useful for building the temporal split

- 57 Lean repos; all revision-pinned in `pipeline/repos.tsv` (url + revision + toolchain)
- creation-date distribution: 68% after 2024-06, 52% after 2025-01, 41% after 2025-06,
  29% after 2026-01
- most repos still active (`pushed_at` 2026-08), so re-mining at HEAD yields genuinely new
  lemmas
- **Watch the skew:** `evm-asm` alone is 7,323 / 21,692 rows (34%); top 3 repos are ~54%.
  Any temporal comparison must be macro-averaged per repo or it becomes a statement about
  `evm-asm`.

## Open questions / what would change the verdict

- **43% from a small model on real repo proofs is high enough that something is helping.**
  "Models are better at this than expected" and "the benchmark is contaminated" are
  different findings and we currently cannot tell them apart. This is the thing to resolve
  before a reviewer asks.
- **Turn-limit (44) exceeded PASS (38)** in the only run we have, so the pass rate is partly
  a property of the request budget. Contamination analysis run on top of a budget-limited
  baseline inherits that confound — pair any contamination sweep with a budget curve.
- Does the contamination gap differ between the `easy` (leaf-hole) and `hard` (whole-body)
  splits? Hypothesis: whole-body holing should depend *more* on knowledge-level
  memorization, since there is no surviving proof skeleton to constrain the reconstruction.
  Untested, and it is a cheap add-on to experiment 1.

## Related repo state

- Issue **#72** — perturbation pipeline for contamination-robust evals. The deletion-count
  sweep is a partial instance of this.
- Issues **#94–#97** — AFP contamination is categorically worse (packaged, mirrored,
  heavily duplicated); perturbation cannot stay a stretch goal on that track.
- `docs/leanstral-baseline-100.md` — the only baseline run to date; source of the per-repo
  pass rates used above.
