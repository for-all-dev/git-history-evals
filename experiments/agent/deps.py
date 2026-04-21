"""Typed dependency container for the pydantic-ai agent.

`AgentDeps` is the payload that pydantic-ai's `RunContext[AgentDeps]` carries
through every tool call. A fresh instance is constructed per slot by
`agent/runner.py` (issue #15) and then read by the tool implementations in
`agent/tools.py` (issue #11): `read_target`, `check`, `write_proof`, etc. The
tools look up paths (`slot_dir`, `repo_dir`, `rel_target`, `attempt_path`),
the declaration under proof (`decl`), the Coq include flags (`coq_flags`),
and the `make` timeout off this object, and append transcript events to `log`.

It is a `pydantic.BaseModel` for consistency with the rest of `experiments/`
(`metrics.py`, `shared/compile.py`'s `CompileResult`) and idiomatic pydantic-ai
usage. Construction validates that paths are `Path` instances rather than
stray strings — cheap insurance against a tool reading the wrong field.
This module deliberately does NOT import `pydantic_ai`: it stays as
standalone data so it can be imported from anywhere (tests, orchestration,
tools) without pulling the agent framework in.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class AgentDeps(BaseModel):
    slot_dir: Path = Field(description="Per-slot directory: holds challenge.v, meta.json, attempt files.")
    repo_dir: Path = Field(description="Per-commit fiat-crypto checkout the agent compiles against.")
    rel_target: Path = Field(description="File inside repo_dir the agent edits, relative to repo_dir.")
    decl: str = Field(description="Coq declaration under proof.")
    attempt_path: Path = Field(description="Where write_proof persists the spliced result.")
    coq_flags: list[str] = Field(description="Project _CoqProject -R/-Q/-I flags for coqc/coqtop invocations.")
    make_timeout_s: int = Field(default=600, description="Per-compile timeout passed to subprocess.run.")
    log: list[str] = Field(default_factory=list, description="Append-only event log for transcript reconstruction.")
