"""Tests for ``experiments/run_agent_experiment.py``.

The CLI is pure glue — slot discovery + file plumbing — so these tests stub
``agent.runner.run_one`` with a canned ``ExperimentResult`` and assert that:

* The CLI renders its options (``--help``).
* Filesystem discovery finds the synthetic slot and writes a JSONL row.
* ``RUN_CONFIG_JSON`` short-circuits discovery (a sentinel on-disk slot that
  the filesystem walker *would* pick up is NOT used when the env var is set).

No network and no Coq toolchain touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import run_agent_experiment
from metrics import ExperimentResult


def _build_cli_app() -> typer.Typer:
    """Wrap ``_main`` as a Typer app so CliRunner can drive it."""
    app = typer.Typer()
    app.command()(run_agent_experiment._main)
    return app


def _canned_result(slot_name: str, deletion_size: int, condition: str = "B") -> ExperimentResult:
    """Build a minimal ``mode="agent"`` row the CLI can serialize."""
    return ExperimentResult(
        challenge_id=slot_name,
        declaration="Foo.bar",
        deletion_size=deletion_size,
        condition=condition,  # type: ignore[arg-type]
        verdict="PASS",
        inference_time_s=1.23,
        output_tokens=42,
        mode="agent",
        agent_n_turns=3,
        agent_give_up_reason="compile_success",
        agent_total_input_tokens=100,
        agent_total_output_tokens=42,
    )


def _write_slot(slot_dir: Path, deletion_size: int, condition: str = "B") -> None:
    """Materialize the minimum file layout the walker looks for."""
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "meta.json").write_text(json.dumps({
        "declaration": "Foo.bar",
        "file_path": "src/Foo.v",
        "commit_hash": "deadbeefdeadbeef",
    }))
    challenge_name = "challenge.v" if deletion_size == -1 else f"challenge{deletion_size}.v"
    (slot_dir / challenge_name).write_text("Lemma bar : True. Proof. Admitted.\n")


@pytest.fixture()
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Point EXP_A/EXP_B at ``tmp_path`` so filesystem discovery is hermetic."""
    exp_a = tmp_path / "admitted-proofs"
    exp_b = tmp_path / "experiments3"
    exp_a.mkdir()
    exp_b.mkdir()
    monkeypatch.setattr(run_agent_experiment, "EXP_A", exp_a)
    monkeypatch.setattr(run_agent_experiment, "EXP_B", exp_b)
    # Make sure no stray RUN_CONFIG_JSON leaks in from the host shell.
    monkeypatch.delenv("RUN_CONFIG_JSON", raising=False)
    return exp_a, exp_b


def test_help_renders() -> None:
    """`eval-agent --help` renders every documented option."""
    runner = CliRunner()
    result = runner.invoke(_build_cli_app(), ["--help"])
    assert result.exit_code == 0
    out = result.output
    for flag in (
        "--max-challenges",
        "--deletion-sizes",
        "--skip-a",
        "--skip-b",
        "--only",
        "--model",
        "--max-turns",
        "--results-dir",
    ):
        assert flag in out, f"missing flag in --help output: {flag}"


