# experiments/results/

Host-side mirror of per-run evaluation artifacts. Populated by `experiments/orchestrate/aggregate.sh`.

## Layout

```
experiments/results/
├── <run_id>/                        # one dir per ./run-all.sh invocation
│   ├── compose.yml                  # snapshot from gen-compose.py
│   ├── run.log                      # controller tmux window tails this
│   ├── raw/<sha-prefix>/            # cp'd from docker volume results-<sha-prefix>
│   │   ├── agent.jsonl
│   │   ├── baseline.jsonl
│   │   └── transcripts/<slot>_d<size>.json
│   ├── agent.jsonl                  # concatenated across SHAs
│   ├── baseline.jsonl
│   ├── summary.json
│   └── summary.md
└── latest -> <run_id>/              # symlink updated by aggregate.sh
```

## Two-layer persistence

**Source of truth:** per-SHA docker named volumes (`results-<sha-prefix>`). The in-container runner writes to `/results/<run_id>/...`; the volume is keyed by SHA so results accumulate across runs of the same commit.

**Host mirror (this directory):** populated by `./orchestrate/aggregate.sh <run_id>`, which copies the `<run_id>` subtree out of each named volume into `raw/<sha-prefix>/`, then concatenates per-mode JSONLs and runs `summary.py`.

## Re-aggregating without re-running

```bash
./experiments/orchestrate/aggregate.sh <run_id>
```

Because named volumes persist, you can re-aggregate stale runs at any time.
