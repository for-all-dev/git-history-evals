"""Load-and-solve quickstart for `for-all-dev/ablation-eval` (issue #141).

Ties together the pieces that already exist in this package into the thing
`docs/SoW.md` promises as a deliverable — "demonstration of how to download the
data from huggingface and make a solver":

    1. `datasets.load_dataset("for-all-dev/ablation-eval", split="easy")`
    2. filter to one repo via the per-row `manifest.repo` field
    3. splice the challenge into a work copy, call a model, score by real
       compilation — via the *existing* `apply_ablate.baseline.run` driver
       (its `--repo` manifest-aware slicing is what #128/#155 added; this
       script reuses that, it does not reimplement it)
    4. print PASS / trivial / malformed / turn-limit / ... using the shipped
       taxonomy, printed by `baseline.run` itself, including the scorable
       denominator (`scorable = total - malformed - trivial - context_exceeded`)

PREREQUISITES (read this before running)
-----------------------------------------
- Network access to huggingface.co (step 1 only needs this).
- A **built** checkout of the target repo's toolchain under
  `data/lean/<repo>` — i.e. `lake build` (or the Coq/Isabelle equivalent) has
  already succeeded there. The dataset's Lean checkouts are *cloned but not
  built* by default (`pipeline/clone_repos.sh lean`) — a fresh clone is not
  enough. Without a build, every row will preflight-check as `malformed`,
  which reads as "the model is bad" when it is really "the checkout is cold".
  See `pipeline/README.md`'s "Validation is a build problem" section.
- The matching toolchain on PATH for that repo's `proof_assistant` (Lean:
  `elan`/`lake`; Coq: `coqc`; Isabelle: `isabelle`) — `pipeline/repos.tsv`
  records each repo's pinned toolchain version.
- `ANTHROPIC_API_KEY` set (in the environment, or a `.env` at the repo root
  or in `baselines/`) — skip this only when passing `--dry-run`, which
  splices + preflight-compiles every row but never calls a model.

Usage
-----
    cd baselines
    uv run python quickstart.py --list-repos                     # what's in `easy`
    uv run python quickstart.py --repo <name> --src ../data/lean/<name> --dry-run
    uv run python quickstart.py --repo <name> --src ../data/lean/<name> \\
        --model anthropic:claude-sonnet-4-6 --limit 3

`--repo` must match a `manifest.repo` value in the split (see `--list-repos`);
`--src` must be that repo's checkout, already built (see PREREQUISITES).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


def _rows_by_repo(split_rows, repo: str | None) -> tuple[list[dict], list[str]]:
    """Group `split_rows` by `manifest.repo`; return (matching rows, all repo names)."""
    by_repo: dict[str, list[dict]] = {}
    for row in split_rows:
        manifest = row.get("manifest") or {}
        name = manifest.get("repo")
        if name:
            by_repo.setdefault(name, []).append(row)
    available = sorted(by_repo)
    if repo is None:
        return [], available
    return by_repo.get(repo, []), available


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--split",
        default="easy",
        choices=["easy", "hard"],
        help="ablation-eval split to pull from (default: easy).",
    )
    parser.add_argument(
        "--list-repos",
        action="store_true",
        help="print every manifest.repo value present in the split, then exit "
        "(no --repo/--src needed).",
    )
    parser.add_argument(
        "--repo",
        help="run only rows whose manifest.repo matches this name (required "
        "unless --list-repos).",
    )
    parser.add_argument(
        "--src",
        type=Path,
        help="path to that repo's BUILT checkout, e.g. ../data/lean/<repo> "
        "(required unless --list-repos). See PREREQUISITES in this file's "
        "docstring.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="max challenges to run from the filtered rows (default: 3; 0 = all).",
    )
    parser.add_argument(
        "--model",
        default="anthropic:claude-sonnet-4-6",
        help="pydantic-ai model id (default: anthropic:claude-sonnet-4-6).",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=30,
        help="agent request budget per challenge (default: 30).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="per-compile timeout in seconds (default: 600).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("quickstart-results.jsonl"),
        help="results JSONL (default: ./quickstart-results.jsonl).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="splice + preflight-compile every row and log to Logfire, but never "
        "call a model (no ANTHROPIC_API_KEY needed, no tokens spent). Use this "
        "to check the checkout is actually built before spending a model budget.",
    )
    args = parser.parse_args(argv)

    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit(
            "error: the `datasets` package is not installed. Run this from "
            "baselines/ with `uv sync` (it's a declared dependency), or "
            "`uv run python quickstart.py ...`."
        )

    print(
        f"loading for-all-dev/ablation-eval split={args.split!r} ...", file=sys.stderr
    )
    ds = load_dataset("for-all-dev/ablation-eval", split=args.split)
    print(f"  {len(ds)} rows", file=sys.stderr)

    if args.list_repos:
        _, available = _rows_by_repo(ds, None)
        print(f"repos in split={args.split!r}:")
        for name in available:
            print(f"  {name}")
        return

    if not args.repo or not args.src:
        parser.error("--repo and --src are required (or pass --list-repos)")

    rows, available = _rows_by_repo(ds, args.repo)
    if not rows:
        sys.exit(
            f"error: --repo {args.repo!r} matches no rows in split={args.split!r}. "
            f"Repos present: {', '.join(available) or '(none)'}\n"
            "Run with --list-repos to see the full list."
        )
    if args.limit > 0:
        rows = rows[: args.limit]
    print(
        f"repo: {args.repo} ({len(rows)} row(s) selected from split={args.split!r})",
        file=sys.stderr,
    )

    if not args.src.is_dir():
        sys.exit(
            f"error: --src {args.src} is not a directory. It must be a BUILT "
            f"checkout of {args.repo!r} (see PREREQUISITES at the top of "
            "quickstart.py / `python quickstart.py --help`)."
        )

    # Write the filtered rows to a scratch JSONL and hand off to the *existing*
    # baseline driver (apply_ablate.baseline.run) — it already knows how to slice
    # by manifest.repo, splice+compile via apply_ablate.apply, call the model via
    # apply_ablate.solve, and print the PASS/trivial/malformed/... taxonomy with
    # the scorable denominator. Reuse it rather than reimplementing any of that.
    from apply_ablate.baseline import run as baseline_run

    with tempfile.TemporaryDirectory() as td:
        challenges_path = Path(td) / f"{args.repo}-{args.split}.jsonl"
        with challenges_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

        baseline_run(
            challenges=challenges_path,
            src=args.src,
            model=args.model,
            repo=args.repo,
            limit=0,  # already sliced above
            max_turns=args.max_turns,
            timeout=args.timeout,
            out=args.out,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
