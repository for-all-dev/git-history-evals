"""Coq/Rocq backend: `coqc <_CoqProject flags> <file>`.

Mirrors `rocq-ablator/bin/build_check.ml`. We walk up to the nearest `_CoqProject`,
extract its load-path flags (`-R`/`-Q`/`-I`), and run `coqc` from that project dir.
`Admitted.` is always accepted by `coqc` (exit 0 with a warning), so a holed challenge
compiles; `allow_holes` does not change the command. Dependencies must already exist
as `.vo` files — that is what `prepare` builds.
"""

from __future__ import annotations

from pathlib import Path

from apply_ablate.provers.base import CheckResult, run


def _find_coqproject(start: Path) -> Path | None:
    here = start if start.is_dir() else start.parent
    for d in (here, *here.parents):
        if (d / "_CoqProject").exists():
            return d / "_CoqProject"
    return None


def coq_flags(coqproject: Path) -> list[str]:
    """Parse `-R d n`, `-Q d n`, `-I d` load-path flags from a `_CoqProject`.

    File listings and other lines are ignored. Directories are left as written in the
    file (relative to the project dir, which is also the `coqc` cwd).
    """
    flags: list[str] = []
    tokens: list[str] = []
    for line in coqproject.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.extend(line.split())
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-R", "-Q") and i + 2 < len(tokens):
            flags += [tok, tokens[i + 1], tokens[i + 2]]
            i += 3
        elif tok == "-I" and i + 1 < len(tokens):
            flags += [tok, tokens[i + 1]]
            i += 2
        else:
            i += 1
    return flags


class CoqProver:
    key = "coq"

    def prepare(self, repo: Path, *, timeout: int) -> CheckResult:
        if (repo / "Makefile").exists():
            return run(["make", "-j"], cwd=repo, timeout=timeout)
        if (repo / "dune-project").exists():
            return run(["dune", "build"], cwd=repo, timeout=timeout)
        # Single-file project with no deps: nothing to prebuild.
        return CheckResult(ok=True, cmd=[], stdout="", stderr="", note="no-deps")

    def check(
        self, repo: Path, rel: Path, *, allow_holes: bool, timeout: int
    ) -> CheckResult:
        target = (repo / rel).resolve()
        proj = _find_coqproject(target)
        if proj is not None:
            cwd = proj.parent
            flags = coq_flags(proj)
        else:
            cwd = target.parent
            flags = []
        return run(["coqc", *flags, str(target)], cwd=cwd, timeout=timeout)
