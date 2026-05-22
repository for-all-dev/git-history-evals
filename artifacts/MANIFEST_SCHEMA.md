# Dataset Manifest Schema

Every mined eval dataset lives at `artifacts/<repo>-eval/<version>/` and ships
a `manifest.json` describing its provenance, identity, and static statistics.

The manifest is the canonical artifact for both reproducibility and
publication: it is what solver runs reference (by content hash), and its
contents drive the auto-generated section of the HuggingFace dataset card.

## Persistence model

Three stores, each doing what it's good at:

| Store         | Holds                                                            | Identity            |
|---------------|------------------------------------------------------------------|---------------------|
| **git**       | manifests, READMEs, schema docs, miner code snapshots, `_index.json` | path                |
| **DO Spaces** | bulk payloads: `challenges.jsonl`, intermediate mining outputs, agent-miner design transcripts | URL + sha256        |
| **HuggingFace** | published cuts of versioned datasets (re-uploaded copy, not a live link) | `<org>/<name>@rev`  |

Bulk files never sit in the git working tree. The manifest declares each
bulk file in a `blobs` list with both a Spaces URL (for browsing) and a
sha256 (for integrity verification on download).

## Layout

```
artifacts/                            # everything here is git-tracked unless noted
├── _index.json
├── _transcripts/                     # ← contents in DO Spaces, dir tracked but empty
│   └── (blobs referenced by hash from dataset manifests)
└── <repo>-eval/
    └── <tag>-<short_hash>/
        ├── manifest.json             # tracked — declares blobs below
        ├── README.md                 # tracked — becomes the HF dataset card
        ├── miner/                    # tracked — code snapshot
        └── challenges.jsonl          # ← NOT tracked, lives in DO Spaces
```

`<short_hash>` is the first 8 hex chars of the manifest content hash. `<tag>`
is human-readable (`v1-handcrafted`, `v2-agent-opus47`).

Bulk payload paths in the layout above (`challenges.jsonl`,
`_transcripts/*.jsonl`) match the local path a dev sees after pulling the
blob down from Spaces; the manifest's `blobs` entries name the same paths.
`.gitignore` excludes `*.jsonl` and `*.txt` under `artifacts/`.

## Manifest fields

```json
{
  "schema_version": 1,

  "dataset_id": "forall/fiat-crypto-eval",
  "version": "v2-agent-opus47-a3f2b1c8",
  "created_at": "2026-05-22T12:34:56Z",

  "source": {
    "repo": "mit-plv/fiat-crypto",
    "submodule_path": "data/fiat-crypto",
    "range": {
      "from": "<sha>",
      "to": "<sha>",
      "filter": "commits touching *.v with deletion_size in [10, 500]"
    },
    "shas": ["<sha>", "<sha>", "..."]
  },

  "miner": {
    "kind": "handcrafted",
    "code_hash": "<sha256 of scaffold/src/miners/<repo>/ files>",
    "code_snapshot": "miner/",
    "prompt_hash": null,
    "scaffold_version": "<git sha of this repo at mining time>",
    "agent": null
  },

  "schema": {
    "assistant": "coq",
    "core_fields": [
      "sha", "file", "theorem_name",
      "deletion_size", "proof_text", "context"
    ],
    "extras_namespace": "coq"
  },

  "stats": {
    "n_challenges": 1082,
    "deletion_size_histogram": {"1-10": 412, "11-50": 380, "51+": 290},
    "proof_length_chars_histogram": {"<100": 200, "100-500": 600, "500+": 282},
    "tactic_count_histogram": {"1": 50, "2-5": 600, "6+": 432}
  },

  "blobs": [
    {
      "path": "challenges.jsonl",
      "hash": "<sha256>",
      "size_bytes": 65129070,
      "url": "s3://forall-evals/fiat-crypto-eval/v2-agent-opus47-a3f2b1c8/challenges.jsonl"
    }
  ]
}
```

### Field notes

