"""Pydantic models for qualitative trajectory analysis output."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ObservationCode(str, Enum):
    """Qualitative codes for proof trajectory events."""

    strategy_shift = "strategy_shift"
    collaboration_handoff = "collaboration_handoff"
    backtrack = "backtrack"
    incremental_progress = "incremental_progress"
    sorry_introduced = "sorry_introduced"
    sorry_resolved = "sorry_resolved"
    tactic_style_change = "tactic_style_change"
    proof_compression = "proof_compression"
    specification_change = "specification_change"
    blocked_period = "blocked_period"
    exploratory_phase = "exploratory_phase"
    breakthrough = "breakthrough"


class Observation(BaseModel):
    """A single coded observation about a proof trajectory event."""

    code: ObservationCode
    commit_hash: str = Field(description="Short hash of the relevant commit")
    evidence: str = Field(description="Brief explanation grounded in the commit data")


class TrajectoryAnalysis(BaseModel):
    """Complete qualitative analysis of a single proof's evolution."""

    declaration: str
    file: str
    observations: list[Observation] = Field(
        description="Coded observations, one per significant event in the trajectory"
    )
    narrative: str = Field(
        description=(
            "2-4 paragraph interpretive narrative of this proof's human engineering "
            "journey. Highlight what makes it distinctly human: iteration, insight, "
            "collaboration, false starts."
        )
    )
    trajectory_signature: str = Field(
        description=(
            "One-sentence characterization of this proof's development pattern, "
            "suitable for later comparison with agent trajectories"
        )
    )
    complexity: int = Field(
        ge=1,
        le=5,
        description="How complex was this proof journey (1=straightforward, 5=tortuous)",
    )
    dominant_strategy: str = Field(
        description=(
            "Primary proof strategy observed (e.g. 'rewrite chains', "
            "'case analysis + automation', 'algebraic reasoning')"
        )
    )
