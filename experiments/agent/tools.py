"""Tool registrations for the pydantic-ai ReAct proof-completion agent.

`register_tools(agent)` attaches four `@agent.tool` functions that give the
model the minimum surface area it needs to: inspect the source tree
(`read_file`), write a candidate proof (`write_proof`), compile it against
the per-commit repo (`compile`), and concede (`give_up`). Each tool appends
a one-line event to `ctx.deps.log` so the runner can reconstruct a
transcript without inspecting tool message history.

The actual Agent construction lives in `agent/agent.py` (#10); the run loop
that assembles `AgentDeps` and drives the agent lives in `agent/runner.py`
(#15). Keeping tools in their own module means both can import this one
without a cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import RunContext

from agent.deps import AgentDeps
from shared.compile import run_make_target
from shared.splice import patch_admitted

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from pydantic_ai import Agent

    from agent.agent import AgentVerdict


# Per-call read cap (lines). Kept small to protect the model's context window.
_MAX_READ_LINES = 2000

# Per-stream cap on compile output returned to the model (bytes). `run_make_target`
# already truncates to ~8KB; we trim further here so a single tool call can't
# burn the full context.
_COMPILE_TRUNC_BYTES = 4 * 1024


def _resolve_under(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root`` and reject any traversal outside ``root``.

    Uses ``Path.resolve().is_relative_to(root.resolve())`` so symlinks and
    ``..`` segments are both normalised before the containment check.
    """
    root_resolved = root.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise ValueError(f"path {rel!r} escapes allowed root {root_resolved}")
    return candidate


def _trim_bytes(s: str, limit: int) -> str:
    """Truncate ``s`` to at most ``limit`` UTF-8 bytes, decoding defensively."""
    data = s.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return s
    return data[:limit].decode("utf-8", errors="replace")


def register_tools(agent: "Agent[AgentDeps, AgentVerdict]") -> None:
    """Attach the four proof-completion tools to ``agent`` in place.

    Call this exactly once per Agent instance, after construction and before
    ``agent.run(...)``. Tools close over no external state; everything they
    need arrives via ``RunContext[AgentDeps]``.
    """

    @agent.tool
    def read_file(
        ctx: RunContext[AgentDeps],
        path: str,
        start: int = 1,
        end: int = -1,
    ) -> str:
        """Read a slice of a source file from the repo or slot directory.

        Use this to inspect definitions, imports, or neighbouring proofs
        before writing tactics. ``path`` must live under the per-commit
        repo checkout or the current slot directory; traversal outside those
        roots is rejected. ``start`` and ``end`` are 1-indexed, inclusive;
        ``end=-1`` means "to EOF". At most 2000 lines are returned per call,
        so page through large files with successive calls.
        """
        last_err: Exception | None = None
        resolved: Path | None = None
        for root in (ctx.deps.repo_dir, ctx.deps.slot_dir):
            try:
                resolved = _resolve_under(root, path)
                break
            except ValueError as e:
                last_err = e
                continue
        if resolved is None:
            ctx.deps.log.append(f"read_file path={path!r} rejected=escape")
            raise ValueError(str(last_err) if last_err else f"{path!r} not under any allowed root")

        if not resolved.exists() or not resolved.is_file():
            ctx.deps.log.append(f"read_file path={path!r} missing=True")
            raise FileNotFoundError(f"{path!r} does not exist under allowed roots")

        with resolved.open("r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        total = len(all_lines)

        start_idx = max(1, start) - 1
        end_idx = total if end == -1 else min(total, end)
        if end_idx < start_idx:
            end_idx = start_idx
        # Enforce hard cap.
        if end_idx - start_idx > _MAX_READ_LINES:
            end_idx = start_idx + _MAX_READ_LINES

        chunk = "".join(all_lines[start_idx:end_idx])
        ctx.deps.log.append(
            f"read_file path={path!r} lines={start_idx + 1}-{end_idx} total={total}"
        )
        return chunk

    @agent.tool
    def write_proof(ctx: RunContext[AgentDeps], tactics: str) -> dict:
        """Splice ``tactics`` into the challenge in place of ``Admitted.``.

        Write only the proof body (tactic script followed by ``Qed.`` or
        ``Defined.``); the surrounding declaration is preserved from the
        slot's ``challenge.v``. The result is persisted to the slot's
        attempt path and copied onto the repo target so that ``compile``
        builds what you just wrote. Returns the size of the written file.
        """
        challenge_src = ctx.deps.slot_dir / "challenge.v"
        content = challenge_src.read_text(encoding="utf-8")
        patched = patch_admitted(content, ctx.deps.decl, tactics)

        attempt_path = ctx.deps.attempt_path
        attempt_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_path.write_text(patched, encoding="utf-8")

        # Mirror onto the repo so `compile` sees the new proof. Guard against
        # rel_target escaping repo_dir just as `read_file` does.
        repo_target = _resolve_under(ctx.deps.repo_dir, str(ctx.deps.rel_target))
        repo_target.parent.mkdir(parents=True, exist_ok=True)
        repo_target.write_text(patched, encoding="utf-8")

        chars_written = len(patched)
        lines_written = patched.count("\n") + (0 if patched.endswith("\n") or not patched else 1)
        ctx.deps.log.append(
            f"write_proof chars={chars_written} lines={lines_written}"
        )
        return {"chars_written": chars_written, "lines_written": lines_written}

    @agent.tool
    def compile(ctx: RunContext[AgentDeps]) -> dict:
        """Compile the target file with ``make`` and return the result.

        Run this after every ``write_proof`` to confirm the proof typechecks.
        ``ok`` is ``True`` only if ``make`` exited zero *and* the ``.vo``
        was produced. Stdout/stderr are trimmed to ~4KB each; inspect them
        to guide the next tactic edit.
        """
        result = run_make_target(
            ctx.deps.repo_dir,
            ctx.deps.rel_target,
            timeout=ctx.deps.make_timeout_s,
        )
        ctx.deps.log.append(
            f"compile ok={result.ok} elapsed={result.elapsed_s:.2f}s exit={result.exit_code}"
        )
        return {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "stdout": _trim_bytes(result.stdout, _COMPILE_TRUNC_BYTES),
            "stderr": _trim_bytes(result.stderr, _COMPILE_TRUNC_BYTES),
            "elapsed_s": result.elapsed_s,
        }

    @agent.tool
    def give_up(ctx: RunContext[AgentDeps], reason: str) -> dict:
        """Signal that you cannot complete this proof and explain why.

        Use this only after at least one real compile attempt has failed
        and you have no promising next step. The runner treats this as a
        terminal event.
        """
        ctx.deps.log.append(f"give_up reason={reason!r}")
        return {"acknowledged": True}
