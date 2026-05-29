"""Tests for the profile-driven ProofAnalyzer."""

from __future__ import annotations

from scaffold.analyzers import ProfileAnalyzer


class TestCoqAnalyzer:
    def test_proof_assistant(self, coq_analyzer: ProfileAnalyzer) -> None:
        assert coq_analyzer.proof_assistant == "coq"

    def test_matches_file(self, coq_analyzer: ProfileAnalyzer) -> None:
        assert coq_analyzer.matches_file("proof.v")
        assert coq_analyzer.matches_file("src/Foo.v")
        assert not coq_analyzer.matches_file("proof.lean")

    def test_find_holes_admitted(
        self, coq_analyzer: ProfileAnalyzer, sample_coq_with_holes: str
    ) -> None:
        holes = coq_analyzer.find_holes(sample_coq_with_holes)
        admitted_holes = [h for h in holes if h.kind == "admitted"]
        assert len(admitted_holes) == 2

    def test_find_holes_admit(
        self, coq_analyzer: ProfileAnalyzer, sample_coq_with_holes: str
    ) -> None:
        holes = coq_analyzer.find_holes(sample_coq_with_holes)
        admit_holes = [h for h in holes if h.kind == "admit"]
        assert len(admit_holes) == 1

    def test_enclosing_decl(
        self, coq_analyzer: ProfileAnalyzer, sample_coq_with_holes: str
    ) -> None:
        holes = coq_analyzer.find_holes(sample_coq_with_holes)
        decls = {h.enclosing_decl for h in holes}
        assert "foo" in decls
        assert "bar" in decls

    def test_find_filled_holes(
        self,
        coq_analyzer: ProfileAnalyzer,
        sample_coq_with_holes: str,
        sample_coq_filled: str,
    ) -> None:
        filled = coq_analyzer.find_filled_holes(
            sample_coq_with_holes, sample_coq_filled
        )
        assert len(filled) == 3  # 2 Admitted + 1 admit


class TestIsabelleAnalyzer:
    def test_proof_assistant(self, isabelle_analyzer: ProfileAnalyzer) -> None:
        assert isabelle_analyzer.proof_assistant == "isabelle"

    def test_find_holes(
        self, isabelle_analyzer: ProfileAnalyzer, sample_isabelle_with_holes: str
    ) -> None:
        holes = isabelle_analyzer.find_holes(sample_isabelle_with_holes)
        assert len(holes) == 2
        kinds = {h.kind for h in holes}
        assert "sorry" in kinds
        assert "oops" in kinds

    def test_enclosing_decl(
        self, isabelle_analyzer: ProfileAnalyzer, sample_isabelle_with_holes: str
    ) -> None:
        holes = isabelle_analyzer.find_holes(sample_isabelle_with_holes)
        decls = {h.enclosing_decl for h in holes}
        assert "test_thm" in decls
        assert "helper" in decls


class TestLeanAnalyzer:
    def test_proof_assistant(self, lean_analyzer: ProfileAnalyzer) -> None:
        assert lean_analyzer.proof_assistant == "lean4"

    def test_find_holes(
        self, lean_analyzer: ProfileAnalyzer, sample_lean_with_holes: str
    ) -> None:
        holes = lean_analyzer.find_holes(sample_lean_with_holes)
        assert len(holes) == 2
        assert all(h.kind == "sorry" for h in holes)

    def test_enclosing_decl(
        self, lean_analyzer: ProfileAnalyzer, sample_lean_with_holes: str
    ) -> None:
        holes = lean_analyzer.find_holes(sample_lean_with_holes)
        decls = {h.enclosing_decl for h in holes}
        assert "test_thm" in decls
        assert "helper" in decls

    def test_matches_file(self, lean_analyzer: ProfileAnalyzer) -> None:
        assert lean_analyzer.matches_file("Mathlib/Foo.lean")
        assert not lean_analyzer.matches_file("proof.v")
