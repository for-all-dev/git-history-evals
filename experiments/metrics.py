"""
Structured output schemas for the progressive deletion experiment.

Each ExperimentResult captures one (challenge_slot × deletion_size) run.
Results are written to experiment-results.jsonl (one JSON object per line).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── Per-proof quality snapshot ────────────────────────────────────────────────

class ProofMetrics(BaseModel):
    """Quantitative description of a proof body (human or LLM-generated)."""

    tactic_count: int = Field(description="Number of tactic sentences, excluding Qed/Admitted")
    automation_ratio: float = Field(description="Fraction of tactics that are automation (0–1)")
    unique_tactic_types: int = Field(description="Count of distinct leading tactic keywords")
    max_bullet_depth: int = Field(description="Maximum { } nesting depth in the proof block")
    proof_chars: int = Field(description="Character length of the proof body")
    proof_lines: int = Field(description="Line count of the proof body")
    ends_with_admitted: bool = Field(description="True if the proof terminates with Admitted.")


# ── Per-run result ────────────────────────────────────────────────────────────

class ExperimentResult(BaseModel):
    """Full result for one challenge × deletion-size run."""

    # Identification
    challenge_id: str = Field(description="Slot name, e.g. '01-of_prefancy_identZ_correct'")
    declaration: str = Field(description="Coq declaration name")
    deletion_size: int = Field(
        description="Number of tactics removed. -1 means full proof replaced (Condition A)."
    )
    condition: Literal["A", "B"] = Field(
        description="A = full proof replaced; B = last N tactics removed"
    )

    # Execution
    verdict: Literal["PASS", "FAIL", "TIMEOUT", "ERROR"] = Field(
        description="Outcome of coqc compilation"
    )
    inference_time_s: float = Field(description="Wall-clock seconds for Claude API call")
    output_tokens: int = Field(description="Output tokens returned by Claude")

    # Proof quality
    human_metrics: ProofMetrics | None = Field(
        default=None,
        description="Metrics for the ground-truth human proof (solution.v)"
    )
    llm_metrics: ProofMetrics | None = Field(
        default=None,
        description="Metrics for the LLM-generated proof (attempt.v)"
    )

    # Similarity
    tactic_edit_distance: int | None = Field(
        default=None,
        description=(
            "Levenshtein distance on tactic-name sequences between human and LLM proofs. "
            "Only meaningful when both proofs are available and LLM did not Admit."
        )
    )
    normalized_edit_distance: float | None = Field(
        default=None,
        description=(
            "tactic_edit_distance / max(len_human, len_llm). "
            "0 = identical tactic sequences, 1 = maximally different."
        )
    )


# ── Experiment summary ────────────────────────────────────────────────────────

class DeletionConditionSummary(BaseModel):
    """Aggregate stats for all challenges at a given deletion size."""

    deletion_size: int
    condition: Literal["A", "B"]
    n_challenges: int
    n_pass: int
    n_fail: int
    n_error_or_timeout: int
    pass_rate: float
    mean_inference_time_s: float
    mean_output_tokens: float
    mean_tactic_edit_distance: float | None = None
    mean_normalized_edit_distance: float | None = None


class ExperimentSummary(BaseModel):
    """Top-level summary across all conditions and deletion sizes."""

    model: str
    date: str
    n_total_runs: int
    conditions: list[DeletionConditionSummary]

    # Faithfulness score: Pearson r between deletion_size and pass_rate
    # (should be strongly negative for a faithful eval)
    faithfulness_correlation: float | None = Field(
        default=None,
        description=(
            "Pearson r between deletion_size and pass_rate across B conditions. "
            "Negative = harder deletions → lower pass rate (expected for faithful eval). "
            "Near zero or positive = possible memorization / non-monotonic degradation."
        )
    )