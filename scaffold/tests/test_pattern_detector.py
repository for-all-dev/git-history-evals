"""Tests for pattern_detector.py — classification, enrichment, keywords, tactic groups.

Covers:
  - classify_commit priority ordering (infra > proof_complete > spec_change > ...)
  - Structural heuristic (body-free, net deletions, proof context)
  - enrich_record (message-only first pass)
  - enrich_record_with_diff (diff-based second pass) — mocked git layer
  - proof_new / spec_change preservation across diff enrichment
  - extract_keywords
  - assign_tactic_groups
  - Edge cases: empty signal banks, empty tactic vocabulary, no proof files
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scaffold.models import CommitClass, CommitRecord
from scaffold.pattern_detector import (
    assign_tactic_groups,
    classify_commit,
    enrich_record,
    enrich_record_with_diff,
    extract_keywords,
)
from scaffold.profile import CompiledProfile, HoleMarker, RepoProfile


# ---------------------------------------------------------------------------
# Fixtures — profiles with commit_signals and tactic_groups populated
# ---------------------------------------------------------------------------

_COQ_DECL = r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Fact|Remark|Example|Definition|Fixpoint|Program)\s+(\w+)"


@pytest.fixture
def coq_signals_profile() -> RepoProfile:
    """Coq profile with commit signals, tactic vocabulary, and tactic groups."""
    return RepoProfile(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[
            HoleMarker(regex=r"\bAdmitted\b", kind="admitted"),
            HoleMarker(regex=r"\badmit\b", kind="admit"),
        ],
        declaration_patterns=[_COQ_DECL],
        commit_signals={
            "infra": [r"\bdeps?\b", r"\bci\b", r"\bbump\b", r"\bopam\b"],
            "proof_complete": [
                r"\bclose\b",
                r"\bfill\b",
                r"\bfinish\b",
                r"\bremove admitted\b",
            ],
            "spec_change": [r"\bchange statement\b", r"\bmodify spec\b"],
            "proof_new": [r"\badd lemma\b", r"\bnew theorem\b"],
            "proof_add": [r"\bprogress\b", r"\bpartial\b", r"\bwip\b"],
            "refactor": [r"\brefactor\b", r"\brename\b", r"\bmove\b"],
            "fix": [r"\bfix\b", r"\bbugfix\b"],
        },
        proof_context_regex=r"\bproof\b|\blemma\b|\btheorem\b|\bAdmitted\b|\bQed\b",
        tactic_vocabulary=[
            "intros",
            "rewrite",
            "apply",
            "simpl",
            "reflexivity",
            "induction",
            "destruct",
            "auto",
            "omega",
            "lia",
        ],
        tactic_groups={
            "intros": "hypothesis_management",
            "rewrite": "rewrite_reduce",
            "apply": "application",
            "simpl": "rewrite_reduce",
            "reflexivity": "rewrite_reduce",
            "induction": "case_induction",
            "destruct": "case_induction",
            "auto": "meta_tactical",
            "omega": "arithmetic_algebra",
            "lia": "arithmetic_algebra",
        },
        structural_terms=["lemma", "theorem", "proof", "qed", "admitted"],
        domain_terms=["montgomery", "x25519"],
    )


@pytest.fixture
def compiled(coq_signals_profile: RepoProfile) -> CompiledProfile:
    return coq_signals_profile.compiled()


@pytest.fixture
def empty_signals_profile() -> RepoProfile:
    """Profile with no commit signals at all — everything should classify as other."""
    return RepoProfile(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[HoleMarker(regex=r"\bAdmitted\b", kind="admitted")],
        declaration_patterns=[_COQ_DECL],
    )


@pytest.fixture
def compiled_empty(empty_signals_profile: RepoProfile) -> CompiledProfile:
    return empty_signals_profile.compiled()


def _make_record(
    *,
    subject: str = "",
    body: str = "",
    touches_proof: bool = False,
    proof_files: list[str] | None = None,
    insertions: int = 0,
    deletions: int = 0,
    parent_hashes: list[str] | None = None,
    commit_class: CommitClass = CommitClass.other,
) -> CommitRecord:
    """Helper to build a CommitRecord with only the fields that matter for tests."""
    return CommitRecord(
        hash="abc123",
        parent_hashes=parent_hashes if parent_hashes is not None else ["def456"],
        date="2025-01-01T00:00:00+00:00",
        message_subject=subject,
        message_body=body,
        touches_proof_files=touches_proof,
        proof_files_changed=proof_files or [],
        insertions=insertions,
        deletions=deletions,
        commit_class=commit_class,
    )


# ===========================================================================
# classify_commit — priority ordering
# ===========================================================================


class TestClassifyCommit:
    """Decision tree classification with priority ordering."""

    def test_infra_subject_only(self, compiled: CompiledProfile) -> None:
        """Infra signals in subject without proof context → infra."""
        r = _make_record(subject="bump deps")
        assert classify_commit(r, compiled) == CommitClass.infra

    def test_infra_does_not_fire_with_proof_context(
        self, compiled: CompiledProfile
    ) -> None:
        """Infra signal + proof context in subject → NOT infra."""
        r = _make_record(subject="bump deps and fix proof lemma")
        assert classify_commit(r, compiled) != CommitClass.infra

    def test_proof_complete_explicit(self, compiled: CompiledProfile) -> None:
        """proof_complete signal + proof context → proof_complete."""
        r = _make_record(subject="close admitted proof", touches_proof=True)
        assert classify_commit(r, compiled) == CommitClass.proof_complete

    def test_proof_complete_needs_proof_context(
        self, compiled: CompiledProfile
    ) -> None:
        """proof_complete signal without proof context → NOT proof_complete."""
        r = _make_record(subject="close the issue")
        assert classify_commit(r, compiled) != CommitClass.proof_complete

    def test_proof_complete_structural_heuristic(
        self, compiled: CompiledProfile
    ) -> None:
        """Body-free commit with net deletions + proof context + proof files → proof_complete."""
        r = _make_record(
            subject="lemma cleanup",
            body="",
            touches_proof=True,
            insertions=2,
            deletions=10,
        )
        assert classify_commit(r, compiled) == CommitClass.proof_complete

    def test_structural_heuristic_no_net_deletions(
        self, compiled: CompiledProfile
    ) -> None:
        """Structural heuristic doesn't fire when insertions >= deletions."""
        r = _make_record(
            subject="lemma work",
            body="",
            touches_proof=True,
            insertions=10,
            deletions=2,
        )
        # Should not be proof_complete via structural heuristic
        assert classify_commit(r, compiled) != CommitClass.proof_complete

    def test_structural_heuristic_requires_no_body(
        self, compiled: CompiledProfile
    ) -> None:
        """Structural heuristic doesn't fire when body is present."""
        r = _make_record(
            subject="lemma cleanup",
            body="some long body description",
            touches_proof=True,
            insertions=2,
            deletions=10,
        )
        result = classify_commit(r, compiled)
        # With body present, the structural heuristic won't fire.
        # It might still be proof_complete via the explicit signal if "lemma" matches
        # proof_context. Let's just check it went through a different code path.
        # Actually, "lemma cleanup" has no proof_complete signal, so it won't be
        # proof_complete at all.
        assert result != CommitClass.proof_complete

    def test_spec_change(self, compiled: CompiledProfile) -> None:
        """spec_change signal + proof files → spec_change."""
        r = _make_record(subject="change statement of foo", touches_proof=True)
        assert classify_commit(r, compiled) == CommitClass.spec_change

    def test_spec_change_needs_proof_files(self, compiled: CompiledProfile) -> None:
        """spec_change signal without proof files → not spec_change."""
        r = _make_record(subject="change statement of foo", touches_proof=False)
        assert classify_commit(r, compiled) != CommitClass.spec_change

    def test_proof_new(self, compiled: CompiledProfile) -> None:
        """proof_new signal + proof files → proof_new."""
        r = _make_record(subject="add lemma for commutativity", touches_proof=True)
        assert classify_commit(r, compiled) == CommitClass.proof_new

    def test_proof_add(self, compiled: CompiledProfile) -> None:
        """proof_add signal + proof files → proof_add."""
        r = _make_record(subject="wip on induction case", touches_proof=True)
        assert classify_commit(r, compiled) == CommitClass.proof_add

    def test_refactor_on_proof_files_becomes_proof_add(
        self, compiled: CompiledProfile
    ) -> None:
        """Refactor touching proof files → reclassified as proof_add."""
        r = _make_record(subject="refactor helper module", touches_proof=True)
        assert classify_commit(r, compiled) == CommitClass.proof_add

    def test_fix_on_proof_files_becomes_proof_add(
        self, compiled: CompiledProfile
    ) -> None:
        """Fix touching proof files → reclassified as proof_add."""
        r = _make_record(subject="fix extraction bug", touches_proof=True)
        assert classify_commit(r, compiled) == CommitClass.proof_add

    def test_refactor_on_non_proof_files(self, compiled: CompiledProfile) -> None:
        """Refactor not touching proof files → stays refactor."""
        r = _make_record(subject="refactor build scripts", touches_proof=False)
        assert classify_commit(r, compiled) == CommitClass.refactor

    def test_fix_on_non_proof_files(self, compiled: CompiledProfile) -> None:
        """Fix not touching proof files → stays fix."""
        r = _make_record(subject="fix typo in readme", touches_proof=False)
        assert classify_commit(r, compiled) == CommitClass.fix

    def test_proof_file_touching_with_no_signals_becomes_proof_add(
        self, compiled: CompiledProfile
    ) -> None:
        """Any commit touching proof files with no other signal → proof_add."""
        r = _make_record(subject="minor tweaks", touches_proof=True)
        assert classify_commit(r, compiled) == CommitClass.proof_add

    def test_other_fallback(self, compiled: CompiledProfile) -> None:
        """No signals, no proof files → other."""
        r = _make_record(subject="update changelog", touches_proof=False)
        assert classify_commit(r, compiled) == CommitClass.other

    def test_priority_infra_over_proof_complete(
        self, compiled: CompiledProfile
    ) -> None:
        """Infra checked first — if subject matches infra and not proof context, infra wins."""
        r = _make_record(subject="bump ci config", touches_proof=False)
        assert classify_commit(r, compiled) == CommitClass.infra

    def test_priority_proof_complete_over_spec_change(
        self, compiled: CompiledProfile
    ) -> None:
        """proof_complete + spec_change signals → proof_complete wins (higher priority)."""
        r = _make_record(
            subject="fill in proof, change statement for lemma", touches_proof=True
        )
        assert classify_commit(r, compiled) == CommitClass.proof_complete

    def test_body_signals_contribute(self, compiled: CompiledProfile) -> None:
        """Signals in message body are also matched (except for infra subject-only check)."""
        r = _make_record(
            subject="some commit",
            body="close the proof of the lemma",
            touches_proof=True,
        )
        assert classify_commit(r, compiled) == CommitClass.proof_complete

    def test_structural_heuristic_blocked_by_infra_signal(
        self, compiled: CompiledProfile
    ) -> None:
        """Structural heuristic has a `not has("infra", text)` guard.

        A commit with proof context + net deletions + no body, but with an infra
        keyword in the subject, should NOT trigger the structural heuristic.
        """
        r = _make_record(
            subject="bump proof deps",
            body="",
            touches_proof=True,
            insertions=1,
            deletions=5,
        )
        # "bump" matches infra, "proof" matches proof_context.
        # Infra fires first (subject has infra + proof_context → infra suppressed).
        # But even if infra didn't fire, the structural heuristic has its own
        # `not has("infra", text)` guard. Let's verify it's not proof_complete.
        result = classify_commit(r, compiled)
        assert result != CommitClass.proof_complete


