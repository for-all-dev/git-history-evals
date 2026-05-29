"""Tier-1 calibration agent: synthesise a RepoProfile from a repo + its history.

A pydantic-ai (``CodeMode``) agent explores a proof-engineering repository,
samples its git history, and emits a :class:`~scaffold.profile.RepoProfile` — the
declarative spec the deterministic Phase-1 engine (git_walker / pattern_detector)
consumes. This is the "agentic miner" of the SoW: one scaffold, drop any proof
repo in, and the conventions are detected on the fly rather than hardcoded.

Public surface:
  - ``build_profile`` — run the agent end-to-end and persist the profile.
  - ``make_profiler_agent`` — the bare Agent factory.
  - ``register_tools`` / ``TOOL_NAMES`` — host tools and their sandbox names.
  - ``ProfilerDeps`` — the host-side config the tools close over.
"""

from __future__ import annotations

from .agent import make_profiler_agent
from .deps import ProfilerDeps
from .runner import ProfileBuildResult, build_profile
from .tools import TOOL_NAMES, build_tools, register_tools

__all__ = [
    "build_profile",
    "ProfileBuildResult",
    "make_profiler_agent",
    "build_tools",
    "register_tools",
    "TOOL_NAMES",
    "ProfilerDeps",
]
