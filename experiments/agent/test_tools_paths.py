"""Tests for the path-escape guard and basic read_file behaviour.

We construct a minimal ``AgentDeps`` pointing at a tempdir, register tools on
a real ``pydantic_ai.Agent`` so the decorator runs, then invoke the
underlying tool function directly through a lightweight ``RunContext`` mock.
This exercises the actual module import path (``from agent.tools import
register_tools``) and the resolve-based containment check.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent.deps import AgentDeps
from agent.tools import register_tools


@dataclass
class _StubCtx:
    """Minimal RunContext stand-in: only ``.deps`` is accessed by the tools."""

    deps: AgentDeps


def _make_deps(tmp_path: Path) -> AgentDeps:
    repo_dir = tmp_path / "repo"
    slot_dir = tmp_path / "slot"
    repo_dir.mkdir()
    slot_dir.mkdir()
    # Plant an in-tree file the tool can legitimately read.
    (repo_dir / "hello.v").write_text("Lemma h : True.\nProof. exact I. Qed.\n")
    return AgentDeps(
        slot_dir=slot_dir,
        repo_dir=repo_dir,
        rel_target=Path("hello.v"),
        decl="h",
        attempt_path=slot_dir / "attempt.v",
        coq_flags=[],
    )


def _capture_read_file() -> Any:
    """Register tools on a dummy object and return the captured read_file fn."""

    captured: dict[str, Any] = {}

    class _Capture:
        def tool(self, fn):  # type: ignore[no-untyped-def]
            captured[fn.__name__] = fn
            return fn

    register_tools(_Capture())  # type: ignore[arg-type]
    return captured["read_file"]


def test_read_file_rejects_parent_escape(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    # Plant a sensitive-looking file OUTSIDE both roots.
    (tmp_path / "secret.txt").write_text("nope")
    read_file = _capture_read_file()
    ctx = _StubCtx(deps=deps)

    with pytest.raises(ValueError, match="escapes allowed root"):
        read_file(ctx, "../../etc/passwd")
    # Log should record the rejection.
    assert any("rejected=escape" in ev for ev in deps.log)


def test_read_file_reads_in_tree_file(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    read_file = _capture_read_file()
    ctx = _StubCtx(deps=deps)

    content = read_file(ctx, "hello.v")
    assert "Lemma h : True." in content
    assert any(ev.startswith("read_file path='hello.v'") for ev in deps.log)


def test_read_file_respects_line_range(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    (deps.repo_dir / "long.v").write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n")
    read_file = _capture_read_file()
    ctx = _StubCtx(deps=deps)

    chunk = read_file(ctx, "long.v", start=3, end=5)
    assert chunk.splitlines() == ["line 3", "line 4", "line 5"]


def test_read_file_missing_file_raises(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    read_file = _capture_read_file()
    ctx = _StubCtx(deps=deps)

    with pytest.raises(FileNotFoundError):
        read_file(ctx, "does-not-exist.v")
