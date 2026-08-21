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
import time

# proof-source extensions the agent may read (no internet / git / other files)
PROOF_EXTS = {".thy", ".lean", ".v"}
# a real re-derivation may not cheat with holes or new axioms. Two classes, treated
# differently at SUBMIT time: HOLES (sorry/admit/...) are legitimate scaffolding — a
# partial submission is compiled with holes allowed for feedback, but never scored;
# CHEATING (new axioms) is always rejected. The final PASS check (below) still requires
# a fully hole-free file via FORBIDDEN, so allowing holes mid-iteration cannot inflate
# the score.
FORBIDDEN = re.compile(r"\b(sorry|admit|Admitted|oops|axiomatization|axiom|Axiom)\b")
CHEATING = re.compile(r"\b(axiomatization|axiom|Axiom)\b")

SYSTEM_PROMPT = """\
You are a proof engineer. The following {assistant} proof contains one or more holes
that are left to be proved. Your job: edit the file so it compiles properly, deriving
any helper lemmas as you need.

Rules:
- Your final (scored) file MUST compile and MUST NOT contain `sorry`, `admit`,
  `Admitted`, `oops`, or any `axiom`/`axiomatization`. No cheating.
- You MAY submit a PARTIAL proof with `sorry`/`admit` placeholders while iterating: it is
  compiled with holes allowed and you get feedback — whether the rest type-checks and how
  many holes remain — so you can lock in the structure first, then fill each hole. A file
  with holes is never scored as solved (only a fully hole-free, compiling file is), and
  `axiom`/`axiomatization` are rejected outright.
- You may explore proof files (.thy/.lean/.v) to learn definitions, lemmas, and idioms:
  `list_proof_files` lists the repo's own files; `search` regex-searches the repo AND its
  vendored libraries (e.g. mathlib under `.lake`) and shows matches with surrounding
  context; `read_file` reads any of them (including library files like
  `.lake/packages/mathlib/...`). Prefer `search` to locate a relevant lemma, then
  `read_file` it.
- You may NOT access the internet or git history. Work only from the files provided.
- Iterate: call `submit_solution` with the full file; it compiles it and returns the
  compiler errors (and, for a partial submission, the count of remaining holes). Fix and
  resubmit until it compiles hole-free, or `give_up` with a reason.
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


def _cheating(content: str) -> str | None:
    """A new-axiom token (always rejected), as opposed to a hole (allowed for feedback)."""
    m = CHEATING.search(content)
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


def _retrying_async_client(max_wait: float = 90.0, attempts: int = 8):
    """An httpx client that backs off on rate-limit / transient statuses.

    Mistral's client (unlike Anthropic's `max_retries`) does not retry HTTP 429s, so a
    single rate-limit response would abort a whole challenge. This transport retries
    429/502/503/504, honouring the `Retry-After` header (falling back to exponential
    backoff), so bursts of `Rate limit exceeded` self-throttle instead of erroring —
    important on the free tier / with any concurrency.
    """
    import httpx
    from pydantic_ai.retries import (
        AsyncTenacityTransport,
        RetryConfig,
        wait_retry_after,
    )
    from tenacity import retry_if_exception_type, stop_after_attempt

    def validate_response(response: httpx.Response) -> None:
        if response.status_code in (429, 502, 503, 504):
            response.raise_for_status()

    transport = AsyncTenacityTransport(
        config=RetryConfig(
            # Retry rate-limit/5xx statuses AND transport-level timeouts: the leanstral
            # free-tier endpoint intermittently `ReadTimeout`s mid-stream, and without
            # this a single slow response aborts the whole challenge as a harness-err
            # instead of yielding a real outcome. `httpx.TimeoutException` covers
            # Read/Connect/Write/Pool timeouts.
            retry=retry_if_exception_type(
                (httpx.HTTPStatusError, httpx.TimeoutException)
            ),
            wait=wait_retry_after(max_wait=max_wait),
            stop=stop_after_attempt(attempts),
            reraise=True,
        ),
        validate_response=validate_response,
    )
    # Generous read timeout: leanstral can take >2 min to stream a full corrected file
    # at max_tokens=32000, so the previous 120 s read timeout fired mid-generation and
    # surfaced as a harness-err. Keep connect short so genuinely dead connections fail fast.
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(300.0, connect=15.0),
    )


def make_agent(model: str):
    """A pydantic-ai Agent for a `<provider>:<name>` model id.

    Anthropic (the default, prefix optional) gets an explicit retry/backoff client,
    mirroring experiments/. Mistral (`mistral:labs-leanstral-1-5`, the Lean-4 prover
    Leanstral) is also supported — it reads `MISTRAL_API_KEY` from the env and gets a
    429-backoff HTTP client. Any other known pydantic-ai prefix falls through.
    """
    from pydantic_ai import Agent, ModelRetry, RunContext

    if model.startswith("mistral:"):
        from pydantic_ai.models.mistral import MistralModel
        from pydantic_ai.providers.mistral import MistralProvider

        model_obj = MistralModel(
            model.removeprefix("mistral:"),
            provider=MistralProvider(http_client=_retrying_async_client()),
        )
    else:
        # default provider is Anthropic; the prefix is optional for it
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
        """Submit the full file. It is compiled and the result returned. A hole-free file
        that compiles cleanly is scored as solved; a file with `sorry`/`admit` is compiled
        with holes allowed for FEEDBACK (never scored) so you can iterate on partial
        proofs; `axiom`/`axiomatization` are rejected as cheating."""
        n_holes = _hole_count(solution)
        _trace(
            "submit_solution",
            solution_len=len(solution),
            head=solution[:80],
            tail=solution[-80:],
            n_sorry=n_holes,
        )
        ctx.deps.submitted = True
        left, note = _budget(ctx)

        def _feedback(outcome: str, payload: dict) -> dict:
            # Log the EXACT payload returned to the agent (the tool result it actually
            # receives), so the feedback loop is visible in Logfire — not only the span.
            log(
                "submit feedback",
                assistant=ctx.deps.assistant,
                file=str(ctx.deps.rel),
                outcome=outcome,
                ok=payload.get("ok"),
                compiles=payload.get("compiles"),
                holes_remaining=n_holes,
                returned_error=payload.get("error", ""),
            )
            return payload

        cheat = _cheating(solution)
        if cheat is not None:
            return _feedback(
                "rejected-cheat",
                {
                    "ok": False,
                    "error": f"forbidden token `{cheat}` — introducing an axiom is "
                    "cheating; re-derive it for real",
                    "turns_left": left,
                    "budget": note,
                },
            )

        ctx.deps.current = solution
        ctx.deps.target.write_text(solution, encoding="utf-8")
        has_holes = n_holes > 0
        with span(
            "compile",
            assistant=ctx.deps.assistant,
            file=str(ctx.deps.rel),
            allow_holes=has_holes,
        ) as sp:
            res = ctx.deps.prover.check(
                ctx.deps.work_dir,
                ctx.deps.rel,
                allow_holes=has_holes,
                timeout=ctx.deps.timeout,
            )
            set_attrs(
                sp,
                ok=res.ok,
                note=res.note,
                returncode=res.returncode,
                error=("" if res.ok else res.trimmed(800)),
            )

        solved = res.ok and not has_holes
        if not has_holes:
            error = "" if res.ok else res.trimmed(3000)
        elif res.ok:
            # the non-hole parts type-check; only the placeholders remain to fill
            error = (
                f"Compiles with holes allowed, but {n_holes} `sorry`/`admit` hole(s) "
                "remain — replace each with a real proof. Not scored until hole-free."
            )
        else:
            error = (
                f"{n_holes} hole(s) remain, and there are still errors even with holes "
                f"allowed:\n{res.trimmed(3000)}"
            )
        return _feedback(
            "pass" if solved else ("holes" if has_holes else "compile-error"),
            {
                "ok": solved,
                "compiles": res.ok,
                "holes_remaining": n_holes,
                "error": error,
                "turns_left": left,
                "budget": note,
            },
        )

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
    # stable, unique per-challenge id from the enriched ablator record (None on legacy
    # datasets); the exact join key for difficulty feature<->label tables
    challenge_id: str | None = None
    assistant: str
    file_path: str
    succeeded: bool
    gave_up: bool
    reason: str | None = None
    solution_diff: str = ""  # challenge -> agent solution (empty if unsolved/unchanged)
    agent_solution: str = ""  # the agent's final delivered file (whole)
    original_solution: str = ""  # the ablator's ground-truth solution (whole)
    # what the ablator removed/holed in this challenge (may be several — delete/corollary
    # mode deletes N lemmas and holes all their in-file users)
    deleted_lemmas: list[str] = Field(default_factory=list)
    holed_theorems: list[str] = Field(default_factory=list)
    error: str | None = None
    malformed_challenge: bool = (
        False  # the challenge itself failed to compile (ablator bug)
    )
    turn_limit: bool = False  # the agent exhausted its request budget
    trivial: bool = False  # empty solution diff (nothing deleted/holed) — not scorable
    # the challenge did not fit the model's context window (provider rejected the prompt).
    # Not a wrong answer — the model never saw the problem — so it is excluded from the
    # PASS denominator rather than counted as a failure.
    context_exceeded: bool = False
    dry_run: bool = False  # inspected only; the model was never called
    tampered: bool = False  # cheated: deleted/weakened a holed theorem's statement
    solution_compiles: bool | None = (
        None  # dry-run: did the ablator's solution compile?
    )
    turns_used: int | None = None  # actual number of requests used
    input_tokens: int | None = None  # input tokens consumed
    output_tokens: int | None = None  # output tokens consumed
    elapsed_seconds: float | None = None  # wall-clock time in seconds
    max_turns: int | None = None  # the --max-turns this run was invoked with


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


def _log_solutions(record: AblationRecord, final: str, *, outcome: str) -> None:
    """Log the agent's final file and the ablator's original ground-truth solution
    together (with diffs) so a human can copy them out of Logfire and compare. Emits both
    the agent-vs-original diff (how the agent's re-derivation differs from the human proof)
    and the agent-vs-challenge diff (what the agent actually changed/added to the ablated
    file). Emitted once the agent finishes — pass, fail, give-up, or turn-limit."""
    original = record.solution_text()
    log(
        "solution",
        file_path=record.file_path,
        assistant=record.assistant,
        task_id=record.task_id,
        outcome=outcome,
        holed_theorems=record.holed_theorems,
        agent_solution=final,
        original_solution=original,
        agent_vs_original_diff=unified_or_empty(original, final),
        agent_vs_challenge_diff=unified_or_empty(record.challenge_file_content, final),
    )


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
    start_time = time.perf_counter()
    if record.solution_diff.strip() == "":
        return SolveResult(
            task_id=record.task_id,
            assistant=record.assistant,
            file_path=record.file_path,
            succeeded=False,
            gave_up=False,
            trivial=True,
            error="trivial-challenge: empty solution diff (nothing deleted/holed)",
            max_turns=max_turns,
            elapsed_seconds=time.perf_counter() - start_time,
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
    # Surface the deleted lemma(s) — the crux the agent must re-derive — when the
    # challenge was produced in delete/corollary mode (empty for pure proof-ablation).
    deleted = record.deleted_lemmas
    log(
        "challenge",
        file_path=record.file_path,
        assistant=record.assistant,
        task_id=record.task_id,
        holes=_hole_count(chal),
        chars=len(chal),
        dry_run=dry_run,
        deleted_lemmas=record.deleted_lemma_names,
        deleted_lemma_text="\n\n".join(d.text for d in deleted if d.text) or None,
        holed_theorems=record.holed_theorems,
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
            max_turns=max_turns,
            elapsed_seconds=time.perf_counter() - start_time,
        )
    if dry_run:
        # Well-posedness check: the ablator's OWN ground-truth solution must compile
        # hole-free. If it doesn't, the (challenge, solution) pair is broken — no model
        # could ever pass it. This catches bad solutions the preflight (which only
        # compiles the *holed* challenge) cannot. Two failure modes both count as a bad
        # solution: (a) the solution still contains forbidden tokens (`sorry`/`axiom`/…),
        # which happens when the source file has PRE-EXISTING holes that a prefix shrink
        # keeps — the challenge is then unwinnable; (b) it doesn't compile.
        sol = record.solution_text()
        sol_ok: bool | None = None
        sol_note: str | None = None
        if sol.strip():
            forbidden = _forbidden(sol)
            if forbidden is not None:
                sol_ok = False
                sol_note = (
                    f"ground-truth solution contains {forbidden!r} "
                    "(pre-existing hole/axiom in the source — challenge is unwinnable)"
                )
            else:
                with span(
                    "dry-run-solution",
                    assistant=record.assistant,
                    file_path=record.file_path,
                ) as sp:
                    target.write_text(sol, encoding="utf-8")
                    sol_ok = prover.check(
                        work, rel, allow_holes=False, timeout=timeout
                    ).ok
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
            error=sol_note,
            max_turns=max_turns,
            elapsed_seconds=time.perf_counter() - start_time,
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
            _log_solutions(
                record,
                final,
                outcome="pass" if ok else ("tampered" if tamper else "turn_limit"),
            )
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
                agent_solution=final,
                original_solution=record.solution_text(),
                max_turns=max_turns,
                turns_used=max_turns,
                elapsed_seconds=time.perf_counter() - start_time,
            )
        # A challenge that does not FIT in the model's context window is not a wrong
        # answer — the model never saw the problem. Scoring it as a failure blames the
        # solver for a property of the (challenge, model) pair, so flag it and exclude it
        # from the PASS denominator, exactly as `malformed`/`trivial` are excluded.
        msg = str(e)
        oversized = "context" in msg.lower() and (
            "too large for model" in msg or "maximum context length" in msg
        )
        return SolveResult(
            task_id=record.task_id,
            assistant=record.assistant,
            file_path=record.file_path,
            succeeded=False,
            gave_up=False,
            context_exceeded=oversized,
            error=f"{type(e).__name__}: {e}",
            max_turns=max_turns,
            elapsed_seconds=time.perf_counter() - start_time,
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
    _log_solutions(
        record,
        final,
        outcome="pass"
        if ok
        else ("tampered" if tamper else ("gave_up" if v.gave_up else "fail")),
    )
    diff = unified_or_empty(record.challenge_file_content, final)
    # pydantic-ai 2.x exposes usage as a RunUsage property; 1.x had a method
    usage = out.usage() if callable(out.usage) else out.usage
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
        agent_solution=final,
        original_solution=record.solution_text(),
        max_turns=max_turns,
        turns_used=usage.requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        elapsed_seconds=time.perf_counter() - start_time,
    )
