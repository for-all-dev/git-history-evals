"""Tests for the LLM-based curation pipeline.

All tests are pure-function unit tests — no LLM API calls needed.
"""

from __future__ import annotations

from scaffold.curator import (
    CurationResult,
    _build_curation_prompt,
    _parse_response,
    apply_curation,
)
from scaffold.models import EvalChallenge, ProofHole


def _make_challenge(**overrides) -> EvalChallenge:
    """Build a minimal EvalChallenge with sensible defaults."""
    defaults = {
        "task_id": "test_repo_abc123_file",
        "repo": "test-repo",
        "proof_assistant": "coq",
        "commit_hash": "abc123",
        "parent_hash": "def456",
        "commit_message": "Complete proof of add_comm",
        "file_path": "src/Proof.v",
        "challenge_file_content": "Theorem foo : True.\nProof.\n  Admitted.\n",
        "solution_file_content": "Theorem foo : True.\nProof.\n  trivial.\nQed.\n",
        "holes_filled": [
            ProofHole(line=3, column=2, kind="admitted", proof_assistant="coq")
        ],
        "diff": "--- a/src/Proof.v\n+++ b/src/Proof.v\n-  Admitted.\n+  trivial.\n+Qed.\n",
        "instructions": "Fill in the proof of foo in src/Proof.v.",
    }
    defaults.update(overrides)
    return EvalChallenge(**defaults)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


class TestBuildCurationPrompt:
    def test_contains_key_fields(self) -> None:
        ch = _make_challenge()
        prompt = _build_curation_prompt(ch)

        assert "test-repo" in prompt
        assert "coq" in prompt
        assert "src/Proof.v" in prompt
        assert "Complete proof of add_comm" in prompt
        assert "Fill in the proof of foo" in prompt
        assert "Admitted" in prompt

    def test_truncates_long_diff(self) -> None:
        long_diff = "x" * 20000
        ch = _make_challenge(diff=long_diff)
        prompt = _build_curation_prompt(ch)

        assert "truncated" in prompt
        assert "20000 chars total" in prompt
        # The prompt should not contain the full 20k chars
        assert len(prompt) < 15000

    def test_short_diff_not_truncated(self) -> None:
        ch = _make_challenge(diff="short diff")
        prompt = _build_curation_prompt(ch)

        assert "truncated" not in prompt
        assert "short diff" in prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_accept(self) -> None:
        verdict, rationale = _parse_response(
            "VERDICT: ACCEPT\nRATIONALE: This is a genuine proof completion."
        )
        assert verdict == "accept"
        assert "genuine proof" in rationale

    def test_reject(self) -> None:
        verdict, rationale = _parse_response(
            "VERDICT: REJECT\nRATIONALE: Just deleting unused dead code."
        )
        assert verdict == "reject"
        assert "dead code" in rationale

    def test_defer(self) -> None:
        verdict, rationale = _parse_response(
            "VERDICT: DEFER\nRATIONALE: Ambiguous whether this is real proof work."
        )
        assert verdict == "defer"
        assert "Ambiguous" in rationale

    def test_case_insensitive(self) -> None:
        verdict, _ = _parse_response("verdict: accept\nrationale: looks good")
        assert verdict == "accept"

    def test_malformed_falls_back_to_defer(self) -> None:
        verdict, rationale = _parse_response("I'm not sure about this one.")
        assert verdict == "defer"
        assert len(rationale) > 0

    def test_empty_response(self) -> None:
        verdict, rationale = _parse_response("")
        assert verdict == "defer"
        assert "Could not parse" in rationale

    def test_verdict_only_no_rationale(self) -> None:
        verdict, rationale = _parse_response("VERDICT: ACCEPT")
        assert verdict == "accept"
        # Should still have some rationale (falls back to raw text)
        assert len(rationale) > 0

    def test_extra_text_around_verdict(self) -> None:
        verdict, rationale = _parse_response(
            "Let me analyze this.\nVERDICT: REJECT\n"
            "RATIONALE: No actual proof content changed."
        )
        assert verdict == "reject"
        assert "proof content" in rationale


