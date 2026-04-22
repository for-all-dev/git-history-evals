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
    faith = summary["faithfulness_by_mode"]
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


# ── Drift + baseline-vs-agent fixture (issue #25) ─────────────────────────────
#
# Fixture geometry: 2 SHAs × 3 deletion sizes (3, 7, 15) × 2 modes
# (baseline, agent) = 12 rows, all with matching llm + human ProofMetrics so
# every ratio and diff is well-defined. Values are deliberately spread so
# baseline and agent differ at every deletion_size.

def _proof_metrics(
    *, vo_bytes: int, compile_time_s: float, n_assumptions: int,
    proof_chars: int, proof_lines: int, tactic_count: int,
) -> dict:
    return {
        "tactic_count": tactic_count,
        "automation_ratio": 0.3,
        "unique_tactic_types": 2,
        "max_bullet_depth": 0,
        "proof_chars": proof_chars,
        "proof_lines": proof_lines,
        "ends_with_admitted": False,
        "vo_bytes": vo_bytes,
        "compile_time_s": compile_time_s,
        "n_assumptions": n_assumptions,
    }


def _fixture_row(
    *, sha: str, dsize: int, mode: str, verdict: str,
    llm: dict, human: dict, agent_n_turns: int | None = None,
    normalized_edit_distance: float = 0.25,
) -> dict:
    row = {
        "challenge_id": f"{sha}-d{dsize}-{mode}",
        "declaration":  f"decl_{sha}_{dsize}",
        "deletion_size": dsize,
        "condition": "B",
        "verdict": verdict,
        "inference_time_s": 1.0,
        "output_tokens": 100,
        "mode": mode,
        "llm_metrics": llm,
        "human_metrics": human,
        "normalized_edit_distance": normalized_edit_distance,
    }
    if agent_n_turns is not None:
        row["agent_n_turns"] = agent_n_turns
    return row