# ===========================================================================
# classify_commit — empty signal banks
# ===========================================================================


class TestClassifyCommitEmptySignals:
    """With empty commit_signals, classification degrades gracefully."""

    def test_no_signals_proof_files_becomes_proof_add(
        self, compiled_empty: CompiledProfile
    ) -> None:
        """With no signals, touching proof files still → proof_add (catch-all rule 8)."""
        r = _make_record(subject="fill admitted lemma", touches_proof=True)
        assert classify_commit(r, compiled_empty) == CommitClass.proof_add

    def test_no_signals_no_proof_files_becomes_other(
        self, compiled_empty: CompiledProfile
    ) -> None:
        """With no signals and no proof files → other."""
        r = _make_record(subject="update readme")
        assert classify_commit(r, compiled_empty) == CommitClass.other


# ===========================================================================
# extract_keywords
# ===========================================================================


class TestExtractKeywords:
    def test_extracts_structural_terms(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("add lemma for commutativity", "", compiled)
        assert "lemma" in kw

    def test_extracts_tactic_names(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("use rewrite and intros", "", compiled)
        assert "rewrite" in kw
        assert "intros" in kw

    def test_extracts_domain_terms(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("montgomery ladder for x25519", "", compiled)
        assert "montgomery" in kw
        assert "x25519" in kw

    def test_extracts_hole_kinds(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("remove admitted from proof", "", compiled)
        assert "admitted" in kw

    def test_deduplicates(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("proof proof proof lemma lemma", "", compiled)
        assert kw.count("proof") == 1
        assert kw.count("lemma") == 1

    def test_case_insensitive(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("Admitted ADMITTED admitted", "", compiled)
        assert kw.count("admitted") == 1

    def test_body_contributes(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("some subject", "uses induction and auto", compiled)
        assert "induction" in kw
        assert "auto" in kw

    def test_empty_inputs(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("", "", compiled)
        assert kw == []

    def test_no_matches(self, compiled: CompiledProfile) -> None:
        kw = extract_keywords("unrelated commit message", "", compiled)
        assert kw == []

    def test_empty_vocabulary(self, compiled_empty: CompiledProfile) -> None:
        """With no structural/domain/tactic terms, only hole kinds are extracted."""
        kw = extract_keywords("remove admitted lemma", "", compiled_empty)
        # "lemma" is not a structural term in the empty profile → not extracted
        assert "lemma" not in kw
        # But "admitted" IS a hole kind from hole_markers → still extracted
        assert "admitted" in kw


# ===========================================================================
# assign_tactic_groups
# ===========================================================================


class TestAssignTacticGroups:
    def test_maps_tactics_to_groups(self, compiled: CompiledProfile) -> None:
        groups = assign_tactic_groups(["intros", "rewrite", "apply"], compiled)
        assert "hypothesis_management" in groups
        assert "rewrite_reduce" in groups
        assert "application" in groups

    def test_deduplicates_groups(self, compiled: CompiledProfile) -> None:
        """Multiple tactics in the same group → group appears once."""
        groups = assign_tactic_groups(
            ["rewrite", "simpl", "reflexivity"], compiled
        )
        assert groups.count("rewrite_reduce") == 1

    def test_preserves_order(self, compiled: CompiledProfile) -> None:
        """Groups appear in the order their first tactic is encountered."""
        groups = assign_tactic_groups(["induction", "intros", "auto"], compiled)
        assert groups.index("case_induction") < groups.index(
            "hypothesis_management"
        )

    def test_unknown_tactics_skipped(self, compiled: CompiledProfile) -> None:
        """Tactics not in tactic_groups are silently skipped."""
        groups = assign_tactic_groups(["unknown_tactic", "intros"], compiled)
        assert groups == ["hypothesis_management"]

    def test_empty_input(self, compiled: CompiledProfile) -> None:
        assert assign_tactic_groups([], compiled) == []

    def test_case_insensitive(self, compiled: CompiledProfile) -> None:
        groups = assign_tactic_groups(["Intros", "REWRITE"], compiled)
        assert "hypothesis_management" in groups
        assert "rewrite_reduce" in groups

    def test_empty_tactic_groups(self, compiled_empty: CompiledProfile) -> None:
        """Profile with no tactic_groups → returns empty."""
        groups = assign_tactic_groups(["intros", "rewrite"], compiled_empty)
        assert groups == []


# ===========================================================================
# enrich_record (message-only first pass)
# ===========================================================================


class TestEnrichRecord:
    def test_sets_commit_class(self, compiled: CompiledProfile) -> None:
        r = _make_record(subject="close the admitted proof of lemma")
        enriched = enrich_record(r, compiled)
        assert enriched.commit_class == CommitClass.proof_complete

    def test_sets_keywords(self, compiled: CompiledProfile) -> None:
        r = _make_record(subject="add lemma using rewrite and induction")
        enriched = enrich_record(r, compiled)
        assert "lemma" in enriched.keywords
        assert "rewrite" in enriched.keywords

    def test_sets_confidence_heuristic(self, compiled: CompiledProfile) -> None:
        r = _make_record(subject="some commit")
        enriched = enrich_record(r, compiled)
        assert enriched.class_confidence == "heuristic"

    def test_returns_copy(self, compiled: CompiledProfile) -> None:
        """enrich_record should not mutate the original record."""
        r = _make_record(subject="fill proof of lemma")
        enriched = enrich_record(r, compiled)
        assert r.commit_class == CommitClass.other  # original unchanged
        assert enriched.commit_class != CommitClass.other


# ===========================================================================
# enrich_record_with_diff (diff-based second pass)
# ===========================================================================


class TestEnrichRecordWithDiff:
    """Tests for diff-based enrichment, with git_walker.analyze_proof_diff mocked."""

    def test_sorry_removed_becomes_proof_complete(
        self, compiled: CompiledProfile
    ) -> None:
        r = _make_record(
            subject="fill proof",
            touches_proof=True,
            proof_files=["proof.v"],
        )
        diff_data = {
            "sorry_removed": True,
            "net_proof_lines": 5,
            "tactic_tags": ["intros", "rewrite"],
            "proof_style": ["tactic_mode"],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        assert enriched.commit_class == CommitClass.proof_complete
        assert enriched.class_confidence == "diff"
        assert enriched.diff_sorry_removed is True
        assert enriched.tactic_tags == ["intros", "rewrite"]
        assert enriched.proof_style == ["tactic_mode"]

    def test_net_negative_lines_becomes_proof_optimise(
        self, compiled: CompiledProfile
    ) -> None:
        r = _make_record(
            subject="clean up proof",
            touches_proof=True,
            proof_files=["proof.v"],
        )
        diff_data = {
            "sorry_removed": False,
            "net_proof_lines": -10,
            "tactic_tags": [],
            "proof_style": [],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        assert enriched.commit_class == CommitClass.proof_optimise

    def test_net_positive_lines_becomes_proof_add(
        self, compiled: CompiledProfile
    ) -> None:
        r = _make_record(
            subject="work on proof",
            touches_proof=True,
            proof_files=["proof.v"],
        )
        diff_data = {
            "sorry_removed": False,
            "net_proof_lines": 15,
            "tactic_tags": ["auto"],
            "proof_style": ["tactic_mode"],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        assert enriched.commit_class == CommitClass.proof_add

    def test_zero_net_lines_becomes_proof_add(
        self, compiled: CompiledProfile
    ) -> None:
        """net_proof_lines == 0 (unchanged) still → proof_add."""
        r = _make_record(
            subject="tweak proof",
            touches_proof=True,
            proof_files=["proof.v"],
        )
        diff_data = {
            "sorry_removed": False,
            "net_proof_lines": 0,
            "tactic_tags": [],
            "proof_style": [],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        assert enriched.commit_class == CommitClass.proof_add

    def test_diff_downgrades_proof_complete_to_proof_add(
        self, compiled: CompiledProfile
    ) -> None:
        """proof_complete from message pass is NOT preserved by the diff pass.

        Only proof_new and spec_change are in the preservation list (lines 185-186
        of pattern_detector.py). If the message heuristic said proof_complete but
        the diff shows no sorry removal and positive net lines, the diff overrides
        to proof_add.
        """
        r = _make_record(
            subject="close the proof of lemma",
            touches_proof=True,
            proof_files=["proof.v"],
            commit_class=CommitClass.proof_complete,
        )
        diff_data = {
            "sorry_removed": False,
            "net_proof_lines": 10,
            "tactic_tags": ["intros"],
            "proof_style": ["tactic_mode"],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        # Diff is authoritative: no sorry removed + positive lines → proof_add,
        # overriding the message-level proof_complete.
        assert enriched.commit_class == CommitClass.proof_add

    def test_preserves_proof_new(self, compiled: CompiledProfile) -> None:
        """proof_new from message pass is preserved even when diff says proof_add."""
        r = _make_record(
            subject="add lemma for commutativity",
            touches_proof=True,
            proof_files=["proof.v"],
            commit_class=CommitClass.proof_new,
        )
        diff_data = {
            "sorry_removed": False,
            "net_proof_lines": 20,
            "tactic_tags": ["intros"],
            "proof_style": ["tactic_mode"],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        assert enriched.commit_class == CommitClass.proof_new

    def test_preserves_spec_change(self, compiled: CompiledProfile) -> None:
        """spec_change from message pass is preserved."""
        r = _make_record(
            subject="change statement of lemma",
            touches_proof=True,
            proof_files=["proof.v"],
            commit_class=CommitClass.spec_change,
        )
        diff_data = {
            "sorry_removed": False,
            "net_proof_lines": 3,
            "tactic_tags": [],
            "proof_style": [],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        assert enriched.commit_class == CommitClass.spec_change

    def test_no_proof_files_returns_unchanged(
        self, compiled: CompiledProfile
    ) -> None:
        """Record with no proof_files_changed → returned as-is (no diff call)."""
        r = _make_record(subject="update readme", proof_files=[])
        with patch(
            "scaffold.git_walker.analyze_proof_diff"
        ) as mock_diff:
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        mock_diff.assert_not_called()
        assert enriched.commit_class == r.commit_class

    def test_sets_confidence_diff(self, compiled: CompiledProfile) -> None:
        r = _make_record(
            subject="proof work",
            touches_proof=True,
            proof_files=["proof.v"],
        )
        diff_data = {
            "sorry_removed": False,
            "net_proof_lines": 1,
            "tactic_tags": [],
            "proof_style": [],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        assert enriched.class_confidence == "diff"

    def test_populates_diff_fields(self, compiled: CompiledProfile) -> None:
        r = _make_record(
            subject="work",
            touches_proof=True,
            proof_files=["proof.v"],
        )
        diff_data = {
            "sorry_removed": True,
            "net_proof_lines": 7,
            "tactic_tags": ["apply", "destruct"],
            "proof_style": ["tactic_mode", "ssreflect"],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ):
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        assert enriched.diff_sorry_removed is True
        assert enriched.diff_net_proof_lines == 7
        assert enriched.tactic_tags == ["apply", "destruct"]
        assert enriched.proof_style == ["tactic_mode", "ssreflect"]

    def test_empty_parent_hashes_passes_empty_string(
        self, compiled: CompiledProfile
    ) -> None:
        """Initial commit (no parents) passes empty string to analyze_proof_diff.

        analyze_proof_diff has a `if not parent_hash: return empty` guard, so
        this should return the record unchanged except for diff-field defaults.
        """
        r = _make_record(
            subject="initial commit",
            touches_proof=True,
            proof_files=["proof.v"],
            parent_hashes=[],
        )
        # analyze_proof_diff returns empty dict when parent_hash is empty
        empty_diff = {
            "sorry_removed": False,
            "net_proof_lines": 0,
            "tactic_tags": [],
            "proof_style": [],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=empty_diff
        ) as mock_diff:
            enriched = enrich_record_with_diff(r, "/fake/repo", compiled)
        # Verify empty string was passed as parent_hash
        mock_diff.assert_called_once()
        call_args = mock_diff.call_args
        assert call_args[0][1] == ""  # parent_hash arg
        assert enriched.commit_class == CommitClass.proof_add

    def test_merge_commit_uses_first_parent(
        self, compiled: CompiledProfile
    ) -> None:
        """Merge commits (multiple parents) diff against the first parent only."""
        r = _make_record(
            subject="merge branch",
            touches_proof=True,
            proof_files=["proof.v"],
            parent_hashes=["parent1_sha", "parent2_sha"],
        )
        diff_data = {
            "sorry_removed": False,
            "net_proof_lines": 5,
            "tactic_tags": [],
            "proof_style": [],
        }
        with patch(
            "scaffold.git_walker.analyze_proof_diff", return_value=diff_data
        ) as mock_diff:
            enrich_record_with_diff(r, "/fake/repo", compiled)
        mock_diff.assert_called_once()
        call_args = mock_diff.call_args
        assert call_args[0][1] == "parent1_sha"  # first parent used

    def test_analyze_proof_diff_exception_propagates(
        self, compiled: CompiledProfile
    ) -> None:
        """If analyze_proof_diff raises, the exception propagates uncaught."""
        r = _make_record(
            subject="proof work",
            touches_proof=True,
            proof_files=["proof.v"],
        )
        with patch(
            "scaffold.git_walker.analyze_proof_diff",
            side_effect=RuntimeError("git subprocess failed"),
        ):
            with pytest.raises(RuntimeError, match="git subprocess failed"):
                enrich_record_with_diff(r, "/fake/repo", compiled)


# ===========================================================================
# enrich_record_with_diff — integration with tmp_git_repo
# ===========================================================================


def _git(repo, *args: str):
    """Run a git command in the given repo."""
    import os
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
        },
    )


def _commit_hashes(repo_path):
    """Return list of commit hashes oldest-first."""
    result = _git(repo_path, "log", "--format=%H", "--reverse")
    return result.stdout.strip().split("\n")


class TestEnrichRecordWithDiffIntegration:
    """Integration tests using a real git repo (tmp_git_repo fixture)."""

    def test_fill_admitted_detected_as_proof_complete(
        self, tmp_git_repo, compiled: CompiledProfile
    ) -> None:
        """Commit 2 fills an Admitted → should detect sorry_removed via real diff."""
        hashes = _commit_hashes(tmp_git_repo)
        # Commit 2 (index 1) fills the hole from commit 1 (index 0)
        r = CommitRecord(
            hash=hashes[1],
            parent_hashes=[hashes[0]],
            date="2025-01-01T00:00:00+00:00",
            message_subject="Complete proof of add_comm",
            proof_files_changed=["proof.v"],
            touches_proof_files=True,
            insertions=5,
            deletions=1,
        )
        enriched = enrich_record_with_diff(r, str(tmp_git_repo), compiled)
        assert enriched.diff_sorry_removed is True
        assert enriched.commit_class == CommitClass.proof_complete
        assert enriched.class_confidence == "diff"

    def test_add_admitted_not_sorry_removed(
        self, tmp_git_repo, compiled: CompiledProfile
    ) -> None:
        """Commit 3 adds a new file with admit/Admitted → sorry NOT removed (net increase)."""
        hashes = _commit_hashes(tmp_git_repo)
        # Commit 3 (index 2) adds helpers.v with Admitted
        r = CommitRecord(
            hash=hashes[2],
            parent_hashes=[hashes[1]],
            date="2025-01-01T00:00:00+00:00",
            message_subject="Add helper lemma (incomplete)",
            proof_files_changed=["helpers.v"],
            touches_proof_files=True,
            insertions=4,
            deletions=0,
        )
        enriched = enrich_record_with_diff(r, str(tmp_git_repo), compiled)
        assert enriched.diff_sorry_removed is False
        assert enriched.commit_class == CommitClass.proof_add

    def test_initial_commit_no_parent(
        self, tmp_git_repo, compiled: CompiledProfile
    ) -> None:
        """The first commit has no parent; analyze_proof_diff returns empty signals."""
        hashes = _commit_hashes(tmp_git_repo)
        r = CommitRecord(
            hash=hashes[0],
            parent_hashes=[],
            date="2025-01-01T00:00:00+00:00",
            message_subject="Add theorem with Admitted",
            proof_files_changed=["proof.v"],
            touches_proof_files=True,
            insertions=3,
            deletions=0,
        )
        enriched = enrich_record_with_diff(r, str(tmp_git_repo), compiled)
        # No parent → analyze_proof_diff returns empty → net_proof_lines=0 → proof_add
        assert enriched.diff_sorry_removed is False
        assert enriched.commit_class == CommitClass.proof_add
        assert enriched.class_confidence == "diff"


class TestProofOptimiseIntegration:
    """Integration test for proof_optimise: a commit that shortens a proof."""

    @pytest.fixture
    def repo_with_shortened_proof(self, tmp_git_repo: Path) -> Path:
        """Extend tmp_git_repo with a 4th commit that shortens proof.v.

        After commit 2, proof.v has a 6-line verbose proof. This commit
        replaces it with a 2-line proof (same theorem, fewer lines, still Qed).
        The diff should have net negative proof lines → proof_optimise.
        """
        import textwrap

        proof_v = tmp_git_repo / "proof.v"
        proof_v.write_text(
            textwrap.dedent("""\
            Theorem add_comm : forall n m : nat, n + m = m + n.
            Proof. intros. lia. Qed.
        """)
        )
        _git(tmp_git_repo, "add", "proof.v")
        _git(tmp_git_repo, "commit", "-m", "Shorten proof of add_comm")
        return tmp_git_repo

    def test_shortened_proof_detected_as_proof_optimise(
        self, repo_with_shortened_proof: Path, compiled: CompiledProfile
    ) -> None:
        """A commit that shortens a proof (net negative lines, no sorry change) → proof_optimise."""
        repo = repo_with_shortened_proof
        hashes = _commit_hashes(repo)
        # Commit 4 (index 3) shortens the proof from commit 2 (index 1)
        r = CommitRecord(
            hash=hashes[3],
            parent_hashes=[hashes[2]],
            date="2025-01-01T00:00:00+00:00",
            message_subject="Shorten proof of add_comm",
            proof_files_changed=["proof.v"],
            touches_proof_files=True,
            insertions=2,
            deletions=6,
        )
        enriched = enrich_record_with_diff(r, str(repo), compiled)
        assert enriched.diff_sorry_removed is False
        assert enriched.diff_net_proof_lines < 0
        assert enriched.commit_class == CommitClass.proof_optimise
        assert enriched.class_confidence == "diff"
