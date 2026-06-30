"""apply-ablate: materialise an ablation challenge onto disk and verify it builds.

The four ablators (rocq/isabelle-rust/isabelle-scala/lean) emit a JSONL where every
row is a self-contained (challenge, solution) pair. This package consumes such a row
and writes the ablated (holed) file into a copy of the source repo, optionally
pre-building per-prover dependencies and checking that the result compiles. It is the
substrate for the upcoming pydantic-ai proving loop.
"""

from apply_ablate.diff import apply as apply_diff
from apply_ablate.record import AblationRecord, load_record

__all__ = ["AblationRecord", "load_record", "apply_diff"]