- **`schema_version`** — single integer; bumps on any breaking change to
  manifest *or* challenge row shape.

- **`version`** — `<tag>-<short_hash>`, where `<short_hash>` is the first 8 hex
  chars of `sha256(canonical_json(manifest_without_short_hash_in_version))`.
  Verbose but readable in `ls`; full hash recoverable from manifest content.

- **`source.range` + `source.shas`** — range/filter is documentation; the SHA
  list is the source of truth for reproducibility, even if upstream history
  is rewritten.

- **`miner.kind`** — `"handcrafted"` or `"agent"`. When `"agent"`, the
  `prompt_hash` and `agent` block must be populated and the corresponding
  transcript is a Spaces blob keyed at
  `s3://forall-evals/_transcripts/<transcript_hash>.jsonl`. The dataset's
  `blobs` list does *not* need to include the transcript — `agent.transcript_hash`
  is the canonical reference.

- **`miner.code_hash`** — `sha256` over the contents of
  `scaffold/src/miners/<repo>/` (the miner module(s) only). Shared scaffold
  utilities are *not* hashed; their identity is captured separately via
  `scaffold_version`.

- **`miner.scaffold_version`** — git SHA of the `git-history-evals` repo at
  the time of mining. Same miner code under a different scaffold version
  produces a different dataset version; the miner identity itself remains
  stable.

- **`miner.agent`** (when `kind == "agent"`):
  ```json
  {
    "model": "claude-opus-4-7",
    "transcript_hash": "<sha256 of design transcript file>",
    "design_run_id": "<optional run id from scaffold/agent_miner/runs/>"
  }
  ```

- **`schema.assistant`** — `"coq"` or `"isabelle"`. Determines which
  extras namespace appears on each row.

- **`stats`** — static, solver-free metrics only. Computed at mining time
  from the ground-truth proofs. Solver scores are *not* written back into
  the manifest; they live in `experiments/results/<run_id>/summary.json`
  and are joined to the dataset via the run's `dataset_manifest_hash`.

- **`blobs`** — every bulk payload that belongs to this dataset. Each entry
  declares:
  - `path`: relative path within the dataset dir (where the file lands
    locally after a pull from Spaces).
  - `hash`: `sha256` of the blob contents. Used to verify integrity on
    download and to detect drift.
  - `size_bytes`: file size, for sanity-checking and progress bars.
  - `url`: `s3://<bucket>/<key>` pointing at the Spaces object. Bucket
    layout mirrors `artifacts/` paths so the console is browsable.

  The `blobs` list is part of what gets hashed into the manifest content
  hash, so any change to a blob (new content → new hash) produces a new
  dataset version.

## Challenge row schema (shared core + assistant extras)

```json
{
  "sha": "<source-repo commit sha that introduced this challenge>",
  "file": "src/Foo.v",
  "theorem_name": "foo_correct",
  "deletion_size": 23,
  "proof_text": "Proof. ... Qed.",
  "context": {"imports": ["..."], "preceding_defs": ["..."]},

  "coq": {
    "tactic_count": 7,
    "uses_ltac": true,
    "dependencies": ["..."]
  }
  // OR
  "isabelle": {
    "apply_script_depth": 3,
    "uses_isar": false,
    "locale": "..."
  }
}
```

`summary.py` and other cross-repo aggregations operate on the core fields
only; deep per-assistant analysis lives in tools that read the namespaced
extras.

## `_index.json` shape

```json
{
  "schema_version": 1,
  "description": "...",
  "datasets": [
    {
      "manifest_hash": "<sha256>",
      "path": "fiat-crypto-eval/v1-handcrafted-a3f2b1c8/",
      "dataset_id": "forall/fiat-crypto-eval",
      "version": "v1-handcrafted-a3f2b1c8",
      "created_at": "2026-05-22T..."
    }
  ]
}
```

Updated by scaffold whenever a dataset is mined or removed. Solver runs
read this to resolve `dataset_manifest_hash` → local path.
