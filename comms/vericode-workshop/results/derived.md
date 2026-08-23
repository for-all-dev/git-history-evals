# Derived statistics for the VeriCodeGen results section

Regenerate with `python3 derive.py report` (reads `outcomes.tsv`).
Macro/micro PASS and their bootstrap CIs are NOT recomputed here — they are
quoted from the aggregator output in `grid-*.md` / `grid-*.json`.

## Outcome composition (counts over 113 attempts per model x strategy)

| model | strategy | pass | tampered | fail | turn_limit | gave_up | error | context_exceeded | malformed |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| claude-sonnet-5 | leaf | 30 | 27 | 19 | 18 | 2 | 7 | 0 | 10 |
| claude-sonnet-5 | whole | 30 | 26 | 17 | 25 | 2 | 3 | 0 | 10 |
| mistral:labs-leanstral-1-5 | leaf | 30 | 26 | 1 | 16 | 1 | 23 | 6 | 10 |
| mistral:labs-leanstral-1-5 | whole | 29 | 22 | 0 | 18 | 2 | 29 | 3 | 10 |
| openai:gpt-5.6-sol | leaf | 51 | 50 | 0 | 0 | 0 | 2 | 0 | 10 |
| openai:gpt-5.6-sol | whole | 49 | 53 | 0 | 0 | 0 | 1 | 0 | 10 |

## Tamper reason split

| model | strategy | declaration removed | statement weakened | total |
|---|---|--:|--:|--:|
| claude-sonnet-5 | leaf | 26 | 1 | 27 |
| claude-sonnet-5 | whole | 23 | 3 | 26 |
| mistral:labs-leanstral-1-5 | leaf | 25 | 1 | 26 |
| mistral:labs-leanstral-1-5 | whole | 22 | 0 | 22 |
| openai:gpt-5.6-sol | leaf | 41 | 9 | 50 |
| openai:gpt-5.6-sol | whole | 42 | 11 | 53 |

## Compile-only oracle (what a scorer without the tamper guard would report)

| model | strategy | pass | + tampered | scorable | compile-only rate | true rate |
|---|---|--:|--:|--:|--:|--:|
| claude-sonnet-5 | leaf | 30 | 27 | 103 | 55.3% | 29.1% |
| claude-sonnet-5 | whole | 30 | 26 | 103 | 54.4% | 29.1% |
| mistral:labs-leanstral-1-5 | leaf | 30 | 26 | 97 | 57.7% | 30.9% |
| mistral:labs-leanstral-1-5 | whole | 29 | 22 | 100 | 51.0% | 29.0% |
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
| mistral:labs-leanstral-1-5 | all | 95 | 5 | 5 | 1.000 |
| mistral:labs-leanstral-1-5 | no-error | 58 | 3 | 3 | 1.000 |
| openai:gpt-5.6-sol | all | 103 | 6 | 4 | 0.754 |
| openai:gpt-5.6-sol | no-error | 100 | 5 | 3 | 0.727 |

## Transport-error sensitivity (macro PASS, repo-averaged)

| model | strategy | macro (errors as non-pass) | repos | macro (errors dropped) | repos |
|---|---|--:|--:|--:|--:|
| claude-sonnet-5 | leaf | 29.2% | 53 | 30.8% | 52 |
| claude-sonnet-5 | whole | 28.3% | 53 | 28.8% | 52 |
| mistral:labs-leanstral-1-5 | leaf | 30.8% | 52 | 39.4% | 47 |
| mistral:labs-leanstral-1-5 | whole | 28.3% | 53 | 40.4% | 47 |
| openai:gpt-5.6-sol | leaf | 49.1% | 53 | 49.1% | 53 |
| openai:gpt-5.6-sol | whole | 47.2% | 53 | 47.2% | 53 |

## Cost

Average turns is over attempts that reached a solver outcome (i.e. excluding
`malformed` and provider-error rows, which never consumed the budget).

| model | avg turns | avg turns (leaf) | avg turns (whole) | input Mtok | output Mtok | solver time (h) |
|---|--:|--:|--:|--:|--:|--:|
| claude-sonnet-5 | 38.8 | 37.1 | 40.5 | 281.4 | 4.45 | 21.0 |
| mistral:labs-leanstral-1-5 | 28.6 | 27.0 | 30.4 | 121.3 | 4.35 | 65.1 |
| openai:gpt-5.6-sol | 8.0 | 7.7 | 8.3 | 38.4 | 1.64 | 9.2 |
| **total** | --- | --- | --- | 441.2 | 10.44 | 95.3 |
