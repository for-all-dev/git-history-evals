#!/usr/bin/env python3
"""Per-SHA Coq version detector for fiat-crypto.

For any fiat-crypto SHA, return the best Coq version tag to base the
per-commit Docker image on (a tag suitable for ``coqorg/coq:<TAG>`` on
Docker Hub, e.g. ``8.20.0``, ``8.18.0``, ``dev``).

Detection precedence (each step reads a file at the given SHA via
``git -C <repo> show <sha>:<path>``):

1. ``.github/workflows/coq-docker.yml`` -- parse the first non-``dev``
   ``DOCKER_COQ_VERSION`` from the matrix; if only ``dev`` entries are
   present, return ``dev``.
2. ``.github/workflows/coq-opam-package.yml`` -- parse the first matrix
   ``coq-version`` (e.g. ``8.20.0``).
3. ``.github/workflows/coq-debian.yml`` -- return ``dev`` (the Dockerfile
   will fall back to a Debian-based image).
4. ``.travis.yml`` -- parse the FIRST non-``master`` matrix entry. If it
   has ``COQ_PACKAGE="coq-X.Y.Z"``, return ``X.Y.Z``; otherwise if it has
   ``COQ_PACKAGE="coq"`` + ``COQ_VERSION="vX.Y"``, return ``X.Y``.
5. If none matched, exit with code 2 and stderr ``unknown``.

This module uses only the Python standard library (no PyYAML, no
third-party regex).
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Callable, Optional


WORKFLOW_DOCKER = ".github/workflows/coq-docker.yml"
WORKFLOW_OPAM = ".github/workflows/coq-opam-package.yml"
WORKFLOW_DEBIAN = ".github/workflows/coq-debian.yml"
TRAVIS = ".travis.yml"

# Paths queried, in precedence order, by ``detect_from_contents``.
_PATHS = (WORKFLOW_DOCKER, WORKFLOW_OPAM, WORKFLOW_DEBIAN, TRAVIS)


def _parse_docker(content: str) -> Optional[str]:
    """Return first non-``dev`` ``DOCKER_COQ_VERSION`` or ``dev`` if all are dev."""
    matches = re.findall(r'DOCKER_COQ_VERSION\s*:\s*["\']?([^"\',}\s]+)', content)
    if not matches:
        return None
    for v in matches:
        if v != "dev":
            return v
    return "dev"


def _parse_opam(content: str) -> Optional[str]:
    """Return the first matrix ``coq-version`` entry, skipping ``dev``/``master``."""
    # Find the matrix block's ``coq-version`` array, e.g.:
    #   coq-version: ['dev', '8.20.0']
    m = re.search(
        r"coq-version\s*:\s*\[([^\]]*)\]",
        content,
    )
    if m:
        items = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
        for v in items:
            if v not in ("dev", "master"):
                return v
        if items:
            return items[0]
    # Fallback: YAML list style
    #   coq-version:
    #     - 'dev'
    #     - '8.20.0'
    m = re.search(
        r"coq-version\s*:\s*\n((?:\s*-\s*['\"][^'\"]+['\"]\s*\n?)+)",
        content,
    )
    if m:
        items = re.findall(r"-\s*['\"]([^'\"]+)['\"]", m.group(1))
        for v in items:
            if v not in ("dev", "master"):
                return v
        if items:
            return items[0]
    return None


def _parse_debian(content: str) -> Optional[str]:
    """If the debian workflow is present at all, signal ``dev`` fallback."""
    if content.strip():
        return "dev"
    return None


def _parse_travis(content: str) -> Optional[str]:
    """Return the first non-``master`` matrix entry's Coq version hint.

    Walks ``env:`` lines of the ``jobs:`` matrix. Accepts either
    ``COQ_PACKAGE="coq-X.Y.Z"`` (returns ``X.Y.Z``) or
    ``COQ_PACKAGE="coq"`` + ``COQ_VERSION="vX.Y"`` (returns ``X.Y``).
    """
    # Match any line carrying both COQ_VERSION and COQ_PACKAGE env assignments.
    line_re = re.compile(
        r'COQ_VERSION\s*=\s*"([^"]+)".*?COQ_PACKAGE\s*=\s*"([^"]+)"'
    )
    for line in content.splitlines():
        m = line_re.search(line)
        if not m:
            continue
        coq_version = m.group(1)
        coq_package = m.group(2)
        if coq_version == "master":
            continue
        # Prefer explicit X.Y.Z from the package name.
        pkg_m = re.fullmatch(r"coq-(\d+\.\d+(?:\.\d+)?)", coq_package)
        if pkg_m:
            return pkg_m.group(1)
        # Fall back to COQ_VERSION="vX.Y" hint.
        ver_m = re.fullmatch(r"v?(\d+\.\d+(?:\.\d+)?)", coq_version)
        if ver_m:
            return ver_m.group(1)
    return None


# Per-path parser dispatch, used by ``detect_from_contents``.
_PARSERS: dict[str, Callable[[str], Optional[str]]] = {
    WORKFLOW_DOCKER: _parse_docker,
    WORKFLOW_OPAM: _parse_opam,
    WORKFLOW_DEBIAN: _parse_debian,
    TRAVIS: _parse_travis,
}


def detect_from_contents(files: dict[str, str]) -> Optional[str]:
    """Apply detection precedence over a dict of ``{path: content}``.

    Keys absent from ``files`` are treated as missing (skipped silently).
    Returns the detected version string (e.g. ``8.20.0`` or ``dev``), or
    ``None`` if no step yielded a result.
    """
    for path in _PATHS:
        content = files.get(path)
        if content is None:
            continue
        result = _PARSERS[path](content)
        if result is not None:
            return result
    return None


def _git_show(repo: str, sha: str, path: str) -> Optional[str]:
    """Return the contents of ``<sha>:<path>`` in ``repo``, or ``None`` if missing."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "show", f"{sha}:{path}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def detect(repo: str, sha: str) -> Optional[str]:
    """Detect the Coq version for ``sha`` in the git repo at ``repo``."""
    files: dict[str, str] = {}
    for path in _PATHS:
        content = _git_show(repo, sha, path)
        if content is not None:
            files[path] = content
    return detect_from_contents(files)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            f"usage: {argv[0]} <fiat_crypto_repo_path> <sha>",
            file=sys.stderr,
        )
        return 2
    repo, sha = argv[1], argv[2]
    result = detect(repo, sha)
    if result is None:
        print("unknown", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
