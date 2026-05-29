"""Dataset versioning — write a mined eval as a manifest-schema version dir.

Implements ``artifacts/MANIFEST_SCHEMA.md``: every mined dataset lives at
``artifacts/<repo>-eval/<tag>-<short_hash>/`` and ships a ``manifest.json``
(provenance + identity + static stats), the declarative miner snapshot under
``miner/`` (the ``RepoProfile`` that produced it), and the bulk
``challenges.jsonl`` declared as a sha256 blob.

Why a directory and not a single ``profile.json``: a calibration run is a
bundle, and A/B-ing prompt strategies produces many valid profiles per repo.
The version id is **content-addressed** — ``<short_hash>`` is the first 8 hex of
a hash over the manifest with ``version``/``created_at`` removed — so the same
strategy yielding the same profile + challenges lands in the same dir
(idempotent), while a different prompt produces a sibling dir instead of
overwriting. ``_index.json`` maps manifest hash -> path for solver runs.

The blessed ``artifacts/<repo>-eval/profile.json`` that ``scaffold mine-all``
reads is a *promotion* of one version's profile, set deliberately (see
``runner``/CLI ``--promote``), never auto-overwritten by a calibration run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scaffold.models import EvalChallenge, MiningResult
from scaffold.profile import RepoProfile

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ROW_VERSION = 0  # same pre-canonical EvalChallenge row shape as init-draft
_ROW_VERSION_NOTES = (
    "Pre-canonical: same EvalChallenge row shape as fiat-crypto-eval/init-draft "
    "(task_id/commit_hash/file_path/challenge_file_content/solution_file_content/"
    "diff/holes_filled/instructions), not the sha/file/theorem_name canonical row."
)
_CODE_HASH_INPUTS = (
    "sha256 of (path:sha256 of each *.py under scaffold/src/scaffold/, sorted by "
    "path; __pycache__ excluded)."
)


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """Stable JSON for hashing: sorted keys, no whitespace, unicode preserved."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def scaffold_code_hash(pkg_dir: Path) -> str:
    """sha256 over ``path:sha256`` of every ``*.py`` under ``pkg_dir`` (sorted)."""
    parts: list[str] = []
    for p in sorted(pkg_dir.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(pkg_dir).as_posix()
        parts.append(f"{rel}:{hashlib.sha256(p.read_bytes()).hexdigest()}")
    return sha256_text("\n".join(parts))


def _git_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return r.stdout.strip()


def _repo_slug(repo: Path) -> str:
    """`owner/name` from the repo's origin remote, or the dir name as fallback."""
    r = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
    )
    url = r.stdout.strip()
    if not url:
        return repo.name
    url = url.removesuffix(".git")
    if url.startswith("git@") and ":" in url:
        url = url.split(":", 1)[1]
    elif "://" in url:
        url = url.split("://", 1)[1].split("/", 1)[-1]
    return url.strip("/")


# ---------------------------------------------------------------------------
# Stats (static, solver-free)
# ---------------------------------------------------------------------------


def _bucket(value: int, edges: list[tuple[str, int, int]]) -> str:
    for label, lo, hi in edges:
        if lo <= value < hi:
            return label
    return edges[-1][0]


def _challenge_stats(challenges: list[EvalChallenge]) -> dict:
    shas = {c.commit_hash for c in challenges}
    files = {c.file_path for c in challenges}
    diff_edges = [
        ("lt_500", 0, 500),
        ("500_to_2000", 500, 2000),
        ("2000_to_10000", 2000, 10000),
        ("gte_10000", 10000, 1 << 62),
    ]
    holes_edges = [
        ("1", 1, 2),
        ("2_to_3", 2, 4),
        ("4_to_10", 4, 11),
        ("gt_10", 11, 1 << 62),
    ]
    diff_hist: Counter[str] = Counter()
    holes_hist: Counter[str] = Counter()
    for c in challenges:
        diff_hist[_bucket(len(c.diff), diff_edges)] += 1
        holes_hist[_bucket(max(1, len(c.holes_filled)), holes_edges)] += 1
    return {
        "n_challenges": len(challenges),
        "n_source_commits": len(shas),
        "n_files_touched": len(files),
        "diff_size_chars_histogram": {
            k: diff_hist[k] for k, _, _ in diff_edges if diff_hist[k]
        },
        "holes_per_row_histogram": {
            k: holes_hist[k] for k, _, _ in holes_edges if holes_hist[k]
        },
    }


# ---------------------------------------------------------------------------
# Manifest assembly
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    dataset_id: str,
    source: dict,
    miner: dict,
    assistant: str,
    stats: dict,
    blobs: list[dict],
    tag: str,
    created_at: str,
) -> tuple[dict, str, str]:
    """Assemble a manifest dict; return ``(manifest, version, manifest_hash)``.

    ``version`` is ``<tag>-<short_hash>`` where ``short_hash`` is the first 8 hex
    of a hash over the manifest with ``version`` and ``created_at`` excluded
    (content-addressing: same inputs -> same version, regardless of run time).
    ``manifest_hash`` is the full sha256 over the finished manifest (what
    ``_index.json`` keys on).
    """
    manifest: dict = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "version": None,
        "created_at": created_at,
        "source": source,
        "miner": miner,
        "schema": {
            "row_version": ROW_VERSION,
            "row_version_notes": _ROW_VERSION_NOTES,
            "assistant": assistant,
            "core_fields": list(EvalChallenge.model_fields),
            "extras_namespace": None,
        },
        "stats": stats,
        "blobs": blobs,
    }
    content = {k: v for k, v in manifest.items() if k not in ("version", "created_at")}
    short_hash = sha256_text(canonical_json(content))[:8]
    version = f"{tag}-{short_hash}"
    manifest["version"] = version
    manifest_hash = sha256_text(canonical_json(manifest))
    return manifest, version, manifest_hash


