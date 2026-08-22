# Derived statistics for the VeriCodeGen results section

Regenerate with `python3 derive.py report` (reads `outcomes.tsv`).
Macro/micro PASS and their bootstrap CIs are NOT recomputed here — they are
quoted from the aggregator output in `grid-*.md` / `grid-*.json`.

## Outcome composition (counts over 113 attempts per model x strategy)

| model | strategy | pass | tampered | fail | turn_limit | gave_up | error | context_exceeded | malformed |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| claude-sonnet-5 | leaf | 30 | 27 | 19 | 18 | 2 | 7 | 0 | 10 |
| claude-sonnet-5 | whole | 30 | 26 | 17 | 25 | 2 | 3 | 0 | 10 |
| mistral:labs-leanstral-1-5 | leaf | 18 | 15 | 1 | 9 | 0 | 60 | 0 | 10 |
| mistral:labs-leanstral-1-5 | whole | 25 | 19 | 0 | 11 | 1 | 44 | 3 | 10 |
| openai:gpt-5.6-sol | leaf | 51 | 50 | 0 | 0 | 0 | 2 | 0 | 10 |
| openai:gpt-5.6-sol | whole | 49 | 53 | 0 | 0 | 0 | 1 | 0 | 10 |

## Tamper reason split

| model | strategy | declaration removed | statement weakened | total |
|---|---|--:|--:|--:|
| claude-sonnet-5 | leaf | 26 | 1 | 27 |
| claude-sonnet-5 | whole | 23 | 3 | 26 |
| mistral:labs-leanstral-1-5 | leaf | 14 | 1 | 15 |
| mistral:labs-leanstral-1-5 | whole | 19 | 0 | 19 |
| openai:gpt-5.6-sol | leaf | 41 | 9 | 50 |
| openai:gpt-5.6-sol | whole | 42 | 11 | 53 |

## Compile-only oracle (what a scorer without the tamper guard would report)

| model | strategy | pass | + tampered | scorable | compile-only rate | true rate |
|---|---|--:|--:|--:|--:|--:|
| claude-sonnet-5 | leaf | 30 | 27 | 103 | 55.3% | 29.1% |
| claude-sonnet-5 | whole | 30 | 26 | 103 | 54.4% | 29.1% |
| mistral:labs-leanstral-1-5 | leaf | 18 | 15 | 103 | 32.0% | 17.5% |
| mistral:labs-leanstral-1-5 | whole | 25 | 19 | 100 | 44.0% | 25.0% |
| openai:gpt-5.6-sol | leaf | 51 | 50 | 103 | 98.1% | 49.5% |
| openai:gpt-5.6-sol | whole | 49 | 53 | 103 | 99.0% | 47.6% |

## Paired comparison, leaf vs whole-body holing (exact McNemar)

`all` counts every scorable attempt, matching the aggregator's denominator;
`no-error` additionally drops pairs where either side ended in a provider
transport error, which only matters for leanstral-1-5.

| model | set | n pairs | leaf-only PASS | whole-only PASS | p |
|---|---|--:|--:|--:|--:|
| claude-sonnet-5 | all | 103 | 6 | 6 | 1.000 |
| claude-sonnet-5 | no-error | 96 | 6 | 5 | 1.000 |
| mistral:labs-leanstral-1-5 | all | 100 | 4 | 11 | 0.118 |
| mistral:labs-leanstral-1-5 | no-error | 30 | 1 | 3 | 0.625 |
| openai:gpt-5.6-sol | all | 103 | 6 | 4 | 0.754 |
| openai:gpt-5.6-sol | no-error | 100 | 5 | 3 | 0.727 |

## Transport-error sensitivity (macro PASS, repo-averaged)

| model | strategy | macro (errors as non-pass) | repos | macro (errors dropped) | repos |
|---|---|--:|--:|--:|--:|
| claude-sonnet-5 | leaf | 29.2% | 53 | 30.8% | 52 |
| claude-sonnet-5 | whole | 28.3% | 53 | 28.8% | 52 |
| mistral:labs-leanstral-1-5 | leaf | 17.9% | 53 | 40.3% | 31 |
| mistral:labs-leanstral-1-5 | whole | 24.0% | 52 | 43.8% | 40 |
| openai:gpt-5.6-sol | leaf | 49.1% | 53 | 49.1% | 53 |
| openai:gpt-5.6-sol | whole | 47.2% | 53 | 47.2% | 53 |

## Cost

Average turns is over attempts that reached a solver outcome (i.e. excluding
`malformed` and provider-error rows, which never consumed the budget).

| model | avg turns | avg turns (leaf) | avg turns (whole) | input Mtok | output Mtok | solver time (h) |
|---|--:|--:|--:|--:|--:|--:|
| claude-sonnet-5 | 38.8 | 37.1 | 40.5 | 281.4 | 4.45 | 21.0 |
| mistral:labs-leanstral-1-5 | 27.5 | 25.1 | 29.3 | 87.8 | 2.80 | 87.3 |
| openai:gpt-5.6-sol | 8.0 | 7.7 | 8.3 | 38.4 | 1.64 | 9.2 |
| **total** | --- | --- | --- | 407.6 | 8.89 | 117.5 |
