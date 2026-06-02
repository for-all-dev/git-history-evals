"""Profile-driven proof analyzer.

A single ``ProfileAnalyzer`` replaces the former per-language subclasses
(CoqAnalyzer/IsabelleAnalyzer/LeanAnalyzer). All language-specific knowledge —
which files are proof files, what a hole marker looks like, what a declaration
looks like — now comes from a ``CompiledProfile`` instead of hardcoded regexes.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

from scaffold.models import ProofHole
from scaffold.profile import CompiledProfile

__all__ = ["ProfileAnalyzer"]


class ProfileAnalyzer:
    """Finds proof holes / filled holes in source, configured entirely by a profile."""

    def __init__(self, compiled: CompiledProfile) -> None:
        self._c = compiled

    @property
    def proof_assistant(self) -> str:
        return self._c.profile.proof_assistant

    def matches_file(self, path: str) -> bool:
        """True if ``path`` is a proof file under any of the profile's globs."""
        if any(fnmatchcase(path, g) for g in self._c.profile.exclude_globs):
            return False
        return any(fnmatchcase(path, g) for g in self._c.profile.proof_file_globs)

    def find_holes(self, content: str, file_path: str = "") -> list[ProofHole]:
        """Find all proof holes in file content."""
        holes: list[ProofHole] = []
        lines = content.splitlines()
        pa = self.proof_assistant
        for line_idx, line in enumerate(lines):
            for pattern, kind in self._c.hole_res:
                for match in pattern.finditer(line):
                    holes.append(
                        ProofHole(
                            line=line_idx + 1,
                            column=match.start(),
                            kind=kind,
                            proof_assistant=pa,
                            context=self._extract_context(lines, line_idx),
                            enclosing_decl=self._find_enclosing_decl(lines, line_idx),
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
        return [
            h for h in parent_holes if (h.enclosing_decl, h.kind) not in child_decls
        ]

    def _find_enclosing_decl(self, lines: list[str], line_idx: int) -> str:
        """Walk backwards to find the enclosing declaration name (group 1)."""
        for i in range(line_idx, -1, -1):
            for pat in self._c.declaration_res:
                m = pat.search(lines[i])
                if m:
                    return m.group(1)
        return ""

    def _extract_context(self, lines: list[str], line_idx: int, radius: int = 3) -> str:
        start = max(0, line_idx - radius)
        end = min(len(lines), line_idx + radius + 1)
        return "\n".join(lines[start:end])
