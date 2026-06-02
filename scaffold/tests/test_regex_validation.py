"""Tests for regex validation in RepoProfile / CompiledProfile.

Covers the hardening from issue #61: pydantic validators on RepoProfile catch
malformed regexes at deserialization time with actionable field-level errors,
and CompiledProfile.from_profile() wraps compilation per field as defense-in-depth.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from scaffold.profile import CompiledProfile, HoleMarker, RepoProfile

# A minimal valid profile dict — tests override individual fields.
_VALID_BASE: dict = {
    "proof_assistant": "coq",
    "proof_file_globs": ["*.v"],
    "hole_markers": [{"regex": r"\bAdmitted\b", "kind": "admitted"}],
    "declaration_patterns": [r"^\s*Theorem\s+(\w+)"],
}


def _profile_with(**overrides: object) -> dict:
    """Return a copy of _VALID_BASE with fields overridden."""
    d = dict(_VALID_BASE)
    d.update(overrides)
    return d


# ── HoleMarker.regex validator ────────────────────────────────────────────


def test_hole_marker_valid_regex() -> None:
    hm = HoleMarker(regex=r"\bAdmitted\b", kind="admitted")
    assert hm.regex == r"\bAdmitted\b"


def test_hole_marker_bad_regex() -> None:
    with pytest.raises(ValidationError, match="hole_markers.regex"):
        HoleMarker(regex="(unclosed", kind="bad")


# ── RepoProfile field validators ──────────────────────────────────────────


def test_valid_profile_round_trips() -> None:
    p = RepoProfile.model_validate(_VALID_BASE)
    assert p.proof_assistant == "coq"
    # compiled() should also work fine
    cp = p.compiled()
    assert len(cp.hole_res) == 1


def test_bad_declaration_pattern() -> None:
    data = _profile_with(declaration_patterns=["(unclosed"])
    with pytest.raises(ValidationError, match="declaration_patterns"):
        RepoProfile.model_validate(data)


def test_bad_declaration_pattern_identifies_index() -> None:
    data = _profile_with(declaration_patterns=[r"^\s*Theorem\s+(\w+)", "(bad"])
    with pytest.raises(ValidationError, match=r"declaration_patterns\[1\]"):
        RepoProfile.model_validate(data)


def test_bad_commit_signal() -> None:
    data = _profile_with(commit_signals={"proof_complete": [r"complete", "(broken"]})
    with pytest.raises(ValidationError, match="commit_signals"):
        RepoProfile.model_validate(data)


def test_bad_commit_signal_names_class_and_index() -> None:
    data = _profile_with(commit_signals={"infra": [r"ci", r"build"], "fix": ["(oops"]})
    with pytest.raises(ValidationError, match=r"commit_signals\.fix\[0\]"):
        RepoProfile.model_validate(data)


def test_bad_proof_context_regex() -> None:
    data = _profile_with(proof_context_regex="[unclosed")
    with pytest.raises(ValidationError, match="proof_context_regex"):
        RepoProfile.model_validate(data)


def test_empty_proof_context_regex_is_ok() -> None:
    """Empty string is the default and should not trigger validation."""
    p = RepoProfile.model_validate(_profile_with(proof_context_regex=""))
    assert p.proof_context_regex == ""


def test_bad_proof_style_signal() -> None:
    data = _profile_with(proof_style_signals={"term_mode": "(bad"})
    with pytest.raises(ValidationError, match=r"proof_style_signals\.term_mode"):
        RepoProfile.model_validate(data)


def test_bad_hole_markers_in_profile() -> None:
    data = _profile_with(hole_markers=[{"regex": "(bad", "kind": "broken"}])
    with pytest.raises(ValidationError, match="hole_markers"):
        RepoProfile.model_validate(data)


# ── CompiledProfile defense-in-depth ──────────────────────────────────────


def test_compiled_profile_bad_hole_markers() -> None:
    """from_profile() wraps hole_markers compilation."""
    profile = RepoProfile.model_construct(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[HoleMarker.model_construct(regex="(bad", kind="x")],
        declaration_patterns=[],
        commit_signals={},
        proof_context_regex="",
        tactic_vocabulary=[],
        tactic_groups={},
        proof_style_signals={},
        structural_terms=[],
        domain_terms=[],
    )
    with pytest.raises(ValueError, match=r"hole_markers\[0\]"):
        CompiledProfile.from_profile(profile)


def test_compiled_profile_bad_declaration_patterns() -> None:
    profile = RepoProfile.model_construct(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[],
        declaration_patterns=["(bad"],
        commit_signals={},
        proof_context_regex="",
        tactic_vocabulary=[],
        tactic_groups={},
        proof_style_signals={},
        structural_terms=[],
        domain_terms=[],
    )
    with pytest.raises(ValueError, match=r"declaration_patterns\[0\]"):
        CompiledProfile.from_profile(profile)


def test_compiled_profile_bad_commit_signal() -> None:
    """Individual signals are validated before joining with '|'."""
    profile = RepoProfile.model_construct(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[],
        declaration_patterns=[],
        commit_signals={"fix": ["ok", "(bad"]},
        proof_context_regex="",
        tactic_vocabulary=[],
        tactic_groups={},
        proof_style_signals={},
        structural_terms=[],
        domain_terms=[],
    )
    with pytest.raises(ValueError, match=r"commit_signals\.fix\[1\]"):
        CompiledProfile.from_profile(profile)


def test_compiled_profile_bad_proof_context_regex() -> None:
    profile = RepoProfile.model_construct(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[],
        declaration_patterns=[],
        commit_signals={},
        proof_context_regex="[bad",
        tactic_vocabulary=[],
        tactic_groups={},
        proof_style_signals={},
        structural_terms=[],
        domain_terms=[],
    )
    with pytest.raises(ValueError, match="proof_context_regex"):
        CompiledProfile.from_profile(profile)


def test_compiled_profile_bad_proof_style_signal() -> None:
    profile = RepoProfile.model_construct(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[],
        declaration_patterns=[],
        commit_signals={},
        proof_context_regex="",
        tactic_vocabulary=[],
        tactic_groups={},
        proof_style_signals={"ssreflect": "(bad"},
        structural_terms=[],
        domain_terms=[],
    )
    with pytest.raises(ValueError, match=r"proof_style_signals\.ssreflect"):
        CompiledProfile.from_profile(profile)


# ── test_profile tool integration ─────────────────────────────────────────


def test_test_profile_surfaces_bad_regex(tmp_path: Path) -> None:
    """The test_profile tool returns the validation error, not a crash.

    This verifies the claim from the issue: pydantic validators on RepoProfile
    surface through test_profile's existing model_validate() try/except.
    """
    from scaffold.profiler.deps import ProfilerDeps
    from scaffold.profiler.tools import build_tools

    deps = ProfilerDeps(repo_path=tmp_path)  # type: ignore[arg-type]
    tools = build_tools(deps)
    test_profile = tools["test_profile"]

    bad_profile = _profile_with(
        declaration_patterns=["(broken"],
    )
    res = test_profile(bad_profile)
    assert res["ok"] is False
    assert "declaration_patterns" in res["error"]
