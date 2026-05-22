# fiat-crypto-eval

Mined eval datasets from the [mit-plv/fiat-crypto](https://github.com/mit-plv/fiat-crypto) Coq codebase. Each subdirectory is one miner version; see `../MANIFEST_SCHEMA.md` for the manifest spec.

Target HuggingFace dataset: `forall/fiat-crypto-eval` (subject to org availability).

## Migration note

The flat files at `artifacts/fiat-crypto-*.jsonl` (`fiat-crypto-challenges.jsonl`, `fiat-crypto-commits-*.jsonl`, etc.) predate the versioned layout. They are the output of the original hand-tuned miner and should be migrated to `v1-handcrafted-<hash>/` here once a `manifest.json` is generated for them retroactively. Until then, treat them as the de facto v0.
