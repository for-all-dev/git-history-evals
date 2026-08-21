# leaderboard data

Everything the `#leaderboard` page reads lives here as static files — no backend, no build-time
data fetch. `src/leaderboard/Leaderboard.tsx` fetches `index.json`, then fetches whatever file
`index.json.file` names, and expects that file to hold a JSON array of `apply_ablate.aggregate`'s
`GroupResult` objects (see `baselines/src/apply_ablate/aggregate.py`).

## Current contents (sample, not real results)

- `manifest.sample.json` — a synthetic two-repo `ablate-aggregate` manifest.
- `res_rocq.sample.jsonl`, `res_lean.sample.jsonl` — the synthetic per-repo `SolveResult` rows
  that manifest points at.
- `results.sample.json` — `ablate-aggregate manifest.sample.json`'s real output over those rows
  (not hand-transcribed — see the generator this was produced with, in the git history of this
  PR). This is what `index.json` currently points `file` at.

`index.json.status` is `"sample"` — the page renders an explicit "awaiting model grid" banner
instead of presenting this as real data. **Do not remove that banner without also replacing the
data.**

## Publishing real results (once #130's model grid exists)

1. Run `ablate-baseline` for each (model, mode, max_turns) cell you want on the board, per repo —
   see the root `CLAUDE.md` and `baselines/README.md`.
2. Build a manifest (`{path, model, mode, max_turns, repo}` entries) over the resulting
   `res_<repo>.jsonl` files and run:

   ```bash
   cd baselines
   uv run ablate-aggregate manifest.json --out-json results.json
   ```
3. Copy `results.json` (and, for reproducibility, the manifest + the `res_*.jsonl` files it
   references) into this directory.
4. Edit `index.json`: set `"status": "real"` and `"file": "results.json"`.
5. Commit and open a PR against `website/`. Leave the `*.sample.*` files in place — they're the
   fixture the "awaiting model grid" state falls back to if `results.json` is ever removed.

The page needs no code changes for this — it renders whatever `index.json` points at.
