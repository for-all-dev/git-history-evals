"""Tests for dataset versioning — manifest schema, hashing, and materialization."""

from __future__ import annotations

import json
import re
from pathlib import Path


from scaffold.dataset import (
    DatasetVersion,
    build_manifest,
    canonical_json,
    materialize_dataset_version,
    promote_profile,
    sha256_text,
)
from scaffold.models import EvalChallenge, MiningResult, ProofHole
from scaffold.profile import RepoProfile


def make_result(n: int = 3) -> MiningResult:
    """Build a synthetic MiningResult with n EvalChallenge rows."""
    chs = []
    for i in range(n):
        chs.append(
            EvalChallenge(
                task_id=f"t{i}",
                repo="x",
                proof_assistant="coq",
                commit_hash=f"sha{i % 2}",  # only 2 distinct commits on purpose
                parent_hash="p",
                commit_message="msg",
                file_path=f"src/F{i}.v",
                challenge_file_content="Admitted.",
                solution_file_content="Qed.",
                holes_filled=[
                    ProofHole(line=1, column=0, kind="admitted", proof_assistant="coq")
                ],
                diff="+" * 600,
                instructions="do it",
            )
        )
    return MiningResult(
        repo_name="x",
        proof_assistant="coq",
        total_commits_scanned=10,
        total_challenges=n,
        challenges=chs,
    )


def test_canonical_json_stable() -> None:
    """Canonical JSON sorts keys, has no spaces; equal dicts produce identical strings."""
    d1 = {"b": 1, "a": 2}
    d2 = {"a": 2, "b": 1}
    s1 = canonical_json(d1)
    s2 = canonical_json(d2)
    assert s1 == s2
    assert " " not in s1
    assert s1 == '{"a":2,"b":1}'


def test_build_manifest_content_addressed() -> None:
    """Version is content-addressed (identical with different created_at); manifest_hash differs."""
    manifest1, version1, hash1 = build_manifest(
        dataset_id="forall/test-eval",
        source={"repo": "test/repo", "submodule_path": "test"},
        miner={"kind": "agent", "code_hash": "abc123"},
        assistant="coq",
        stats={"n_challenges": 1, "n_source_commits": 1},
        blobs=[{"path": "challenges.jsonl", "hash": "xyz"}],
        tag="mytag",
        created_at="2026-01-01T00:00:00Z",
    )
    manifest2, version2, hash2 = build_manifest(
        dataset_id="forall/test-eval",
        source={"repo": "test/repo", "submodule_path": "test"},
        miner={"kind": "agent", "code_hash": "abc123"},
        assistant="coq",
        stats={"n_challenges": 1, "n_source_commits": 1},
        blobs=[{"path": "challenges.jsonl", "hash": "xyz"}],
        tag="mytag",
        created_at="2026-12-31T23:59:59Z",
    )

    # Version is identical (content-addressed)
    assert version1 == version2
    assert re.match(r"^mytag-[0-9a-f]{8}$", version1)

    # But manifest_hash differs (created_at is included in full manifest)
    assert hash1 != hash2


def test_build_manifest_version_changes_with_content() -> None:
    """Changing stats or blobs changes version; changing tag changes prefix."""
    m1, v1, _ = build_manifest(
        dataset_id="forall/test-eval",
        source={"repo": "test/repo", "submodule_path": "test"},
        miner={"kind": "agent", "code_hash": "abc123"},
        assistant="coq",
        stats={"n_challenges": 1, "n_source_commits": 1},
        blobs=[{"path": "challenges.jsonl", "hash": "xyz"}],
        tag="tag_a",
        created_at="2026-01-01T00:00:00Z",
    )
    m2, v2, _ = build_manifest(
        dataset_id="forall/test-eval",
        source={"repo": "test/repo", "submodule_path": "test"},
        miner={"kind": "agent", "code_hash": "abc123"},
        assistant="coq",
        stats={"n_challenges": 2, "n_source_commits": 1},
        blobs=[{"path": "challenges.jsonl", "hash": "xyz"}],
        tag="tag_a",
        created_at="2026-01-01T00:00:00Z",
    )
    m3, v3, _ = build_manifest(
        dataset_id="forall/test-eval",
        source={"repo": "test/repo", "submodule_path": "test"},
        miner={"kind": "agent", "code_hash": "abc123"},
        assistant="coq",
        stats={"n_challenges": 1, "n_source_commits": 1},
        blobs=[{"path": "challenges.jsonl", "hash": "xyz"}],
        tag="tag_b",
        created_at="2026-01-01T00:00:00Z",
    )

    # Different stats -> different version
    assert v1 != v2
    assert v1.startswith("tag_a-")
    assert v2.startswith("tag_a-")

    # Different tag -> different prefix
    assert v1.startswith("tag_a-")
    assert v3.startswith("tag_b-")


