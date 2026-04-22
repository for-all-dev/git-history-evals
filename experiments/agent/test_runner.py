"""End-to-end tests for ``agent/runner.py``.

These tests pin the contract between the runner and its three collaborators
(``agent/agent.py``, ``agent/tools.py``, ``shared/compile.py``) using a fake
pydantic-ai model — no network is touched. We drive a ``FunctionModel`` that
emits the canonical ReAct sequence (``write_proof`` → ``compile`` → final
``AgentVerdict``) and stub ``run_make_target`` so coqc need not be installed.

The fixtures plant a minimal repo + slot on disk and initialise the repo as a
real git worktree so ``run_one``'s terminal ``git checkout --`` actually
restores the challenge bytes. That lets the test assert the reset happened
without reaching into the runner's internals.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.agent import AgentVerdict
from agent.deps import AgentDeps
from agent.tools import register_tools
from metrics import ExperimentResult
from shared.compile import CompileResult


CHALLENGE_SRC = "Lemma triv : True.\nProof.\nAdmitted.\n"
SOLUTION_SRC = "Lemma triv : True.\nProof.\nexact I.\nQed.\n"
ATTEMPT_TACTICS = "exact I.\nQed."


def _init_repo(repo: Path, rel_target: Path, content: str) -> None:
    """Create a minimal git worktree at ``repo`` with ``rel_target`` tracked."""
    (repo / rel_target.parent).mkdir(parents=True, exist_ok=True)
    (repo / rel_target).write_text(content)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", str(rel_target)], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "initial"],
        check=True, env=env,
    )


def _build_fake_model(final_succeeded: bool = True) -> FunctionModel:
    """Build a FunctionModel that drives one write_proof → compile → final turn."""
    step = [0]

    async def fake(messages, info: AgentInfo) -> ModelResponse:
        step[0] += 1
        i = step[0]
        if i == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="write_proof", args={"tactics": ATTEMPT_TACTICS})]
            )
        if i == 2:
            return ModelResponse(parts=[ToolCallPart(tool_name="compile", args={})])
        # Emit the structured AgentVerdict as the output tool call.
        for t in info.output_tools:
            return ModelResponse(
                parts=[ToolCallPart(
                    tool_name=t.name,
                    args={
                        "succeeded": final_succeeded,
                        "final_tactics": ATTEMPT_TACTICS,
                        "give_up_reason": None if final_succeeded else "stuck",
                        "n_turns": 2,
                    },
                )]
            )
        raise AssertionError("output tool not registered")  # pragma: no cover

    return FunctionModel(fake)


def _patch_make_agent(monkeypatch: pytest.MonkeyPatch, model: FunctionModel) -> None:
    """Replace ``agent.runner.make_agent`` with one that returns a FunctionModel-backed Agent."""

    def fake_make_agent(_model: str) -> Agent[AgentDeps, AgentVerdict]:
        return Agent(
            model,
            deps_type=AgentDeps,
            output_type=AgentVerdict,
            defer_model_check=True,
        )

    import agent.runner as runner_mod
    monkeypatch.setattr(runner_mod, "make_agent", fake_make_agent)


def _patch_make_target_pass(
    monkeypatch: pytest.MonkeyPatch,
    vo_size: int = 12345,
) -> None:
    """Stub ``run_make_target`` to a successful CompileResult and make vo_bytes return ``vo_size``."""
    import agent.runner as runner_mod

    def fake_run_make_target(repo_dir: Path, rel_target: Path, *, timeout: int = 600) -> CompileResult:
        # Touch the .vo so any direct os.path.getsize checks see it exist.
        vo = (repo_dir / rel_target).with_suffix(".vo")
        vo.parent.mkdir(parents=True, exist_ok=True)
        vo.write_bytes(b"\x00" * vo_size)
        return CompileResult(
            ok=True, exit_code=0, stdout="", stderr="", elapsed_s=0.01, target=str(vo.name),
        )

    def fake_vo_bytes(repo_dir: Path, rel_target: Path) -> int:
        return vo_size

    def fake_print_assumptions(*_a, **_k) -> list[str]:
        return []

    monkeypatch.setattr(runner_mod, "run_make_target", fake_run_make_target)
    monkeypatch.setattr(runner_mod, "vo_bytes", fake_vo_bytes)
    monkeypatch.setattr(runner_mod, "print_assumptions", fake_print_assumptions)


@pytest.fixture()
def slot_and_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal repo-as-git and sibling slot directory."""
    repo = tmp_path / "repo"
    slot = tmp_path / "slot"
    slot.mkdir()
    rel_target = Path("Lemmas/Triv.v")

    _init_repo(repo, rel_target, CHALLENGE_SRC)

    # Slot artefacts that the runner reads.
    (slot / "challenge.v").write_text(CHALLENGE_SRC)
    (slot / "solution.v").write_text(SOLUTION_SRC)
    (slot / "meta.json").write_text(
        json.dumps({
            "declaration": "triv",
            "file_path": str(rel_target),
            "commit_hash": "deadbeefdeadbeef",
        })
    )
    return slot, repo


