"""Tests for profile-driven proof-span regexes (issue #88, Isabelle support).

The spec_change splicer historically hardcoded Coq's ``Proof.`` … ``Qed.``
boundaries; these tests pin the new profile fields, their backward-compatible
defaults, and span parsing/splicing on Isabelle-shaped sources.
"""

from __future__ import annotations

import re

import pytest

from scaffold.git_walker import parse_decl_spans, splice_spec_change
from scaffold.profile import (
    DEFAULT_PROOF_END_REGEX,
    DEFAULT_PROOF_START_REGEX,
    HoleMarker,
    RepoProfile,
)

_ISABELLE_DECL = [re.compile(r"^\s*(?:lemma|theorem|corollary)\s+(\w+)", re.MULTILINE)]
_ISABELLE_START = re.compile(r"^\s*(?:proof|apply|by)\b", re.MULTILINE)
_ISABELLE_END = re.compile(r"^\s*(?:qed|done|by|sorry|oops)\b", re.MULTILINE)

_ISABELLE_PARENT = """\
theory Foo imports Main begin

lemma foo: "A ==> A"
  apply simp
  done

lemma bar: "B = B"
  by auto

end
"""

_ISABELLE_CHILD = """\
theory Foo imports Main begin

lemma foo: "A ==> A & A"
  apply blast
  done

lemma bar: "B = B"
  by auto

end
"""

_COQ_CONTENT = """\
Lemma foo : True.
Proof.
  trivial.
Qed.
"""


def _make_profile(
    proof_start_regex: str = DEFAULT_PROOF_START_REGEX,
    proof_end_regex: str = DEFAULT_PROOF_END_REGEX,
) -> RepoProfile:
    return RepoProfile(
        proof_assistant="isabelle",
        proof_file_globs=["**/*.thy"],
        hole_markers=[HoleMarker(regex=r"\bsorry\b", kind="sorry")],
        declaration_patterns=[r"^\s*(?:lemma|theorem)\s+(\w+)"],
        proof_start_regex=proof_start_regex,
        proof_end_regex=proof_end_regex,
    )


class TestProfileFields:
    def test_defaults_are_coq_for_backward_compat(self) -> None:
        # A profile dict written before the fields existed (e.g. every
        # committed profile.json) must load and behave exactly as before.
        profile = _make_profile()
        assert profile.proof_start_regex == DEFAULT_PROOF_START_REGEX
        assert profile.proof_end_regex == DEFAULT_PROOF_END_REGEX
        compiled = profile.compiled()
        assert compiled.proof_start_re.search("Proof.")
        assert compiled.proof_end_re.search("Qed.")

    def test_overrides_compile(self) -> None:
        profile = _make_profile(
            proof_start_regex=r"^\s*(?:proof|apply|by)\b",
            proof_end_regex=r"^\s*(?:qed|done)\b",
        )
        compiled = profile.compiled()
        assert compiled.proof_start_re.search("  apply simp")
        assert compiled.proof_end_re.search("  qed")
        assert not compiled.proof_start_re.search("Proof.")

    def test_invalid_regex_rejected(self) -> None:
        with pytest.raises(ValueError, match="proof_start_regex"):
            _make_profile(proof_start_regex="(unclosed")
        with pytest.raises(ValueError, match="proof_end_regex"):
            _make_profile(proof_end_regex="(unclosed")


class TestParseDeclSpans:
    def test_coq_defaults_unchanged(self) -> None:
        # No span regexes passed — the legacy Coq behavior is preserved.
        spans = parse_decl_spans(
            _COQ_CONTENT, [re.compile(r"^\s*Lemma\s+(\w+)", re.MULTILINE)]
        )
        assert [s.name for s in spans] == ["foo"]
        assert "trivial" in spans[0].proof_body

    def test_isabelle_spans(self) -> None:
        spans = parse_decl_spans(
            _ISABELLE_PARENT, _ISABELLE_DECL, _ISABELLE_START, _ISABELLE_END
        )
        by_name = {s.name: s for s in spans}
        assert set(by_name) == {"foo", "bar"}
        assert "apply simp" in by_name["foo"].proof_body
        assert "done" in by_name["foo"].proof_body
        assert by_name["foo"].statement.startswith('lemma foo: "A ==> A"')
        # Single-line `by auto` proof: start and terminator on the same line.
        assert by_name["bar"].proof_body.strip() == "by auto"

    def test_isabelle_with_coq_defaults_finds_nothing(self) -> None:
        # The pre-fix behavior this change exists to eliminate: Coq-shaped
        # span regexes silently match nothing on Isabelle sources.
        spans = parse_decl_spans(_ISABELLE_PARENT, _ISABELLE_DECL)
        assert spans == []


class TestSpliceSpecChange:
    def test_isabelle_splice(self) -> None:
        result = splice_spec_change(
            _ISABELLE_PARENT,
            _ISABELLE_CHILD,
            _ISABELLE_DECL,
            _ISABELLE_START,
            _ISABELLE_END,
        )
        assert result is not None
        spliced, changed = result
        assert changed == ["foo"]
        # New statement from the child, old proof body from the parent.
        assert 'lemma foo: "A ==> A & A"' in spliced
        assert "apply simp" in spliced
        assert "apply blast" not in spliced
        # Unchanged declaration left alone.
        assert "by auto" in spliced

    def test_isabelle_without_span_regexes_degrades_to_none(self) -> None:
        result = splice_spec_change(_ISABELLE_PARENT, _ISABELLE_CHILD, _ISABELLE_DECL)
        assert result is None

    def test_unchanged_statements_yield_none(self) -> None:
        result = splice_spec_change(
            _ISABELLE_PARENT,
            _ISABELLE_PARENT,
            _ISABELLE_DECL,
            _ISABELLE_START,
            _ISABELLE_END,
        )
        assert result is None
