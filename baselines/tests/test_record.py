"""Tests for AblationRecord + load_record."""

from pathlib import Path

import pytest

from apply_ablate.record import (
    AblationRecord,
    RecordError,
    count_records,
    load_record,
)


def test_loads_each_fixture(fixture_jsonl: Path):
    rec = load_record(fixture_jsonl, 0)
    assert isinstance(rec, AblationRecord)
    assert rec.assistant in ("coq", "isabelle", "lean")
    assert rec.assistant == fixture_jsonl.stem
    assert rec.file_path
    assert rec.challenge_file_content
    assert count_records(fixture_jsonl) == 1


def test_extra_fields_ignored(fixture_jsonl: Path):
    # the real records carry ~20 knob fields we don't model; loading must not fail
    rec = load_record(fixture_jsonl, 0)
    assert not hasattr(rec, "ablation_prob")  # extra=ignore drops it


def test_index_out_of_range(fixture_jsonl: Path):
    with pytest.raises(RecordError, match="out of range"):
        load_record(fixture_jsonl, 5)


def test_negative_index(fixture_jsonl: Path):
    with pytest.raises(RecordError, match=">= 0"):
        load_record(fixture_jsonl, -1)


def test_unknown_assistant(tmp_path: Path):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        '{"proof_assistant":"agda","file_path":"X.agda","challenge_file_content":"x"}\n'
    )
    with pytest.raises(RecordError, match="unknown proof_assistant"):
        load_record(p, 0)


def test_blank_lines_skipped(tmp_path: Path):
    p = tmp_path / "padded.jsonl"
    p.write_text(
        '{"proof_assistant":"lean","file_path":"A.lean","challenge_file_content":"a"}\n'
        "\n"
        '{"proof_assistant":"coq","file_path":"B.v","challenge_file_content":"b"}\n'
    )
    assert count_records(p) == 2
    assert load_record(p, 1).assistant == "coq"


def test_solution_text_empty_diff_is_identity(tmp_path: Path):
    p = tmp_path / "nodiff.jsonl"
    p.write_text(
        '{"proof_assistant":"lean","file_path":"A.lean",'
        '"challenge_file_content":"same","solution_diff":""}\n'
    )
    rec = load_record(p, 0)
    assert rec.solution_text() == "same"
