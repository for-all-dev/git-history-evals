"""Prover backend protocol + shared helpers.

A `Prover` knows how to (a) `prepare` a repo (build dependencies once so single-file
checks are fast) and (b) `check` that one file compiles. Holes (`sorry`/`Admitted`)
are allowed when `allow_holes=True` — that is how an ablated *challenge* is verified
to be a well-formed starting point; a recovered *solution* is checked with
`allow_holes=False`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class CheckResult:
    """Outcome of a single compile/check."""

    ok: bool
    cmd: list[str]
    stdout: str
    stderr: str
    returncode: int | None = None
    note: str = ""

    def trimmed(self, limit: int = 4000) -> str:
        """Compact stderr/stdout for human-facing reports."""
        blob = (self.stderr or self.stdout or "").strip()
        if len(blob) > limit:
            blob = blob[:limit] + f"\n… (+{len(blob) - limit} bytes)"
        return blob


class Prover(Protocol):
    """A per-proof-assistant build backend."""

    key: str

    def prepare(self, repo: Path, *, timeout: int) -> CheckResult:
        """Build dependencies in `repo` so later single-file checks are fast."""
        ...

    def check(
        self, repo: Path, rel: Path, *, allow_holes: bool, timeout: int
    ) -> CheckResult:
        """Compile the single file `repo/rel`; `allow_holes` permits sorry/Admitted."""
        ...


def run(
    cmd: list[str],
    cwd: Path,
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> CheckResult:
    """Run `cmd` in `cwd`, capturing output; never raises on non-zero exit."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as e:
        return CheckResult(
            ok=False,
            cmd=cmd,
            stdout="",
            stderr=f"command not found: {e}",
            note="missing-tool",
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        out = out.decode() if isinstance(out, bytes) else out
        err = err.decode() if isinstance(err, bytes) else err
        return CheckResult(
            ok=False, cmd=cmd, stdout=out, stderr=err, note=f"timeout after {timeout}s"
        )
    return CheckResult(
        ok=proc.returncode == 0,
        cmd=cmd,
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
    )
