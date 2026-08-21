#!/usr/bin/env bash
# Regenerate + publish the for-all-dev/ablation-eval easy/hard splits from artifacts/.
#
#   pipeline/publish_hf.sh <scratch-dir> [--yes]
#
# Without --yes this is a dry run: it builds easy.jsonl/hard.jsonl, verifies every source blob
# against artifacts/lean-ablate-whole/_index.json (refusing on any mismatch — see
# build_hf_splits.py), and reports drift against the live HF dataset. Nothing is uploaded.
#
# With --yes it does all of the above and then pushes easy.jsonl + hard.jsonl to
# for-all-dev/ablation-eval via `hf upload`.
#
# Credentials, from the environment only — never hardcoded:
#   BUCKET_ACCESS_KEY / BUCKET_SECRET_KEY   to pull source blobs from the DO Space
#                                            (same creds pipeline/upload_ablations.sh uses)
#   HF_TOKEN                                to push to HuggingFace (only required with --yes)
set -euo pipefail
cd "$(dirname "$0")/.."

SCRATCH="${1:?usage: publish_hf.sh <scratch-dir> [--yes]}"
YES=0
[ "${2:-}" = "--yes" ] && YES=1

: "${BUCKET_ACCESS_KEY:?set BUCKET_ACCESS_KEY (needed to pull source blobs from DO Space)}"
: "${BUCKET_SECRET_KEY:?set BUCKET_SECRET_KEY (needed to pull source blobs from DO Space)}"

echo "== build + verify =="
python3 pipeline/build_hf_splits.py build --scratch "$SCRATCH"

echo
echo "== drift check vs live HF =="
set +e
python3 pipeline/build_hf_splits.py drift-check --scratch "$SCRATCH"
DRIFT=$?
set -e

if [ "$YES" -ne 1 ]; then
  echo
  echo "Dry run only (pass --yes to publish). Not uploading."
  exit 0
fi

: "${HF_TOKEN:?set HF_TOKEN to publish (never hardcoded)}"

echo
echo "== uploading to for-all-dev/ablation-eval =="
hf upload for-all-dev/ablation-eval "$SCRATCH/easy.jsonl" ablation-eval/easy.jsonl \
  --repo-type dataset --token "$HF_TOKEN" \
  --commit-message "Republish easy split from artifacts/lean-ablate (source-verified)"
hf upload for-all-dev/ablation-eval "$SCRATCH/hard.jsonl" ablation-eval/hard.jsonl \
  --repo-type dataset --token "$HF_TOKEN" \
  --commit-message "Republish hard split from artifacts/lean-ablate-whole (source-verified)"
echo "UPLOAD DONE (pre-upload drift status was $DRIFT — that drift is what this upload fixes)"
