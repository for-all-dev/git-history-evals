#!/usr/bin/env bash
# Publish both ablation batches to the DigitalOcean Space s3://forall-ablations/lean/.
# Credentials come from the environment — never hardcode them here:
#   export BUCKET_ACCESS_KEY=... BUCKET_SECRET_KEY=...
# Objects are uploaded PRIVATE. Making them public is a separate, deliberate step:
#   s3cmd setacl --acl-public --recursive s3://forall-ablations/lean/
set -euo pipefail
: "${BUCKET_ACCESS_KEY:?set BUCKET_ACCESS_KEY}"; : "${BUCKET_SECRET_KEY:?set BUCKET_SECRET_KEY}"
cd "$(dirname "$0")/.."
CFG="$(mktemp)"; trap 'rm -f "$CFG"' EXIT
umask 077; cat > "$CFG" <<CFGEOF
[default]
access_key = $BUCKET_ACCESS_KEY
secret_key = $BUCKET_SECRET_KEY
host_base = nyc3.digitaloceanspaces.com
host_bucket = %(bucket)s.nyc3.digitaloceanspaces.com
use_https = True
CFGEOF
B=s3://forall-ablations/lean
s3="s3cmd -c $CFG --no-progress"
$s3 sync --exclude '_index.json' artifacts/lean-ablate/       "$B/corollary-leaves/"
$s3 sync --exclude 'README.md' --exclude '_index.json' artifacts/lean-ablate-whole/ "$B/corollary-whole/"
$s3 put artifacts/lean-ablate-whole/_index.json "$B/_index.json"
$s3 put artifacts/lean-ablate-whole/README.md   "$B/README.md"
echo "UPLOAD DONE"