def test_run_one_happy_path_populates_metrics_and_resets(
    slot_and_repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    slot, repo = slot_and_repo

    _patch_make_agent(monkeypatch, _build_fake_model(final_succeeded=True))
    _patch_make_target_pass(monkeypatch, vo_size=4096)

    from agent.runner import run_one

    res = run_one(
        slot_dir=slot,
        condition="A",
        deletion_size=-1,
        challenge_file="challenge.v",
        repo_dir=repo,
        model="test:dummy",
        max_turns=5,
    )

    # Acceptance: returned ExperimentResult.mode == "agent".
    assert isinstance(res, ExperimentResult)
    assert res.mode == "agent"

    # Acceptance: agent_n_turns is populated.
    assert res.agent_n_turns is not None
    assert res.agent_n_turns >= 1

    # Acceptance: on PASS, llm_metrics.vo_bytes is populated.
    assert res.verdict == "PASS"
    assert res.llm_metrics is not None
    assert res.llm_metrics.vo_bytes == 4096
    assert res.llm_metrics.compile_time_s is not None

    # Human metrics also get populated via the per-SHA cache.
    assert res.human_metrics is not None
    assert res.human_metrics.vo_bytes == 4096

    # Acceptance: in-repo file reset via git checkout — challenge should be
    # back to its original committed contents, not the patched attempt.
    assert (repo / "Lemmas" / "Triv.v").read_text() == CHALLENGE_SRC

    # Schema round-trip: model_dump_json must not raise.
    dumped = res.model_dump_json()
    assert '"mode":"agent"' in dumped


def test_run_one_writes_attempt_and_transcript_is_written_by_main(
    slot_and_repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``main()`` drives the full pipeline: jsonl row + per-slot transcript."""
    slot, repo = slot_and_repo

    _patch_make_agent(monkeypatch, _build_fake_model(final_succeeded=True))
    _patch_make_target_pass(monkeypatch, vo_size=2048)

    results_base = tmp_path / "results_base"
    run_id = "test-run-0001"

    config = {
        "run_id": run_id,
        "model": "test:dummy",
        "max_turns": 5,
        "slots": [
            {
                "slot_dir": str(slot),
                "condition": "A",
                "deletion_size": -1,
                "challenge_file": "challenge.v",
            }
        ],
        "repo_dir": str(repo),
    }

    monkeypatch.setenv("RUN_CONFIG_JSON", json.dumps(config))
    monkeypatch.setenv("RESULTS_BASE", str(results_base))

    from agent.runner import main

    assert main() == 0

    run_dir = results_base / run_id
    jsonl_path = run_dir / "agent.jsonl"
    transcripts_dir = run_dir / "transcripts"
    assert jsonl_path.exists(), "agent.jsonl should be created"
    assert transcripts_dir.exists() and transcripts_dir.is_dir()

    # Exactly one line, valid JSON, mode=agent.
    lines = jsonl_path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["mode"] == "agent"
    assert row["verdict"] == "PASS"

    # Transcript file is named after the slot + deletion size and carries
    # both the log list and the verdict payload.
    transcript_files = list(transcripts_dir.glob("*.json"))
    assert len(transcript_files) == 1
    assert transcript_files[0].name == f"{slot.name}_d-1.json"
    payload = json.loads(transcript_files[0].read_text())
    assert payload["slot"] == slot.name
    assert payload["deletion_size"] == -1
    assert isinstance(payload["log"], list)
    assert payload["verdict"] is not None
    assert payload["verdict"]["succeeded"] is True
    # The AgentDeps.log should at minimum record the write_proof event the
    # fake model triggered; without this, transcripts would be useless.
    assert any("write_proof" in ev for ev in payload["log"])


def test_main_without_run_config_json_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("RUN_CONFIG_JSON", raising=False)

    from agent.runner import main

    rc = main()
    assert rc != 0
    err = capsys.readouterr().err
    assert "RUN_CONFIG_JSON" in err


def test_main_catches_per_slot_exceptions_and_continues(
    slot_and_repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A bad slot (missing meta.json) must log + continue, not abort the run."""
    slot, repo = slot_and_repo

    _patch_make_agent(monkeypatch, _build_fake_model(final_succeeded=True))
    _patch_make_target_pass(monkeypatch, vo_size=64)

    # Create a second slot that'll blow up — no meta.json.
    bad_slot = tmp_path / "bad_slot"
    bad_slot.mkdir()

    results_base = tmp_path / "results_base"
    run_id = "mixed-run"

    config = {
        "run_id": run_id,
        "model": "test:dummy",
        "max_turns": 5,
        "slots": [
            {
                "slot_dir": str(bad_slot),
                "condition": "A",
                "deletion_size": -1,
                "challenge_file": "challenge.v",
            },
            {
                "slot_dir": str(slot),
                "condition": "A",
                "deletion_size": -1,
                "challenge_file": "challenge.v",
            },
        ],
        "repo_dir": str(repo),
    }

    monkeypatch.setenv("RUN_CONFIG_JSON", json.dumps(config))
    monkeypatch.setenv("RESULTS_BASE", str(results_base))

    from agent.runner import main

    assert main() == 0

    run_dir = results_base / run_id
    # The good slot still produced a row.
    rows = (run_dir / "agent.jsonl").read_text().strip().splitlines()
    assert len(rows) == 1
    # The bad slot logged an exception.
    log_text = (run_dir / "run.log").read_text()
    assert "failed" in log_text