def test_discovers_slots_and_writes_jsonl(
    cli_env: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filesystem discovery → run_one stub → two JSONL lines with mode=agent."""
    exp_a, exp_b = cli_env

    # Two slots under Condition B, deletion_size=3 (the first size in the default list).
    slot_a = exp_b / "01-alpha"
    slot_b = exp_b / "02-beta"
    _write_slot(slot_a, 3, condition="B")
    _write_slot(slot_b, 3, condition="B")

    calls: list[dict] = []

    def fake_run_one(
        slot_dir,
        condition,
        deletion_size,
        challenge_file,
        repo_dir,
        model,
        max_turns,
    ) -> ExperimentResult:
        calls.append({
            "slot_dir": Path(slot_dir),
            "condition": condition,
            "deletion_size": deletion_size,
            "challenge_file": challenge_file,
            "model": model,
            "max_turns": max_turns,
        })
        return _canned_result(Path(slot_dir).name, deletion_size, condition)

    monkeypatch.setattr(run_agent_experiment, "run_one", fake_run_one)

    results_dir = tmp_path / "results"
    runner = CliRunner()
    result = runner.invoke(
        _build_cli_app(),
        [
            "--deletion-sizes", "3",
            "--skip-a",
            "--max-challenges", "5",
            "--results-dir", str(results_dir),
            "--model", "test:dummy",
            "--max-turns", "7",
        ],
    )
    assert result.exit_code == 0, result.output

    # Both slots got dispatched once each.
    assert len(calls) == 2
    assert {c["slot_dir"].name for c in calls} == {slot_a.name, slot_b.name}
    # CLI options flowed through to run_one.
    assert all(c["model"] == "test:dummy" for c in calls)
    assert all(c["max_turns"] == 7 for c in calls)
    assert all(c["condition"] == "B" and c["deletion_size"] == 3 for c in calls)

    jsonl = results_dir / "agent.jsonl"
    assert jsonl.exists(), "agent.jsonl must be written"
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        row = json.loads(ln)
        assert row["mode"] == "agent"
        assert row["verdict"] == "PASS"


def test_run_config_json_short_circuits_discovery(
    cli_env: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When RUN_CONFIG_JSON is set, the CLI uses its slots verbatim, ignoring the on-disk walker."""
    exp_a, exp_b = cli_env

    # This "decoy" slot WOULD be picked up by filesystem discovery — asserting
    # it isn't touched proves the short-circuit worked.
    decoy = exp_b / "99-decoy"
    _write_slot(decoy, 3, condition="B")

    # The synthetic slot we *do* want to run lives outside EXP_A/EXP_B so it
    # could only reach run_one through RUN_CONFIG_JSON.
    synth = tmp_path / "synthetic-slot"
    _write_slot(synth, -1, condition="A")

    calls: list[dict] = []

    def fake_run_one(
        slot_dir,
        condition,
        deletion_size,
        challenge_file,
        repo_dir,
        model,
        max_turns,
    ) -> ExperimentResult:
        calls.append({
            "slot_dir": Path(slot_dir),
            "condition": condition,
            "deletion_size": deletion_size,
            "model": model,
            "max_turns": max_turns,
            "repo_dir": Path(repo_dir),
        })
        return _canned_result(Path(slot_dir).name, deletion_size, condition)

    monkeypatch.setattr(run_agent_experiment, "run_one", fake_run_one)

    config = {
        "run_id": "rcj-test",
        "model": "config:model",
        "max_turns": 9,
        "repo_dir": str(tmp_path / "fake-repo"),
        "slots": [
            {
                "slot_dir": str(synth),
                "condition": "A",
                "deletion_size": -1,
                "challenge_file": "challenge.v",
            },
        ],
    }
    monkeypatch.setenv("RUN_CONFIG_JSON", json.dumps(config))

    results_dir = tmp_path / "results-rcj"
    runner = CliRunner()
    # Pass CLI flags that RUN_CONFIG_JSON should override (model, max_turns).
    result = runner.invoke(
        _build_cli_app(),
        [
            "--results-dir", str(results_dir),
            "--model", "cli:ignored",
            "--max-turns", "2",
        ],
    )
    assert result.exit_code == 0, result.output

    # Exactly one call, and it's the synthetic slot — NOT the decoy.
    assert len(calls) == 1
    call = calls[0]
    assert call["slot_dir"] == synth
    assert call["slot_dir"].name != decoy.name
    # Config values won over CLI flags.
    assert call["model"] == "config:model"
    assert call["max_turns"] == 9
    assert call["repo_dir"] == tmp_path / "fake-repo"

    jsonl = results_dir / "agent.jsonl"
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["mode"] == "agent"
    assert row["challenge_id"] == synth.name
