"""A prover-agnostic pydantic-ai ReAct loop that asks a model to solve an ablation
challenge.

The agent is restricted to reading other proof files in the repo — no internet, no git —
and its solution must contain no `sorry`/`admit`/ `Admitted`/`oops`/`axiom`, enforced
both textually and by compiling without holes.

"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from apply_ablate.apply import _LINK_DIRS, apply_record
from apply_ablate.diff import unified_or_empty
from apply_ablate.obs import log, set_attrs, span
from apply_ablate.provers import Prover, get_prover
from apply_ablate.record import AblationRecord

# proof-source extensions the agent may read (no internet / git / other files)
PROOF_EXTS = {".thy", ".lean", ".v"}
# a real re-derivation may not cheat with holes or new axioms
FORBIDDEN = re.compile(r"\b(sorry|admit|Admitted|oops|axiomatization|axiom|Axiom)\b")

SYSTEM_PROMPT = """\
You are a proof engineer. The following {assistant} proof contains one or more holes
that are left to be proved. Your job: edit the file so it compiles properly, deriving
any helper lemmas as you need.

Rules:
- Your final file MUST compile and MUST NOT contain `sorry`, `admit`, `Admitted`,
  `oops`, or any `axiom`/`axiomatization`. No cheating.
- You may explore proof files (.thy/.lean/.v) to learn definitions, lemmas, and idioms:
  `list_proof_files` lists the repo's own files; `search` regex-searches the repo AND its
  vendored libraries (e.g. mathlib under `.lake`) and shows matches with surrounding
  context; `read_file` reads any of them (including library files like
  `.lake/packages/mathlib/...`). Prefer `search` to locate a relevant lemma, then
  `read_file` it.
- You may NOT access the internet or git history. Work only from the files provided.
- Iterate: call `submit_solution` with the full corrected file; it compiles it and
  returns errors. Fix and resubmit until it passes, or `give_up` with a reason.
- BUDGET: you have at most {budget} model requests — every `read_file`, `list_proof_files`,
  `search`, and `submit_solution` call counts, and you are HARD-CUT-OFF when the budget is
  exhausted (no final turn). Every tool result reports your remaining budget (`turns_left`
  / `[budget: …]`); WATCH IT. Submit a COMPLETE first attempt EARLY (within your first few
  turns) — a full file you then refine against compile errors — rather than exploring
  until you run out. Don't over-explore; `read_file`/`search` are AUTO-DISABLED once few
  requests remain, leaving you only `submit_solution`. Only a fully hole-free submission
  is scored, so always get at least one complete attempt submitted.
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
    max_turns: int = 30  # request budget (surfaced to the agent so it can ration)
    submitted: bool = False  # did the agent call submit_solution at least once?


class Verdict(BaseModel):
    succeeded: bool = Field(description="Did the final submission compile cleanly?")
    gave_up: bool = Field(default=False)
    reason: str | None = Field(default=None, description="Give-up reason, if any.")


def _forbidden(content: str) -> str | None:
    m = FORBIDDEN.search(content)
    return m.group(0) if m else None


def _trace(event: str, **kw) -> None:
    """Append a tool-call trace line to $ABLATE_TRACE (if set) — a no-op otherwise.

    Diagnostic for what each tool actually receives/returns (e.g. whether
    submit_solution is called with a real `solution` arg or a blank one)."""
    path = os.environ.get("ABLATE_TRACE")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, **kw}) + "\n")
    except Exception:  # noqa: BLE001 - tracing must never break a run
        pass


