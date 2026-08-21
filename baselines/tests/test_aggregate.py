"""Tests for the cross-run micro/macro aggregator (apply_ablate.aggregate)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from apply_ablate.aggregate import (
    ManifestEntry,
    aggregate,
    load_manifest,
    to_json,
    to_markdown,
)
from apply_ablate.aggregate import app as aggregate_app

runner = CliRunner()


def _row(**kw) -> dict:
    """A minimal synthetic SolveResult row; flags default to a plain FAIL."""
    base = {
        "task_id": "t",
        "assistant": "lean",
        "file_path": "A.lean",
        "succeeded": False,
        "gave_up": False,
        "malformed_challenge": False,
        "trivial": False,
        "context_exceeded": False,
        "tampered": False,
        "turn_limit": False,
        "dry_run": False,
        "error": None,
        "max_turns": 30,
    }
    base.update(kw)
    return base


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> Path:
    """A synthetic two-repo, two-mode results corpus.

    repo `big` (skews the pool: 8 rows) has a LOW pass rate (1/8 scorable).
    repo `small` (2 rows) has a HIGH pass rate (2/2 scorable) — a macro average must
    weight `small` equally with `big`, unlike a pooled (micro) average.
    One malformed + one trivial + one context_exceeded row check the scorable formula.
    """
    big_rows = [_row(succeeded=(i == 0)) for i in range(8)]
    small_rows = [_row(succeeded=True) for _ in range(2)]
    edge_rows = [
        _row(malformed_challenge=True),
        _row(trivial=True),
        _row(context_exceeded=True),
    ]

    _write_jsonl(tmp_path / "res_big.jsonl", big_rows)
    _write_jsonl(tmp_path / "res_small.jsonl", small_rows)
    _write_jsonl(tmp_path / "res_edge.jsonl", edge_rows)

    manifest = [
        {"path": "res_big.jsonl", "model": "m1", "mode": "leaves"},
        {"path": "res_small.jsonl", "model": "m1", "mode": "leaves"},
        {"path": "res_edge.jsonl", "model": "m1", "mode": "leaves", "repo": "edge"},
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_micro_vs_macro_diverge(tmp_path: Path):
    manifest_path = _fixture(tmp_path)
    entries = load_manifest(manifest_path)
    [g] = aggregate(entries, n_boot=200, seed=0, base_dir=tmp_path)

    assert g.model == "m1"
    assert g.mode == "leaves"
    assert g.max_turns == 30  # inferred from row max_turns

    # total rows: 8 + 2 + 3 = 13; scorable excludes malformed/trivial/context_exceeded
    assert g.total == 13
    assert g.scorable == 10

    # micro: pooled pass/scorable = (1 + 2) / (8 + 2) = 3/10 = 30%
    assert g.micro_pass == 3
    assert abs(g.micro_rate - 0.3) < 1e-9

    # macro: mean of per-repo rates = mean(1/8, 2/2) = mean(0.125, 1.0) = 0.5625
    assert g.macro_n_repos == 2  # `edge` has 0 scorable rows -> excluded
    assert g.macro_rate is not None
    assert abs(g.macro_rate - 0.5625) < 1e-9

    # macro pulls the average up relative to micro, which is dominated by `big`
    assert g.macro_rate > g.micro_rate

    # bootstrap CIs are present and bracket the point estimate
    assert g.micro_ci_lo is not None and g.micro_ci_hi is not None
    assert g.micro_ci_lo <= g.micro_rate <= g.micro_ci_hi
    assert g.macro_ci_lo is not None and g.macro_ci_hi is not None
    assert g.macro_ci_lo <= g.macro_rate <= g.macro_ci_hi

    # outcome breakdown covers the full taxonomy
    assert g.outcomes["pass"] == 3
    assert g.outcomes["malformed"] == 1
    assert g.outcomes["trivial"] == 1
    assert g.outcomes["context_exceeded"] == 1
    assert g.outcomes["fail"] == 7  # the unsuccessful `big` rows (7 of 8)

    per_repo_rates = {r: v["rate"] for r, v in g.per_repo.items()}
    assert abs(per_repo_rates["big"] - 0.125) < 1e-9
    assert abs(per_repo_rates["small"] - 1.0) < 1e-9


def test_grouping_keys_on_model_mode_max_turns(tmp_path: Path):
    _write_jsonl(tmp_path / "a.jsonl", [_row(succeeded=True, max_turns=30)])
    _write_jsonl(tmp_path / "b.jsonl", [_row(succeeded=False, max_turns=50)])
    manifest = [
        {"path": "a.jsonl", "model": "m1", "mode": "leaves", "repo": "r1"},
        {"path": "b.jsonl", "model": "m1", "mode": "leaves", "repo": "r1"},
        {"path": "a.jsonl", "model": "m2", "mode": "leaves", "repo": "r1"},
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    results = aggregate(
        load_manifest(manifest_path), n_boot=50, seed=1, base_dir=tmp_path
    )
    keys = {(g.model, g.mode, g.max_turns) for g in results}
    assert keys == {("m1", "leaves", 30), ("m1", "leaves", 50), ("m2", "leaves", 30)}


def test_repo_inference_strips_res_prefix(tmp_path: Path):
    entry = ManifestEntry(path="res_evm-asm.jsonl", model="m1", mode="whole")
    from apply_ablate.aggregate import _infer_repo

    assert _infer_repo(entry.path) == "evm-asm"


def test_to_markdown_and_json_roundtrip(tmp_path: Path):
    manifest_path = _fixture(tmp_path)
    results = aggregate(
        load_manifest(manifest_path), n_boot=50, seed=0, base_dir=tmp_path
    )
    md = to_markdown(results)
    assert "micro PASS" in md
    assert "macro PASS" in md
    assert "m1" in md and "leaves" in md

    js = json.loads(to_json(results))
    assert len(js) == 1
    assert js[0]["model"] == "m1"
    assert js[0]["scorable"] == 10


def test_cli_writes_json_and_md(tmp_path: Path):
    manifest_path = _fixture(tmp_path)
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    result = runner.invoke(
        aggregate_app,
        [
            str(manifest_path),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
            "--n-boot",
            "20",
            "--seed",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_json.exists()
    assert out_md.exists()
    data = json.loads(out_json.read_text())
    assert data[0]["model"] == "m1"
