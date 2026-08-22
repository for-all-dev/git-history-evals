| model | mode | max_turns | n | scorable | micro PASS | micro CI | macro PASS | macro CI | repos |
|---|---|--:|--:|--:|--:|---|--:|---|--:|
| openai:gpt-5.6-sol | leaves | 50 | 113 | 103 | 49.5% (51/103) | [40.8%, 59.2%] | 49.1% | [43.4%, 54.7%] | 53 |
| openai:gpt-5.6-sol | whole | 50 | 113 | 103 | 47.6% (49/103) | [38.8%, 57.3%] | 47.2% | [42.5%, 51.9%] | 53 |

Outcome breakdown:

| model | mode | max_turns | pass | dry_run | trivial | malformed | context_exceeded | tampered | gave_up | turn_limit | error | fail |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| openai:gpt-5.6-sol | leaves | 50 | 51 | 0 | 0 | 10 | 0 | 50 | 0 | 0 | 2 | 0 |
| openai:gpt-5.6-sol | whole | 50 | 49 | 0 | 0 | 10 | 0 | 53 | 0 | 0 | 1 | 0 |
