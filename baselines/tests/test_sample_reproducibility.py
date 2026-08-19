"""Test proving byte-identical sample.jsonl across separate processes with same seed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_DISJOINT = ROOT / "pipeline" / "sample_disjoint.py"


def test_sample_reproducibility(tmp_path: Path) -> None:
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"

    cmd1 = [sys.executable, str(SAMPLE_DISJOINT), str(out1), "10", "--seed", "42"]
    cmd2 = [sys.executable, str(SAMPLE_DISJOINT), str(out2), "10", "--seed", "42"]

    res1 = subprocess.run(cmd1, capture_output=True, text=True)
    assert res1.returncode == 0, f"Process 1 failed:\nSTDOUT:\n{res1.stdout}\nSTDERR:\n{res1.stderr}"

    res2 = subprocess.run(cmd2, capture_output=True, text=True)
    assert res2.returncode == 0, f"Process 2 failed:\nSTDOUT:\n{res2.stdout}\nSTDERR:\n{res2.stderr}"

    sample1 = (out1 / "sample.jsonl").read_bytes()
    sample2 = (out2 / "sample.jsonl").read_bytes()

    assert sample1 == sample2, "sample.jsonl files are not byte-identical across separate processes"

    manifest1 = json.loads((out1 / "manifest.json").read_text())
    manifest2 = json.loads((out2 / "manifest.json").read_text())

    assert manifest1 == manifest2
    for entry in manifest1:
        assert entry.get("seed") == 42
