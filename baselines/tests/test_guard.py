"""Tests for the no-cheat statement-preservation guard (`_tamper_reason`) and the
whole-file `solution_text` / `holed_theorems` plumbing on AblationRecord."""

from apply_ablate.record import AblationRecord
from apply_ablate.solve import _tamper_reason


# --- Lean: exact-statement comparison (up to the `:=` proof delimiter) ---

LEAN_CHALLENGE = """\
theorem foo (n : Nat) : n + 0 = n := by sorry
theorem bar : 1 = 1 := rfl
"""


def test_lean_genuine_solution_passes():
    sol = "theorem foo (n : Nat) : n + 0 = n := by simp\ntheorem bar : 1 = 1 := rfl\n"
    assert _tamper_reason(LEAN_CHALLENGE, sol, ["foo"], "lean") is None


def test_lean_deleted_theorem_is_tampered():
    sol = "theorem bar : 1 = 1 := rfl\n"  # foo removed entirely
    reason = _tamper_reason(LEAN_CHALLENGE, sol, ["foo"], "lean")
    assert reason is not None and "foo" in reason


def test_lean_weakened_statement_is_tampered():
    # changed the statement (different goal) then trivially proved it
    sol = "theorem foo (n : Nat) : n = n := by rfl\ntheorem bar : 1 = 1 := rfl\n"
    reason = _tamper_reason(LEAN_CHALLENGE, sol, ["foo"], "lean")
    assert reason is not None and "foo" in reason


def test_lean_whitespace_insensitive():
    sol = "theorem foo  (n : Nat)  :  n + 0 = n  := by\n  simp\n"
    assert _tamper_reason(LEAN_CHALLENGE, sol, ["foo"], "lean") is None


# --- Coq / Isabelle: name-presence fallback ---

COQ_CHALLENGE = "Lemma foo : 1 = 1.\nProof. Admitted.\n"


def test_coq_name_present_passes():
    sol = "Lemma foo : 1 = 1.\nProof. reflexivity. Qed.\n"
    assert _tamper_reason(COQ_CHALLENGE, sol, ["foo"], "coq") is None


def test_coq_name_missing_is_tampered():
    sol = "Lemma other : 2 = 2.\nProof. reflexivity. Qed.\n"
    reason = _tamper_reason(COQ_CHALLENGE, sol, ["foo"], "coq")
    assert reason is not None and "foo" in reason


def test_no_holed_names_never_tampered():
    # nothing to preserve (e.g. proof-ablation record with no theorem names)
    assert _tamper_reason(LEAN_CHALLENGE, "anything", [], "lean") is None


# --- record plumbing ---


def _rec(**kw) -> AblationRecord:
    base = dict(
        proof_assistant="lean",
        file_path="A.lean",
        challenge_file_content="theorem foo : 1 = 1 := by sorry\n",
    )
    base.update(kw)
    return AblationRecord.model_validate(base)


def test_solution_text_prefers_whole_file():
    rec = _rec(solution_file_content="theorem foo : 1 = 1 := rfl\n")
    assert rec.solution_text() == "theorem foo : 1 = 1 := rfl\n"


def test_holed_theorems_extracted():
    rec = _rec(holes_filled=[{"theorem_name": "foo"}, {"theorem_name": ""}])
    assert rec.holed_theorems == ["foo"]


def test_deleted_lemma_names_extracted():
    rec = _rec(
        deleted_lemmas=[
            {"name": "helper", "text": "theorem helper : 1 = 1 := rfl"},
            {"name": "", "text": ""},
        ]
    )
    assert rec.deleted_lemma_names == ["helper"]
    assert rec.deleted_lemmas[0].text.startswith("theorem helper")


def test_deleted_lemmas_default_empty():
    assert _rec().deleted_lemma_names == []
