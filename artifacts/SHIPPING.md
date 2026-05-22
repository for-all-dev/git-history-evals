# Shipping a new mined dataset

This is the operational runbook for adding a new dataset under `artifacts/<repo>-eval/<version>/`. For the data-shape reference, see [MANIFEST_SCHEMA.md](./MANIFEST_SCHEMA.md). For an example manifest produced by following this runbook, see [`fiat-crypto-eval/init-draft/manifest.json`](./fiat-crypto-eval/init-draft/manifest.json).

> **Status of tooling.** Several steps below are still manual because the helpers the schema implies (manifest builder, Spaces uploader, `_index.json` updater, HuggingFace publisher) have not been written. Those steps are marked **FIXME — manual**. When you find yourself doing one of them by hand, that's a signal to land the helper in `scaffold/src/dataset/` (proposed module) and update this doc.

---

## 0. Decide what you're shipping

A dataset is one `(repo, miner, scaffold_version, source SHA range)` tuple. Two outputs from the same miner over different SHAs are two datasets. Same SHAs through a different miner are two datasets. If you're unsure whether a change warrants a new version: bump it. Identity is cheap, ambiguity is expensive.

Pick a `<tag>`:
- `v1-handcrafted` — first stable miner for this repo
- `v2-agent-<model>` — agent-synthesised miner, model suffix included
- `init-draft`, `wip-<thing>` — exploratory; do **not** publish to HuggingFace from these

The directory will be `artifacts/<repo>-eval/<tag>-<short_hash>/` once you have a manifest hash. Before then, work in `artifacts/<repo>-eval/<tag>/` and rename at the end.

---

## 1. Pin the source repo

Add the target as a git submodule under `data/` if it isn't already:

```bash
git submodule add https://github.com/<org>/<repo>.git data/<repo>
git submodule update --init --recursive data/<repo>
```

Record its current SHA — this goes into `miner.scaffold_version`'s sibling in your head, not in the manifest, but you'll want it for reproducibility notes.

---

## 2. Write the miner

Create `scaffold/src/miners/<repo>/` (this directory does not yet exist — `compcert` will be the first one). The miner module must:

