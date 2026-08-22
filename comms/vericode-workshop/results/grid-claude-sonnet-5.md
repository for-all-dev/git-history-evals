| model | mode | max_turns | n | scorable | micro PASS | micro CI | macro PASS | macro CI | repos |
|---|---|--:|--:|--:|--:|---|--:|---|--:|
| claude-sonnet-5 | leaves | 50 | 113 | 103 | 29.1% (30/103) | [20.4%, 38.8%] | 29.2% | [23.6%, 34.9%] | 53 |
| claude-sonnet-5 | whole | 50 | 113 | 103 | 29.1% (30/103) | [20.4%, 37.9%] | 28.3% | [22.6%, 34.0%] | 53 |

Outcome breakdown:

| model | mode | max_turns | pass | dry_run | trivial | malformed | context_exceeded | tampered | gave_up | turn_limit | error | fail |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| claude-sonnet-5 | leaves | 50 | 30 | 0 | 0 | 10 | 0 | 27 | 2 | 18 | 7 | 19 |
| claude-sonnet-5 | whole | 50 | 30 | 0 | 0 | 10 | 0 | 26 | 2 | 25 | 3 | 17 |
