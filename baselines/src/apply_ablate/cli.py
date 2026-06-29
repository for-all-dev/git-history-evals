"""`apply-ablate` CLI: materialise an ablation challenge and (optionally) verify it.

    apply-ablate JSONL INDEX SRC DST [--prepare] [--check] [--full-check]
                                     [--overwrite] [--solution] [--timeout N]

Pipeline (each flag adds a stage; default = apply only):
  --prepare     build deps in SRC in place (copies inherit the warm artifacts)
  (always)      copy SRC->DST, write the challenge at DST/<file_path>
  --check       holes-allowed compile of the applied challenge in DST
  --full-check  --check, then recover the original via solution_diff and compile it
                (DST is left in challenge state)
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from apply_ablate.apply import apply_record, write_content
from apply_ablate.provers import CheckResult, get_prover
from apply_ablate.record import load_record

app = typer.Typer(add_completion=False, help=__doc__)


def _echo(msg: str) -> None:
    typer.echo(msg)


def _report(label: str, res: CheckResult) -> bool:
    status = "PASS" if res.ok else "FAIL"
    note = f" ({res.note})" if res.note else ""
    _echo(f"{label}: {status}{note}")
    if not res.ok:
        blob = res.trimmed()
        if blob:
            _echo(blob)
    return res.ok


@app.command()
def run(
    jsonl: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, readable=True, help="Ablation JSONL file."
        ),
    ],
    index: Annotated[
        int, typer.Argument(help="0-based record index within the JSONL.")
    ],
    src: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, help="Pristine source repo.")
    ],
    dst: Annotated[
        Path,
        typer.Argument(file_okay=False, help="Destination working copy to create."),
    ],
    prepare: Annotated[
        bool, typer.Option("--prepare", help="Build deps in SRC first.")
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="Holes-allowed compile of the challenge.")
    ] = False,
    full_check: Annotated[
        bool,
        typer.Option(
            "--full-check", help="--check plus compile the recovered solution."
        ),
    ] = False,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace DST if it exists.")
    ] = False,
    solution: Annotated[
        bool,
        typer.Option(
            "--solution", help="Write the recovered solution instead of the challenge."
        ),
    ] = False,
    include_git: Annotated[
        bool, typer.Option("--include-git", help="Copy the .git directory too.")
    ] = False,
    timeout: Annotated[
        int, typer.Option("--timeout", help="Per-command timeout (seconds).")
    ] = 600,
) -> None:
    try:
        record = load_record(jsonl, index)
        prover = get_prover(record.assistant)
    except (OSError, ValueError, KeyError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2) from None
    _echo(f"record: {record.assistant} {record.file_path} (task_id={record.task_id})")

    if prepare:
        _report("prepare", prover.prepare(src, timeout=timeout))

    # --full-check always materialises the challenge (so DST is left in challenge state).
    write_solution = solution and not full_check
    try:
        target = apply_record(
            record,
            src,
            dst,
            overwrite=overwrite,
            solution=write_solution,
            include_git=include_git,
        )
    except (OSError, RuntimeError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2) from None
    rel = (
        target.relative_to(dst.resolve())
        if target.is_absolute()
        else target.relative_to(dst)
    )
    _echo(f"apply: {target} ({'solution' if write_solution else 'challenge'})")

    ok = True
    if full_check:
        write_content(target, record.challenge_file_content)
        ok &= _report(
            "check (challenge, holes)",
            prover.check(dst, rel, allow_holes=True, timeout=timeout),
        )
        write_content(target, record.solution_text())
        ok &= _report(
            "check (solution)",
            prover.check(dst, rel, allow_holes=False, timeout=timeout),
        )
        write_content(
            target, record.challenge_file_content
        )  # leave DST in challenge state
    elif check:
        ok &= _report(
            f"check ({'solution' if write_solution else 'challenge'})",
            prover.check(dst, rel, allow_holes=not write_solution, timeout=timeout),
        )

    if not ok:
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
