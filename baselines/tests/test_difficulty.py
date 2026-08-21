"""Tests for the difficulty feature extractor + label plumbing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apply_ablate.difficulty import dataset
from apply_ablate.difficulty.features import FEATURE_KEYS, extract_features
from apply_ablate.difficulty.label import is_trainable, label_of, outcome_of


def _enriched_record() -> dict:
    """A record shaped like the enriched ablator schema (two deleted lemmas, two holes)."""
    return {
        "task_id": "ablate_abc",
        "challenge_id": "cid1",
        "proof_assistant": "coq",
        "file_path": "A/B.v",
        "challenge_type": "lemma_delete",
        "by_centrality": False,
        "leaves_only": False,
        "ablation_prob": 0.5,
        "min_depth": 1,
        "max_depth": "inf",
        "min_size": 0,
        "max_size": "inf",
        "min_centrality": 0,
        "max_centrality": "inf",
        "seed": 7,
        "n_proofs": 10,
        "n_ablated": 2,
        "closure_size": 3,
        "deleted_lemmas": [
            {
                "name": "l1",
                "text": "x",
                "fan_in": 2,
                "n_lines": 4,
                "n_chars": 40,
                "n_subproofs": 1,
                "n_tactics": 5,
                "cyclomatic": 2,
            },
            {
                "name": "l2",
                "text": "y",
                "fan_in": 6,
                "n_lines": 10,
                "n_chars": 90,
                "n_subproofs": 3,
                "n_tactics": 9,
                "cyclomatic": 4,
            },
        ],
        "holes_filled": [
            {
                "theorem_name": "t1",
                "depth": 1,
                "n_commands": 0,
                "n_lines": 3,
                "is_leaf": True,
                "centrality": 1,
                "n_chars": 30,
                "n_subproofs": 0,
                "n_tactics": 4,
                "cyclomatic": 1,
            },
            {
                "theorem_name": "t2",
                "depth": 2,
                "n_commands": 1,
                "n_lines": 7,
                "is_leaf": False,
                "centrality": 5,
                "n_chars": 70,
                "n_subproofs": 2,
                "n_tactics": 8,
                "cyclomatic": 3,
            },
        ],
        "corollaries": [
            {
                "name": "cor",
                "fan_in": 0,
                "n_lines": 8,
                "n_chars": 80,
                "n_subproofs": 1,
                "n_tactics": 7,
                "cyclomatic": 3,
            },
        ],
        "challenge_file_content": "a\nb\nc\n",
        "solution_file_content": "a\nb\nc\nd\ne\n",
    }


def test_extract_aggregates_across_deleted_and_holes():
    f = extract_features(_enriched_record())
    assert f["n_deleted_lemmas"] == 2
    assert f["n_holes"] == 2
    assert f["n_corollaries"] == 1
    assert f["closure_size"] == 3
    # deleted-lemma aggregates: fan_in {2,6}
    assert f["del_fan_in_sum"] == 8
    assert f["del_fan_in_max"] == 6
    assert f["del_fan_in_mean"] == 4.0
    assert f["del_cyclomatic_max"] == 4
    assert f["del_n_tactics_sum"] == 14
    # hole aggregates
    assert f["hole_cyclomatic_max"] == 3
    assert f["hole_centrality_max"] == 5
    assert f["hole_n_leaves"] == 1
    # corollary aggregates
    assert f["cor_cyclomatic_max"] == 3
    assert f["cor_n_lines_max"] == 8
    # sizes (n_lines convention = 1 + #newlines, matching the ablators)
    assert f["challenge_n_lines"] == 4
    assert f["solution_n_lines"] == 6
    assert f["solution_minus_challenge_lines"] == 2


def test_extract_legacy_degrades_to_none():
    """A record missing the enriched metric fields yields None aggregates, not errors."""
    legacy = {
        "proof_assistant": "lean",
        "file_path": "X.lean",
        "n_proofs": 4,
        "n_ablated": 1,
        "deleted_lemmas": [{"name": "l", "text": "t"}],  # no fan_in/metrics
        "holes_filled": [{"theorem_name": "t"}],
        "challenge_file_content": "one line",
    }
    f = extract_features(legacy)
    assert f["challenge_id"] is None
    assert f["n_deleted_lemmas"] == 1
    assert f["del_fan_in_max"] is None
    assert f["del_cyclomatic_sum"] is None
    assert f["cor_n_lines_max"] is None  # empty corollaries list
    assert f["challenge_n_lines"] == 1


def test_feature_keys_stable_and_complete():
    """Every record produces exactly the FEATURE_KEYS column set, in order."""
    assert list(extract_features({}).keys()) == FEATURE_KEYS
    assert list(extract_features(_enriched_record()).keys()) == FEATURE_KEYS


def test_outcome_precedence_and_label():
    assert outcome_of({"succeeded": True}) == "pass"
    # succeeded wins even if other flags set
    assert outcome_of({"succeeded": True, "trivial": True}) == "pass"
    assert outcome_of({"succeeded": False, "trivial": True}) == "trivial"
    assert outcome_of({"succeeded": False, "malformed_challenge": True}) == "malformed"
    assert outcome_of({"succeeded": False, "gave_up": True}) == "gave_up"
    assert outcome_of({"succeeded": False, "turn_limit": True}) == "turn_limit"
    assert outcome_of({"succeeded": False, "error": "boom"}) == "error"
    assert outcome_of({"succeeded": False}) == "fail"
    assert label_of({"succeeded": True}) == 1
    assert label_of({"succeeded": False}) == 0
    assert is_trainable({"succeeded": False, "gave_up": True}) is True
    assert is_trainable({"succeeded": False, "trivial": True}) is False


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _synthetic_labelled(n: int = 80) -> list[dict]:
    """Rows where higher fan-in/cyclomatic/size lowers pass probability."""
    import random

    rng = random.Random(0)
    rows = []
    for _ in range(n):
        fan, cyc, lines = rng.randint(0, 9), rng.randint(1, 5), rng.randint(2, 40)
        import math

        p = 1 / (1 + math.exp(-(2.5 - 0.35 * fan - 0.4 * cyc - 0.05 * lines)))
        rows.append(
            {
                "label": int(rng.random() < p),
                "trainable": True,
                "del_fan_in_sum": fan,
                "del_fan_in_max": fan,
                "del_cyclomatic_max": cyc,
                "del_cyclomatic_sum": cyc,
                "hole_cyclomatic_max": cyc,
                "challenge_n_lines": lines,
                "n_holes": 1,
                "n_deleted_lemmas": 1,
            }
        )
    return rows


def test_model_trains_and_ranks_difficulty():
    from apply_ablate.difficulty import model as M

    rows = M.trainable_rows(_synthetic_labelled())
    fit = M.train(rows, min_n=20)
    assert fit.n_train == len(rows)
    assert 0.0 <= fit.pos_rate <= 1.0
    # a hard challenge should score more difficult than an easy one
    easy = {
        "del_fan_in_sum": 0,
        "del_fan_in_max": 0,
        "del_cyclomatic_max": 1,
        "del_cyclomatic_sum": 1,
        "hole_cyclomatic_max": 1,
        "challenge_n_lines": 3,
        "n_holes": 1,
        "n_deleted_lemmas": 1,
    }
    hard = {
        "del_fan_in_sum": 9,
        "del_fan_in_max": 9,
        "del_cyclomatic_max": 5,
        "del_cyclomatic_sum": 5,
        "hole_cyclomatic_max": 5,
        "challenge_n_lines": 38,
        "n_holes": 1,
        "n_deleted_lemmas": 1,
    }
    d_easy, d_hard = fit.score([easy, hard])
    assert 0.0 <= d_easy <= 1.0 and 0.0 <= d_hard <= 1.0
    assert d_hard > d_easy


def test_model_guards_small_and_single_class():
    from apply_ablate.difficulty import model as M

    rows = M.trainable_rows(_synthetic_labelled(10))
    with pytest.raises(M.NotEnoughData):
        M.train(rows, min_n=20)
    one_class = [{"label": 1, "trainable": True, "n_holes": 1} for _ in range(30)]
    with pytest.raises(M.NotEnoughData):
        M.train(one_class, min_n=20)


def test_model_drops_all_missing_columns():
    from apply_ablate.difficulty import model as M

    # legacy-style rows: metrics absent, only counts/sizes present
    rows = M.trainable_rows(_synthetic_labelled())
    for r in rows:
        for k in (
            "del_fan_in_sum",
            "del_fan_in_max",
            "del_cyclomatic_max",
            "del_cyclomatic_sum",
            "hole_cyclomatic_max",
        ):
            r.pop(k, None)
    fit = M.train(rows, min_n=20)
    assert "del_fan_in_sum" in fit.dropped_features
    # a populated, non-dropped default predictor survives
    assert "n_holes" in fit.feature_names


def test_build_table_joins_by_challenge_id(tmp_path: Path):
    chals = [
        {
            "challenge_id": "a",
            "task_id": "t",
            "file_path": "F",
            "deleted_lemmas": [],
            "holes_filled": [],
        },
        {
            "challenge_id": "b",
            "task_id": "t",
            "file_path": "F",
            "deleted_lemmas": [],
            "holes_filled": [],
        },
    ]
    # results deliberately out of order to prove id-join (not position) is used
    results = [
        {"challenge_id": "b", "succeeded": True},
        {"challenge_id": "a", "succeeded": False, "gave_up": True},
    ]
    cpath, rpath = tmp_path / "c.jsonl", tmp_path / "r.jsonl"
    _write_jsonl(cpath, chals)
    _write_jsonl(rpath, results)
    rows, warnings = dataset.build_table(cpath, rpath)
    assert warnings == []
    by_id = {r["challenge_id"]: r for r in rows}
    assert by_id["a"]["outcome"] == "gave_up" and by_id["a"]["label"] == 0
    assert by_id["b"]["outcome"] == "pass" and by_id["b"]["label"] == 1
    assert all(r["matched_by"] == "challenge_id" for r in rows)


def test_build_table_positional_fallback_and_mismatch(tmp_path: Path):
    # legacy: no challenge_id -> positional join, with a cross-check
    chals = [
        {"task_id": "t0", "file_path": "F0", "deleted_lemmas": [], "holes_filled": []},
        {"task_id": "t1", "file_path": "F1", "deleted_lemmas": [], "holes_filled": []},
    ]
    results = [
        {"task_id": "t0", "file_path": "F0", "succeeded": True},
        {"task_id": "WRONG", "file_path": "MISMATCH", "succeeded": False},
    ]
    cpath, rpath = tmp_path / "c.jsonl", tmp_path / "r.jsonl"
    _write_jsonl(cpath, chals)
    _write_jsonl(rpath, results)
    rows, warnings = dataset.build_table(cpath, rpath)
    assert rows[0]["matched_by"] == "position" and rows[0]["label"] == 1
    # second row's task_id/file_path disagree -> unmatched, warned
    assert rows[1]["matched_by"] is None and rows[1]["label"] is None
    assert any("mismatch" in w for w in warnings)


def test_build_table_pairs_by_challenge_id_and_mode(tmp_path: Path):
    """A paired easy/hard sample (pipeline/sample_paired.py) shares `challenge_id` across
    modes on purpose (the ablator's id hash excludes mode -- see
    ablators/lean/Ablator/Record.lean:87-96). When the challenges file is itself
    mode-tagged, the join must land on the SAME mode's result, not whichever one happens
    to be in the results file."""
    chals = [
        {
            "challenge_id": "shared",
            "sample_mode": "leaves",
            "task_id": "t",
            "file_path": "F",
            "deleted_lemmas": [],
            "holes_filled": [],
        },
    ]
    # pooled results: both modes' outcomes for the SAME challenge_id, disagreeing on PASS
    results = [
        {"challenge_id": "shared", "sample_mode": "whole", "succeeded": False},
        {"challenge_id": "shared", "sample_mode": "leaves", "succeeded": True},
    ]
    cpath, rpath = tmp_path / "c.jsonl", tmp_path / "r.jsonl"
    _write_jsonl(cpath, chals)
    _write_jsonl(rpath, results)
    rows, warnings = dataset.build_table(cpath, rpath)
    assert warnings == []
    assert len(rows) == 1
    assert rows[0]["outcome"] == "pass" and rows[0]["label"] == 1
    assert rows[0]["matched_by"] == "challenge_id"


def test_build_table_pooled_results_do_not_silently_collide(tmp_path: Path):
    """If the challenges file lacks a mode tag but the pooled results carry two different
    modes' rows under the same `challenge_id`, the join is genuinely ambiguous -- it must
    be skipped (and warned about), never silently resolved to whichever row loaded last."""
    chals = [
        {
            "challenge_id": "shared",
            "task_id": "t",
            "file_path": "F",
            "deleted_lemmas": [],
            "holes_filled": [],
        },
    ]
    results = [
        {"challenge_id": "shared", "sample_mode": "leaves", "succeeded": True},
        {"challenge_id": "shared", "sample_mode": "whole", "succeeded": False},
    ]
    cpath, rpath = tmp_path / "c.jsonl", tmp_path / "r.jsonl"
    _write_jsonl(cpath, chals)
    _write_jsonl(rpath, results)
    rows, warnings = dataset.build_table(cpath, rpath)
    assert len(rows) == 1
    assert rows[0]["matched_by"] is None
    assert rows[0]["outcome"] is None and rows[0]["label"] is None
    assert any("ambiguous" in w for w in warnings)
