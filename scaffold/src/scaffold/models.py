"""Core data models for proof engineering eval mining."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProofAssistant(str, Enum):
    coq = "coq"
    isabelle = "isabelle"
    lean4 = "lean4"


class ProofHoleKind(str, Enum):
    sorry = "sorry"
    admitted = "admitted"
    admit = "admit"
    oops = "oops"
    placeholder = "placeholder"
    new_obligation = "new_obligation"


class ProofHole(BaseModel):
    """A location in source where a proof is incomplete."""

    line: int
    column: int
    kind: ProofHoleKind
    proof_assistant: ProofAssistant
    context: str = Field(default="", description="Surrounding lines for context")
    enclosing_decl: str = Field(
        default="", description="Name of the enclosing theorem/lemma"
    )


class RepoMetadata(BaseModel):
    """Metadata about a proof engineering repository."""

    name: str
    url: str = ""
    local_path: str
    proof_assistant: ProofAssistant
    file_extensions: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    discovered_patterns: dict[str, Any] = Field(default_factory=dict)


class CommitProofDiff(BaseModel):
    """A diff of a proof file between two commits."""

    file_path: str
    parent_content: str = ""
    child_content: str = ""
    diff_text: str = ""
    holes_in_parent: list[ProofHole] = Field(default_factory=list)
    holes_in_child: list[ProofHole] = Field(default_factory=list)
    holes_filled: list[ProofHole] = Field(
        default_factory=list,
        description="Holes present in parent but absent in child (i.e. filled)",
    )


class CommitCandidate(BaseModel):
    """A commit that may contain proof-filling changes."""

    commit_hash: str
    parent_hash: str
    message: str = ""
    author: str = ""
    date: datetime | None = None
    repo_name: str = ""
    proof_diffs: list[CommitProofDiff] = Field(default_factory=list)


class EvalChallenge(BaseModel):
    """A single eval challenge extracted from git history.

    The challenge presents the file at the parent commit (with holes)
    and the solution is the file at the child commit (holes filled).
    """

    task_id: str = Field(description="Unique identifier: {repo}_{commit_hash}_{file}")
    repo: str
    proof_assistant: ProofAssistant
    commit_hash: str
    parent_hash: str
    commit_message: str = ""
    file_path: str
    challenge_file_content: str = Field(
        description="File content at parent commit (contains hole)"
    )
    solution_file_content: str = Field(
        description="File content at child commit (hole filled)"
    )
    holes_filled: list[ProofHole] = Field(default_factory=list)
    diff: str = Field(default="", description="Unified diff between challenge and solution")
    instructions: str = Field(
        default="",
        description="Human-readable instructions for the challenge",
    )


class MiningResult(BaseModel):
    """Result of mining a repository."""

    repo_name: str
    proof_assistant: ProofAssistant
    total_commits_scanned: int = 0
    total_challenges: int = 0
    challenges: list[EvalChallenge] = Field(default_factory=list)