# ---------------------------------------------------------------------------
# Materialization (IO)
# ---------------------------------------------------------------------------


@dataclass
class DatasetVersion:
    version: str
    path: Path
    manifest: dict
    manifest_hash: str
    n_challenges: int


def materialize_dataset_version(
    *,
    profile: RepoProfile,
    result: MiningResult,
    repo_path: Path,
    artifacts_root: Path,
    monorepo_root: Path,
    miner_pkg_dir: Path,
    model: str,
    prompt_text: str,
    tag: str,
    transcript: list[str] | None = None,
    range_filter: str = "",
    created_at: str | None = None,
) -> DatasetVersion:
    """Write a mined eval as a ``<repo>-eval/<tag>-<short_hash>/`` version dir.

    Snapshots the profile under ``miner/profile.json``, writes the bulk
    ``challenges.jsonl`` (declared as a sha256 blob in the manifest, not meant
    for git), writes ``manifest.json``, and updates ``_index.json``. Re-running
    with the same inputs lands in the same content-addressed dir (idempotent).
    """
    repo_path = Path(repo_path)
    artifacts_root = Path(artifacts_root)
    created_at = created_at or datetime.now(timezone.utc).isoformat()

    # Serialize once so the blob hash matches exactly what we write.
    challenges_text = "".join(c.model_dump_json() + "\n" for c in result.challenges)
    blob_hash = sha256_text(challenges_text)
    blob_size = len(challenges_text.encode("utf-8"))

    profile_text = json.dumps(profile.model_dump(), indent=2) + "\n"
    profile_hash = sha256_text(profile_text)

    transcript_text = "\n".join(transcript) + "\n" if transcript else ""
    transcript_hash = sha256_text(transcript_text) if transcript_text else None

    try:
        submodule_path = (
            repo_path.resolve().relative_to(monorepo_root.resolve()).as_posix()
        )
    except ValueError:
        submodule_path = repo_path.name

    shas = sorted({c.commit_hash for c in result.challenges})
    source = {
        "repo": _repo_slug(repo_path),
        "submodule_path": submodule_path,
        "range": {
            "from": None,
            "to": None,
            "filter": range_filter
            or "Commits whose proof-file diff fills a hole; SHA list is the source of truth.",
        },
        "shas": shas,
    }
    miner = {
        "kind": "agent",
        "code_hash": scaffold_code_hash(miner_pkg_dir),
        "code_hash_inputs": _CODE_HASH_INPUTS,
        "code_snapshot": "miner/",
        "profile_hash": profile_hash,
        "prompt_hash": sha256_text(prompt_text),
        "scaffold_version": _git_sha(monorepo_root),
        "agent": {
            "model": model,
            "transcript_hash": transcript_hash,
            "design_run_id": None,
        },
    }
    blobs = [
        {
            "path": "challenges.jsonl",
            "hash": blob_hash,
            "size_bytes": blob_size,
            "url": None,
            "role": "challenges",
        }
    ]

    manifest, version, manifest_hash = build_manifest(
        dataset_id=f"forall/{repo_path.name}-eval",
        source=source,
        miner=miner,
        assistant=profile.proof_assistant,
        stats=_challenge_stats(result.challenges),
        blobs=blobs,
        tag=tag,
        created_at=created_at,
    )

    ddir = artifacts_root / f"{repo_path.name}-eval" / version
    (ddir / "miner").mkdir(parents=True, exist_ok=True)
    (ddir / "miner" / "profile.json").write_text(profile_text)
    (ddir / "challenges.jsonl").write_text(challenges_text)
    if transcript_text:
        (ddir / "miner" / "transcript.txt").write_text(transcript_text)
    (ddir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    update_index(artifacts_root, manifest, manifest_hash)
    logger.info(
        "Materialized dataset version %s (%d challenges)",
        version,
        len(result.challenges),
    )

    return DatasetVersion(
        version=version,
        path=ddir,
        manifest=manifest,
        manifest_hash=manifest_hash,
        n_challenges=len(result.challenges),
    )


def update_index(artifacts_root: Path, manifest: dict, manifest_hash: str) -> Path:
    """Upsert the dataset into ``_index.json`` (deduped by version path)."""
    artifacts_root = Path(artifacts_root)
    idx_path = artifacts_root / "_index.json"
    idx: dict = {
        "schema_version": SCHEMA_VERSION,
        "description": (
            "Hash-to-path index for mined eval datasets. Solver runs reference "
            "datasets by manifest content hash; this index resolves hashes to paths."
        ),
        "datasets": [],
    }
    if idx_path.exists():
        idx = json.loads(idx_path.read_text())

    repo_eval = manifest["dataset_id"].split("/", 1)[-1]
    rel_path = f"{repo_eval}/{manifest['version']}/"
    entry = {
        "manifest_hash": manifest_hash,
        "path": rel_path,
        "dataset_id": manifest["dataset_id"],
        "version": manifest["version"],
        "schema_version": manifest["schema_version"],
        "row_version": manifest["schema"]["row_version"],
        "created_at": manifest["created_at"],
    }
    datasets: list[dict] = [
        d for d in idx.get("datasets", []) if d.get("path") != rel_path
    ]
    datasets.append(entry)
    datasets.sort(key=lambda d: d.get("path", ""))
    idx["datasets"] = datasets
    idx_path.write_text(json.dumps(idx, indent=2) + "\n")
    return idx_path
