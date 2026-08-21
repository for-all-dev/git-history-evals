"""Tests for pipeline/sample_paired.py: the shared-challenge_id easy/hard sample.

Exercises the module directly (via `root=`/monkeypatched `ROOT`) against a small synthetic
artifact tree, plus one subprocess run to check cross-process determinism under a fixed seed
(mirroring test_sample_reproducibility.py's convention for sample_disjoint.py).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_DIR = REPO_ROOT / "pipeline"

# sample_paired.py is a standalone script outside any package -- load it by path so tests
# don't need pipeline/ on sys.path for the whole session.
_spec = importlib.util.spec_from_file_location(
    "sample_paired", PIPELINE_DIR / "sample_paired.py"
)
assert _spec is not None and _spec.loader is not None
sample_paired = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sample_paired)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _record(cid: str, text: str) -> dict:
    return {
        "challenge_id": cid,
        "challenge_file_content": text,
        "solution_file_content": text + " solved",
        "task_id": f"task_{cid}",
        "file_path": f"F/{cid}.lean",
        "proof_assistant": "lean",
    }


@pytest.fixture
def fixture_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch tree shaped like the real repo root: registry_all.tsv + both artifact trees.

    repo_a: 3 challenge_ids shared by both modes (leaves text differs from whole text, as it
            does in reality -- same problem, different holing) -- one of those 3 has DUPLICATE
            text in the leaves pool under a different id, to prove text dedup still applies.
    repo_b: 2 shared ids, plus one id present only in `leaves` (validation-dropped in `whole`,
            as the real corpora do) to prove the intersection excludes unpaired ids.
    """
    root = tmp_path / "root"
    (root / "pipeline").mkdir(parents=True)
    (root / "pipeline" / "registry_all.tsv").write_text(
        "repo_a\tdata/lean/repo_a\nrepo_b\tdata/lean/repo_b\n"
    )

    leaves = [
        _record("a1", "repo_a leaves challenge 1"),
        _record("a2", "repo_a leaves challenge 2"),
        _record("a3", "repo_a leaves challenge 3"),
        # duplicate TEXT under a different id -- must be deduped out of the leaves pool
        _record("a1dup", "repo_a leaves challenge 1"),
    ]
    _write_jsonl(root / "artifacts/lean-ablate/repo_a/challenges.jsonl", leaves)
    whole = [
        _record("a1", "repo_a whole challenge 1"),
        _record("a2", "repo_a whole challenge 2"),
        _record("a3", "repo_a whole challenge 3"),
    ]
    _write_jsonl(root / "artifacts/lean-ablate-whole/repo_a/challenges.jsonl", whole)

    _write_jsonl(
        root / "artifacts/lean-ablate/repo_b/challenges.jsonl",
        [
            _record("b1", "repo_b leaves challenge 1"),
            _record("b2", "repo_b leaves challenge 2"),
            _record("b3-leaves-only", "repo_b leaves challenge 3 (unpaired)"),
        ],
    )
    _write_jsonl(
        root / "artifacts/lean-ablate-whole/repo_b/challenges.jsonl",
        [
            _record("b1", "repo_b whole challenge 1"),
            _record("b2", "repo_b whole challenge 2"),
        ],
    )

    monkeypatch.setattr(sample_paired, "ROOT", root)
    return root


def test_load_pool_dedups_by_text(fixture_root: Path) -> None:
    pool = sample_paired.load_pool("leaves", root=fixture_root)
    # a1dup shares text with a1 -- one of the two must have been dropped
    assert len(pool["repo_a"]) == 3
    ids = set(pool["repo_a"])
    assert ids == {"a1", "a2", "a3"} or ids == {"a1dup", "a2", "a3"}
    for line in pool["repo_a"].values():
        assert json.loads(line)["sample_mode"] == "leaves"


def test_intersection_excludes_unpaired_ids(fixture_root: Path) -> None:
    easy_pool = sample_paired.load_pool("leaves", root=fixture_root)
    hard_pool = sample_paired.load_pool("whole", root=fixture_root)
    pairs = sample_paired.intersect(easy_pool, hard_pool)
    assert set(pairs["repo_a"]) == {"a1", "a2", "a3"}
    # b3-leaves-only has no `whole` counterpart -- must not appear in the pairing
    assert set(pairs["repo_b"]) == {"b1", "b2"}


