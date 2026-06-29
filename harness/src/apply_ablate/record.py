"""The ablation JSONL record schema (shared by all four ablators).

Verified identical across `rocq-ablator/lib/record.ml`,
`isabelle-ablator/rust/src/record.rs`, `lean-ablator/Ablator/Record.lean`, and
`isabelle-ablator/scala/src/Ablate.scala`. We model only the fields this tool needs
and ignore the rest (knob metadata, `holes_filled`, etc.) so schema drift never
breaks loading.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from apply_ablate.diff import apply as apply_diff

# proof_assistant value -> canonical prover key
PROOF_ASSISTANTS = frozenset({"coq", "isabelle", "lean"})


class AblationRecord(BaseModel):
    """One row of an ablator JSONL: a self-contained (challenge, solution) pair."""

    model_config = ConfigDict(extra="ignore")

    proof_assistant: str
    file_path: str
    challenge_file_content: str
    solution_diff: str = ""
    # informational
    task_id: str | None = None
    theory: str | None = None
    session: str | None = None

    @property
    def assistant(self) -> str:
        """Normalised proof-assistant key (lowercased)."""
        return self.proof_assistant.strip().lower()

    def solution_text(self) -> str:
        """The original (un-ablated) file recovered from `solution_diff`."""
        return apply_diff(self.challenge_file_content, self.solution_diff)


class RecordError(ValueError):
    """Raised when a JSONL record cannot be loaded or is out of range."""


def load_record(jsonl: Path, index: int) -> AblationRecord:
    """Load the 0-based `index`-th record from `jsonl`.

    Blank lines are skipped (some emitters pad indented JSONL with them), so the
    index counts only non-empty rows — matching how a reader would enumerate them.
    """
    if index < 0:
        raise RecordError(f"index must be >= 0, got {index}")
    text = jsonl.read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.strip()]
    if index >= len(rows):
        raise RecordError(
            f"index {index} out of range: {jsonl} has {len(rows)} record(s)"
        )
    try:
        obj = json.loads(rows[index])
    except json.JSONDecodeError as e:  # pragma: no cover - defensive
        raise RecordError(f"record {index} in {jsonl} is not valid JSON: {e}") from e
    rec = AblationRecord.model_validate(obj)
    if rec.assistant not in PROOF_ASSISTANTS:
        raise RecordError(
            f"record {index}: unknown proof_assistant {rec.proof_assistant!r} "
            f"(expected one of {sorted(PROOF_ASSISTANTS)})"
        )
    return rec


def count_records(jsonl: Path) -> int:
    """Number of non-empty records in `jsonl`."""
    return sum(1 for ln in jsonl.read_text(encoding="utf-8").splitlines() if ln.strip())
