"""Tests for experiments/summary.py.

Writes a tiny synthetic JSONL with 4 rows across 2 deletion_sizes (all
mode="baseline") and exercises the CLI via subprocess, then asserts the JSON
summary contains the expected groups with correct pass_rate and a finite
Pearson r.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).parent


def _write_synthetic_jsonl(path: Path) -> None:
    """3+ deletion_sizes are needed for Pearson r to be computed."""
    rows = [
        # deletion_size=3: 2/2 pass → pass_rate 1.0
        {
            "challenge_id": "c-a",
            "declaration": "foo",
            "deletion_size": 3,
            "condition": "B",
            "verdict": "PASS",
            "inference_time_s": 1.0,
            "output_tokens": 100,
            "mode": "baseline",
            "llm_metrics": {
                "tactic_count": 5, "automation_ratio": 0.4, "unique_tactic_types": 3,
                "max_bullet_depth": 0, "proof_chars": 50, "proof_lines": 3,
                "ends_with_admitted": False,
                "vo_bytes": 1000, "compile_time_s": 0.5, "n_assumptions": 0,
            },
            "human_metrics": {
                "tactic_count": 5, "automation_ratio": 0.4, "unique_tactic_types": 3,
                "max_bullet_depth": 0, "proof_chars": 50, "proof_lines": 3,
                "ends_with_admitted": False,
                "vo_bytes": 1000, "compile_time_s": 0.5, "n_assumptions": 0,
            },
            "normalized_edit_distance": 0.0,
        },
        {
            "challenge_id": "c-b",
            "declaration": "bar",
            "deletion_size": 3,
            "condition": "B",
            "verdict": "PASS",
            "inference_time_s": 2.0,
            "output_tokens": 200,
            "mode": "baseline",
            "llm_metrics": {
                "tactic_count": 4, "automation_ratio": 0.25, "unique_tactic_types": 2,
                "max_bullet_depth": 0, "proof_chars": 40, "proof_lines": 2,
                "ends_with_admitted": False,
                "vo_bytes": 800, "compile_time_s": 0.4, "n_assumptions": 0,
            },
        },
        # deletion_size=7: 1/2 pass → pass_rate 0.5
        {
            "challenge_id": "c-c",
            "declaration": "baz",
            "deletion_size": 7,
            "condition": "B",
            "verdict": "PASS",
            "inference_time_s": 3.0,
            "output_tokens": 300,
            "mode": "baseline",
            "llm_metrics": {
                "tactic_count": 6, "automation_ratio": 0.5, "unique_tactic_types": 4,
                "max_bullet_depth": 1, "proof_chars": 70, "proof_lines": 4,
                "ends_with_admitted": False,
                "vo_bytes": 1200, "compile_time_s": 0.7, "n_assumptions": 1,
            },
        },
        {
            "challenge_id": "c-d",
            "declaration": "qux",
            "deletion_size": 7,
            "condition": "B",
            "verdict": "FAIL",
            "inference_time_s": 4.0,
            "output_tokens": 400,
            "mode": "baseline",
        },
        # deletion_size=15: 0/1 pass → pass_rate 0.0
        {
            "challenge_id": "c-e",
            "declaration": "quux",
            "deletion_size": 15,
            "condition": "B",
            "verdict": "FAIL",
            "inference_time_s": 5.0,
            "output_tokens": 500,
            "mode": "baseline",
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_summary_cli(tmp_path: Path) -> None:
    tmpfile = tmp_path / "runs.jsonl"
    _write_synthetic_jsonl(tmpfile)

    out_json = tmp_path / "summary.json"
    out_md = tmp_path / "summary.md"

    result = subprocess.run(
        [
            sys.executable, str(HERE / "summary.py"),
            "--inputs", str(tmpfile),
            "--out", str(out_json),
            "--markdown", str(out_md),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"summary.py failed: {result.stderr}"

    summary = json.loads(out_json.read_text())

    # n_total_runs should be 5
    assert summary["n_total_runs"] == 5

    # Check groups: 3 (mode, deletion_size) groups expected
    groups = {(g["mode"], g["deletion_size"]): g for g in summary["groups"]}
    assert ("baseline", 3) in groups
    assert ("baseline", 7) in groups
    assert ("baseline", 15) in groups

    # Pass rates
    assert groups[("baseline", 3)]["n_total"] == 2
    assert groups[("baseline", 3)]["n_pass"] == 2
    assert groups[("baseline", 3)]["pass_rate"] == 1.0

    assert groups[("baseline", 7)]["n_total"] == 2
    assert groups[("baseline", 7)]["n_pass"] == 1
    assert groups[("baseline", 7)]["pass_rate"] == 0.5

    assert groups[("baseline", 15)]["n_total"] == 1
    assert groups[("baseline", 15)]["n_pass"] == 0
    assert groups[("baseline", 15)]["pass_rate"] == 0.0

    # Extended metrics only computed where llm_metrics exist
    g3 = groups[("baseline", 3)]
    assert g3["mean_vo_bytes"] is not None
    # (1000 + 800) / 2 = 900
    assert abs(g3["mean_vo_bytes"] - 900.0) < 1e-6
    # Only the first record has human_metrics.vo_bytes, ratio = 1000/1000 = 1.0
    assert g3["mean_vo_bytes_human_ratio"] is not None
    assert abs(g3["mean_vo_bytes_human_ratio"] - 1.0) < 1e-6
    assert g3["mean_normalized_edit_distance"] == 0.0

    # Pearson r: with deletion_sizes [3, 7, 15] and pass_rates [1.0, 0.5, 0.0]
    # correlation should be strongly negative and finite.
    faith = summary["faithfulness_correlation"]
    assert "baseline" in faith
    r = faith["baseline"]
    assert r is not None
    assert math.isfinite(r)
    assert r < 0, f"expected negative correlation, got {r}"

    # Markdown file written and contains the mode header.
    md = out_md.read_text()
    assert "mode = `baseline`" in md
    assert "Faithfulness correlation" in md


def test_summary_no_inputs(tmp_path: Path) -> None:
    """Glob matches nothing → clean error exit code 1, no crash."""
    result = subprocess.run(
        [
            sys.executable, str(HERE / "summary.py"),
            "--inputs", str(tmp_path / "does_not_exist_*.jsonl"),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "no input files" in result.stderr.lower()


def test_summary_help() -> None:
    result = subprocess.run(
        [sys.executable, str(HERE / "summary.py"), "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "--inputs" in result.stdout
    assert "--out" in result.stdout
    assert "--markdown" in result.stdout
