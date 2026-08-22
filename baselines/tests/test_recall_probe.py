"""Tests for pipeline/recall_probe.py: the verbatim-recall contamination probe (#134).

Covers the two parts that carry the methodology: the header/body split (what the model is
shown vs. what it must recall) and the scoring/statistics layer. The network probe itself
is not exercised here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_DIR = REPO_ROOT / "pipeline"

_spec = importlib.util.spec_from_file_location(
    "recall_probe", PIPELINE_DIR / "recall_probe.py"
)
assert _spec is not None and _spec.loader is not None
recall_probe = importlib.util.module_from_spec(_spec)
sys.modules["recall_probe"] = recall_probe
_spec.loader.exec_module(recall_probe)


# --------------------------------------------------------------------------- split


def test_split_at_top_level_assign():
    text = "lemma foo (h : a = b) : c = d := by\n  simp [h]\n"
    header, body = recall_probe.split_signature(text)
    assert header.endswith("c = d")
    assert body == "by\n  simp [h]"


def test_split_ignores_assign_inside_binders():
    """A `:=` inside a default-value binder must not end the signature."""
    text = "lemma foo (n : Nat := 3) : n = n := by rfl"
    header, body = recall_probe.split_signature(text)
    assert "n : Nat := 3" in header
    assert body == "by rfl"


def test_split_ignores_assign_inside_docstring():
    text = "/-- writes `x := y` -/\nlemma foo : True := trivial"
    header, body = recall_probe.split_signature(text)
    assert header.startswith("/--")
    assert body == "trivial"


def test_split_returns_none_without_assign():
    assert recall_probe.split_signature("lemma foo : True") is None


# --------------------------------------------------------------------------- scoring


def test_clean_response_strips_fences_and_echoed_assign():
    assert recall_probe.clean_response("```lean\nby simp\n```") == "by simp"
    assert recall_probe.clean_response(":= by simp") == "by simp"


def test_exact_match_is_whitespace_insensitive():
    a, b = recall_probe.norm_ws("by\n  simp [h]"), recall_probe.norm_ws("by simp   [h]")
    assert a == b
    assert recall_probe.levenshtein_ratio(a, b) == 1.0
    assert recall_probe.token_f1(a, b) == 1.0


def test_similarity_degrades():
    gt = "by simp [foo, bar]"
    assert recall_probe.levenshtein_ratio(gt, "by simp [foo, baz]") > 0.8
    assert recall_probe.levenshtein_ratio(gt, "by omega") < 0.6
    assert recall_probe.token_f1(gt, "") == 0.0


def test_band_thresholds():
    assert recall_probe.band(0.95) == "high"
    assert recall_probe.band(0.7) == "mid"
    assert recall_probe.band(0.1) == "low"


# --------------------------------------------------------------------------- stats


def test_pearson_and_wilson():
    assert recall_probe.pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    p, lo, hi = recall_probe.wilson(5, 10)
    assert p == 0.5 and lo < 0.5 < hi


def test_permutation_p_on_pure_noise_is_large():
    xs = [float(i % 3) for i in range(30)]
    ys = [float((i * 7) % 2) for i in range(30)]
    assert recall_probe.permutation_p(xs, ys, iters=500) > 0.05


# --------------------------------------------------------------------------- end-to-end


def _lemma_row(cid: str, name: str, body: str) -> dict:
    return {
        "challenge_id": cid,
        "repo": "github.com/x/y",
        "revision": "deadbeef",
        "file_path": "Y/Z.lean",
        "deleted_lemmas": [
            {"name": name, "text": f"lemma {name} : True := {body}\n", "n_tactics": 4}
        ],
    }


def _result_row(cid: str, succeeded: bool, **kw) -> dict:
    row = {
        "challenge_id": cid,
        "succeeded": succeeded,
        "tampered": False,
        "malformed_challenge": False,
        "trivial": False,
    }
    row.update(kw)
    return row


def test_score_joins_outcomes_and_writes_tsv(tmp_path: Path):
    sample = tmp_path / "sample.jsonl"
    sample.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                _lemma_row("c1", "lem_one", "by simp [alpha]"),
                _lemma_row("c2", "lem_two", "by omega"),
            ]
        )
    )
    responses = tmp_path / "responses.jsonl"
    responses.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"model": "m", "challenge_id": "c1", "response": "by  simp [alpha]"},
                {"model": "m", "challenge_id": "c2", "response": "by decide"},
            ]
        )
    )
    tree = tmp_path / "tree"
    for split, rows in {
        "easy": [_result_row("c1", True), _result_row("c2", False)],
        # c2 excluded from the correlation: tampered "successes" are not PASS
        "hard": [
            _result_row("c1", False),
            _result_row("c2", True, tampered=True),
        ],
    }.items():
        (tree / split).mkdir(parents=True)
        (tree / split / "results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows)
        )

    lemmas = recall_probe.load_lemmas(sample)
    results = {"m": recall_probe.load_results(tree)}
    rows = recall_probe.score(responses, lemmas, results)
    by_id = {r.lemma.challenge_id: r for r in rows}

    assert by_id["c1"].exact == 1  # whitespace-normalized verbatim hit
    assert by_id["c1"].easy == "pass" and by_id["c1"].hard == "fail"
    assert by_id["c2"].exact == 0
    assert by_id["c2"].hard == "fail"  # tampered is not a pass

    out = tmp_path / "out.tsv"
    recall_probe.write_tsv(rows, out)
    lines = out.read_text().splitlines()
    assert lines[0].split("\t") == recall_probe.TSV_COLUMNS
    assert len(lines) == 3
    assert "### Recall scores" in recall_probe.report(rows)


def test_excluded_outcomes_are_dropped(tmp_path: Path):
    row = _result_row("c1", False, malformed_challenge=True)
    assert recall_probe.outcome(row) == "excluded"
    assert recall_probe.outcome(_result_row("c1", True, trivial=True)) == "excluded"
