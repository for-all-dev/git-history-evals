| model | mode | max_turns | n | scorable | micro PASS | micro CI | macro PASS | macro CI | repos |
|---|---|--:|--:|--:|--:|---|--:|---|--:|
| mistral:labs-leanstral-1-5 | leaves | 50 | 113 | 103 | 17.5% (18/103) | [10.7%, 25.2%] | 17.9% | [13.2%, 22.6%] | 53 |
| mistral:labs-leanstral-1-5 | whole | 50 | 113 | 100 | 25.0% (25/100) | [17.0%, 34.0%] | 24.0% | [19.2%, 28.8%] | 52 |

Outcome breakdown:

| model | mode | max_turns | pass | dry_run | trivial | malformed | context_exceeded | tampered | gave_up | turn_limit | error | fail |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| mistral:labs-leanstral-1-5 | leaves | 50 | 18 | 0 | 0 | 10 | 0 | 15 | 0 | 9 | 60 | 1 |
| mistral:labs-leanstral-1-5 | whole | 50 | 25 | 0 | 0 | 10 | 3 | 19 | 1 | 11 | 44 | 0 |
