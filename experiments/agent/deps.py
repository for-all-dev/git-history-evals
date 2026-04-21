"""Typed dependency container for the pydantic-ai agent.

`AgentDeps` is the payload that pydantic-ai's `RunContext[AgentDeps]` carries
through every tool call. A fresh instance is constructed per slot by
`agent/runner.py` (issue #15) and then read by the tool implementations in
`agent/tools.py` (issue #11): `read_target`, `check`, `write_proof`, etc. The
tools look up paths (`slot_dir`, `repo_dir`, `rel_target`, `attempt_path`),
the declaration under proof (`decl`), the Coq include flags (`coq_flags`),
and the `make` timeout off this object, and append transcript events to `log`.

It is intentionally a plain `dataclasses.dataclass` rather than a
`pydantic.BaseModel`: these are pure in-process data, not validated I/O at a
trust boundary, so runtime validation buys nothing. pydantic-ai accepts any
deps type for `RunContext`, so there is no framework-side reason to pay for
`BaseModel`. This module also deliberately does NOT import `pydantic_ai` — it
stays as standalone data so it can be imported from anywhere (tests,
orchestration, tools) without pulling the agent framework in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentDeps:
    slot_dir: Path
    repo_dir: Path
    rel_target: Path
    decl: str
    attempt_path: Path
    coq_flags: list[str]
    make_timeout_s: int = 600
    log: list[str] = field(default_factory=list)
