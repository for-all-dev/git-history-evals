"""Proof analyzer registry and detection."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from scaffold.analyzers.base import ProofAnalyzer
from scaffold.analyzers.coq import CoqAnalyzer
from scaffold.analyzers.isabelle import IsabelleAnalyzer
from scaffold.analyzers.lean import LeanAnalyzer
from scaffold.models import ProofAssistant

ANALYZERS: dict[ProofAssistant, ProofAnalyzer] = {
    ProofAssistant.coq: CoqAnalyzer(),
    ProofAssistant.isabelle: IsabelleAnalyzer(),
    ProofAssistant.lean4: LeanAnalyzer(),
}

# Map file extensions to proof assistants
_EXT_MAP: dict[str, ProofAssistant] = {
    ".v": ProofAssistant.coq,
    ".thy": ProofAssistant.isabelle,
    ".lean": ProofAssistant.lean4,
}


def get_analyzer(pa: ProofAssistant) -> ProofAnalyzer:
    return ANALYZERS[pa]


def detect_proof_assistant(repo_path: str | Path) -> ProofAssistant | None:
    """Detect the primary proof assistant used in a repo by file extension frequency.

    Walks the repo (skipping hidden dirs and common non-proof dirs) and counts
    proof-relevant file extensions.
    """
    repo = Path(repo_path)
    counts: Counter[ProofAssistant] = Counter()
    skip_dirs = {".git", ".jj", ".hg", "__pycache__", "node_modules", ".venv", "build"}

    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            ext = os.path.splitext(fname)[1]
            if ext in _EXT_MAP:
                counts[_EXT_MAP[ext]] += 1

    if not counts:
        return None
    return counts.most_common(1)[0][0]


__all__ = [
    "ANALYZERS",
    "CoqAnalyzer",
    "IsabelleAnalyzer",
    "LeanAnalyzer",
    "ProofAnalyzer",
    "detect_proof_assistant",
    "get_analyzer",
]