# ---------------------------------------------------------------------------
# apply_curation
# ---------------------------------------------------------------------------


class TestApplyCuration:
    def test_partitions_correctly(self) -> None:
        challenges = [_make_challenge(task_id=f"t{i}") for i in range(4)]
        results = [
            CurationResult(verdict="accept", model="haiku", rationale="good"),
            CurationResult(verdict="reject", model="haiku", rationale="junk"),
            CurationResult(verdict="borderline", model="sonnet", rationale="unclear"),
            CurationResult(verdict="accept", model="haiku", rationale="good too"),
        ]

        accepted, rejected, borderline = apply_curation(challenges, results)

        assert len(accepted) == 2
        assert len(rejected) == 1
        assert len(borderline) == 1

        assert accepted[0].task_id == "t0"
        assert accepted[1].task_id == "t3"
        assert rejected[0].task_id == "t1"
        assert borderline[0].task_id == "t2"

    def test_annotations_set_on_challenges(self) -> None:
        challenges = [_make_challenge()]
        results = [
            CurationResult(
                verdict="accept", model="test-model", rationale="real proof work"
            ),
        ]

        accepted, _, _ = apply_curation(challenges, results)

        ch = accepted[0]
        assert ch.curation_verdict == "accept"
        assert ch.curation_model == "test-model"
        assert ch.curation_rationale == "real proof work"

    def test_original_challenge_not_mutated(self) -> None:
        original = _make_challenge()
        results = [
            CurationResult(verdict="accept", model="m", rationale="r"),
        ]

        apply_curation([original], results)

        # Original should not have curation fields set
        assert original.curation_verdict is None
        assert original.curation_model is None
        assert original.curation_rationale is None

    def test_rejected_excluded_from_accepted_and_borderline(self) -> None:
        challenges = [_make_challenge(task_id=f"t{i}") for i in range(3)]
        results = [
            CurationResult(verdict="accept", model="m", rationale="ok"),
            CurationResult(verdict="reject", model="m", rationale="bad"),
            CurationResult(verdict="borderline", model="m", rationale="meh"),
        ]

        accepted, rejected, borderline = apply_curation(challenges, results)

        # Rejected task_id should not appear in accepted or borderline
        accepted_ids = {c.task_id for c in accepted}
        borderline_ids = {c.task_id for c in borderline}
        rejected_ids = {c.task_id for c in rejected}

        assert rejected_ids == {"t1"}
        assert "t1" not in accepted_ids
        assert "t1" not in borderline_ids

    def test_mismatched_lengths_raises(self) -> None:
        import pytest

        challenges = [_make_challenge(), _make_challenge()]
        results = [CurationResult(verdict="accept", model="m", rationale="r")]

        with pytest.raises(ValueError, match="same length"):
            apply_curation(challenges, results)

    def test_all_accepted(self) -> None:
        challenges = [_make_challenge(task_id=f"t{i}") for i in range(3)]
        results = [
            CurationResult(verdict="accept", model="m", rationale="good")
            for _ in range(3)
        ]

        accepted, rejected, borderline = apply_curation(challenges, results)

        assert len(accepted) == 3
        assert len(rejected) == 0
        assert len(borderline) == 0

    def test_all_rejected(self) -> None:
        challenges = [_make_challenge(task_id=f"t{i}") for i in range(3)]
        results = [
            CurationResult(verdict="reject", model="m", rationale="junk")
            for _ in range(3)
        ]

        accepted, rejected, borderline = apply_curation(challenges, results)

        assert len(accepted) == 0
        assert len(rejected) == 3
        assert len(borderline) == 0

    def test_empty_input(self) -> None:
        accepted, rejected, borderline = apply_curation([], [])

        assert accepted == []
        assert rejected == []
        assert borderline == []
