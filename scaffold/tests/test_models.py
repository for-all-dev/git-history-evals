"""Tests for data models — serialization round-trips."""

from __future__ import annotations

from scaffold.models import (
    EvalChallenge,
    MiningResult,
    ProofAssistant,
    ProofHole,
    ProofHoleKind,
    RepoMetadata,
)


def test_proof_hole_roundtrip() -> None:
    hole = ProofHole(
        line=10,
        column=2,
        kind=ProofHoleKind.admitted,
        proof_assistant=ProofAssistant.coq,
        context="  Admitted.",
        enclosing_decl="add_comm",
    )
    json_str = hole.model_dump_json()
    restored = ProofHole.model_validate_json(json_str)
    assert restored == hole


def test_eval_challenge_roundtrip() -> None:
    challenge = EvalChallenge(
        task_id="test_abc123_deadbeef",
        repo="test-repo",
        proof_assistant=ProofAssistant.coq,
        commit_hash="abc123",
        parent_hash="def456",
        commit_message="Complete proof",
        file_path="proof.v",
        challenge_file_content="Theorem foo. Admitted.",
        solution_file_content="Theorem foo. Proof. reflexivity. Qed.",
        holes_filled=[
            ProofHole(
                line=1,
                column=13,
                kind=ProofHoleKind.admitted,
                proof_assistant=ProofAssistant.coq,
            )
        ],
        diff="--- a/proof.v\n+++ b/proof.v",
        instructions="Fill in the proof.",
    )
    json_str = challenge.model_dump_json()
    restored = EvalChallenge.model_validate_json(json_str)
    assert restored == challenge
    assert restored.task_id == "test_abc123_deadbeef"


def test_mining_result_roundtrip() -> None:
    result = MiningResult(
        repo_name="test",
        proof_assistant=ProofAssistant.isabelle,
        total_commits_scanned=100,
        total_challenges=5,
    )
    json_str = result.model_dump_json()
    restored = MiningResult.model_validate_json(json_str)
    assert restored.total_commits_scanned == 100
    assert restored.total_challenges == 5


def test_repo_metadata_defaults() -> None:
    meta = RepoMetadata(
        name="test",
        local_path="/tmp/test",
        proof_assistant=ProofAssistant.lean4,
    )
    assert meta.file_extensions == []
    assert meta.exclude_paths == []
    assert meta.discovered_patterns == {}