def _write_full_fixture(path: Path) -> None:
    """2 SHAs × 3 deletion_sizes × 2 modes fixture with varied drift signals."""
    rows: list[dict] = []
    shas = ["sha1", "sha2"]
    dsizes = [3, 7, 15]

    # Baseline: llm = human exactly (ratios = 1.0, diffs = 0) at dsize=3,
    # then progressively drifts wider as deletion_size grows (ratios > 1,
    # diffs > 0). Pass rate also degrades across deletion sizes.
    baseline_pass_by_d = {3: True, 7: True, 15: False}
    # (vo_ratio, compile_ratio, n_assum_diff, pchars_ratio, plines_ratio, tac_ratio)
    baseline_drift = {
        3:  (1.0, 1.0, 0, 1.0, 1.0, 1.0),
        7:  (1.2, 1.5, 1, 1.1, 1.1, 1.25),
        15: (1.5, 2.0, 2, 1.2, 1.5, 1.5),
    }
    # Agent: mostly passes, tighter drift (closer to 1.0) and more n_turns
    # at larger deletion sizes.
    agent_pass_by_d = {3: True, 7: True, 15: True}
    agent_drift = {
        3:  (1.1, 1.1, 0, 1.05, 1.0, 1.0),
        7:  (1.0, 1.2, 0, 1.0,  1.0, 1.1),
        15: (1.2, 1.5, 1, 1.1,  1.2, 1.25),
    }
    agent_turns_by_d = {3: 2, 7: 4, 15: 6}

    for sha in shas:
        for dsize in dsizes:
            # Baseline row.
            vor, ctr, nad, pcr, plr, tcr = baseline_drift[dsize]
            human = _proof_metrics(
                vo_bytes=1000, compile_time_s=1.0, n_assumptions=0,
                proof_chars=100, proof_lines=10, tactic_count=4,
            )
            llm = _proof_metrics(
                vo_bytes=int(round(1000 * vor)),
                compile_time_s=1.0 * ctr,
                n_assumptions=0 + nad,
                proof_chars=int(round(100 * pcr)),
                proof_lines=int(round(10 * plr)),
                tactic_count=int(round(4 * tcr)),
            )
            rows.append(_fixture_row(
                sha=sha, dsize=dsize, mode="baseline",
                verdict="PASS" if baseline_pass_by_d[dsize] else "FAIL",
                llm=llm, human=human,
            ))

            # Agent row.
            vor, ctr, nad, pcr, plr, tcr = agent_drift[dsize]
            human_a = _proof_metrics(
                vo_bytes=1000, compile_time_s=1.0, n_assumptions=0,
                proof_chars=100, proof_lines=10, tactic_count=4,
            )
            llm_a = _proof_metrics(
                vo_bytes=int(round(1000 * vor)),
                compile_time_s=1.0 * ctr,
                n_assumptions=0 + nad,
                proof_chars=int(round(100 * pcr)),
                proof_lines=int(round(10 * plr)),
                tactic_count=int(round(4 * tcr)),
            )
            rows.append(_fixture_row(
                sha=sha, dsize=dsize, mode="agent",
                verdict="PASS" if agent_pass_by_d[dsize] else "FAIL",
                llm=llm_a, human=human_a,
                agent_n_turns=agent_turns_by_d[dsize],
                normalized_edit_distance=0.1,
            ))

    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_summary_drift_and_cross_mode(tmp_path: Path) -> None:
    tmpfile = tmp_path / "runs.jsonl"
    _write_full_fixture(tmpfile)

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

    # Six (mode, deletion_size) groups (2 modes × 3 sizes), 12 total runs.
    assert summary["n_total_runs"] == 12
    groups = {(g["mode"], g["deletion_size"]): g for g in summary["groups"]}
    assert len(groups) == 6

    # ── Baseline drift metrics (averaged across 2 SHAs per deletion_size).
    b3 = groups[("baseline", 3)]
    assert abs(b3["mean_vo_bytes_ratio"] - 1.0) < 1e-6
    assert abs(b3["mean_compile_time_ratio"] - 1.0) < 1e-6
    assert abs(b3["mean_n_assumptions_diff"] - 0.0) < 1e-6
    assert abs(b3["mean_proof_chars_ratio"] - 1.0) < 1e-6
    assert abs(b3["mean_proof_lines_ratio"] - 1.0) < 1e-6
    assert abs(b3["mean_tactic_count_ratio"] - 1.0) < 1e-6
    # Baseline rows never populate agent turns.
    assert b3["mean_agent_n_turns"] is None

    b7 = groups[("baseline", 7)]
    assert abs(b7["mean_vo_bytes_ratio"] - 1.2) < 1e-6
    assert abs(b7["mean_compile_time_ratio"] - 1.5) < 1e-6
    assert abs(b7["mean_n_assumptions_diff"] - 1.0) < 1e-6
    # proof_chars: 110/100 for each SHA at dsize=7, mean = 1.1
    assert abs(b7["mean_proof_chars_ratio"] - 1.1) < 1e-6
    # tactic_count: round(4*1.25)=5, 5/4 = 1.25
    assert abs(b7["mean_tactic_count_ratio"] - 1.25) < 1e-6

    b15 = groups[("baseline", 15)]
    assert abs(b15["mean_vo_bytes_ratio"] - 1.5) < 1e-6
    assert abs(b15["mean_compile_time_ratio"] - 2.0) < 1e-6
    assert abs(b15["mean_n_assumptions_diff"] - 2.0) < 1e-6

    # ── Agent drift metrics + agent_n_turns populated.
    a3 = groups[("agent", 3)]
    assert abs(a3["mean_vo_bytes_ratio"] - 1.1) < 1e-6
    assert a3["mean_agent_n_turns"] is not None
    assert abs(a3["mean_agent_n_turns"] - 2.0) < 1e-6
    a7 = groups[("agent", 7)]
    assert abs(a7["mean_agent_n_turns"] - 4.0) < 1e-6
    a15 = groups[("agent", 15)]
    assert abs(a15["mean_agent_n_turns"] - 6.0) < 1e-6

    # ── Pass rates: baseline {1.0, 1.0, 0.0}, agent {1.0, 1.0, 1.0}.
    assert groups[("baseline", 3)]["pass_rate"] == 1.0
    assert groups[("baseline", 7)]["pass_rate"] == 1.0
    assert groups[("baseline", 15)]["pass_rate"] == 0.0
    assert groups[("agent", 3)]["pass_rate"] == 1.0
    assert groups[("agent", 7)]["pass_rate"] == 1.0
    assert groups[("agent", 15)]["pass_rate"] == 1.0

    # ── baseline_vs_agent: one row per shared deletion_size.
    cmp_rows = summary["baseline_vs_agent"]
    assert [r["deletion_size"] for r in cmp_rows] == [3, 7, 15]
    cmp_by_d = {r["deletion_size"]: r for r in cmp_rows}
    # dsize=15: agent passes all, baseline fails all → Δpass_rate = 1.0.
    assert abs(cmp_by_d[15]["delta_pass_rate"] - 1.0) < 1e-6
    assert abs(cmp_by_d[3]["delta_pass_rate"] - 0.0) < 1e-6
    # Δnorm_edit_dist: agent 0.1 − baseline 0.25 = -0.15.
    assert abs(cmp_by_d[3]["delta_normalized_edit_distance"] - (-0.15)) < 1e-6
    # Δmean_agent_n_turns reports the agent-side turn count directly.
    assert abs(cmp_by_d[7]["delta_mean_agent_n_turns"] - 4.0) < 1e-6

    # ── drift_faithfulness: per-metric Pearson r vs deletion_size, per mode.
    drift_faith = summary["drift_faithfulness"]
    # Keys we required on each row are present.
    for metric in (
        "mean_vo_bytes_ratio", "mean_compile_time_ratio",
        "mean_n_assumptions_diff", "mean_proof_chars_ratio",
        "mean_proof_lines_ratio", "mean_tactic_count_ratio",
        "pass_rate", "mean_normalized_edit_distance",
    ):
        assert metric in drift_faith, f"missing {metric} in drift_faithfulness"
        assert "baseline" in drift_faith[metric]
        assert "agent" in drift_faith[metric]

    # Baseline vo_bytes ratio rises monotonically with deletion_size → r ~ +1.
    r_vo_baseline = drift_faith["mean_vo_bytes_ratio"]["baseline"]
    assert r_vo_baseline is not None
    assert math.isfinite(r_vo_baseline)
    assert r_vo_baseline > 0.9, f"expected strong positive r, got {r_vo_baseline}"

    # Baseline pass_rate drops with deletion_size → r < 0.
    r_pass_baseline = drift_faith["pass_rate"]["baseline"]
    assert r_pass_baseline is not None
    assert r_pass_baseline < 0

    # ── Markdown: three tables + drift-faithfulness block.
    md = out_md.read_text()
    assert "mode = `baseline`" in md
    assert "mode = `agent`" in md
    assert "baseline vs agent" in md
    # Drift columns surfaced in the baseline / agent tables.
    assert "vo_bytes_ratio" in md
    assert "n_assumptions_diff" in md
    # Agent-only column present at least in the agent table.
    assert "mean_agent_n_turns" in md
    # Three distinct header bars → three tables rendered.
    assert md.count("| deletion_size |") >= 2
    assert "Δpass_rate" in md
    # Drift faithfulness block.
    assert "drift faithfulness" in md
