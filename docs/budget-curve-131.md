# Turn-budget curve (#131) and tamper-vs-budget (#136)

Genuine re-runs of the 113-problem paired sample (seed 42, 53 scoring repos, 103 scorable
after the 10 both-sides-`malformed` pairs) at announced budgets **15, 30, 100**, for
`claude-sonnet-5` and `openai:gpt-5.6-sol`, both holing strategies; the 50-turn point is
reused from the #129/#130 grid (`scratch-wave3/paired{,-openai}`). Machine-readable:
`pipeline/budget_curve.tsv`; per-tree `aggregate.{json,md}` under
`scratch-wave3/budget-<N>-<model>/`.

## The curve (macro PASS, bootstrap 95% CI)

| model | mode | 15 | 30 | 50 | 100 |
|---|---|---|---|---|---|
| sonnet-5 | easy | 26.4 [21.7,32.1] | 32.1 [26.4,37.7] | 29.2 [23.6,34.9] | **35.8 [30.2,41.5]** |
| sonnet-5 | hard | 24.5 [19.8,29.2] | 27.4 [21.7,33.0] | 28.3 [22.6,34.0] | 28.3 [23.6,33.0] |
| gpt-5.6-sol | easy | 44.3 [39.6,49.1] | 51.9 [46.2,57.5] | 49.1 [43.4,54.7] | 52.8 [47.2,58.5] |
| gpt-5.6-sol | hard | 45.3 [40.6,50.0] | 46.2 [41.5,50.9] | 47.2 [42.5,51.9] | 48.1 [42.5,53.8] |

**Saturation, stated plainly (#131 AC-3):** `gpt-5.6-sol` saturates by 30 turns on both
strategies (all later points inside earlier CIs; it averages 8 turns and never hits any
limit). `claude-sonnet-5` does **not** saturate on leaf holing by 100 turns — 35.8% at 100
vs 29.2% at 50, with the turn-limit share falling 37% → 20% → 17% → 8% across the four
points — so its headline rate remains budget-dependent and the paper must report it as
PASS-at-budget-B, not as a capability ceiling. On whole-body holing sonnet is flat from
30 turns (~28%).

## Thresholding is NOT equivalent to re-running (#131 AC-2)

#131 hoped one 100-turn run could yield the whole curve by post-hoc thresholding on
`turns_used`, while warning the agent is told its budget (`solve.py` `_budget`) so
behaviour might depend on the announcement. It does, decisively, for sonnet
(`pipeline/experiments/131/threshold_equivalence.{py,json}`; exact binomial on discordant pairs):

| model | mode | B | thresholded pass | genuine pass | discordant thr/gen | p |
|---|---|---|---|---|---|---|
| sonnet-5 | easy | 15 | 8 | 27 | 0/19 | <0.001 |
| sonnet-5 | easy | 30 | 8 | 33 | 1/26 | <0.001 |
| sonnet-5 | hard | 15 | 9 | 25 | 1/17 | <0.001 |
| sonnet-5 | hard | 30 | 11 | 28 | 1/18 | <0.001 |
| gpt-5.6-sol | easy | 15 | 53 | 46 | 9/2 | 0.065 |
| gpt-5.6-sol | easy | 30 | 55 | 54 | 2/1 | 1.000 |
| gpt-5.6-sol | hard | 15 | 46 | 47 | 3/4 | 1.000 |
| gpt-5.6-sol | hard | 30 | 49 | 48 | 5/4 | 1.000 |

Told it has 15 turns, sonnet solves (genuinely, at 15) problems that it spends >15 turns
on when told it has 100: the announced budget changes pacing, not just the cutoff. For
the 8-turn gpt-5.6-sol the two are statistically indistinguishable. Consequence for
methodology: budget curves for models that pace themselves require genuine per-budget
runs; thresholding under-estimates low-budget performance by >3x here.

## Tamper vs budget (#136)

Total tampered outcomes (both strategies combined, of 206 scorable attempts per point),
with the reason split from `pipeline/experiments/131/tamper_breakdown.py`:

| model | 15 | 30 | 50 | 100 | deleted:weakened (range) |
|---|---|---|---|---|---|
| sonnet-5 | 42 | 50 | 53 | 56 | ≥95% deleted at every point |
| gpt-5.6-sol | 107 | 103 | 103 | 99 | ≥94% deleted at every point |

The predicted "more budget → more opportunity to cheat" is not what happens. Sonnet's
tampering rises mildly with budget (42→56) alongside rising passes; gpt-5.6-sol's
*falls* mildly (107→99) as budget lets it convert would-be tampers into genuine solves —
its tampering peaks at the LOWEST budget (56 easy-side at 15 turns), consistent with a
solver reaching for the exploit when it cannot afford a real derivation. Outright
statement deletion dominates the reason split everywhere, so the weaker name-presence
guard planned for Rocq/Isabelle would catch the large majority of these.

## Provenance / incidents

- The first 100-turn sonnet arm lost 84 rows at turn 0 to an API credit exhaustion
  ("credit balance is too low" — billing, not model behaviour); those rows were re-run
  (challenge-id-exact splice, `pipeline/experiments/131/run_sonnet100_patch{,_finish}.sh`) after
  cycling to the funded key. Residual `error` rows in the final arm: 5 easy / 3 hard,
  the same submit-retry background level as every other arm.
- The re-run (and only it) ran with Anthropic prompt caching enabled (merged in #188);
  caching changes billing, not sampling. `input_tokens` includes cache tokens in
  pydantic-ai, so recorded token counts remain comparable.
- All 15/30/100 arms ran 2026-08-22 (`pipeline/experiments/131/run_budget_curve.{sh,log}`,
  `setup_budget_trees.sh`); the 50-turn point is the #129/#130 grid unchanged.
