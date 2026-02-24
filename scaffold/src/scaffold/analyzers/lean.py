"""Lean 4 proof analyzer."""

from __future__ import annotations

import re

from scaffold.analyzers.base import ProofAnalyzer
from scaffold.models import ProofAssistant, ProofHoleKind


class LeanAnalyzer(ProofAnalyzer):
    """Analyzer for Lean 4 .lean files."""

    @property
    def proof_assistant(self) -> ProofAssistant:
        return ProofAssistant.lean4

    @property
    def file_extensions(self) -> list[str]:
        return [".lean"]

    @property
    def hole_markers(self) -> list[re.Pattern[str]]:
        return [
            re.compile(r"\bsorry\b"),
        ]

    @property
    def declaration_pattern(self) -> re.Pattern[str]:
        return re.compile(
            r"^\s*(?:theorem|lemma|def|instance)\s+(\w+)"
        )

    def _classify_hole(self, matched_text: str) -> ProofHoleKind:
        return ProofHoleKind.sorry
