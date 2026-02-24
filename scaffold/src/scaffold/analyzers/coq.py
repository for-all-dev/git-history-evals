"""Coq proof analyzer."""

from __future__ import annotations

import re

from scaffold.analyzers.base import ProofAnalyzer
from scaffold.models import ProofAssistant, ProofHoleKind


class CoqAnalyzer(ProofAnalyzer):
    """Analyzer for Coq .v files."""

    @property
    def proof_assistant(self) -> ProofAssistant:
        return ProofAssistant.coq

    @property
    def file_extensions(self) -> list[str]:
        return [".v"]

    @property
    def hole_markers(self) -> list[re.Pattern[str]]:
        return [
            re.compile(r"\bAdmitted\b"),
            re.compile(r"\badmit\b"),
        ]

    @property
    def declaration_pattern(self) -> re.Pattern[str]:
        return re.compile(
            r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Fact|Remark|Example|Definition|Fixpoint|Program)\s+(\w+)"
        )

    def _classify_hole(self, matched_text: str) -> ProofHoleKind:
        text = matched_text.strip()
        if text == "Admitted":
            return ProofHoleKind.admitted
        if text == "admit":
            return ProofHoleKind.admit
        return ProofHoleKind.placeholder