def test_main_emits_paired_dirs_with_equal_challenge_id_sets(fixture_root: Path) -> None:
    out_dir = fixture_root / "out"
    sample_paired.main([str(out_dir), "5", "--seed", "42"])

    easy_sample = [json.loads(line) for line in (out_dir / "easy" / "sample.jsonl").open()]
    hard_sample = [json.loads(line) for line in (out_dir / "hard" / "sample.jsonl").open()]

    easy_ids = {r["challenge_id"] for r in easy_sample}
    hard_ids = {r["challenge_id"] for r in hard_sample}
    assert easy_ids == hard_ids
    assert easy_ids  # non-empty
    # unpaired id never selected
    assert "b3-leaves-only" not in easy_ids

    # each mode carries its own tag, and the tag matches eval_sample.sh's --mode vocabulary
    assert {r["sample_mode"] for r in easy_sample} == {"leaves"}
    assert {r["sample_mode"] for r in hard_sample} == {"whole"}

    # the two files disagree on TEXT for every shared id (leaves vs whole holing differs) --
    # pairing is on challenge_id, not on byte-identical content
    easy_by_id = {r["challenge_id"]: r["challenge_file_content"] for r in easy_sample}
    hard_by_id = {r["challenge_id"]: r["challenge_file_content"] for r in hard_sample}
    for cid in easy_ids:
        assert easy_by_id[cid] != hard_by_id[cid]


def test_main_deterministic_under_fixed_seed(fixture_root: Path) -> None:
    out1 = fixture_root / "out1"
    out2 = fixture_root / "out2"
    sample_paired.main([str(out1), "3", "--seed", "7"])
    sample_paired.main([str(out2), "3", "--seed", "7"])
    for tag in ("easy", "hard"):
        assert (out1 / tag / "sample.jsonl").read_bytes() == (
            out2 / tag / "sample.jsonl"
        ).read_bytes()
        assert (out1 / tag / "manifest.json").read_text() == (
            out2 / tag / "manifest.json"
        ).read_text()


def test_main_different_seed_can_differ(fixture_root: Path) -> None:
    out1 = fixture_root / "out1"
    out2 = fixture_root / "out2"
    sample_paired.main([str(out1), "2", "--seed", "1"])
    sample_paired.main([str(out2), "2", "--seed", "2"])
    ids1 = {json.loads(line)["challenge_id"] for line in (out1 / "easy" / "sample.jsonl").open()}
    ids2 = {json.loads(line)["challenge_id"] for line in (out2 / "easy" / "sample.jsonl").open()}
    # both still pair correctly regardless of which ids the seed happened to pick
    hard_ids2 = {
        json.loads(line)["challenge_id"] for line in (out2 / "hard" / "sample.jsonl").open()
    }
    assert ids2 == hard_ids2
    # not asserting ids1 != ids2 (small pool -- a coincidental match is possible); the
    # meaningful property is per-seed determinism + pairing, checked above and in the
    # fixed-seed test.
    assert ids1 and ids2


def test_exclude_drops_ids_by_text(fixture_root: Path) -> None:
    # First sample everything from repo_a.
    first = fixture_root / "first"
    sample_paired.main([str(first), "10", "--seed", "42"])
    excl = first / "easy" / "sample.jsonl"

    second = fixture_root / "second"
    sample_paired.main([str(second), "10", "--seed", "42", "--exclude", str(excl)])
    remaining = [json.loads(line) for line in (second / "easy" / "sample.jsonl").open()]
    # everything paired was already excluded (small fixture), so nothing is left to sample
    assert remaining == []


def test_subprocess_reproducibility_across_processes(fixture_root: Path) -> None:
    """Same seed -> byte-identical output across two independent Python processes."""
    out1 = fixture_root / "sub_out1"
    out2 = fixture_root / "sub_out2"
    env = {"PIPELINE_SAMPLE_ROOT": str(fixture_root), "PATH": "/usr/bin:/bin"}
    for out in (out1, out2):
        result = subprocess.run(
            [sys.executable, str(PIPELINE_DIR / "sample_paired.py"), str(out), "4", "--seed", "9"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr

    for tag in ("easy", "hard"):
        assert (out1 / tag / "sample.jsonl").read_bytes() == (
            out2 / tag / "sample.jsonl"
        ).read_bytes()
