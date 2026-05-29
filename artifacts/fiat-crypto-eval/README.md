# fiat-crypto-eval

Mined eval datasets from the [mit-plv/fiat-crypto](https://github.com/mit-plv/fiat-crypto) Coq codebase. Each subdirectory is one miner version (`<tag>-<short_hash>/`), holding its own `manifest.json`, `miner/profile.json` (the profile that produced it), and bulk `challenges.jsonl` blob; see `../MANIFEST_SCHEMA.md`.

`profile.json` here is a **symlink** into the currently-blessed version's `miner/profile.json` (set by `scaffold ... --promote`); it is what `scaffold mine-all` reads. The profile is never duplicated — it lives once, inside its dataset.

Target HuggingFace dataset: `forall/fiat-crypto-eval` (subject to org availability).

## Versions

- `v1-handcrafted-<hash>/` — the original hand-tuned Coq profile, materialized into the versioned layout via `scaffold materialize ... --kind handcrafted` (640 challenges; `miner.kind = "handcrafted"`). **Currently blessed** (`profile.json` points here).
- `init-draft/` — the pre-canonical v0 snapshot (`row_version 0`): the same 640-challenge payload plus the intermediate mining/lifecycle/quali JSONLs from the original run, before this versioned layout existed. Kept as the historical record.

Agent-synthesised profiles land as their own siblings (e.g. `agentic_1-<hash>/`) and can be promoted in turn.
