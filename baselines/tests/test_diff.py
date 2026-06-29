"""Tests for the shared unified-diff `apply` (the `solution_diff` consumer)."""

from pathlib import Path

import pytest

from apply_ablate.diff import apply
from apply_ablate.record import load_record

FIXTURES = Path(__file__).parent / "fixtures"


def test_empty_diff_is_identity():
    assert apply("a\nb\nc\n", "") == "a\nb\nc\n"
    assert apply("", "") == ""


def test_single_substitution():
    assert apply("a\nb\nc\n", "@@ -2,1 +2,1 @@\n-b\n+B\n") == "a\nB\nc\n"


def test_multi_hunk():
    diff = "@@ -1,1 +1,1 @@\n-l1\n+L1\n@@ -3,1 +3,1 @@\n-l3\n+L3\n"
    assert apply("l1\nl2\nl3\nl4\n", diff) == "L1\nl2\nL3\nl4\n"


def test_pure_insertion():
    assert apply("a\nc\n", "@@ -1,1 +1,2 @@\n a\n+b\n") == "a\nb\nc\n"


def test_pure_deletion():
    assert apply("a\nb\nc\n", "@@ -2,1 +1,0 @@\n-b\n") == "a\nc\n"


def test_no_trailing_newline_roundtrip():
    # challenge without a trailing newline: final split element is non-empty
    assert apply("a\nb", "@@ -2,1 +2,1 @@\n-b\n+c\n") == "a\nc"


@pytest.mark.parametrize("name", ["coq", "isabelle", "lean"])
def test_fixture_recovers_holefree_solution(name: str):
    """Every real fixture's solution_diff recovers a non-hole solution != challenge."""
    rec = load_record(FIXTURES / f"{name}.jsonl", 0)
    assert rec.solution_diff, "fixture must exercise a non-empty diff"
    sol = rec.solution_text()
    assert sol != rec.challenge_file_content
    assert not any(h in sol for h in ("sorry", "Admitted", "admit"))
