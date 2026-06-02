---
name: publish-dataset
description: Publish a mined eval dataset version (artifacts/<repo>-eval/<tag>-<hash>/) to the forall-git-evals DigitalOcean Space using s3cmd — sync the bulk challenges.jsonl + manifest, optionally make it world-readable, and verify the published blob's sha256 against the manifest. Use when shipping a dataset version to Spaces / HuggingFace.
---

# Publish a dataset version to DigitalOcean Spaces

Upload a mined eval dataset version directory to the `forall-git-evals` Space
(nyc3). The bucket key **mirrors the `artifacts/` layout minus the `artifacts/`
prefix**, so the console stays browsable (see `artifacts/MANIFEST_SCHEMA.md`).

## Prerequisites
- `s3cmd` installed and `~/.s3cfg` configured for DO Spaces nyc3
  (`access_key`/`secret_key`, `host_base = nyc3.digitaloceanspaces.com`). Verify
  access with `s3cmd ls s3://forall-git-evals/`.
- Run from the repo root. The version dir must exist locally (produced by
  `scaffold profile … --tag …` or `scaffold materialize … --tag …`). Note the
  bulk `challenges.jsonl` is gitignored, so Spaces is its only home.

## Procedure

```bash
# --- pick the version to publish ---
REPO=fiat-crypto                  # source repo => artifacts/<REPO>-eval/
VER=v1-handcrafted-0152ee12       # the <tag>-<short_hash> version dir
SRC="artifacts/${REPO}-eval/${VER}"
DST="s3://forall-git-evals/${REPO}-eval/${VER}/"   # mirrors artifacts/ (no 'artifacts/' prefix)

# 1. Upload (idempotent — re-runnable; uploads challenges.jsonl + manifest.json + miner/profile.json)
s3cmd sync --no-progress "$SRC/" "$DST"

# 2. Make world-readable — ONLY if this dataset is meant to be public.
#    Publishing to the world is hard to reverse; skip this step to keep it private.
s3cmd setacl --acl-public --recursive "$DST"

# 3. Verify integrity: the published blob's sha256 must equal the manifest's declared hash.
BASE="https://forall-git-evals.nyc3.digitaloceanspaces.com/${REPO}-eval/${VER}"
echo "manifest-declared:"; python3 -c "import json; print(json.load(open('$SRC/manifest.json'))['blobs'][0]['hash'])"
echo "published object: "; curl -s "$BASE/challenges.jsonl" | sha256sum | cut -d' ' -f1
curl -sI "$BASE/challenges.jsonl" | grep -iE '^HTTP|content-length'   # expect 200 + matching size
```

The three hashes (manifest-declared, local `sha256sum "$SRC/challenges.jsonl"`,
and the published object) must all match.

## Public URL pattern
`https://forall-git-evals.nyc3.digitaloceanspaces.com/<repo>-eval/<tag>-<hash>/{challenges.jsonl,manifest.json,miner/profile.json}`

## Notes
- Integrity is the sha256 in `manifest.json` → `blobs[0].hash`, not the URL.
- The manifest's `blobs[].url` is currently left `null`: in `scaffold/dataset.py`
  the `url` field is part of the manifest content hash, so writing it would
  change the version's `<short_hash>` (and the dir name). The download location
  is derivable by the path convention above. (Fix would be to exclude `url` from
  the content-addressing, then backfill it.)
- `setacl --acl-public` is reversible (`s3cmd setacl --acl-private --recursive "$DST"`),
  but objects may be cached/indexed once public — make the public call deliberately.
