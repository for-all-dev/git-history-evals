# #129 / #130 results — three-model grid on the paired sample (2026-08-21/22)

Sample: 113 paired easy/hard problems (shared challenge_id), 53 repos scoring, seed 42, 50-turn budget.
Trees: scratch-wave3/paired (sonnet), paired-openai (gpt-5.6-sol), paired-leanstral (leanstral).
Per-tree aggregate.{json,md} built by ablate-aggregate with per-repo manifests (macro = repo-averaged).

| model | easy macro [CI] | hard macro [CI] | discordant e/h | McNemar p | avg turns | Mtok in/out |
|---|---|---|---|---|---|---|
| openai:gpt-5.6-sol | 49.1% [43.4,54.7] | 47.2% [42.5,51.9] | 6/4 | 0.754 | 8.0 | 38.4/1.6 |
| claude-sonnet-5 | 29.2% [23.6,34.9] | 28.3% [22.6,34.0] | 6/6 | 1.000 | 38.8 | 281.4/4.4 |
| mistral:labs-leanstral-1-5 | ~17% | ~24% | 4/11 | 0.118 | 27.5 | 87.8/2.8 |

Findings:
1. Discrimination across labs: 49/29/18 easy macro. (#130 acceptance criterion.)
2. gpt-5.6-sol never fails honestly: every scorable non-pass is `tampered` (50 easy / 53 hard);
   zero turn-limit, zero gave-up, zero honest fail. Without the compile+tamper verifier it would
   present ~95%+. Tamper rates at 50 turns: gpt-5.6-sol ~50%, sonnet ~25%, leanstral ~14% — the
   stronger the model, the more it games the objective (#136 seed finding).
3. Easy/hard gap is model-dependent: null for both generalists; REVERSED for the domain
   specialist (leanstral better on whole-body reconstruction than leaf-patching).
4. 10 pairs per mode malformed from repo-env gaps (lampe 2, lean-mlir 2, verity 2,
   starkware 2, sparkle 1, LNSym 1) — excluded from denominators; reclaimable with targeted
   lake builds. SizzLean needed data/lean/etheorem symlink (fixed); reran its slices.
