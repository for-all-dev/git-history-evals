"""Abstract base class for proof analyzers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from scaffold.models import ProofAssistant, ProofHole, ProofHoleKind


class ProofAnalyzer(ABC):
    """Base class for language-specific proof analyzers.

    Each subclass knows how to find proof holes (sorry, Admitted, etc.)
    in source files for a specific proof assistant.
    """

    @property
    @abstractmethod
    def proof_assistant(self) -> ProofAssistant: ...

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]: ...

    @property
    @abstractmethod
    def hole_markers(self) -> list[re.Pattern[str]]: ...

    @property
    @abstractmethod
    def declaration_pattern(self) -> re.Pattern[str]:
        """Regex matching theorem/lemma declarations. Group 1 should be the name."""
        ...

    def find_holes(self, content: str, file_path: str = "") -> list[ProofHole]:
        """Find all proof holes in file content."""
        holes: list[ProofHole] = []
        lines = content.splitlines()
        for line_idx, line in enumerate(lines):
            for pattern in self.hole_markers:
                for match in pattern.finditer(line):
                    enclosing = self._find_enclosing_decl(lines, line_idx)
                    context = self._extract_context(lines, line_idx)
                    holes.append(
                        ProofHole(
                            line=line_idx + 1,
                            column=match.start(),
                            kind=self._classify_hole(match.group()),
                            proof_assistant=self.proof_assistant,
                            context=context,
                            enclosing_decl=enclosing,
                        )
                    )
        return holes

    def find_filled_holes(
        self, parent_content: str, child_content: str, file_path: str = ""
    ) -> list[ProofHole]:
        """Find holes present in parent but absent in child (i.e. filled)."""
        parent_holes = self.find_holes(parent_content, file_path)
        child_holes = self.find_holes(child_content, file_path)

        child_decls = {(h.enclosing_decl, h.kind) for h in child_holes}
        filled = [
            h for h in parent_holes if (h.enclosing_decl, h.kind) not in child_decls
        ]
        return filled

    def matches_file(self, path: str) -> bool:
        """Check if a file path is a proof file for this analyzer."""
        return any(path.endswith(ext) for ext in self.file_extensions)

    @abstractmethod
    def _classify_hole(self, matched_text: str) -> ProofHoleKind: ...

    def _find_enclosing_decl(self, lines: list[str], line_idx: int) -> str:
        """Walk backwards to find the enclosing theorem/lemma declaration."""
        for i in range(line_idx, -1, -1):
            m = self.declaration_pattern.search(lines[i])
            if m:
                return m.group(1)
        return ""

    def _extract_context(self, lines: list[str], line_idx: int, radius: int = 3) -> str:
        """Extract surrounding lines for context."""
        start = max(0, line_idx - radius)
        end = min(len(lines), line_idx + radius + 1)
        return "\n".join(lines[start:end])
