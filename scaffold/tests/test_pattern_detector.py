"""Tests for classify_commit — including data-driven classification_priority."""

from __future__ import annotations

import pytest

from scaffold.models import CommitClass, CommitRecord
from scaffold.pattern_detector import classify_commit
from scaffold.profile import HoleMarker, RepoProfile


def _make_profile(**overrides: object) -> RepoProfile:
    """Build a Coq-ish profile with commit signals suitable for classification tests."""
    defaults: dict[str, object] = {
        "proof_assistant": "coq",
        "proof_file_globs": ["*.v"],
        "hole_markers": [HoleMarker(regex=r"\bAdmitted\b", kind="admitted")],
        "declaration_patterns": [
            r"^\s*(?:Theorem|Lemma)\s+(\w+)",
        ],
        "commit_signals": {
            "proof_complete": [r"complete", r"close\s+proof", r"fill\b"],
            "proof_new": [r"add\s+lemma", r"new\s+theorem"],
            "proof_add": [r"progress", r"partial"],
            "spec_change": [r"change\s+statement", r"modify\s+spec"],
            "infra": [r"bump\b", r"\bci\b", r"makefile"],
            "refactor": [r"refactor", r"cleanup"],
            "fix": [r"\bfix\b", r"bugfix"],
        },
        "proof_context_regex": r"proof|lemma|theorem|coq",
    }
    defaults.update(overrides)
    return RepoProfile(**defaults)  # type: ignore[arg-type]


def _make_record(**overrides: object) -> CommitRecord:
    """Build a minimal CommitRecord, overriding any fields."""
    defaults: dict[str, object] = {
        "hash": "aaa",
        "date": "2024-01-01T00:00:00",
        "message_subject": "",
        "message_body": "",
        "touches_proof_files": False,
        "insertions": 0,
        "deletions": 0,
    }
    defaults.update(overrides)
    return CommitRecord(**defaults)  # type: ignore[arg-type]


# ── Default-priority classification ───────────────────────────────────────


