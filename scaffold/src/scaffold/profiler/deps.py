"""Configuration container for the calibration agent's host-side tools.

Unlike ``experiments/agent`` (which threads state through a ``RunContext`` deps
object), the profiler's tools are registered as ``@agent.tool_plain`` closures
over a single ``ProfilerDeps`` instance — the model never sees the repo path or
budgets, only the meaningful per-call arguments. The deps object carries the
repo under calibration, the sampling caps that bound tool cost on huge
histories (seL4 is 1M+ lines), and an append-only event log the runner reads
back for a transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProfilerDeps:
    """Host-side state shared by every profiler tool (closed over, not in ctx)."""

    repo_path: Path
    # Cost guards — the agent can ask for more, but defaults keep a single
    # `test_profile` round cheap enough to iterate on interactively.
    default_test_commits: int = 1500
    max_files_scanned: int = 400
    max_read_lines: int = 2000
    log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.repo_path = Path(self.repo_path)
