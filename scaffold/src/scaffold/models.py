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


class CommitClass(str, Enum):
    """Proof-relevance classification for a commit.

    proof_complete  — a sorry/Admitted/oops hole was fully closed/removed.
    proof_new       — a new lemma, theorem, or definition was added with a proof.
    proof_add       — proof content added or extended but goals may still be open
                      (partial progress: new tactics, new cases, etc.).
    spec_change     — the *statement* of a theorem/lemma was modified, affecting
                      provability without necessarily advancing the proof itself.
    infra           — build system, CI, dependency bumps, generated files (noise).
    refactor        — code reorganisation, rename, move, cleanup with no proof change.
    fix             — non-proof bug fix (e.g. in extraction, code-gen, tooling).
    other           — does not fit any of the above.
    """

    proof_complete = "proof_complete"
    proof_new = "proof_new"
    proof_add = "proof_add"
    proof_optimise = "proof_optimise"
    spec_change = "spec_change"
    infra = "infra"
    refactor = "refactor"
    fix = "fix"
    other = "other"


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


class CommitRecord(BaseModel):
    """A single commit record for the flat commit-message dataset.

    Captures everything needed for proof-relevance analysis:
    - Full commit message (subject + body) for semantic mining
    - Per-file change list so file-propagation chains can be traced
    - Coq-file subset for quick proof-file filtering
    - Insertion/deletion counts as a rough proxy for proof size change
    - Proof-relevance classification and extracted keywords
    """

    hash: str
    parent_hashes: list[str] = Field(
        default_factory=list,
        description="All parent SHAs (merge commits have more than one)",
    )
    author: str = ""
    author_email: str = ""
    date: str = Field(description="ISO 8601 author date")
    message_subject: str = Field(description="First line of the commit message")
    message_body: str = Field(
        default="",
        description="Remainder of the commit message after the subject line",
    )
    files_changed_count: int = 0
    insertions: int = 0
    deletions: int = 0
    changed_files: list[str] = Field(
        default_factory=list,
        description="All file paths touched by this commit",
    )
    coq_files_changed: list[str] = Field(
        default_factory=list,
        description=".v files touched — direct proof relevance signal",
    )
    touches_proof_files: bool = Field(
        default=False,
        description="True if any .v file was modified",
    )
    commit_class: CommitClass = Field(
        default=CommitClass.other,
        description="Proof-relevance classification (see CommitClass docstring)",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Proof-relevant terms extracted from subject (always) and body (when present). "
            "Useful for downstream retrieval and as a proxy when body is absent."
        ),
    )
    class_confidence: str = Field(
        default="heuristic",
        description="'heuristic' | 'diff' | 'llm' — how the class was determined",
    )
    # --- diff-based fields (populated by enrich_record_with_diff) -----------
    diff_sorry_removed: bool = Field(
        default=False,
        description="True if a sorry/Admitted/oops line was net-removed in .v diffs",
    )
    diff_net_proof_lines: int = Field(
        default=0,
        description=(
            "Added lines minus removed lines across all .v files. "
            "Positive = proof grew; negative = proof shrank (optimisation)."
        ),
    )
    tactic_tags: list[str] = Field(
        default_factory=list,
        description="Tactic names found in added lines of .v diffs (diff-based).",
    )
    proof_style: list[str] = Field(
        default_factory=list,
        description=(
            "High-level proof style signals: 'tactic_mode', 'term_mode', "
            "'ssreflect', 'mixed'. Derived from added lines of .v diffs."
        ),
    )