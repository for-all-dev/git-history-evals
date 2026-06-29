"""CLI smoke test for the apply-only path (no prover toolchain required)."""

from pathlib import Path

from typer.testing import CliRunner

from apply_ablate.cli import app
from apply_ablate.record import load_record

runner = CliRunner()


def test_cli_apply_only(tmp_path: Path, fixture_jsonl: Path):
    rec = load_record(fixture_jsonl, 0)
    src = tmp_path / "src"
    (src / Path(rec.file_path).parent).mkdir(parents=True, exist_ok=True)
    (src / rec.file_path).write_text("placeholder\n")
    dst = tmp_path / "dst"

    result = runner.invoke(app, [str(fixture_jsonl), "0", str(src), str(dst)])
    assert result.exit_code == 0, result.output
    assert (dst / rec.file_path).read_text() == rec.challenge_file_content
    assert "apply:" in result.output


def test_cli_overwrite_guard(tmp_path: Path, fixture_jsonl: Path):
    rec = load_record(fixture_jsonl, 0)
    src = tmp_path / "src"
    (src / Path(rec.file_path).parent).mkdir(parents=True, exist_ok=True)
    (src / rec.file_path).write_text("placeholder\n")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "stale").write_text("x")

    # non-empty dst without --overwrite -> error exit (2)
    result = runner.invoke(app, [str(fixture_jsonl), "0", str(src), str(dst)])
    assert result.exit_code == 2

    result = runner.invoke(
        app, [str(fixture_jsonl), "0", str(src), str(dst), "--overwrite"]
    )
    assert result.exit_code == 0, result.output
    assert not (dst / "stale").exists()
