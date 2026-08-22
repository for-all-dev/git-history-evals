"""Locate the monorepo root and the fixed inputs figures read from.

This project lives at ``comms/vericode-workshop/figures`` inside the
``git-history-evals`` monorepo. Rather than hardcode a parent-count (fragile
if this project is ever moved), walk up from this file looking for the
sibling directories that must always exist together: ``pipeline/`` (TSV
inputs) and ``comms/`` (this project's own ancestor).
"""

from __future__ import annotations

from pathlib import Path


class RepoLayoutError(RuntimeError):
    """Raised when the monorepo root can't be located from this file."""


def find_repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pipeline").is_dir() and (candidate / "comms").is_dir():
            return candidate
    raise RepoLayoutError(
        f"could not find the git-history-evals repo root walking up from {here} "
        "(expected a directory containing both pipeline/ and comms/)"
    )


def data_dir(repo_root: Path) -> Path:
    return repo_root / "comms" / "vericode-workshop" / "data"


def pipeline_dir(repo_root: Path) -> Path:
    return repo_root / "pipeline"


def out_dir(figures_project_root: Path) -> Path:
    return figures_project_root / "out"
