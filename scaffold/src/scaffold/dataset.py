"""Dataset versioning — write a mined eval as a manifest-schema version dir.

Implements ``artifacts/MANIFEST_SCHEMA.md``: every mined dataset lives at
``artifacts/<repo>-eval/<tag>-<short_hash>/`` and ships a ``manifest.json``
(provenance + identity + static stats), the declarative miner snapshot under
``miner/`` (the ``RepoProfile`` that produced it), and the bulk
``challenges.jsonl`` declared as a sha256 blob.

One dataset (= one run, agentic or handcrafted) owns exactly one profile, and
that profile lives once, at ``<version>/miner/profile.json`` — co-located with
its manifest and challenges. The version id is **content-addressed**:
``<short_hash>`` is the first 8 hex of a hash over the manifest with
``version``/``created_at`` removed, so the same profile + challenges land in the
same dir (idempotent) while a different run produces a sibling dir instead of
overwriting. ``_index.json`` maps manifest hash -> path for solver runs.

The blessed ``artifacts/<repo>-eval/profile.json`` that ``scaffold mine-all``
reads is a relative **symlink** into one version's ``miner/profile.json`` (see
``promote_profile`` / CLI ``--promote``) — a pointer, not a copy, so the profile
still exists exactly once.
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
# HuggingFace namespace these datasets publish under (`<HF_ORG>/<repo>-eval`).
# Historical manifests carry the legacy `forall/...` id and are left immutable
# (their id is content-addressed); new mines use the correct org going forward.
HF_ORG = "for-all-dev"
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


def _monorepo_root(repo_path: Path) -> Path:
    """Find the outer (git-history-evals) repo root that owns ``artifacts/``.

    ``repo_path`` (e.g. ``data/fiat-crypto``) is a git **submodule** with its own
    ``.git``, so ``rev-parse --show-toplevel`` on it returns the *submodule* root,
    not the monorepo — which would scatter datasets inside the submodule tree.
    Use ``--show-superproject-working-tree`` (the submodule's parent repo); fall
    back to the CWD's toplevel (we run from inside the monorepo), then to
    ``repo_path``'s grandparent.
    """
    sup = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--show-superproject-working-tree"],
        capture_output=True,
        text=True,
    )
    if sup.returncode == 0 and sup.stdout.strip():
        return Path(sup.stdout.strip())
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if top.returncode == 0 and top.stdout.strip():
        return Path(top.stdout.strip())
    return repo_path.resolve().parents[1]


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


def promote_profile(dv: DatasetVersion) -> Path:
    """Bless ``dv`` as the canonical dataset for its repo via a relative symlink.

    Points ``<repo>-eval/profile.json`` at this version's
    ``<version>/miner/profile.json``. There is no duplicated profile content:
    the profile lives exactly once (inside its dataset dir); the flat path that
    ``mine-all`` reads is just a symlink into the blessed dataset. Re-promoting
    another version repoints the link. Returns the symlink path.
    """
    blessed = dv.path.parent / "profile.json"
    target = Path(dv.version) / "miner" / "profile.json"
    # Atomic swap via a temporary symlink to avoid a TOCTOU race between
    # unlink() and symlink_to().
    tmp = blessed.with_suffix(".json.tmp")
    tmp.symlink_to(target)
    tmp.rename(blessed)
    return blessed


def materialize_dataset_version(
    *,
    profile: RepoProfile,
    result: MiningResult,
    repo_path: Path,
    artifacts_root: Path,
    monorepo_root: Path,
    miner_pkg_dir: Path,
    model: str = "",
    prompt_text: str | None = None,
    tag: str,
    miner_kind: str = "agent",
    transcript: list[str] | None = None,
    range_filter: str = "",
    created_at: str | None = None,
    curation_stats: dict | None = None,
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
        "kind": miner_kind,
        "code_hash": scaffold_code_hash(miner_pkg_dir),
        "code_hash_inputs": _CODE_HASH_INPUTS,
        "code_snapshot": "miner/",
        "profile_hash": profile_hash,
        "prompt_hash": sha256_text(prompt_text) if prompt_text else None,
        "scaffold_version": _git_sha(monorepo_root),
        "agent": {
            "model": model,
            "transcript_hash": transcript_hash,
            "design_run_id": None,
        }
        if miner_kind == "agent"
        else None,
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

    stats = _challenge_stats(result.challenges)
    if curation_stats:
        stats["curation"] = curation_stats

    manifest, version, manifest_hash = build_manifest(
        dataset_id=f"{HF_ORG}/{repo_path.name}-eval",
        source=source,
        miner=miner,
        assistant=profile.proof_assistant,
        stats=stats,
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


def mine_and_materialize(
    *,
    profile: RepoProfile,
    repo_path: Path,
    tag: str,
    miner_kind: str = "agent",
    artifacts_root: Path | None = None,
    model: str = "",
    prompt_text: str | None = None,
    transcript: list[str] | None = None,
    curate: bool = False,
    tier1_model: str = "",
    tier2_model: str = "",
) -> DatasetVersion:
    """Mine `repo_path` full-history with `profile` and materialize a version dir.

    When ``curate=True``, runs an LLM curation pass between mining and
    materialization.  Rejected challenges are excluded; borderline challenges
    are included with their curation annotation.
    """
    from scaffold.analyzers import ProfileAnalyzer
    from scaffold.git_walker import mine_repo

    repo_path = Path(repo_path)
    monorepo_root = _monorepo_root(repo_path)
    artifacts_root = (
        Path(artifacts_root) if artifacts_root else (monorepo_root / "artifacts")
    )
    miner_pkg_dir = Path(__file__).resolve().parent  # .../src/scaffold
    analyzer = ProfileAnalyzer(profile.compiled())
    mined = mine_repo(repo_path, repo_path.name, analyzer)

    curation_stats: dict | None = None
    if curate and mined.challenges:
        from scaffold.curator import (
            DEFAULT_TIER1_MODEL,
            DEFAULT_TIER2_MODEL,
            apply_curation,
            curate_challenges_sync,
        )

        t1 = tier1_model or DEFAULT_TIER1_MODEL
        t2 = tier2_model or DEFAULT_TIER2_MODEL
        results = curate_challenges_sync(
            mined.challenges, tier1_model=t1, tier2_model=t2
        )
        accepted, rejected, borderline = apply_curation(mined.challenges, results)
        curation_stats = {
            "n_before_curation": len(mined.challenges),
            "n_accepted": len(accepted),
            "n_rejected": len(rejected),
            "n_borderline": len(borderline),
            "tier1_model": t1,
            "tier2_model": t2,
        }
        logger.info(
            "Curation: %d -> %d challenges (%d rejected, %d borderline)",
            len(mined.challenges),
            len(accepted) + len(borderline),
            len(rejected),
            len(borderline),
        )
        # Replace mined challenges with curated set (accepted + borderline)
        mined = mined.model_copy(
            update={
                "challenges": accepted + borderline,
                "total_challenges": len(accepted) + len(borderline),
            }
        )

    return materialize_dataset_version(
        profile=profile,
        result=mined,
        repo_path=repo_path,
        artifacts_root=artifacts_root,
        monorepo_root=monorepo_root,
        miner_pkg_dir=miner_pkg_dir,
        model=model,
        prompt_text=prompt_text,
        tag=tag,
        miner_kind=miner_kind,
        transcript=transcript,
        curation_stats=curation_stats,
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
