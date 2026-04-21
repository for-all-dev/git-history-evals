"""Tests for experiments.shared.compile.

These tests stub ``make`` with tiny Makefiles in ``tmp_path`` so they do not
depend on fiat-crypto, Coq, or any network. They cover:

* ``vo_bytes`` returning ``None`` for a missing file.
* ``run_make_target`` reporting failure when ``make`` exits non-zero.
* ``run_make_target`` reporting success when the Makefile produces the ``.vo``.
* stdout/stderr truncation to ~8KB.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from shared.compile import CompileResult, run_make_target, vo_bytes
from shared.compile import _truncate, _TRUNC_BYTES


# ``make`` is a hard dependency for the runner tests; skip gracefully if absent.
_HAS_MAKE = shutil.which("make") is not None
requires_make = pytest.mark.skipif(not _HAS_MAKE, reason="`make` not on PATH")


def test_vo_bytes_missing_returns_none(tmp_path: Path) -> None:
    # no file at all
    assert vo_bytes(tmp_path, Path("does/not/exist.vo")) is None
    # directory exists but file does not
    (tmp_path / "sub").mkdir()
    assert vo_bytes(tmp_path, Path("sub/missing.vo")) is None


def test_vo_bytes_reports_size(tmp_path: Path) -> None:
    (tmp_path / "foo.vo").write_bytes(b"hello")
    assert vo_bytes(tmp_path, Path("foo.v")) == 5   # accepts .v input
    assert vo_bytes(tmp_path, Path("foo.vo")) == 5  # also accepts .vo input


@requires_make
def test_run_make_target_reports_failure(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("target.vo: ; false\n")
    result = run_make_target(tmp_path, Path("target.v"), timeout=30)
    assert isinstance(result, CompileResult)
    assert result.ok is False
    assert result.exit_code != 0
    assert result.target == "target.vo"
    # no .vo was produced
    assert vo_bytes(tmp_path, Path("target.v")) is None


@requires_make
def test_run_make_target_reports_success(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("target.vo: ; touch $@\n")
    result = run_make_target(tmp_path, Path("target.v"), timeout=30)
    assert result.ok is True
    assert result.exit_code == 0
    assert result.target == "target.vo"
    assert vo_bytes(tmp_path, Path("target.v")) == 0


@requires_make
def test_run_make_target_success_requires_vo_on_disk(tmp_path: Path) -> None:
    # Makefile exits 0 but does NOT create target.vo — ok must be False.
    (tmp_path / "Makefile").write_text("target.vo: ; true\n")
    result = run_make_target(tmp_path, Path("target.v"), timeout=30)
    assert result.exit_code == 0
    assert result.ok is False


def test_truncate_caps_at_8kb() -> None:
    # sanity: helper truncates to the declared byte budget
    big = "x" * (_TRUNC_BYTES + 1000)
    out = _truncate(big)
    assert len(out.encode("utf-8")) <= _TRUNC_BYTES
    # and leaves short strings alone
    assert _truncate("short") == "short"


@requires_make
def test_run_make_target_truncates_stdout(tmp_path: Path) -> None:
    # Emit well over 8KB of stdout from the recipe.
    (tmp_path / "Makefile").write_text(
        "target.vo:\n"
        "\t@python3 -c 'print(\"x\"*20000)'\n"
        "\t@touch $@\n"
    )
    result = run_make_target(tmp_path, Path("target.v"), timeout=30)
    assert result.ok is True
    assert len(result.stdout.encode("utf-8")) <= _TRUNC_BYTES


def test_compile_result_shape() -> None:
    # Construct directly to lock the schema; tests above exercise the runner.
    r = CompileResult(
        ok=True, exit_code=0, stdout="a", stderr="b", elapsed_s=0.5, target="x.vo"
    )
    assert r.ok is True
    assert r.exit_code == 0
    assert r.stdout == "a"
    assert r.stderr == "b"
    assert r.elapsed_s == 0.5
    assert r.target == "x.vo"