def _budget(ctx) -> tuple[int | None, str]:  # type: ignore[no-untyped-def]
    """Remaining request budget + an advisory string for tool results.

    The run is hard-cut-off at `max_turns` model requests with no final turn, so every
    tool result surfaces how many are left (from `ctx.usage.requests`) and, when low,
    tells the agent to stop exploring and submit a complete file while it still can."""
    usage = getattr(ctx, "usage", None)
    used = getattr(usage, "requests", None)
    if used is None:  # older pydantic-ai / usage not tracked — degrade gracefully
        return None, ""
    left = max(0, ctx.deps.max_turns - int(used))
    if left <= 3:
        note = (
            f"~{left} request(s) left — HARD CUTOFF at 0 with NO final turn. Stop "
            "reading and call submit_solution NOW with a complete, compiling, "
            "sorry-free file (only a fully hole-free submission is scored)."
        )
    else:
        note = f"~{left} of {ctx.deps.max_turns} requests left."
    return left, note


def _with_budget(ctx, text: str) -> str:  # type: ignore[no-untyped-def]
    _, note = _budget(ctx)
    return f"{text}\n\n[budget: {note}]" if note else text


def _explore_blocked(ctx) -> str | None:  # type: ignore[no-untyped-def]
    """Once few requests remain, disable exploration (read_file/search) so the agent is
    forced to spend its last turns submitting instead of exploring until it's hard-cut-off
    with nothing submitted. Reserve ~1/5 of the budget (min 4) for submit+iterate."""
    left, _ = _budget(ctx)
    if left is None:
        return None
    reserve = max(4, ctx.deps.max_turns // 5)
    if left <= reserve:
        return (
            f"exploration disabled — only ~{left} request(s) left. STOP exploring and "
            "call submit_solution NOW with your best complete, compiling, sorry-free "
            "file (read_file/search are off until you submit)."
        )
    return None


def make_agent(model: str):
    """A pydantic-ai Agent (Anthropic gets retry/backoff, mirroring experiments/)."""
    from pydantic_ai import Agent, ModelRetry, RunContext

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
    # CRUCIAL: raise max_tokens well above pydantic-ai's 4096 default — `submit_solution`
    # must emit the ENTIRE corrected file as a tool argument, and a multi-KB proof file
    # exceeds 4096 output tokens, truncating the tool call (it shows up "blank" / fails
    # validation and the agent never actually submits).
    agent = Agent(
        model_obj,
        deps_type=SolveDeps,
        output_type=Verdict,
        retries=3,
        model_settings={"max_tokens": 32000},
    )

    @agent.system_prompt
    def _sys(ctx) -> str:  # type: ignore[no-untyped-def]
        return SYSTEM_PROMPT.format(
            assistant=ctx.deps.assistant, budget=ctx.deps.max_turns
        )

    @agent.tool
    def list_proof_files(ctx, query: str = "") -> str:  # type: ignore[no-untyped-def]
        """List the repo's OWN proof files (.thy/.lean/.v) whose path contains `query`.

        Excludes vendored library dirs (.lake / mathlib etc.) — those are huge; reach
        their lemmas with `search` and read specific hits with `read_file`."""
        root = ctx.deps.src_dir
        hits: list[str] = []
        for p in sorted(root.rglob("*")):
            if p.suffix not in PROOF_EXTS or not p.is_file():
                continue
            rel = p.relative_to(root)
            if rel.parts and rel.parts[0] in _LINK_DIRS:
                continue  # vendored library dir (.lake etc.) — use `search` instead
            if query in str(rel):
                hits.append(str(rel))
        return _with_budget(ctx, "\n".join(hits[:200]) or "(no matching proof files)")

    @agent.tool
    def read_file(ctx, path: str, start: int = 1, end: int = 400) -> str:  # type: ignore[no-untyped-def]
        """Read lines [start,end] of a proof file (relative to the repo root)."""
        # Path safety WITHOUT resolving symlinks: the apply-overlay symlinks sibling
        # files back to the pristine source, so `.resolve()` would land outside src_dir
        # and wrongly refuse every sibling (the agent could list files but not read
        # them). Reject absolute paths / `..` escapes lexically; reading a symlink that
        # points into the pristine source is fine (read-only).
        _trace("read_file", path=path, start=start, end=end)
        if (blocked := _explore_blocked(ctx)) is not None:
            return _with_budget(ctx, blocked)
        rel = Path(path)
        p = ctx.deps.src_dir / rel
        if rel.is_absolute() or ".." in rel.parts or p.suffix not in PROOF_EXTS:
            return _with_budget(
                ctx,
                f"error: refusing to read {path} (only .thy/.lean/.v inside the repo)",
            )
        if not p.is_file():
            # The model often guesses a path; a graceful miss must not abort the run.
            return _with_budget(
                ctx, f"error: no such file {path} (try list_proof_files to find it)"
            )
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        end = min(end, start + 2000, len(lines))
        body = "\n".join(f"{i + 1}\t{lines[i]}" for i in range(max(0, start - 1), end))
        return _with_budget(ctx, body)

    @agent.tool
    def search(ctx, pattern: str, context: int = 2) -> str:  # type: ignore[no-untyped-def]
        r"""Regex-search proof files (.thy/.lean/.v) across the repo AND its vendored
        libraries (e.g. mathlib under .lake) for `pattern`, returning each match as
        `path:line` with `context` surrounding lines. Use it to find lemma statements,
        definitions, and tactic idioms, then `read_file` the most promising hits
        (library files like `.lake/packages/mathlib/...` are readable).

        `pattern` is a ripgrep (Rust-regex) pattern: alternation is `|` (NOT `\|`), and
        `.` is any char. NOTE library lemmas usually appear UNQUALIFIED in source — search
        `sub_add_cancel`, not `Nat.sub_add_cancel`."""
        import shutil
        import subprocess

        rg = shutil.which("rg")
        if rg is None:
            return _with_budget(ctx, "error: ripgrep (rg) is not available")
        root = str(ctx.deps.src_dir.resolve())
        cn = str(max(0, min(int(context), 8)))

        # -L follows the overlay's symlinks (so `.lake`→mathlib is searched). CRUCIAL:
        # `.lake` is hidden AND git-ignored (repos put `/.lake` in .gitignore), so without
        # --hidden --no-ignore ripgrep silently skips all of mathlib. -m caps per-file
        # matches and -M truncates very long lines so one file can't flood.
        def _run(pat: str) -> str | None:
            cmd = [
                rg,
                "--no-heading",
                "-n",
                "-L",
                "--hidden",
                "--no-ignore",
                "-C",
                cn,
                "-M",
                "240",
                "-m",
                "4",
                "-g",
                "*.lean",
                "-g",
                "*.thy",
                "-g",
                "*.v",
                "-e",
                pat,
                root,
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                return None
            return proc.stdout.replace(root + "/", "")

        _trace("search", pattern=pattern, context=context)
        if (blocked := _explore_blocked(ctx)) is not None:
            return _with_budget(ctx, blocked)
        out = _run(pattern)
        if out is None:
            return _with_budget(
                ctx, "error: search timed out — try a more specific pattern"
            )
        note = ""
        # Common mistake: grep/BRE-style alternation `\|` (ripgrep is ERE — alternation
        # is `|`, so `\|` matches a literal pipe). Auto-retry as alternation if empty.
        if not out.strip() and r"\|" in pattern:
            alt = pattern.replace(r"\|", "|")
            retry = _run(alt)
            if retry and retry.strip():
                out = retry
                note = f"[note: treated `\\|` as alternation → searched `{alt}`]\n"
        if not out.strip():
            return _with_budget(
                ctx,
                "(no matches) — tip: ripgrep uses `|` for alternation (not `\\|`); and "
                "library lemmas appear UNQUALIFIED in source (try `sub_add_cancel`, not "
                "`Nat.sub_add_cancel`).",
            )
        lines = out.splitlines()
        if len(lines) > 300:
            lines = lines[:300] + ["… (truncated — narrow your pattern)"]
        return _with_budget(ctx, note + "\n".join(lines))

    @agent.tool
    def submit_solution(ctx, solution: str) -> dict:  # type: ignore[no-untyped-def]
        """Submit the full corrected file. Compiles it (no holes allowed); returns the result."""
        _trace(
            "submit_solution",
            solution_len=len(solution),
            head=solution[:80],
            tail=solution[-80:],
            n_sorry=_hole_count(solution),
        )
        ctx.deps.submitted = True
        bad = _forbidden(solution)
        if bad is not None:
            log(
                "submit rejected: forbidden token",
                token=bad,
                assistant=ctx.deps.assistant,
            )
            left, note = _budget(ctx)
            return {
                "ok": False,
                "error": f"forbidden token `{bad}` — re-derive it for real, no holes/axioms",
                "turns_left": left,
                "budget": note,
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
        left, note = _budget(ctx)
        return {
            "ok": res.ok,
            "error": "" if res.ok else res.trimmed(3000),
            "turns_left": left,
            "budget": note,
        }

    @agent.tool
    def give_up(ctx, reason: str) -> dict:  # type: ignore[no-untyped-def]
        """Abandon this challenge with a short reason."""
        log("agent gave up", reason=reason, assistant=ctx.deps.assistant)
        return {"ok": True, "noted": reason}

    @agent.output_validator  # ty: ignore[no-matching-overload]
    def _require_submission(ctx: RunContext[SolveDeps], output: Verdict) -> Verdict:
        # Close the escape hatch: the agent must actually deliver an attempt via
        # submit_solution (it's scored from the file on disk) — it can't "finish" by
        # emitting a Verdict after only exploring. Giving up is the explicit alternative.
        if not output.gave_up and not ctx.deps.submitted:
            raise ModelRetry(
                "You have not called submit_solution yet. Submit your COMPLETE corrected "
                "file with submit_solution (it is compiled and scored) before finishing, "
                "or set gave_up=true with a reason if you truly cannot proceed."
            )
        return output

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
    tampered: bool = False  # cheated: deleted/weakened a holed theorem's statement
    solution_compiles: bool | None = (
        None  # dry-run: did the ablator's solution compile?
    )


def _hole_count(content: str) -> int:
    return len(re.findall(r"\b(sorry|admit|Admitted|oops)\b", content))


# Declaration keywords per prover, for the statement-preservation guard.
_DECL_KW = {
    "lean": r"(?:theorem|lemma)",
    "coq": r"(?:Theorem|Lemma|Corollary|Proposition|Remark|Fact)",
    "isabelle": r"(?:theorem|lemma|corollary)",
}


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _tamper_reason(
    challenge: str, solution: str, holed: list[str], assistant: str
) -> str | None:
    """Detect the cheat where the agent makes the file compile by DELETING or WEAKENING
    a theorem it was asked to re-prove (the harness otherwise only checks compile +
    no-sorry). For Lean we compare the exact statement (text up to the `:=` proof
    delimiter); for Coq/Isabelle we fall back to name-presence (the proof delimiter is
    less uniform). Returns a reason string if a holed theorem was removed/weakened, else
    None."""
    kw = _DECL_KW.get(assistant, r"(?:theorem|lemma)")
    sol_norm = _norm_ws(solution)
    for name in holed:
        ename = re.escape(name)
        decl = re.search(rf"\b{kw}\s+{ename}\b", solution)
        if assistant == "lean":
            m = re.search(rf"\b{kw}\s+{ename}\b.*?(?=:=)", challenge, re.S)
            if m is not None:
                if _norm_ws(m.group(0)) not in sol_norm:
                    return (
                        f"holed theorem `{name}` was deleted or its statement weakened"
                    )
                continue
            # couldn't isolate the statement — fall back to name-presence
        if decl is None:
            return f"holed theorem `{name}` is missing from the solution"
    return None


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
    # Always log the full challenge up front — the ablated file, the exact prompt the
    # agent will get, and metadata — so every challenge is inspectable on Logfire
    # (dry-run or real), *before* and independent of the preflight outcome. Logging
    # here (not after the preflight gate) means challenges stay inspectable even when
    # the repo's sessions can't be built in this environment (e.g. l4v needs Word_Lib/
    # Monads heaps), where preflight is expected to fail — the whole point of a dry run.
    chal = record.challenge_file_content
    prompt = (
        f"The file `{record.file_path}` has one or more holes where proofs need to be completed. "
        f"Make the file compile. Here is the current (holed) file:\n\n"
        f"```\n{chal}\n```"
    )
    log(
        "challenge",
        file_path=record.file_path,
        assistant=record.assistant,
        task_id=record.task_id,
        holes=_hole_count(chal),
        chars=len(chal),
        dry_run=dry_run,
        system_prompt=SYSTEM_PROMPT.format(
            assistant=record.assistant, budget=max_turns
        ),
        prompt=prompt,
        challenge=chal,
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
        # Well-posedness check: the ablator's OWN ground-truth solution must compile
        # hole-free. If it doesn't, the (challenge, solution) pair is broken (an ablator
        # bug) — no model could ever pass it. This catches bad solutions the preflight
        # (which only compiles the *holed* challenge) cannot.
        sol = record.solution_text()
        sol_ok: bool | None = None
        if sol.strip() and _forbidden(sol) is None:
            with span(
                "dry-run-solution",
                assistant=record.assistant,
                file_path=record.file_path,
            ) as sp:
                target.write_text(sol, encoding="utf-8")
                sol_ok = prover.check(work, rel, allow_holes=False, timeout=timeout).ok
                set_attrs(sp, ok=sol_ok)
            target.write_text(chal, encoding="utf-8")  # restore the holed challenge
        return SolveResult(  # no model call
            task_id=record.task_id,
            assistant=record.assistant,
            file_path=record.file_path,
            succeeded=False,
            gave_up=False,
            dry_run=True,
            solution_compiles=sol_ok,
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
        max_turns=max_turns,
    )
    agent = make_agent(model)
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
            tamper = (
                _tamper_reason(
                    record.challenge_file_content,
                    final,
                    record.holed_theorems,
                    record.assistant,
                )
                if ok
                else None
            )
            if tamper:
                ok = False
            return SolveResult(
                task_id=record.task_id,
                assistant=record.assistant,
                file_path=record.file_path,
                succeeded=ok,
                gave_up=False,
                turn_limit=True,
                tampered=tamper is not None,
                error=tamper
                or (None if ok else f"turn limit ({max_turns} requests) reached"),
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
    # Score the agent's final delivered file by RECOMPILING it (don't trust the agent's
    # self-reported `succeeded`): pass iff it has no holes/axioms and compiles clean.
    final = deps.current
    ok = False
    if not v.gave_up and final.strip() and _forbidden(final) is None:
        target.write_text(final, encoding="utf-8")
        ok = prover.check(work, rel, allow_holes=False, timeout=timeout).ok
    # No-cheat guard: a file can compile hole-free yet still be a cheat if the agent
    # DELETED or WEAKENED the very theorem it was asked to re-prove. Reject those as
    # tampered (not PASS) so the score reflects genuine re-derivation.
    tamper = (
        _tamper_reason(
            record.challenge_file_content,
            final,
            record.holed_theorems,
            record.assistant,
        )
        if ok
        else None
    )
    if tamper:
        ok = False
    diff = unified_or_empty(record.challenge_file_content, final)
    return SolveResult(
        task_id=record.task_id,
        assistant=record.assistant,
        file_path=record.file_path,
        succeeded=ok,
        gave_up=v.gave_up,
        reason=v.reason,
        tampered=tamper is not None,
        error=tamper,
        solution_diff=diff,
    )