def test_materialize_writes_bundle(
    coq_profile: RepoProfile, tmp_git_repo: Path, tmp_path: Path
) -> None:
    """Materialize writes manifest, profile, challenges.jsonl; correct structure."""
    artifacts_root = tmp_path / "artifacts"
    miner_pkg_dir = tmp_path / "miner_pkg"
    miner_pkg_dir.mkdir()
    (miner_pkg_dir / "dummy.py").write_text("# test")

    result = make_result(3)
    dv = materialize_dataset_version(
        profile=coq_profile,
        result=result,
        repo_path=tmp_git_repo,
        artifacts_root=artifacts_root,
        monorepo_root=tmp_git_repo,
        miner_pkg_dir=miner_pkg_dir,
        model="anthropic:claude-sonnet-4-6",
        prompt_text="sys+user",
        tag="agentic_1",
        created_at="2026-01-01T00:00:00Z",
    )

    # Check basic structure
    assert dv.path.exists()
    assert (dv.path / "manifest.json").exists()
    assert (dv.path / "miner" / "profile.json").exists()
    assert (dv.path / "challenges.jsonl").exists()

    # Check challenges.jsonl has 3 lines
    lines = (dv.path / "challenges.jsonl").read_text().strip().split("\n")
    assert len(lines) == 3

    # Check manifest content
    manifest = dv.manifest
    assert manifest["stats"]["n_challenges"] == 3
    assert manifest["stats"]["n_source_commits"] == 2  # sha0 and sha1
    assert manifest["miner"]["kind"] == "agent"
    assert manifest["miner"]["agent"]["model"] == "anthropic:claude-sonnet-4-6"
    assert manifest["schema"]["assistant"] == "coq"

    # Check version format
    assert dv.version.startswith("agentic_1-")
    assert re.match(r"^agentic_1-[0-9a-f]{8}$", dv.version)


def test_materialize_updates_index(
    coq_profile: RepoProfile, tmp_git_repo: Path, tmp_path: Path
) -> None:
    """After materialize, _index.json exists with correct entry."""
    artifacts_root = tmp_path / "artifacts"
    miner_pkg_dir = tmp_path / "miner_pkg"
    miner_pkg_dir.mkdir()
    (miner_pkg_dir / "dummy.py").write_text("# test")

    result = make_result(2)
    dv = materialize_dataset_version(
        profile=coq_profile,
        result=result,
        repo_path=tmp_git_repo,
        artifacts_root=artifacts_root,
        monorepo_root=tmp_git_repo,
        miner_pkg_dir=miner_pkg_dir,
        model="test-model",
        prompt_text="prompt",
        tag="test_tag",
        created_at="2026-01-01T00:00:00Z",
    )

    idx_path = artifacts_root / "_index.json"
    assert idx_path.exists()

    idx = json.loads(idx_path.read_text())
    assert "datasets" in idx
    assert len(idx["datasets"]) == 1

    entry = idx["datasets"][0]
    assert entry["path"] == f"{tmp_git_repo.name}-eval/{dv.version}/"
    assert entry["manifest_hash"] == dv.manifest_hash


