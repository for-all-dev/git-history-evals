"""Pattern detector — repo analysis pre-pass.

Hybrid approach: fast heuristics for known patterns, optional LLM for ambiguous cases.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from scaffold.analyzers import detect_proof_assistant, get_analyzer
from scaffold.models import ProofAssistant, RepoMetadata

logger = logging.getLogger(__name__)

# Known build systems per proof assistant
_BUILD_FILES: dict[str, tuple[ProofAssistant, str]] = {
    "Makefile": (ProofAssistant.coq, "make"),
    "_CoqProject": (ProofAssistant.coq, "coq_makefile"),
    "lakefile.lean": (ProofAssistant.lean4, "lake build"),
    "lakefile.toml": (ProofAssistant.lean4, "lake build"),
    "ROOT": (ProofAssistant.isabelle, "isabelle build"),
}

# Paths to commonly exclude (generated files, vendored deps, etc.)
_COMMON_EXCLUDES = [
    "vendor",
    "third_party",
    "external",
    "_build",
    "build",
    ".build",
    "node_modules",
    "__pycache__",
]


def detect_build_system(repo_path: str | Path) -> dict[str, str]:
    """Detect build files present in the repo root."""
    repo = Path(repo_path)
    found: dict[str, str] = {}
    for fname, (pa, cmd) in _BUILD_FILES.items():
        if (repo / fname).exists():
            found[fname] = cmd
    return found


def detect_exclude_paths(repo_path: str | Path) -> list[str]:
    """Detect directories that should be excluded from mining."""
    repo = Path(repo_path)
    excludes: list[str] = []
    for entry in os.scandir(repo):
        if entry.is_dir() and entry.name in _COMMON_EXCLUDES:
            excludes.append(entry.name)
    return excludes


def analyze_repo(repo_path: str | Path) -> RepoMetadata:
    """Run full heuristic analysis on a repository.

    Detects proof assistant, file extensions, build system, and paths to exclude.
    """
    repo = Path(repo_path)
    name = repo.name

    pa = detect_proof_assistant(repo)
    if pa is None:
        logger.warning("Could not detect proof assistant for %s", name)
        pa = ProofAssistant.coq  # fallback

    analyzer = get_analyzer(pa)
    build_info = detect_build_system(repo)
    excludes = detect_exclude_paths(repo)

    # Try to find the repo URL from git remote
    url = _get_remote_url(repo)

    metadata = RepoMetadata(
        name=name,
        url=url,
        local_path=str(repo),
        proof_assistant=pa,
        file_extensions=analyzer.file_extensions,
        exclude_paths=excludes,
        discovered_patterns={
            "build_files": build_info,
            "hole_markers": [p.pattern for p in analyzer.hole_markers],
        },
    )

    logger.info("Analyzed %s: assistant=%s, extensions=%s", name, pa.value, analyzer.file_extensions)
    return metadata


def _get_remote_url(repo_path: Path) -> str:
    """Try to extract the remote URL from git config."""
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def classify_commit_message(message: str) -> str:
    """Simple heuristic classification of commit messages.

    Returns one of: 'proof_fill', 'refactor', 'new_feature', 'fix', 'other'.
    """
    msg = message.lower()

    proof_keywords = ["proof", "prove", "qed", "admit", "sorry", "lemma", "theorem"]
    fill_keywords = ["complete", "fill", "finish", "close", "resolve"]
    refactor_keywords = ["refactor", "rename", "move", "clean", "reorganize"]
    fix_keywords = ["fix", "bug", "patch", "correct", "repair"]

    if any(pk in msg for pk in proof_keywords) and any(fk in msg for fk in fill_keywords):
        return "proof_fill"
    if any(rk in msg for rk in refactor_keywords):
        return "refactor"
    if any(fk in msg for fk in fix_keywords):
        return "fix"
    if any(pk in msg for pk in proof_keywords):
        return "proof_fill"
    return "other"
