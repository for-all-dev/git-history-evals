"""A prover-agnostic pydantic-ai ReAct loop that asks a model to re-derive the
deleted lemma(s) in an ablation challenge.

The challenge (from `--delete-lemmas-leaves --count N --shrink-*-minimal`) has the
deleted lemma removed and its use sites holed; the holed *leaf* steps are hints. The
agent edits the file to fill the holes (re-deriving whatever it needs), and we record
the diff challenge→solution. The agent is restricted to reading other proof files in
the repo — no internet, no git — and its solution must contain no `sorry`/`admit`/
`Admitted`/`oops`/`axiom`, enforced both textually and by compiling without holes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from apply_ablate.apply import apply_record
from apply_ablate.diff import unified_or_empty
from apply_ablate.obs import log, set_attrs, span
from apply_ablate.provers import Prover, get_prover
from apply_ablate.record import AblationRecord

# proof-source extensions the agent may read (no internet / git / other files)
PROOF_EXTS = {".thy", ".lean", ".v"}
# a real re-derivation may not cheat with holes or new axioms
FORBIDDEN = re.compile(r"\b(sorry|admit|Admitted|oops|axiomatization|axiom|Axiom)\b")

SYSTEM_PROMPT = """\
You are a proof engineer. A lemma was deleted from a {assistant} proof file and every
place that used it was replaced with a hole. Your job: edit the file so it compiles
again, re-deriving whatever the deleted lemma provided (inline, or by reintroducing
helper lemmas of your own). The holed leaf steps show exactly where the deleted lemma
was used — use them as hints.

Rules:
- Your final file MUST compile and MUST NOT contain `sorry`, `admit`, `Admitted`,
  `oops`, or any `axiom`/`axiomatization`. No cheating.
- You may read OTHER proof files in this repository (.thy/.lean/.v) with `read_file`
  and `list_proof_files` to learn definitions, lemmas, and idioms.
- You may NOT access the internet or git history. Work only from the files provided.
- Iterate: call `submit_solution` with the full corrected file; it compiles it and
  returns errors. Fix and resubmit until it passes, or `give_up` with a reason.
