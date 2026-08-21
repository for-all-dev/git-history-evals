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

from pydantic import BaseModel, ConfigDict, Field

from apply_ablate.diff import apply as apply_diff

# proof_assistant value -> canonical prover key
PROOF_ASSISTANTS = frozenset({"coq", "isabelle", "lean"})


class HoleInfo(BaseModel):
    """One holed proof in a challenge (the rest of the ablator's hole metadata is
    ignored). `theorem_name` is the declaration the agent must keep + re-prove."""

    model_config = ConfigDict(extra="ignore")
    theorem_name: str = ""


class DeletedLemma(BaseModel):
    """A lemma the ablator removed entirely from the challenge (in delete/corollary
    mode). `name` is its identifier; `text` is its original source (statement + proof),
    which is the crux the agent must re-derive."""

    model_config = ConfigDict(extra="ignore")
    name: str = ""
    text: str = ""


class RecordManifest(BaseModel):
    """Provenance of the source repo a row was mined from, embedded per-row when a
    dataset multiplexes several repos into one JSONL (e.g. the published HF card's
    multi-repo `easy.jsonl`). Lets a consumer slice a mixed file back down to one
    repo's rows (`--repo`) and sanity-check the checkout it's running against."""

    model_config = ConfigDict(extra="ignore")
    repo: str | None = None
    revision: str | None = None


class AblationRecord(BaseModel):
    """One row of an ablator JSONL: a self-contained (challenge, solution) pair."""

    model_config = ConfigDict(extra="ignore")

    proof_assistant: str
    file_path: str
    challenge_file_content: str
    # The answer as a whole file (newer ablators) — preferred over the diff if present.
    solution_file_content: str = ""
    solution_diff: str = ""
    holes_filled: list[HoleInfo] = Field(default_factory=list)
    deleted_lemmas: list[DeletedLemma] = Field(default_factory=list)
    # informational
    task_id: str | None = None
    # stable, unique per-challenge id (enriched schema; None on legacy datasets)
    challenge_id: str | None = None
    theory: str | None = None
    session: str | None = None
    # per-row provenance (present on multi-repo dataset cuts; None on single-repo
    # legacy datasets, which is fine since there's nothing to disambiguate)
    manifest: RecordManifest | None = None

    @property
    def assistant(self) -> str:
        """Normalised proof-assistant key (lowercased)."""
        return self.proof_assistant.strip().lower()

    @property
    def holed_theorems(self) -> list[str]:
        """Names of the theorems the agent was asked to re-prove (must be preserved)."""
        return [h.theorem_name for h in self.holes_filled if h.theorem_name]

    @property
    def deleted_lemma_names(self) -> list[str]:
        """Names of lemmas the ablator deleted from the challenge (empty if none)."""
        return [d.name for d in self.deleted_lemmas if d.name]

    def solution_text(self) -> str:
        """The (un-ablated) ground-truth file: the whole-file field if the ablator
        emitted it, else recovered by applying `solution_diff` to the challenge."""
        if self.solution_file_content:
            return self.solution_file_content
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
