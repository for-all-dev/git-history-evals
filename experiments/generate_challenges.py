"""
Generate progressive deletion challenge files.

For each slot in experiments3/ that has a solution.v, creates:
  challengeN.v  for N in DELETION_SIZES

challenge3.v and challenge.v (full Admitted) are assumed to already exist.
This script only generates the intermediate sizes.

Usage:
    uv run generate_challenges.py [--slots experiments3] [--sizes 5,7,10,15]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

sys.path.insert(0, str(Path(__file__).parent))
from proof_utils import remove_last_n_tactics

DEFAULT_SIZES = [5, 7, 10, 15]
BASE = Path(__file__).parent
EXP_B = BASE / "experiments3"


def generate_for_slot(slot: Path, sizes: list[int], verbose: bool) -> None:
    meta_path = slot / "meta.json"
    solution_path = slot / "solution.v"

    if not meta_path.exists() or not solution_path.exists():
        if verbose:
            print(f"  skip {slot.name}: missing meta.json or solution.v")
        return

    meta = json.loads(meta_path.read_text())
    decl = meta["declaration"]
    solution = solution_path.read_text()

    for n in sizes:
        out_path = slot / f"challenge{n}.v"
        if out_path.exists():
            if verbose:
                print(f"  {slot.name}/challenge{n}.v already exists, skipping")
            continue

        result = remove_last_n_tactics(solution, decl, n)
        if result is None:
            print(f"  WARNING: could not parse proof of '{decl}' in {slot.name}/solution.v "
                  f"for n={n}")
            continue

        out_path.write_text(result)
        if verbose:
            print(f"  wrote {slot.name}/challenge{n}.v")


def main(
    slots_dir: Path = typer.Option(EXP_B, "--slots", help="Directory containing challenge slots"),
    sizes: str = typer.Option(
        ",".join(map(str, DEFAULT_SIZES)),
        "--sizes",
        help="Comma-separated deletion sizes to generate",
    ),
    verbose: bool = typer.Option(True, "--verbose/--quiet"),
) -> None:
    deletion_sizes = [int(s.strip()) for s in sizes.split(",")]
    slots = sorted(p for p in slots_dir.iterdir() if p.is_dir())

    print(f"Generating challenges for deletion sizes {deletion_sizes} "
          f"across {len(slots)} slots in {slots_dir}")

    for slot in slots:
        generate_for_slot(slot, deletion_sizes, verbose)

    print("Done.")


if __name__ == "__main__":
    typer.run(main)