"""


@dataclass
class SolveDeps:
    work_dir: Path
    src_dir: Path
    target: Path  # absolute path of the holed file in work_dir
    rel: Path  # target relative to work_dir
    assistant: str
    prover: Prover
    timeout: int = 600
    current: str = ""  # latest submitted content (or the challenge initially)


class Verdict(BaseModel):
    succeeded: bool = Field(description="Did the final submission compile cleanly?")
    gave_up: bool = Field(default=False)
    reason: str | None = Field(default=None, description="Give-up reason, if any.")


def _forbidden(content: str) -> str | None:
    m = FORBIDDEN.search(content)
    return m.group(0) if m else None


def make_agent(model: str):
    """A pydantic-ai Agent (Anthropic gets retry/backoff, mirroring experiments/)."""
    from pydantic_ai import Agent

    name = model.removeprefix("anthropic:")
    from anthropic import AsyncAnthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    client = AsyncAnthropic(max_retries=8)
    model_obj = AnthropicModel(
        name, provider=AnthropicProvider(anthropic_client=client)
    )
    # Allow several tool-call validation retries: large proof files occasionally trip a
    # transient tool-arg validation error, which would otherwise abort the whole run.
    agent = Agent(model_obj, deps_type=SolveDeps, output_type=Verdict, retries=3)

    @agent.system_prompt
    def _sys(ctx) -> str:  # type: ignore[no-untyped-def]
        return SYSTEM_PROMPT.format(assistant=ctx.deps.assistant)

    @agent.tool
    def list_proof_files(ctx, query: str = "") -> str:  # type: ignore[no-untyped-def]
        """List proof files (.thy/.lean/.v) in the repo whose path contains `query`."""
        root = ctx.deps.src_dir
        hits = [
            str(p.relative_to(root))
            for p in sorted(root.rglob("*"))
            if p.suffix in PROOF_EXTS and p.is_file() and query in str(p)
        ]
        return "\n".join(hits[:200]) or "(no matching proof files)"

    @agent.tool
    def read_file(ctx, path: str, start: int = 1, end: int = 400) -> str:  # type: ignore[no-untyped-def]
        """Read lines [start,end] of a proof file (relative to the repo root)."""
        p = (ctx.deps.src_dir / path).resolve()
        if ctx.deps.src_dir.resolve() not in p.parents or p.suffix not in PROOF_EXTS:
            return (
                f"error: refusing to read {path} (only .thy/.lean/.v inside the repo)"
            )
        if not p.is_file():
            # The model often guesses a path; a graceful miss must not abort the run.
            return f"error: no such file {path} (try list_proof_files to find it)"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        end = min(end, start + 2000, len(lines))
        return "\n".join(f"{i + 1}\t{lines[i]}" for i in range(max(0, start - 1), end))

    @agent.tool
    def submit_solution(ctx, solution: str) -> dict:  # type: ignore[no-untyped-def]
        """Submit the full corrected file. Compiles it (no holes allowed); returns the result."""
        bad = _forbidden(solution)
        if bad is not None:
            log(
                "submit rejected: forbidden token",
                token=bad,
                assistant=ctx.deps.assistant,
            )
            return {
                "ok": False,
                "error": f"forbidden token `{bad}` — re-derive it for real, no holes/axioms",
            }
        ctx.deps.current = solution
        ctx.deps.target.write_text(solution, encoding="utf-8")
        with span(
            "compile",
            assistant=ctx.deps.assistant,
            file=str(ctx.deps.rel),
            allow_holes=False,
        ) as sp:
            res = ctx.deps.prover.check(
                ctx.deps.work_dir,
                ctx.deps.rel,
                allow_holes=False,
                timeout=ctx.deps.timeout,
            )
            set_attrs(
                sp,
                ok=res.ok,
                note=res.note,
                returncode=res.returncode,
                error=("" if res.ok else res.trimmed(800)),
            )
        log("submit compiled" if res.ok else "submit failed", ok=res.ok, note=res.note)
        return {"ok": res.ok, "error": "" if res.ok else res.trimmed(3000)}

    @agent.tool
    def give_up(ctx, reason: str) -> dict:  # type: ignore[no-untyped-def]
        """Abandon this challenge with a short reason."""
        log("agent gave up", reason=reason, assistant=ctx.deps.assistant)
        return {"ok": True, "noted": reason}

    return agent


class SolveResult(BaseModel):
    task_id: str | None
    assistant: str
    file_path: str
    succeeded: bool
    gave_up: bool
    reason: str | None = None
    solution_diff: str = ""  # challenge -> agent solution (empty if unsolved/unchanged)
    error: str | None = None
    malformed_challenge: bool = (
        False  # the challenge itself failed to compile (ablator bug)
    )
    turn_limit: bool = False  # the agent exhausted its request budget
    trivial: bool = False  # empty solution diff (nothing deleted/holed) — not scorable
    dry_run: bool = False  # inspected only; the model was never called


def _hole_count(content: str) -> int:
    return len(re.findall(r"\b(sorry|admit|Admitted|oops)\b", content))


def solve_one(
    record: AblationRecord,
    src: Path,
    work: Path,
    *,
    model: str,
    max_turns: int = 30,
    timeout: int = 600,
    dry_run: bool = False,
) -> SolveResult:
    """Apply one challenge into `work`, run the agent loop, return the verdict + diff.

    With `dry_run=True` the model is never called: the challenge is still applied and
    pre-flight-compiled (so `malformed`/`trivial` are detected and the challenge content
    + metadata are logged to Logfire), then a `dry_run` result is returned. Lets you
    inspect the whole challenge set on Logfire without spending tokens.
    """
    # A challenge whose solution diff is empty is *trivial*: nothing was deleted/holed,
    # so the challenge already equals a complete file and any model "passes" by doing
    # nothing. That inflates PASS rates, so skip it (excluded from scoring upstream).
    # The real fix is in the ablators (don't emit a record when 0 lemmas are deleted);
    # this is the harness safety net that keeps numbers honest regardless of ablator.
    if record.solution_diff.strip() == "":
        return SolveResult(
            task_id=record.task_id,
            assistant=record.assistant,
            file_path=record.file_path,
            succeeded=False,
            gave_up=False,
            trivial=True,
            error="trivial-challenge: empty solution diff (nothing deleted/holed)",
        )
    prover = get_prover(record.assistant)
    target = apply_record(record, src, work, overwrite=True)
    rel = (
        target.relative_to(work.resolve())
        if target.is_absolute()
        else target.relative_to(work)
    )
    # Pre-flight: a well-formed challenge must compile *with* holes. If it does not,
    # the ablation itself is broken (e.g. a mis-placed hole) — record it as a malformed
    # challenge rather than blaming the model, so ablator bugs stay distinguishable.
    with span(
        "preflight", assistant=record.assistant, file_path=record.file_path
    ) as sp:
        pre = prover.check(work, rel, allow_holes=True, timeout=timeout)
        set_attrs(sp, ok=pre.ok, note=pre.note)
    if not pre.ok:
        return SolveResult(
            task_id=record.task_id,
            assistant=record.assistant,
            file_path=record.file_path,
            succeeded=False,
            gave_up=False,
            error=f"malformed-challenge: {pre.trimmed(1500)}",
            malformed_challenge=True,
        )
    if dry_run:
        # No model call. The challenge body/metadata are attached to the `challenge`
        # span by the caller (baseline.py), so it's inspectable on Logfire as one event.
        return SolveResult(
            task_id=record.task_id,
            assistant=record.assistant,
            file_path=record.file_path,
            succeeded=False,
            gave_up=False,
            dry_run=True,
        )
    deps = SolveDeps(
        work_dir=work,
        src_dir=work,
        target=target,
        rel=rel,
        assistant=record.assistant,
        prover=prover,
        timeout=timeout,
        current=record.challenge_file_content,
    )
    agent = make_agent(model)
    prompt = (
        f"The file `{record.file_path}` has holes where a deleted lemma was used. "
        f"Re-derive it and make the file compile. Here is the current (holed) file:\n\n"
        f"```\n{record.challenge_file_content}\n```"
    )
    try:
        from pydantic_ai.usage import UsageLimits

        from typing import cast

        out = agent.run_sync(
            prompt, deps=deps, usage_limits=UsageLimits(request_limit=max_turns)
        )
        v = cast(Verdict, out.output)
    except Exception as e:  # noqa: BLE001 - record any agent/runtime failure as a non-pass
        # Running out of request budget is not a wrong answer — the agent's last
        # on-disk attempt may still compile, so score it directly. Re-derivation that
        # genuinely failed simply won't compile and stays a (turn-limited) FAIL.
        if type(e).__name__ == "UsageLimitExceeded":
            final = deps.current
            ok = (
                _forbidden(final) is None
                and prover.check(work, rel, allow_holes=False, timeout=timeout).ok
            )
            return SolveResult(
                task_id=record.task_id,
                assistant=record.assistant,
                file_path=record.file_path,
                succeeded=ok,
                gave_up=False,
                turn_limit=True,
                error=None if ok else f"turn limit ({max_turns} requests) reached",
                solution_diff=unified_or_empty(record.challenge_file_content, final),
            )
        return SolveResult(
            task_id=record.task_id,
            assistant=record.assistant,
            file_path=record.file_path,
            succeeded=False,
            gave_up=False,
            error=f"{type(e).__name__}: {e}",
        )
    # the recorded solution is the diff challenge -> whatever the agent left on disk
    final = deps.current
    diff = unified_or_empty(record.challenge_file_content, final)
    return SolveResult(
        task_id=record.task_id,
        assistant=record.assistant,
        file_path=record.file_path,
        succeeded=v.succeeded and _forbidden(final) is None,
        gave_up=v.gave_up,
        reason=v.reason,
        solution_diff=diff,
    )
