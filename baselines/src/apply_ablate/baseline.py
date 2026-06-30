"""Baseline driver: run the agent loop over a JSONL of ablation challenges and report
how often the model re-derives the deleted lemma into a *compiling, hole-free* proof.

Prover-agnostic (coq / isabelle / lean): each challenge's `proof_assistant` selects the
backend, so the same driver produces Rocq, Lean, and Isabelle baselines. Run it inside
the relevant prover's nix dev shell (so `coqc`/`isabelle`/`lake` are on PATH) with
ANTHROPIC_API_KEY available (loaded from the repo `.env`).

    ablate-baseline CHALLENGES.jsonl SRC --model anthropic:claude-sonnet-4-6 --limit 10
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

import typer

from apply_ablate.record import AblationRecord, load_record
from apply_ablate.solve import solve_one

app = typer.Typer(add_completion=False, help=__doc__)


def _load_env() -> None:
    """Load the monorepo .env (ANTHROPIC_API_KEY) if present, best-effort."""
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover
        return
    here = Path(__file__).resolve()
    for d in (Path.cwd(), *here.parents):
        env = d / ".env"
        if env.is_file():
            load_dotenv(env)
            return


def _count_records(jsonl: Path) -> int:
    return sum(1 for ln in jsonl.read_text(encoding="utf-8").splitlines() if ln.strip())


@app.command()
def run(
    challenges: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, help="JSONL of ablation challenges."
        ),
    ],
    src: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Pristine source repo to copy per challenge.",
        ),
    ],
    model: Annotated[
        str, typer.Option("--model", help="pydantic-ai model id.")
    ] = "anthropic:claude-sonnet-4-6",
    limit: Annotated[
        int, typer.Option("--limit", help="Max challenges to run (0 = all).")
    ] = 0,
    max_turns: Annotated[
        int,
        typer.Option(
            "--max-turns",
            help="Agent request budget (counts every model call incl. tool turns).",
        ),
    ] = 30,
    timeout: Annotated[
        int, typer.Option("--timeout", help="Per-compile timeout (s).")
    ] = 600,
    out: Annotated[Path, typer.Option("--out", help="Results JSONL.")] = Path(
        "baseline-results.jsonl"
    ),
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Apply + pre-flight + log each challenge to Logfire; never call the "
            "model (no tokens spent). Use to inspect a challenge set.",
        ),
    ] = False,
) -> None:
    _load_env()
    from apply_ablate.obs import init_logfire, log, set_attrs, span

    init_logfire()
    n = _count_records(challenges)
    n = n if limit <= 0 else min(limit, n)
    results = []
    with out.open("w", encoding="utf-8") as fh, tempfile.TemporaryDirectory() as td:
        for i in range(n):
            rec = load_record(challenges, i)
            work = Path(td) / f"work_{i}"
            typer.echo(f"[{i + 1}/{n}] {rec.assistant} {rec.file_path} …", err=True)
            with span(
                "challenge",
                index=i,
                assistant=rec.assistant,
                file_path=rec.file_path,
                model=model,
            ) as sp:
                res = solve_one(
                    rec,
                    src,
                    work,
                    model=model,
                    max_turns=max_turns,
                    timeout=timeout,
                    dry_run=dry_run,
                )
                outcome = (
                    "pass"
                    if res.succeeded
                    else "dry_run"
                    if res.dry_run
                    else "trivial"
                    if res.trivial
                    else "malformed"
                    if res.malformed_challenge
                    else "tampered"
                    if res.tampered
                    else "gave_up"
                    if res.gave_up
                    else "turn_limit"
                    if res.turn_limit
                    else "error"
                    if res.error
                    else "fail"
                )
                set_attrs(
                    sp,
                    outcome=outcome,
                    succeeded=res.succeeded,
                    gave_up=res.gave_up,
                    turn_limit=res.turn_limit,
                    malformed=res.malformed_challenge,
                    trivial=res.trivial,
                    reason=res.reason,
                    error=res.error,
                )
                # The full challenge (prompt + ablated file + metadata) is logged up
                # front by solve_one as the "challenge" event; this is the *outcome*.
                log(
                    f"outcome: {outcome}",
                    file_path=rec.file_path,
                    assistant=rec.assistant,
                    outcome=outcome,
                    reason=res.reason,
                    error=res.error,
                )
            fh.write(res.model_dump_json() + "\n")
            fh.flush()
            results.append(res)
            tag = (
                "DRY"
                if res.dry_run
                else "PASS"
                if res.succeeded
                else ("GAVE_UP" if res.gave_up else "FAIL")
            )
            typer.echo(
                f"    -> {tag}{(': ' + res.error) if res.error else ''}", err=True
            )
    # summary
    total = len(results)
    malformed = sum(1 for r in results if r.malformed_challenge)
    trivial = sum(1 for r in results if r.trivial)
    typer.echo("")
    if dry_run:
        well_formed = sum(1 for r in results if r.dry_run)
        sol_ok = sum(1 for r in results if r.solution_compiles is True)
        sol_bad = sum(1 for r in results if r.solution_compiles is False)
        typer.echo("=== dry run (no model called) ===")
        typer.echo(f"  challenges  : {total}")
        typer.echo(f"  well-formed : {well_formed} (applied + pre-flight compiled)")
        typer.echo(
            f"  solution ok : {sol_ok} (ablator's ground-truth compiled hole-free)"
        )
        typer.echo(
            f"  solution BAD: {sol_bad} (ground-truth did NOT compile — ablator bug)"
        )
        typer.echo(f"  trivial     : {trivial} (empty diff; nothing deleted)")
        typer.echo(f"  malformed   : {malformed} (challenge did not compile)")
        typer.echo(f"  results     : {out}  (challenges logged to Logfire)")
        return
    scorable = total - malformed - trivial
    passed = sum(1 for r in results if r.succeeded)
    tampered = sum(1 for r in results if r.tampered)
    gave_up = sum(1 for r in results if r.gave_up)
    turn_limited = sum(1 for r in results if r.turn_limit and not r.succeeded)
    errored = sum(
        1
        for r in results
        if r.error
        and not r.malformed_challenge
        and not r.turn_limit
        and not r.trivial
        and not r.tampered
    )
    typer.echo(f"=== baseline: {model} ===")
    typer.echo(f"  challenges : {total}")
    typer.echo(f"  trivial    : {trivial} (empty diff; nothing deleted — excluded)")
    typer.echo(f"  malformed  : {malformed} (ablator bug; excluded from PASS rate)")
    typer.echo(
        f"  PASS       : {passed}/{scorable} "
        f"({(100.0 * passed / scorable if scorable else 0):.0f}% of scorable)"
    )
    typer.echo(
        f"  tampered   : {tampered} (compiled but deleted/weakened a holed theorem)"
    )
    typer.echo(f"  gave up    : {gave_up}")
    typer.echo(f"  turn-limit : {turn_limited} (ran out of request budget, no compile)")
    typer.echo(f"  harness err: {errored}")
    typer.echo(f"  results    : {out}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()


__all__ = ["AblationRecord"]
