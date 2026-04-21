"""Tests for experiments.shared.splice.patch_admitted."""

from __future__ import annotations

from shared.splice import patch_admitted


def test_replaces_admitted_with_indented_tactics() -> None:
    content = (
        "Lemma foo : True.\n"
        "Proof.\n"
        "    Admitted.\n"
        "Qed.\n"
    )
    tactics = "exact I.\nQed."
    result = patch_admitted(content, "foo", tactics)
    # The 4-space indent on `Admitted.` should apply to every non-empty tactic line.
    assert "    exact I." in result
    assert "    Qed." in result
    assert "Admitted." not in result.splitlines()[2]


def test_missing_decl_returns_unchanged() -> None:
    content = "Lemma bar : True.\nProof.\nAdmitted.\n"
    assert patch_admitted(content, "nonexistent_decl", "exact I.\nQed.") == content


def test_missing_admitted_returns_unchanged() -> None:
    content = "Lemma baz : True.\nProof. exact I. Qed.\n"
    assert patch_admitted(content, "baz", "exact I.\nQed.") == content


def test_only_first_admitted_after_decl_is_replaced() -> None:
    content = (
        "Lemma first : True.\n"
        "Proof.\n"
        "Admitted.\n"
        "Lemma second : True.\n"
        "Proof.\n"
        "Admitted.\n"
    )
    result = patch_admitted(content, "first", "exact I.\nQed.")
    # second Lemma's Admitted. must remain untouched
    assert result.count("Admitted.") == 1
    assert "Lemma second" in result


def test_preserves_blank_lines_in_tactics() -> None:
    content = "Lemma q : True.\nProof.\n  Admitted.\n"
    tactics = "intros.\n\nexact I.\nQed."
    result = patch_admitted(content, "q", tactics)
    # non-empty lines get the 2-space indent; blank lines stay blank
    assert "  intros." in result
    assert "  exact I." in result
    assert "  Qed." in result