class TestDefaultPriority:
    """Verify each class fires with the default priority order."""

    @pytest.fixture
    def compiled(self):
        return _make_profile().compiled()

    def test_infra(self, compiled):
        rec = _make_record(message_subject="Bump deps in CI")
        assert classify_commit(rec, compiled) == CommitClass.infra

    def test_infra_suppressed_by_proof_context(self, compiled):
        """Infra signal in subject with proof context → not infra."""
        rec = _make_record(message_subject="Bump proof lemma CI")
        assert classify_commit(rec, compiled) != CommitClass.infra

    def test_proof_complete_signal(self, compiled):
        rec = _make_record(
            message_subject="Complete proof of add_comm",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_complete

    def test_proof_complete_structural_heuristic(self, compiled):
        """Body-free, net deletions on proof files with proof context."""
        rec = _make_record(
            message_subject="Remove admitted in theorem",
            touches_proof_files=True,
            deletions=10,
            insertions=3,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_complete

    def test_spec_change(self, compiled):
        rec = _make_record(
            message_subject="Change statement of foo",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.spec_change

    def test_proof_new(self, compiled):
        rec = _make_record(
            message_subject="Add lemma for bar",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_new

    def test_proof_add(self, compiled):
        rec = _make_record(
            message_subject="Progress on proof",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_add

    def test_refactor_on_proof_files_becomes_proof_add(self, compiled):
        rec = _make_record(
            message_subject="Refactor proof structure",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_add

    def test_fix_on_proof_files_becomes_proof_add(self, compiled):
        rec = _make_record(
            message_subject="Fix proof of foo",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_add

    def test_refactor_on_non_proof_files(self, compiled):
        rec = _make_record(message_subject="Refactor build scripts")
        assert classify_commit(rec, compiled) == CommitClass.refactor

    def test_fix_on_non_proof_files(self, compiled):
        rec = _make_record(message_subject="Fix typo in readme")
        assert classify_commit(rec, compiled) == CommitClass.fix

    def test_proof_file_fallback(self, compiled):
        """Commit touching proof files with no signal → proof_add."""
        rec = _make_record(
            message_subject="Minor tweaks",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_add

    def test_other(self, compiled):
        rec = _make_record(message_subject="Update readme")
        assert classify_commit(rec, compiled) == CommitClass.other

    def test_refactor_and_fix_dual_match_on_proof_files(self, compiled):
        """Commit matching both refactor and fix on proof files → proof_add."""
        rec = _make_record(
            message_subject="Refactor to fix proof regression",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_add

    def test_refactor_and_fix_dual_match_on_non_proof_files(self, compiled):
        """Commit matching both refactor and fix on non-proof files → refactor
        (refactor is checked before fix in default priority)."""
        rec = _make_record(message_subject="Refactor to fix build regression")
        assert classify_commit(rec, compiled) == CommitClass.refactor

    def test_signal_in_body_not_subject(self, compiled):
        """A signal in the message body (not subject) should still match."""
        rec = _make_record(
            message_subject="Misc updates",
            message_body="This change will complete the proof obligation.",
            touches_proof_files=True,
        )
        assert classify_commit(rec, compiled) == CommitClass.proof_complete

    def test_proof_add_signal_on_non_proof_files(self, compiled):
        """proof_add requires touches_proof_files; without it → other."""
        rec = _make_record(message_subject="Progress on documentation")
        assert classify_commit(rec, compiled) == CommitClass.other

    def test_no_proof_context_regex(self):
        """Empty proof_context_regex: infra always fires (guard is vacuous),
        proof_complete signal never fires (context required)."""
        profile = _make_profile(proof_context_regex="")
        compiled = profile.compiled()
        # Infra: no context guard to block it.
        rec_infra = _make_record(message_subject="Bump proof deps")
        assert classify_commit(rec_infra, compiled) == CommitClass.infra
        # proof_complete signal: requires has_ctx which always returns False.
        rec_pc = _make_record(
            message_subject="Complete proof of theorem",
            touches_proof_files=True,
        )
        assert classify_commit(rec_pc, compiled) != CommitClass.proof_complete


# ── Priority reordering ──────────────────────────────────────────────────


class TestPriorityReordering:
    """Verify that reordering classification_priority changes results."""

    def test_spec_change_before_proof_complete(self):
        """When spec_change has higher priority than proof_complete,
        a commit matching both signals should be classified as spec_change."""
        profile = _make_profile(
            classification_priority=[
                "spec_change",
                "proof_complete",
                "infra",
                "proof_new",
                "proof_add",
                "refactor",
                "fix",
            ],
        )
        compiled = profile.compiled()
        # This commit message matches both proof_complete ("complete") and
        # spec_change ("change statement").
        rec = _make_record(
            message_subject="Complete change statement of theorem",
            touches_proof_files=True,
        )
        # With default priority, proof_complete would win.
        default_compiled = _make_profile().compiled()
        assert classify_commit(rec, default_compiled) == CommitClass.proof_complete
        # With spec_change first, spec_change wins.
        assert classify_commit(rec, compiled) == CommitClass.spec_change

    def test_proof_add_before_proof_new(self):
        """When proof_add has higher priority than proof_new, it wins."""
        rec = _make_record(
            message_subject="Add lemma with progress",
            touches_proof_files=True,
        )
        default_compiled = _make_profile().compiled()
        # Default: proof_new comes before proof_add, so proof_new wins.
        assert classify_commit(rec, default_compiled) == CommitClass.proof_new
        # Swap: proof_add before proof_new.
        swapped = _make_profile(
            classification_priority=[
                "infra",
                "proof_complete",
                "spec_change",
                "proof_add",
                "proof_new",
                "refactor",
                "fix",
            ],
        ).compiled()
        assert classify_commit(rec, swapped) == CommitClass.proof_add

    def test_omitting_class_from_priority(self):
        """A class omitted from classification_priority never fires."""
        profile = _make_profile(
            classification_priority=[
                "proof_complete",
                "spec_change",
                "proof_new",
                "proof_add",
                "refactor",
                "fix",
                # "infra" intentionally omitted
            ],
        )
        compiled = profile.compiled()
        rec = _make_record(message_subject="Bump CI deps")
        # With default priority this would be infra; now it falls through to other.
        assert classify_commit(rec, compiled) == CommitClass.other

    def test_fix_before_refactor_dual_match(self):
        """When fix has higher priority than refactor, a dual-match commit
        on non-proof files gets fix instead of refactor."""
        rec = _make_record(message_subject="Refactor to fix build regression")
        default_compiled = _make_profile().compiled()
        assert classify_commit(rec, default_compiled) == CommitClass.refactor
        swapped = _make_profile(
            classification_priority=[
                "infra",
                "proof_complete",
                "spec_change",
                "proof_new",
                "proof_add",
                "fix",
                "refactor",
            ],
        ).compiled()
        assert classify_commit(rec, swapped) == CommitClass.fix

    def test_unknown_class_name_warns_and_skips(self):
        """An unknown entry in classification_priority logs a warning and is skipped."""
        profile = _make_profile(
            classification_priority=[
                "imaginary_class",
                "infra",
                "proof_complete",
                "spec_change",
                "proof_new",
                "proof_add",
                "refactor",
                "fix",
            ],
        )
        compiled = profile.compiled()
        rec = _make_record(message_subject="Bump CI deps")
        assert classify_commit(rec, compiled) == CommitClass.infra

    def test_empty_priority_only_fallback(self):
        """Empty classification_priority → only the proof_add/other fallback."""
        profile = _make_profile(classification_priority=[])
        compiled = profile.compiled()
        rec = _make_record(
            message_subject="Complete proof of theorem",
            touches_proof_files=True,
        )
        # No checkers run, but touches proof files → proof_add fallback.
        assert classify_commit(rec, compiled) == CommitClass.proof_add

        rec2 = _make_record(message_subject="Complete proof of theorem")
        assert classify_commit(rec2, compiled) == CommitClass.other
