"""Isabelle proof analyzer."""

from __future__ import annotations

import re

from scaffold.analyzers.base import ProofAnalyzer
from scaffold.models import ProofAssistant, ProofHoleKind


class IsabelleAnalyzer(ProofAnalyzer):
    """Analyzer for Isabelle .thy files."""

    @property
    def proof_assistant(self) -> ProofAssistant:
        return ProofAssistant.isabelle

    @property
    def file_extensions(self) -> list[str]:
        return [".thy"]

    @property
    def hole_markers(self) -> list[re.Pattern[str]]:
        return [
            re.compile(r"\bsorry\b"),
            re.compile(r"\boops\b"),
        ]

    @property
    def declaration_pattern(self) -> re.Pattern[str]:
        return re.compile(
            r"^\s*(?:theorem|lemma|corollary|proposition|schematic_goal)\s+(\w+)"
        )

    def _classify_hole(self, matched_text: str) -> ProofHoleKind:
        text = matched_text.strip()
        if text == "sorry":
            return ProofHoleKind.sorry
        if text == "oops":
            return ProofHoleKind.oops
        return ProofHoleKind.placeholder
