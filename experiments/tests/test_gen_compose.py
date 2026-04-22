"""Tests for experiments.orchestrate.gen-compose.

Exercise the CLI end-to-end with synthetic SHAs: write a compose.yml
to a tmp path, assert its shape, and (if ``docker compose`` is in PATH)
validate with ``docker compose config --quiet``.

The gen-compose.py script has a hyphen in its name, so it isn't
importable as a module via ``import``. We run it as a subprocess -- the
CLI is the supported entry point.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GEN_COMPOSE = REPO_ROOT / "experiments" / "orchestrate" / "gen-compose.py"

# A pair of real fiat-crypto SHAs whose meta.json lives under
# experiments/admitted-proofs/ (01-of_prefancy_identZ_correct &&
# 02-pull_cast_genericize_op). Using real SHAs means run_config_json
# returns non-empty slot lists, so we also assert on that shape.
SHA_A = "b9f28afec4f3dc3340a8693cdf450721af67d13b"
SHA_B = "07e016ed52f68205f851f3686c64018a0c7a262b"


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke gen-compose.py as a subprocess. Returns the completed proc."""
    return subprocess.run(
        [sys.executable, str(GEN_COMPOSE), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "experiments"),
    )


def test_cli_help_renders() -> None:
    result = _run_cli(["--help"])
    assert result.returncode == 0, result.stderr
    assert "--run-id" in result.stdout
    assert "--mode" in result.stdout
    assert "--shas" in result.stdout
    assert "--out" in result.stdout
    assert "--skip-validate" in result.stdout


def test_cli_generates_expected_shape(tmp_path: Path) -> None:
    out = tmp_path / "compose.yml"
    result = _run_cli(
        [
            "--run-id",
            "unit001",
            "--mode",
            "both",
            "--shas",
            f"{SHA_A},{SHA_B}",
            "--out",
            str(out),
            "--skip-validate",
        ]
    )
    assert result.returncode == 0, (
        f"gen-compose failed.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert out.exists(), "gen-compose did not produce the output file"
    text = out.read_text(encoding="utf-8")

    # Project name.
    assert "name: proof-eval-unit001" in text

    # One service per SHA, keyed by the 8-char prefix.
    assert "cmt-b9f28afe:" in text
    assert "cmt-07e016ed:" in text

    # Image tag matches the Dockerfile's convention.
    assert "image: fc-commit:b9f28afe" in text
    assert "image: fc-commit:07e016ed" in text

    # Container name embeds both prefix and run_id.
    assert "container_name: fc-run-b9f28afe-unit001" in text
    assert "container_name: fc-run-07e016ed-unit001" in text

    # The literal ${ANTHROPIC_API_KEY} string must be present so compose
    # does the interpolation at launch (NOT us at generation time).
    assert "${ANTHROPIC_API_KEY}" in text

    # Top-level volumes section with explicit `name:` for each SHA
    # (this is what makes re-runs accumulate into stable on-disk volumes).
    assert re.search(
        r"^volumes:\s*\n(?:.*\n)*?  results-b9f28afe:\s*\n    name: results-b9f28afe",
        text,
        re.MULTILINE,
    ), "missing or malformed results-b9f28afe volume entry"
    assert re.search(
        r"  results-07e016ed:\s*\n    name: results-07e016ed",
        text,
    ), "missing or malformed results-07e016ed volume entry"

    # YAML anchors present for dedup.
    assert "x-defaults: &defaults" in text
    assert "<<: *defaults" in text

    # Service-level volume mounts: the named volume and the read-only
    # host-experiments bind mount.
    assert '"results-b9f28afe:/results"' in text
    assert '"results-07e016ed:/results"' in text
    assert ":/work/host-experiments:ro" in text

    # Mode + run_id env vars are set on every service.
    assert 'MODE: "both"' in text
    assert 'RUN_ID: "unit001"' in text

    # RUN_CONFIG_JSON is a compact JSON object embedding the commit and
    # slot list. Pull it back out and verify the structure.
    m = re.search(r'RUN_CONFIG_JSON: "(.*)"', text)
    assert m is not None, "RUN_CONFIG_JSON not found in output"
    # The captured group is YAML-escaped (\\" and \\\\). Unescape
    # minimally: this test only relies on the backslash-quote -> quote
    # transformation since we generated the file.
    payload_raw = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
    parsed = json.loads(payload_raw)
    assert parsed["commit"] == SHA_A
    assert isinstance(parsed["slots"], list)

    # restart: "no" via the anchor.
    assert 'restart: "no"' in text

    # Command dispatch for mode=both runs baseline then agent.
    assert "eval-baseline --run-id unit001" in text
    assert "eval-agent --run-id unit001" in text
    assert "/results/unit001/baseline.jsonl" in text
    assert "/results/unit001/agent.jsonl" in text


def test_cli_mode_baseline_omits_agent(tmp_path: Path) -> None:
    out = tmp_path / "compose.yml"
    result = _run_cli(
        [
            "--run-id",
            "base",
            "--mode",
            "baseline",
            "--shas",
            SHA_A,
            "--out",
            str(out),
            "--skip-validate",
        ]
    )
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "eval-baseline" in text
    assert "eval-agent" not in text
    assert 'MODE: "baseline"' in text


def test_cli_mode_agent_omits_baseline(tmp_path: Path) -> None:
    out = tmp_path / "compose.yml"
    result = _run_cli(
        [
            "--run-id",
            "agt",
            "--mode",
            "agent",
            "--shas",
            SHA_A,
            "--out",
            str(out),
            "--skip-validate",
        ]
    )
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "eval-agent" in text
    assert "eval-baseline" not in text
    assert 'MODE: "agent"' in text


def test_cli_default_out_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When --out is omitted, the default lands under experiments/results/<run_id>/."""
    run_id = "default-out-testxyz"
    expected = (
        REPO_ROOT / "experiments" / "results" / run_id / "compose.yml"
    )
    # Clean any stale artifact.
    if expected.exists():
        expected.unlink()
    if expected.parent.exists():
        try:
            expected.parent.rmdir()
        except OSError:
            pass

    try:
        result = _run_cli(
            [
                "--run-id",
                run_id,
                "--mode",
                "baseline",
                "--shas",
                SHA_A,
                "--skip-validate",
            ]
        )
        assert result.returncode == 0, result.stderr
        assert expected.exists(), f"default output not written at {expected}"
    finally:
        if expected.exists():
            expected.unlink()
        if expected.parent.exists():
            try:
                expected.parent.rmdir()
            except OSError:
                pass


def test_cli_rejects_invalid_mode(tmp_path: Path) -> None:
    out = tmp_path / "compose.yml"
    result = _run_cli(
        [
            "--run-id",
            "x",
            "--mode",
            "bogus",
            "--shas",
            SHA_A,
            "--out",
            str(out),
            "--skip-validate",
        ]
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


@pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="docker not in PATH; cannot validate compose.yml",
)
def test_cli_validates_with_docker_compose(tmp_path: Path) -> None:
    out = tmp_path / "compose.yml"
    # NO --skip-validate: exercise the validation pathway.
    result = _run_cli(
        [
            "--run-id",
            "validated",
            "--mode",
            "both",
            "--shas",
            f"{SHA_A},{SHA_B}",
            "--out",
            str(out),
        ]
    )
    # If docker is present but `docker compose` isn't (e.g. podman-docker
    # shim), skip rather than fail.
    probe = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        pytest.skip("`docker compose` subcommand unavailable")
    assert result.returncode == 0, (
        f"gen-compose + validation failed.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert out.exists()
