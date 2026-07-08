#!/usr/bin/env python3
"""Mirror a curated set of AFP entries into the forall-git-evals DO Space.

Issue #113. The ablation playground (`website/`) is a *pure static* Vercel
deploy, so it cannot fetch AFP theories directly from isa-afp.org (no permissive
CORS there). Instead we pre-mirror a curated, version-pinned set of AFP entries
into a bucket **we** control (world-readable + CORS), and the site fetches raw
`.thy` text from there client-side.

For each entry we download the AFP release tarball
(`https://www.isa-afp.org/release/afp-<Entry>-current.tar.gz`), extract every
`.thy` file, and upload it public-read to

    s3://forall-git-evals/afp/<Entry>/<relative/path>.thy

Finally we assemble and upload `afp/index.json`, the manifest the frontend reads
(`src/lib/afp.ts`): entry -> theory list (+ byte sizes + object keys).

Idempotent: re-running re-downloads and re-uploads (overwrites) in place.

Prereqs: `s3cmd` configured for the DO Space (host_base nyc3.digitaloceanspaces
.com). CORS on the bucket is a one-time separate step (see website/README.md).

Usage:
    python scripts/mirror-afp.py                # mirror the CURATED set
    python scripts/mirror-afp.py --entry Kruskal --entry Show   # subset
    python scripts/mirror-afp.py --dry-run      # download+plan, no uploads
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

BUCKET = "forall-git-evals"
PREFIX = "afp"  # object key prefix within the bucket
BASE_URL = f"https://{BUCKET}.nyc3.digitaloceanspaces.com"
RELEASE_URL = "https://www.isa-afp.org/release/afp-{entry}-current.tar.gz"
ENTRY_PAGE = "https://www.isa-afp.org/entries/{entry}.html"

# Curated set: small-to-medium, self-contained entries with several
# cross-citing lemmas, so fan-in-weighted corollary deletion has something to
# chew on. Byte sizes are the release tarball sizes at time of curation.
CURATED: list[str] = [
    "Depth-First-Search",  # ~3KB   graph DFS, tiny + classic
    "Fisher_Yates",        # ~6KB   in-place shuffle correctness
    "List-Index",          # ~6KB   indexed list operations
    "Sqrt_Babylonian",     # ~20KB  Newton/Heron sqrt bounds
    "Show",                # ~22KB  show-class, multi-theory
    "Regular-Sets",        # ~23KB  regex derivatives, rich fan-in
    "Bernoulli",           # ~28KB  Bernoulli numbers
    "Stirling_Formula",    # ~28KB  Stirling's approximation
    "Dijkstra_Shortest_Path",  # ~31KB shortest paths
    "Amortized_Complexity",    # ~32KB amortized analysis
    "Kruskal",             # ~37KB  MST correctness
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def download_tarball(entry: str) -> bytes:
    url = RELEASE_URL.format(entry=entry)
    log(f"  ↓ {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{entry}: HTTP {resp.status}")
        return resp.read()


def s3_put(local: Path, key: str, mime: str, dry_run: bool) -> None:
    cmd = [
        "s3cmd", "put", str(local), f"s3://{BUCKET}/{key}",
        "--acl-public", "--mime-type", mime, "--no-progress",
    ]
    if dry_run:
        log(f"    [dry-run] {' '.join(cmd)}")
        return
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def mirror_entry(entry: str, tmp: Path, dry_run: bool) -> dict | None:
    """Download + extract + upload one entry. Returns its index record."""
    try:
        raw = download_tarball(entry)
    except Exception as exc:  # noqa: BLE001 - report and skip
        log(f"  ✗ {entry}: download failed: {exc}")
        return None

    theories: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile() or not member.name.endswith(".thy"):
                continue
            # member.name looks like "<Entry>/path/to/Theory.thy"; strip the
            # leading "<Entry>/" so the object key mirrors the intra-entry path.
            parts = member.name.split("/", 1)
            rel = parts[1] if len(parts) == 2 else member.name
            data = tf.extractfile(member).read()  # type: ignore[union-attr]
            key = f"{PREFIX}/{entry}/{rel}"

            local = tmp / entry / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            s3_put(local, key, "text/plain; charset=utf-8", dry_run)

            theories.append({
                "file": rel,                       # e.g. "Regular_Set.thy"
                "key": key,                        # object key in the bucket
                "url": f"{BASE_URL}/{key}",        # absolute public URL
                "bytes": len(data),
            })
            log(f"    ↑ {key}  ({len(data)}b)")

    if not theories:
        log(f"  ✗ {entry}: no .thy files found in tarball")
        return None

    theories.sort(key=lambda t: t["file"])
    return {
        "name": entry,
        "afp_url": ENTRY_PAGE.format(entry=entry),
        "theories": theories,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entry", action="append", dest="entries",
                    help="mirror only this entry (repeatable); default: CURATED")
    ap.add_argument("--dry-run", action="store_true",
                    help="download + plan but do not upload")
    args = ap.parse_args()

    entries = args.entries or CURATED
    log(f"Mirroring {len(entries)} AFP entr{'y' if len(entries) == 1 else 'ies'} "
        f"-> s3://{BUCKET}/{PREFIX}/  (dry_run={args.dry_run})")

    records: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="afp-mirror-") as td:
        tmp = Path(td)
        for entry in entries:
            log(f"• {entry}")
            rec = mirror_entry(entry, tmp, args.dry_run)
            if rec is not None:
                records.append(rec)

    records.sort(key=lambda r: r["name"].lower())
    index = {
        "schema": "afp-mirror/1",
        "base_url": BASE_URL,
        "source": "https://www.isa-afp.org/",
        "entries": records,
    }
    index_bytes = json.dumps(index, indent=2).encode()

    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as f:
        f.write(index_bytes)
        index_local = Path(f.name)
    s3_put(index_local, f"{PREFIX}/index.json", "application/json", args.dry_run)
    index_local.unlink()

    log(f"\nDone: {len(records)}/{len(entries)} entries, "
        f"{sum(len(r['theories']) for r in records)} theories.")
    log(f"Index: {BASE_URL}/{PREFIX}/index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
