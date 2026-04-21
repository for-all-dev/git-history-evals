"""Synchronous helpers for compiling a single Coq file and probing its axioms.

Three entry points, all stateless and safe to call concurrently (each call
uses its own subprocess and, where applicable, its own temp probe file):

* ``run_make_target`` — drives the repo's Makefile to build one ``.vo``.
* ``vo_bytes``        — reports the size of a compiled ``.vo``.
* ``print_assumptions`` — runs ``Print Assumptions`` for a single declaration
  via a throwaway probe file compiled with ``coqc``.

The ``_get_coq_flags`` helper is copy-pasted from ``experiments/run_experiment.py``
(issue #14 will dedupe these once ``run_experiment.py`` is refactored).
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, Field


# 8 KB truncation cap for captured stdout/stderr. Kept as a module constant
# so tests can exercise it without depending on subprocess output size.
_TRUNC_BYTES = 8 * 1024


class CompileResult(BaseModel):
    """Outcome of a single ``make -C <repo_dir> <target>.vo`` invocation."""

    ok: bool = Field(description="True iff exit_code == 0 AND the .vo exists post-run.")
    exit_code: int
    stdout: str
    stderr: str
    elapsed_s: float
    target: str


def _truncate(s: str, limit: int = _TRUNC_BYTES) -> str:
    """Return ``s`` truncated to at most ``limit`` bytes of UTF-8.

    We operate on the byte length so the ~8KB budget is a real upper bound
    regardless of multibyte characters. The string is decoded back with
    ``errors='replace'`` so a split codepoint does not raise.
    """
    data = s.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return s
    return data[:limit].decode("utf-8", errors="replace")


def _get_coq_flags(repo_dir: Path) -> list[str]:
    """Parse ``_CoqProject`` and return its ``-R``/``-Q``/``-I`` flag triples.

    Copied verbatim from ``experiments/run_experiment.py`` (lines 231–240).
    Issue #14 tracks the refactor that will let both callers share one copy.
    """
    cp = repo_dir / "_CoqProject"
    if not cp.exists():
        return []
    flags: list[str] = []
    for line in cp.read_text().splitlines():
        parts = line.strip().split()
        if parts and parts[0] in ("-R", "-Q", "-I") and len(parts) >= 3:
            flags.extend(parts[:3])
    return flags


def _vo_path(repo_dir: Path, rel_target: Path) -> Path:
    """Return the absolute ``.vo`` path corresponding to ``rel_target``.

    ``rel_target`` may point at either the source (``.v``) or the compiled
    artifact (``.vo``); we always land on the ``.vo``.
    """
    rel_vo = rel_target.with_suffix(".vo") if rel_target.suffix != ".vo" else rel_target
    return (repo_dir / rel_vo).resolve()


def run_make_target(
    repo_dir: Path,
    rel_target: Path,
    *,
    timeout: int = 600,
) -> CompileResult:
    """Invoke ``make -C <repo_dir> <rel_target>.vo`` and report the outcome.

    ``ok`` is ``True`` iff ``make`` exited zero *and* the ``.vo`` exists on
    disk afterwards — the latter guards against Makefiles that swallow errors
    or produce the wrong artifact. ``stdout``/``stderr`` are truncated to
    ~8KB each.
    """
    rel_vo = rel_target.with_suffix(".vo") if rel_target.suffix != ".vo" else rel_target
    target_str = str(rel_vo)
    vo_abs = _vo_path(repo_dir, rel_target)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["make", "-C", str(repo_dir), target_str],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
        stderr = (e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""))
        stderr = (stderr + f"\n[timeout after {timeout}s]").lstrip("\n")
        exit_code = 124  # conventional "timeout" exit code
    except FileNotFoundError as e:
        stdout = ""
        stderr = f"make not found: {e}"
        exit_code = 127
    elapsed = time.monotonic() - start

    ok = exit_code == 0 and vo_abs.exists()
    return CompileResult(
        ok=ok,
        exit_code=exit_code,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        elapsed_s=elapsed,
        target=target_str,
    )


def vo_bytes(repo_dir: Path, rel_target: Path) -> int | None:
    """Return the size in bytes of the ``.vo`` for ``rel_target``, or ``None``."""
    vo = _vo_path(repo_dir, rel_target)
    try:
        return os.path.getsize(vo)
    except OSError:
        return None


def _derive_module_path(repo_dir: Path, rel_target: Path, flags: list[str]) -> str | None:
    """Map ``rel_target`` (a ``.v`` or ``.vo`` under ``repo_dir``) to a Coq module path.

    Uses ``_CoqProject`` ``-R`` / ``-Q`` / ``-I`` flags: for each triple
    ``(-R|-Q, phys_dir, logical_prefix)``, if ``rel_target`` lives under
    ``phys_dir`` the returned module is ``<logical_prefix>.<dotted_subpath>.<stem>``.
    An ``-I`` flag contributes no logical prefix and is ignored here.
    Returns ``None`` if no flag covers ``rel_target``.
    """
    rel_v = rel_target.with_suffix(".v") if rel_target.suffix != ".v" else rel_target
    # Normalise to a POSIX path relative to repo_dir.
    rel_posix = Path(rel_v).as_posix().lstrip("./")

    # Walk flags in order; `-R`/`-Q` entries have (flag, phys, logical).
    i = 0
    best: tuple[int, str] | None = None  # (match length, module path)
    while i < len(flags):
        tag = flags[i]
        if tag in ("-R", "-Q") and i + 2 < len(flags):
            phys = flags[i + 1].rstrip("/")
            logical = flags[i + 2]
            phys_norm = "" if phys in (".", "") else phys + "/"
            if phys_norm == "" or rel_posix.startswith(phys_norm):
                sub = rel_posix[len(phys_norm):]
                sub_path = Path(sub)
                parts = list(sub_path.parent.parts) if str(sub_path.parent) != "." else []
                stem = sub_path.stem
                pieces = [logical] + parts + [stem] if logical else parts + [stem]
                module = ".".join(p for p in pieces if p)
                score = len(phys_norm)
                if best is None or score > best[0]:
                    best = (score, module)
            i += 3
        elif tag == "-I" and i + 1 < len(flags):
            i += 2
        else:
            i += 1

    return best[1] if best else None


def _parse_axioms(stdout: str) -> list[str]:
    """Extract axiom names from ``coqc`` stdout after a ``Print Assumptions`` call.

    The output begins with a line ``Axioms:`` and lists one axiom per
    subsequent indented line. Parsing stops at a blank line or at
    ``Closed under the global context.``.
    """
    axioms: list[str] = []
    lines = stdout.splitlines()
    in_axioms = False
    for line in lines:
        stripped = line.strip()
        if not in_axioms:
            if stripped.startswith("Axioms:"):
                in_axioms = True
                # "Axioms:" may be followed by an axiom on the same line.
                rest = stripped[len("Axioms:"):].strip()
                if rest:
                    # Take only the bare identifier (drop any ``: type`` part).
                    axioms.append(rest.split(":", 1)[0].strip())
            continue
        # inside the axiom block
        if not stripped or stripped == "Closed under the global context.":
            break
        # indented lines are axiom entries; each entry begins with a name.
        axioms.append(stripped.split(":", 1)[0].strip())
    return [a for a in axioms if a]


def print_assumptions(
    repo_dir: Path,
    rel_target: Path,
    decl: str,
    *,
    timeout: int = 120,
) -> list[str] | None:
    """Return the axioms ``decl`` depends on, via a ``Print Assumptions`` probe.

    Writes ``<stem>_probe.v`` next to the target, compiles it with ``coqc``
    using the repo's ``_CoqProject`` flags, parses the output, and cleans up
    the probe file (and its ``.vo``/``.vok``/``.glob`` siblings) unconditionally.
    Returns ``None`` on any failure (missing ``coqc``, compile error, unparsable
    output, unmappable module path, etc.).
    """
    rel_v = rel_target.with_suffix(".v") if rel_target.suffix != ".v" else rel_target
    target_v = (repo_dir / rel_v).resolve()
    if not target_v.exists():
        return None

    flags = _get_coq_flags(repo_dir)
    module_path = _derive_module_path(repo_dir, rel_v, flags)
    if module_path is None:
        return None

    probe = target_v.with_name(f"{target_v.stem}_probe.v")
    probe.write_text(
        f"Require Import {module_path}.\n"
        f"Print Assumptions {decl}.\n"
    )
    # Sibling artifacts coqc may emit.
    artifacts = [
        probe,
        probe.with_suffix(".vo"),
        probe.with_suffix(".vok"),
        probe.with_suffix(".vos"),
        probe.with_suffix(".glob"),
        probe.with_name(f".{probe.stem}.aux"),
    ]

    try:
        try:
            proc = subprocess.run(
                ["coqc", *flags, str(probe)],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return _parse_axioms(proc.stdout or "")
    finally:
        for p in artifacts:
            try:
                p.unlink()
            except OSError:
                pass