def test_materialize_idempotent(
    coq_profile: RepoProfile, tmp_git_repo: Path, tmp_path: Path
) -> None:
    """Calling materialize twice with same inputs yields same version; _index.json has one entry."""
    artifacts_root = tmp_path / "artifacts"
    miner_pkg_dir = tmp_path / "miner_pkg"
    miner_pkg_dir.mkdir()
    (miner_pkg_dir / "dummy.py").write_text("# test")

    result = make_result(2)
    created_at = "2026-01-01T00:00:00Z"

    dv1 = materialize_dataset_version(
        profile=coq_profile,
        result=result,
        repo_path=tmp_git_repo,
        artifacts_root=artifacts_root,
        monorepo_root=tmp_git_repo,
        miner_pkg_dir=miner_pkg_dir,
        model="test-model",
        prompt_text="prompt",
        tag="idempotent_tag",
        created_at=created_at,
    )

    dv2 = materialize_dataset_version(
        profile=coq_profile,
        result=result,
        repo_path=tmp_git_repo,
        artifacts_root=artifacts_root,
        monorepo_root=tmp_git_repo,
        miner_pkg_dir=miner_pkg_dir,
        model="test-model",
        prompt_text="prompt",
        tag="idempotent_tag",
        created_at=created_at,
    )

    # Same version
    assert dv1.version == dv2.version

    # _index.json has exactly one entry for this path
    idx_path = artifacts_root / "_index.json"
    idx = json.loads(idx_path.read_text())
    matching = [
        d
        for d in idx["datasets"]
        if d["path"] == f"{tmp_git_repo.name}-eval/{dv1.version}/"
    ]
    assert len(matching) == 1


def test_materialize_transcript(
    coq_profile: RepoProfile, tmp_git_repo: Path, tmp_path: Path
) -> None:
    """Transcript is written if provided; transcript_hash is non-empty; None without."""
    artifacts_root = tmp_path / "artifacts"
    miner_pkg_dir = tmp_path / "miner_pkg"
    miner_pkg_dir.mkdir()
    (miner_pkg_dir / "dummy.py").write_text("# test")

    result = make_result(1)

    # With transcript
    dv_with = materialize_dataset_version(
        profile=coq_profile,
        result=result,
        repo_path=tmp_git_repo,
        artifacts_root=artifacts_root,
        monorepo_root=tmp_git_repo,
        miner_pkg_dir=miner_pkg_dir,
        model="test-model",
        prompt_text="prompt",
        tag="with_transcript",
        transcript=["a", "b"],
        created_at="2026-01-01T00:00:00Z",
    )

    assert (dv_with.path / "miner" / "transcript.txt").exists()
    assert dv_with.manifest["miner"]["agent"]["transcript_hash"] is not None
    assert len(dv_with.manifest["miner"]["agent"]["transcript_hash"]) > 0

    # Without transcript
    dv_without = materialize_dataset_version(
        profile=coq_profile,
        result=result,
        repo_path=tmp_git_repo,
        artifacts_root=artifacts_root / "artifacts2",
        monorepo_root=tmp_git_repo,
        miner_pkg_dir=miner_pkg_dir,
        model="test-model",
        prompt_text="prompt",
        tag="without_transcript",
        created_at="2026-01-01T00:00:00Z",
    )

    assert not (dv_without.path / "miner" / "transcript.txt").exists()
    assert dv_without.manifest["miner"]["agent"]["transcript_hash"] is None


def test_challenges_blob_hash_matches(
    coq_profile: RepoProfile, tmp_git_repo: Path, tmp_path: Path
) -> None:
    """Challenges blob hash in manifest matches sha256 of written challenges.jsonl."""
    artifacts_root = tmp_path / "artifacts"
    miner_pkg_dir = tmp_path / "miner_pkg"
    miner_pkg_dir.mkdir()
    (miner_pkg_dir / "dummy.py").write_text("# test")

    result = make_result(2)
    dv = materialize_dataset_version(
        profile=coq_profile,
        result=result,
        repo_path=tmp_git_repo,
        artifacts_root=artifacts_root,
        monorepo_root=tmp_git_repo,
        miner_pkg_dir=miner_pkg_dir,
        model="test-model",
        prompt_text="prompt",
        tag="blob_test",
        created_at="2026-01-01T00:00:00Z",
    )

    # Read challenges.jsonl and recompute hash
    challenges_text = (dv.path / "challenges.jsonl").read_text()
    computed_hash = sha256_text(challenges_text)

    # Assert matches manifest
    assert dv.manifest["blobs"][0]["hash"] == computed_hash
    assert dv.manifest["blobs"][0]["role"] == "challenges"


