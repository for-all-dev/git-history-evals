"""Tests for memory-lean challenge IO (slim read + streamed curated write)."""

from __future__ import annotations

import json

from scaffold.models import EvalChallenge
from scaffold.output import read_jsonl_slim, write_curated_stream, write_jsonl


def _make_challenge(task_id: str) -> EvalChallenge:
    return EvalChallenge(
        task_id=task_id,
        repo="test-repo",
        proof_assistant="coq",
        commit_hash="abc123",
        parent_hash="def456",
        commit_message=f"Change for {task_id}",
        file_path="src/Proof.v",
        challenge_file_content="HEAVY " * 1000,
        solution_file_content="HEAVY " * 1000,
        diff="-Admitted.\n+trivial. Qed.\n",
        instructions="Fill in the proof.",
    )


class TestReadJsonlSlim:
    def test_blanks_heavy_fields_keeps_the_rest(self, tmp_path) -> None:
        path = tmp_path / "challenges.jsonl"
        write_jsonl([_make_challenge("t1"), _make_challenge("t2")], path)

        slim = read_jsonl_slim(path)
        assert [c.task_id for c in slim] == ["t1", "t2"]
        for c in slim:
            assert c.challenge_file_content == ""
            assert c.solution_file_content == ""
            assert c.diff == "-Admitted.\n+trivial. Qed.\n"  # untouched
            assert c.commit_message.startswith("Change for")


class TestWriteCuratedStream:
    def test_streams_survivors_with_annotations_and_full_content(
        self, tmp_path
    ) -> None:
        src = tmp_path / "challenges.jsonl"
        write_jsonl([_make_challenge(f"t{i}") for i in range(4)], src)
        dest = tmp_path / "curated.jsonl"

        verdicts = {
            "t0": ("accept", "model-a", "substantive"),
            "t2": ("borderline", "model-b", "unsure"),
            # t1, t3 rejected — absent from the mapping
        }
        n = write_curated_stream(src, dest, verdicts)
        assert n == 2

        out = [
            EvalChallenge.model_validate_json(line)
            for line in dest.read_text().splitlines()
        ]
        assert [c.task_id for c in out] == ["t0", "t2"]  # input order preserved
        # Full heavy content survives the round trip (slim loading never ran here).
        assert out[0].challenge_file_content.startswith("HEAVY")
        assert out[0].curation_verdict == "accept"
        assert out[0].curation_model == "model-a"
        assert out[1].curation_verdict == "borderline"
        assert out[1].curation_rationale == "unsure"

    def test_empty_verdicts_writes_nothing(self, tmp_path) -> None:
        src = tmp_path / "challenges.jsonl"
        write_jsonl([_make_challenge("t0")], src)
        dest = tmp_path / "curated.jsonl"
        assert write_curated_stream(src, dest, {}) == 0
        assert dest.read_text() == ""

    def test_output_matches_write_jsonl_format(self, tmp_path) -> None:
        # The streamed writer must produce byte-identical rows to the
        # in-memory write_jsonl path it replaces.
        src = tmp_path / "challenges.jsonl"
        challenge = _make_challenge("t0")
        write_jsonl([challenge], src)
        dest = tmp_path / "curated.jsonl"
        write_curated_stream(src, dest, {"t0": ("accept", "m", "r")})

        annotated = challenge.model_copy(
            update={
                "curation_verdict": "accept",
                "curation_model": "m",
                "curation_rationale": "r",
            }
        )
        legacy = tmp_path / "legacy.jsonl"
        write_jsonl([annotated], legacy)
        assert dest.read_text() == legacy.read_text()

    def test_round_trips_unknown_whitespace_lines(self, tmp_path) -> None:
        src = tmp_path / "challenges.jsonl"
        src.write_text(
            json.dumps(_make_challenge("t0").model_dump()) + "\n\n"  # blank line
        )
        dest = tmp_path / "curated.jsonl"
        assert write_curated_stream(src, dest, {"t0": ("accept", "m", "r")}) == 1
