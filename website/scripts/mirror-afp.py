#!/usr/bin/env python3
"""Mirror AFP entries into the forall-evals DO Space (issue #113).

The ablation playground (`website/`) is a *pure static* Vercel deploy, so it
can't fetch AFP theories directly from isa-afp.org (no permissive CORS there).
We pre-mirror a version-pinned copy into a bucket **we** control (world-readable
+ CORS) and the site fetches raw `.thy` text from there client-side.

Two modes:

  --full      Mirror the ENTIRE AFP from the single release tarball
              (`afp-current.tar.gz`, ~1000 entries / ~10k theories / ~280 MB).
              One download, then a parallel upload. This is the deliverable.

  (default)   Mirror only the small CURATED list below — handy for a quick
              refresh or a local smoke test.

Layout in the bucket (both modes):

    afp/index.json                 lightweight manifest the site loads on open:
                                     { schema, base_url, source, release,
                                       entries: [{name, n_theories, afp_url}] }
    afp/<Entry>/theories.json      per-entry theory list, fetched lazily when an
                                     entry is picked: { name, theories:[{file,
                                     key, url, bytes}] }
    afp/<Entry>/<path>.thy         raw theory source (public-read)

Splitting the index keeps first paint light: ~1000 entries is a ~90 KB
index.json, while the ~10k per-theory records live in the lazy shards.

Idempotent: re-running overwrites in place. `s3cmd` must be configured for the
Space. Bucket CORS is a one-time separate step (see website/README.md).

Usage:
    python scripts/mirror-afp.py --full            # whole AFP
    python scripts/mirror-afp.py --full --workers 12
    python scripts/mirror-afp.py                   # curated set
    python scripts/mirror-afp.py --entry Kruskal   # a subset
    python scripts/mirror-afp.py --full --dry-run  # download + plan, no uploads
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BUCKET = "forall-evals"
PREFIX = "ablations/isabelle/_data/afp"
BASE_URL = f"https://{BUCKET}.nyc3.digitaloceanspaces.com/{PREFIX}"
FULL_RELEASE_URL = "https://isa-afp.org/release/afp-current.tar.gz"
ENTRY_RELEASE_URL = "https://www.isa-afp.org/release/afp-{entry}-current.tar.gz"
ENTRY_PAGE = "https://www.isa-afp.org/entries/{entry}.html"

CURATED: list[str] = [
    "Depth-First-Search", "Fisher_Yates", "List-Index", "Sqrt_Babylonian",
    "Show", "Regular-Sets", "Bernoulli", "Stirling_Formula",
    "Dijkstra_Shortest_Path", "Amortized_Complexity", "Kruskal",
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Staging: build a local `<Entry>/<path>.thy` tree + per-entry theories.json    #
# --------------------------------------------------------------------------- #

def _theory_record(entry: str, rel: str, nbytes: int) -> dict:
    key = f"{PREFIX}/{entry}/{rel}"
    return {"file": rel, "key": key, "url": f"{BASE_URL}/{entry}/{rel}", "bytes": nbytes}


def stage_from_full(stage: Path) -> list[dict]:
    """Download the full release tarball and extract every entry's .thy files.
    Returns the lightweight per-entry index records."""
    log(f"↓ {FULL_RELEASE_URL}")
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        with urllib.request.urlopen(FULL_RELEASE_URL, timeout=600) as resp:
            if resp.status != 200:
                raise RuntimeError(f"full release HTTP {resp.status}")
            # stream to disk (tarfile needs seek for gz members on some inputs)
            while chunk := resp.read(1 << 20):
                tmp.write(chunk)
        tarball = Path(tmp.name)
    log(f"  got {tarball.stat().st_size / 1048576:.0f} MB, extracting .thy …")

    release = ""
    per_entry: dict[str, list[dict]] = {}
    with tarfile.open(tarball, mode="r:gz") as tf:
        for m in tf:
            if not m.isfile() or not m.name.endswith(".thy"):
                continue
            # paths look like "afp-YYYY-MM-DD/thys/<Entry>/<rel>.thy"
            parts = m.name.split("/")
            if len(parts) < 4 or parts[1] != "thys":
                continue
            release = release or parts[0]
            entry, rel = parts[2], "/".join(parts[3:])
            data = tf.extractfile(m).read()  # type: ignore[union-attr]
            dest = stage / entry / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            per_entry.setdefault(entry, []).append(_theory_record(entry, rel, len(data)))
    tarball.unlink(missing_ok=True)

    return _finalize_stage(stage, per_entry, release)


def stage_from_entries(stage: Path, entries: list[str]) -> list[dict]:
    """Per-entry tarball download path (curated / subset mode)."""
    import io

    per_entry: dict[str, list[dict]] = {}
    for entry in entries:
        url = ENTRY_RELEASE_URL.format(entry=entry)
        log(f"↓ {url}")
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                raw = resp.read()
        except Exception as exc:  # noqa: BLE001
            log(f"  ✗ {entry}: {exc}")
            continue
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
            for m in tf.getmembers():
                if not m.isfile() or not m.name.endswith(".thy"):
                    continue
                parts = m.name.split("/", 1)
                rel = parts[1] if len(parts) == 2 else m.name
                data = tf.extractfile(m).read()  # type: ignore[union-attr]
                dest = stage / entry / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                per_entry.setdefault(entry, []).append(_theory_record(entry, rel, len(data)))
    return _finalize_stage(stage, per_entry, release="")


def _finalize_stage(stage: Path, per_entry: dict[str, list[dict]], release: str) -> list[dict]:
    """Write per-entry theories.json + top-level index.json into the stage dir."""
    index_entries: list[dict] = []
    for entry, theories in per_entry.items():
        theories.sort(key=lambda t: t["file"])
        (stage / entry / "theories.json").write_text(
            json.dumps({"name": entry, "theories": theories}, indent=2)
        )
        index_entries.append({
            "name": entry,
            "n_theories": len(theories),
            "afp_url": ENTRY_PAGE.format(entry=entry),
        })
    index_entries.sort(key=lambda e: e["name"].lower())
    index = {
        "schema": "afp-mirror/2",
        "base_url": BASE_URL,
        "source": "https://www.isa-afp.org/",
        "release": release,
        "entries": index_entries,
    }
    (stage / "index.json").write_text(json.dumps(index, indent=2))
    return index_entries


# --------------------------------------------------------------------------- #
# Upload: parallel s3cmd (single-threaded per proc, so we shard across entries) #
# --------------------------------------------------------------------------- #

def _upload_entry(stage: Path, entry: str, dry_run: bool) -> str:
    """Upload one entry dir: .thy as text/plain, theories.json as application/json."""
    src = stage / entry
    base = [
        "s3cmd", "put", "--recursive", "--acl-public", "--no-progress",
        "--no-mime-magic",
    ]
    thy = base + [
        "--mime-type", "text/plain; charset=utf-8", "--exclude", "*.json",
        f"{src}/", f"s3://{BUCKET}/{PREFIX}/{entry}/",
    ]
    js = [
        "s3cmd", "put", "--acl-public", "--no-progress",
        "--mime-type", "application/json",
        str(src / "theories.json"), f"s3://{BUCKET}/{PREFIX}/{entry}/theories.json",
    ]
    if dry_run:
        return entry
    subprocess.run(thy, check=True, capture_output=True, text=True)
    subprocess.run(js, check=True, capture_output=True, text=True)
    return entry


def upload(stage: Path, entries: list[dict], workers: int, dry_run: bool) -> None:
    names = [e["name"] for e in entries]
    log(f"↑ uploading {len(names)} entries with {workers} workers "
        f"(dry_run={dry_run}) …")
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_upload_entry, stage, n, dry_run): n for n in names}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                log(f"  ✗ {name}: upload failed: {exc}")
            done += 1
            if done % 50 == 0 or done == len(names):
                log(f"  … {done}/{len(names)} entries")

    # index.json last, so a partial run never advertises missing entries.
    idx = [
        "s3cmd", "put", "--acl-public", "--no-progress",
        "--mime-type", "application/json",
        str(stage / "index.json"), f"s3://{BUCKET}/{PREFIX}/index.json",
    ]
    if not dry_run:
        subprocess.run(idx, check=True, capture_output=True, text=True)
    log(f"index -> {BASE_URL}/index.json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--full", action="store_true", help="mirror the entire AFP")
    ap.add_argument("--entry", action="append", dest="entries",
                    help="curated-mode: mirror only this entry (repeatable)")
    ap.add_argument("--workers", type=int, default=8, help="parallel upload workers")
    ap.add_argument("--dry-run", action="store_true", help="stage but don't upload")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="afp-mirror-") as td:
        stage = Path(td)
        if args.full:
            entries = stage_from_full(stage)
        else:
            entries = stage_from_entries(stage, args.entries or CURATED)
        n_thy = sum(e["n_theories"] for e in entries)
        log(f"staged {len(entries)} entries / {n_thy} theories")
        upload(stage, entries, max(1, args.workers), args.dry_run)

    log("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