1. Walk `data/<repo>`'s git history.
2. Apply a commit filter (touches `*.v` / `*.thy`, contains a proof completion, etc.).
3. Emit one challenge row per `(commit, file, theorem)` triple (or whatever your unit is — the v0 fiat-crypto miner groups by `(commit, file)` with a `holes_filled` list inside; that's a pre-canonical shape, don't copy it for new datasets).
4. Conform to the row schema in [MANIFEST_SCHEMA.md](./MANIFEST_SCHEMA.md#challenge-row-schema-shared-core--assistant-extras): every row must carry `sha`, `file`, `theorem_name`, `deletion_size`, `proof_text`, `context`, plus the assistant-specific extras under a `coq` / `isabelle` namespace.

For now there is no base class to inherit from — use `scaffold/src/scaffold/git_walker.py` and `scaffold/src/scaffold/analyzers/<assistant>.py` as utilities. When you write the second miner, factor a base out.

Land the miner in a PR and merge before mining the dataset. The `miner.code_hash` and `miner.scaffold_version` only mean something if the code is checked in.

---

## 3. Mine

Run your miner. Output goes into `artifacts/<repo>-eval/<tag>/` as:

- `challenges.jsonl` — the dataset proper
- any intermediate `*.jsonl` you want preserved (lifecycle, grouped commits, qualitative pass)
- `miner/` — a snapshot of `scaffold/src/miners/<repo>/` copied into the dataset dir (so the dataset is self-contained)

The `.gitignore` at the repo root excludes `*.jsonl` and `*.txt` under `artifacts/` — that's deliberate, bulk payloads live in DO Spaces. `manifest.json`, `README.md`, and `miner/` are tracked.

---

## 4. Compute hashes

Every hash in the manifest is `sha256`, lowercase hex, no prefix. Until a helper module exists, use these one-liners.

**Blob hash** (per file in `blobs`):
```bash
sha256sum artifacts/<repo>-eval/<tag>/challenges.jsonl | awk '{print $1}'
```

**Code hash** (`miner.code_hash`):
```bash
find scaffold/src/miners/<repo> -type f -name '*.py' -not -path '*__pycache__*' \
  | sort | xargs sha256sum | sha256sum | awk '{print $1}'
```
The traversal is: list files, exclude `__pycache__` and any `*.pyc`, sort by path, sha256 each, sha256 the concatenated lines. This is the convention used for fiat-crypto v0; bake it into a helper before the third miner.

**Scaffold version** (`miner.scaffold_version`):
```bash
git rev-parse HEAD
```
If your tree is dirty when you mine, append `-dirty`. Re-mine cleanly before tagging anything `v1+`.

**Manifest content hash** (becomes `<short_hash>` in the version string and the key in `_index.json`):
```bash
sha256sum artifacts/<repo>-eval/<tag>/manifest.json | awk '{print $1}'
```
The first 8 hex chars are `<short_hash>`. Compute this last, then rename the directory from `<tag>/` to `<tag>-<short_hash>/`.

> **FIXME — canonicalisation.** The schema says the manifest hash is over "canonical JSON of the manifest without the short_hash embedded in version". Today we hash the file bytes directly, with `version` set to the bare `<tag>` (no short hash yet), then rename. This works but isn't reproducible across reformatting. Land a `compute_manifest_hash(manifest_dict)` helper that does RFC 8785 canonicalisation and removes the `version` field before hashing.

---

## 5. Write the manifest

Copy [`fiat-crypto-eval/init-draft/manifest.json`](./fiat-crypto-eval/init-draft/manifest.json) as a template. Fill in every field. New datasets should target `schema_version: 1` and follow the canonical row shape — `init-draft` is `schema_version: 0` because it predates the schema, do not imitate its `core_fields` list.

Required fields with no good default:
- `schema_version` — the *manifest format* version. Currently `1`. You only bump this if you're changing what fields the manifest itself carries (rare, repo-wide event).
- `dataset_id` — `forall/<repo>-eval`
- `version` — `<tag>-<short_hash>` (computed in step 4)
- `created_at` — ISO 8601 UTC
- `source.shas` — the full SHA list, this is the reproducibility source of truth
- `miner.kind` — `"handcrafted"` or `"agent"`. If `"agent"`, also fill `prompt_hash` and the `agent` block
- `schema.row_version` — the *row shape* version for this dataset. New datasets start at `1`. Bump when you change what fields a row carries — this drifts per-dataset and is the field you'll actually touch.
- `schema.assistant` — `"coq"` or `"isabelle"`
- `stats` — at minimum `n_challenges`; add histograms appropriate to the assistant
- `blobs` — every file in the dataset dir that isn't `manifest.json` / `README.md` / `miner/`

> **Two version knobs, on purpose.** `schema_version` covers the manifest format and almost never changes. `schema.row_version` covers the row shape and changes whenever you decide to ship a different cut — fiat-crypto might go from `row_version: 1` to `2` when you add a `dependencies` field, while compcert stays at `1`. Both can travel; don't confuse them.

> **FIXME — validator.** There is no `pydantic` model or JSON schema enforcing manifest shape today. Until one exists, eyeball against the example and the schema doc. Land `scaffold/src/dataset/models.py` with a `Manifest` pydantic model that mirrors `MANIFEST_SCHEMA.md` and refuses to write invalid manifests.

---

## 6. Write the README

`artifacts/<repo>-eval/<tag>-<short_hash>/README.md` becomes the HuggingFace dataset card. Keep it short and concrete:

- one-paragraph description of what's mined and why
- pointer to the upstream repo + license
- row schema (link to MANIFEST_SCHEMA.md, then list the actual fields)
- known limitations (heuristic filters, single-assistant, etc.)
- citation block if relevant

The parent `artifacts/<repo>-eval/README.md` is the per-repo landing page; update it to mention the new version.

---

## 7. Upload bulk payloads to DO Spaces

> **FIXME — uploader.** There is no `scaffold` subcommand for this yet. Until there is, use the AWS CLI directly with DO Spaces credentials.

Credentials live in `.env` at the repo root (not checked in). You need:
- `AWS_ACCESS_KEY_ID` — DO Spaces access key
- `AWS_SECRET_ACCESS_KEY` — DO Spaces secret
- `AWS_ENDPOINT_URL` — e.g. `https://nyc3.digitaloceanspaces.com`

Bucket is `forall-evals`. Key layout mirrors the artifacts tree, so the console is browsable. Upload each blob:

```bash
aws --endpoint-url "$AWS_ENDPOINT_URL" s3 cp \
  artifacts/<repo>-eval/<tag>-<short_hash>/challenges.jsonl \
  s3://forall-evals/<repo>-eval/<tag>-<short_hash>/challenges.jsonl
```

Then verify the round-trip — `s3 cp` back into `/tmp` and `sha256sum` it against the blob hash in your manifest. Mismatches usually mean the upload was retried mid-flight; re-upload.

For agent-mined transcripts, upload to `s3://forall-evals/_transcripts/<transcript_hash>.jsonl` (flat layout, keyed by content hash, deduped across datasets).

Once uploads succeed, fill in the `url` field of each `blobs[]` entry in the manifest and re-hash the manifest (the URL change produces a new short hash → rename the directory). This re-hash dance is annoying; the helper module in step 4's FIXME should compute hashes excluding the URL field so uploads don't invalidate the version. Until then, just accept the rename.

---

## 8. Update `_index.json`

Append an entry to `artifacts/_index.json`:

```json
{
  "manifest_hash": "<sha256 of manifest.json>",
  "path": "<repo>-eval/<tag>-<short_hash>/",
  "dataset_id": "forall/<repo>-eval",
  "version": "<tag>-<short_hash>",
  "schema_version": 1,
  "row_version": 1,
  "created_at": "<same as manifest>"
}
```

`schema_version` and `row_version` are both denormalised into the index so callers can filter without opening every manifest.

This is what `experiments/` and the dashboard use to resolve a manifest hash to a local path. **FIXME — updater.** Edit by hand for now; land a `scaffold dataset register <manifest.json>` command that appends idempotently.

---

## 9. Wire the experiments runner

This is the step most likely to surface broken assumptions. `experiments/orchestrate/run-all.sh` currently reads from `artifacts/fiat-crypto-*.jsonl` directly — it does not consume the new manifest layout. Before declaring victory:

1. Confirm `experiments/` can locate your dataset by `manifest_hash` (today it can't — see FIXME).
2. Run one SHA end-to-end through `eval-baseline` against the new dataset to prove the rows are well-formed and the runner accepts them.
3. If you had to change the runner, that's a separate PR — keep dataset-shipping and runner-changes decoupled.

> **FIXME — manifest-aware runner.** `experiments/run_experiment.py` should take a `--dataset-manifest <hash>` arg, resolve it via `_index.json`, pull missing blobs from Spaces, and verify hashes before running. None of this exists today.

---

## 10. Publish to HuggingFace (optional, gated)

Only publish stable cuts — never `init-draft`, never `wip-*`. Use a dedicated HF token with write access to `forall/<repo>-eval`.

> **FIXME — publisher.** No script yet. The plan is: pull blobs locally, build a `datasets.Dataset` from `challenges.jsonl`, attach `README.md` as the card, push with `dataset.push_to_hub("forall/<repo>-eval", revision="<version>")`. Until that's written, publish manually via the HF web UI and record the resulting revision SHA somewhere durable.

---

## 11. Open the PR

Tracked changes for the PR:
- `artifacts/<repo>-eval/<tag>-<short_hash>/manifest.json`
- `artifacts/<repo>-eval/<tag>-<short_hash>/README.md`
- `artifacts/<repo>-eval/<tag>-<short_hash>/miner/**`
- `artifacts/<repo>-eval/README.md` (updated)
- `artifacts/_index.json` (updated)
- any new code under `scaffold/src/miners/<repo>/`

Not tracked (lives in Spaces):
- `*.jsonl`, `*.txt` under the dataset dir

PR description should include: manifest hash, blob URLs, one example row, and one-line stats summary.

---

## Quick checklist

- [ ] Submodule pinned at known SHA
- [ ] Miner committed to `scaffold/src/miners/<repo>/`
- [ ] `challenges.jsonl` produced, rows conform to canonical schema
- [ ] All blob hashes computed
- [ ] `miner.code_hash` and `miner.scaffold_version` computed on a clean tree
- [ ] `manifest.json` written, validated by eye against MANIFEST_SCHEMA.md
- [ ] Manifest hashed, directory renamed to `<tag>-<short_hash>/`
- [ ] `README.md` written
- [ ] Blobs uploaded to DO Spaces, round-trip-verified
- [ ] Manifest `blobs[].url` populated, directory re-renamed if hash changed
- [ ] `_index.json` updated
- [ ] One smoke run through `experiments/` against the new dataset
- [ ] PR opened