def test_materialize_handcrafted_kind(
    coq_profile: RepoProfile, tmp_git_repo: Path, tmp_path: Path
) -> None:
    """Materialize with handcrafted kind produces correct manifest structure."""
    artifacts_root = tmp_path / "artifacts"
    miner_pkg_dir = tmp_path / "miner_pkg"
    miner_pkg_dir.mkdir()
    (miner_pkg_dir / "dummy.py").write_text("# test")

    result = make_result(2)
    dv = materialize_dataset_version(
        profile=coq_profile,
        result=result,
        repo_path=tmp_git_repo,
        artifacts_root=artifacts_root,
        monorepo_root=tmp_git_repo,
        miner_pkg_dir=miner_pkg_dir,
        tag="v1-handcrafted",
        miner_kind="handcrafted",
        created_at="2026-01-01T00:00:00Z",
    )

    # Check manifest structure for handcrafted miner
    manifest = dv.manifest
    assert manifest["miner"]["kind"] == "handcrafted"
    assert manifest["miner"]["agent"] is None
    assert manifest["miner"]["prompt_hash"] is None
    assert manifest["miner"]["code_hash"] is not None
    assert manifest["miner"]["profile_hash"] is not None
    assert manifest["miner"]["scaffold_version"] is not None


def test_mine_and_materialize_helper(
    coq_profile: RepoProfile, tmp_git_repo: Path, tmp_path: Path
) -> None:
    """mine_and_materialize helper successfully mines repo and materializes version dir."""
    from scaffold.dataset import mine_and_materialize

    dv = mine_and_materialize(
        profile=coq_profile,
        repo_path=tmp_git_repo,
        tag="v1-handcrafted",
        miner_kind="handcrafted",
        artifacts_root=tmp_path / "artifacts",
    )

    # Check version naming
    assert dv.version.startswith("v1-handcrafted-")
    assert re.match(r"^v1-handcrafted-[0-9a-f]{8}$", dv.version)

    # Check that mining found challenges
    assert dv.n_challenges >= 1

    # Check manifest and profile files exist
    assert (dv.path / "manifest.json").exists()
    assert (dv.path / "miner" / "profile.json").exists()

    # Check manifest content
    manifest = dv.manifest
    assert manifest["miner"]["kind"] == "handcrafted"
    assert manifest["miner"]["agent"] is None


def test_promote_profile_is_relative_symlink(tmp_path: Path) -> None:
    """promote_profile points <repo>-eval/profile.json at the version via a relative symlink."""
    ddir = tmp_path / "fiat-crypto-eval" / "agentic_1-abcd1234"
    (ddir / "miner").mkdir(parents=True)
    (ddir / "miner" / "profile.json").write_text('{"proof_assistant": "coq"}')
    dv = DatasetVersion(
        version="agentic_1-abcd1234",
        path=ddir,
        manifest={},
        manifest_hash="h",
        n_challenges=1,
    )

    link = promote_profile(dv)

    assert link == ddir.parent / "profile.json"
    assert link.is_symlink()
    # Relative target into the blessed dataset (no duplicated content).
    assert link.readlink() == Path("agentic_1-abcd1234/miner/profile.json")
    assert link.resolve().read_text() == '{"proof_assistant": "coq"}'


def test_promote_profile_repoints_existing(tmp_path: Path) -> None:
    """Re-promoting another version repoints the symlink rather than erroring."""
    repo_eval = tmp_path / "fiat-crypto-eval"
    for ver in ("v1-handcrafted-1111aaaa", "agentic_2-2222bbbb"):
        d = repo_eval / ver / "miner"
        d.mkdir(parents=True)
        (d / "profile.json").write_text(f'{{"v": "{ver}"}}')
    first = DatasetVersion(
        "v1-handcrafted-1111aaaa", repo_eval / "v1-handcrafted-1111aaaa", {}, "h", 1
    )
    second = DatasetVersion(
        "agentic_2-2222bbbb", repo_eval / "agentic_2-2222bbbb", {}, "h", 1
    )

    promote_profile(first)
    link = promote_profile(second)  # repoint

    assert link.readlink() == Path("agentic_2-2222bbbb/miner/profile.json")
    assert link.resolve().read_text() == '{"v": "agentic_2-2222bbbb"}'
