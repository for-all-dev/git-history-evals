"""Tests for proof analyzers."""

from __future__ import annotations

from scaffold.analyzers.coq import CoqAnalyzer
from scaffold.analyzers.isabelle import IsabelleAnalyzer
from scaffold.analyzers.lean import LeanAnalyzer
from scaffold.models import ProofAssistant, ProofHoleKind


class TestCoqAnalyzer:
    def setup_method(self) -> None:
        self.analyzer = CoqAnalyzer()

    def test_proof_assistant(self) -> None:
        assert self.analyzer.proof_assistant == ProofAssistant.coq

    def test_file_extensions(self) -> None:
        assert self.analyzer.file_extensions == [".v"]
        assert self.analyzer.matches_file("proof.v")
        assert not self.analyzer.matches_file("proof.lean")

    def test_find_holes_admitted(self, sample_coq_with_holes: str) -> None:
        holes = self.analyzer.find_holes(sample_coq_with_holes)
        admitted_holes = [h for h in holes if h.kind == ProofHoleKind.admitted]
        assert len(admitted_holes) == 2

    def test_find_holes_admit(self, sample_coq_with_holes: str) -> None:
        holes = self.analyzer.find_holes(sample_coq_with_holes)
        admit_holes = [h for h in holes if h.kind == ProofHoleKind.admit]
        assert len(admit_holes) == 1

    def test_enclosing_decl(self, sample_coq_with_holes: str) -> None:
        holes = self.analyzer.find_holes(sample_coq_with_holes)
        decls = {h.enclosing_decl for h in holes}
        assert "foo" in decls
        assert "bar" in decls

    def test_find_filled_holes(
        self, sample_coq_with_holes: str, sample_coq_filled: str
    ) -> None:
        filled = self.analyzer.find_filled_holes(
            sample_coq_with_holes, sample_coq_filled
        )
        assert len(filled) == 3  # 2 Admitted + 1 admit


class TestIsabelleAnalyzer:
    def setup_method(self) -> None:
        self.analyzer = IsabelleAnalyzer()

    def test_proof_assistant(self) -> None:
        assert self.analyzer.proof_assistant == ProofAssistant.isabelle

    def test_find_holes(self, sample_isabelle_with_holes: str) -> None:
        holes = self.analyzer.find_holes(sample_isabelle_with_holes)
        assert len(holes) == 2
        kinds = {h.kind for h in holes}
        assert ProofHoleKind.sorry in kinds
        assert ProofHoleKind.oops in kinds

    def test_enclosing_decl(self, sample_isabelle_with_holes: str) -> None:
        holes = self.analyzer.find_holes(sample_isabelle_with_holes)
        decls = {h.enclosing_decl for h in holes}
        assert "test_thm" in decls
        assert "helper" in decls


class TestLeanAnalyzer:
    def setup_method(self) -> None:
        self.analyzer = LeanAnalyzer()

    def test_proof_assistant(self) -> None:
        assert self.analyzer.proof_assistant == ProofAssistant.lean4

    def test_find_holes(self, sample_lean_with_holes: str) -> None:
        holes = self.analyzer.find_holes(sample_lean_with_holes)
        assert len(holes) == 2
        assert all(h.kind == ProofHoleKind.sorry for h in holes)

    def test_enclosing_decl(self, sample_lean_with_holes: str) -> None:
        holes = self.analyzer.find_holes(sample_lean_with_holes)
        decls = {h.enclosing_decl for h in holes}
        assert "test_thm" in decls
        assert "helper" in decls

    def test_matches_file(self) -> None:
        assert self.analyzer.matches_file("Mathlib/Foo.lean")
        assert not self.analyzer.matches_file("proof.v")
