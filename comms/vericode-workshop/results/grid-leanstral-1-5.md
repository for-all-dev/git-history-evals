| model | mode | max_turns | n | scorable | micro PASS | micro CI | macro PASS | macro CI | repos |
|---|---|--:|--:|--:|--:|---|--:|---|--:|
| mistral:labs-leanstral-1-5 | leaves | 50 | 113 | 97 | 30.9% (30/97) | [21.6%, 41.2%] | 30.8% | [26.0%, 36.5%] | 52 |
| mistral:labs-leanstral-1-5 | whole | 50 | 113 | 100 | 29.0% (29/100) | [20.0%, 39.0%] | 28.3% | [22.6%, 34.0%] | 53 |

Outcome breakdown:

| model | mode | max_turns | pass | dry_run | trivial | malformed | context_exceeded | tampered | gave_up | turn_limit | error | fail |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| mistral:labs-leanstral-1-5 | leaves | 50 | 30 | 0 | 0 | 10 | 6 | 26 | 1 | 16 | 23 | 1 |
| mistral:labs-leanstral-1-5 | whole | 50 | 29 | 0 | 0 | 10 | 3 | 22 | 2 | 18 | 29 | 0 |
