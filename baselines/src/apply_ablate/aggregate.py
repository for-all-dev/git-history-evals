"""Cross-run aggregator: micro + macro (per-repo mean) PASS over a set of
`ablate-baseline` result JSONLs, keyed by `(model, mode, max_turns)`.

`docs/contamination.agents.md` requires macro-averaging (mean of per-repo PASS rate)
for any cross-group comparison — evm-asm alone is 7,323/21,692 rows (34%) of the leaf
corpus, so a micro (pooled) average is mostly a statement about that one repo. This
module reports both, plus the full outcome breakdown and bootstrap CIs.

A `SolveResult` row (`apply_ablate.solve.SolveResult`) carries no `model`/`mode`/`repo`
field — `eval_sample.sh` writes one `res_<repo>.jsonl` per repo per (model, mode,
max_turns) run, so that association lives in the filesystem, not the JSONL. The input
to this aggregator is therefore a *manifest*: a JSON list of `{path, model, mode,
max_turns, repo}` entries (repo/max_turns may be omitted and are inferred — repo from
the filename stem, max_turns from the majority value of each row's `max_turns` field).

    ablate-aggregate MANIFEST.json [--out-json F] [--out-md F] [--n-boot 2000] [--seed N]
"""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel, ConfigDict

from apply_ablate.difficulty.label import outcome_of

app = typer.Typer(add_completion=False, help=__doc__)

# display order for the outcome taxonomy (baselines/src/apply_ablate/baseline.py:138-158)
OUTCOME_ORDER: tuple[str, ...] = (
    "pass",
    "dry_run",
    "trivial",
    "malformed",
    "context_exceeded",
    "tampered",
    "gave_up",
    "turn_limit",
    "error",
    "fail",
)
# excluded from the scorable denominator: the challenge is broken (malformed), empty
# (trivial), or the model never saw it (context_exceeded) — none of these are a wrong
# answer, so scoring them 0 would blame the model for a harness/dataset artifact.
NON_SCORABLE: frozenset[str] = frozenset({"malformed", "trivial", "context_exceeded"})


class ManifestEntry(BaseModel):
    """One results file plus the (model, mode, max_turns, repo) it belongs to.

    `repo` and `max_turns` are optional: `repo` defaults to the filename stem (stripping
    a leading `res_` prefix, matching `pipeline/eval_sample.sh`'s naming), and
    `max_turns` defaults to the majority `max_turns` value recorded on the file's own
    rows (present since #124; falls back to `None` -> "unknown" on legacy files).
    """

    model_config = ConfigDict(extra="ignore")

    path: str
    model: str
    mode: str
    max_turns: int | None = None
    repo: str | None = None


def load_manifest(path: Path) -> list[ManifestEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: manifest must be a JSON list of entries")
    return [ManifestEntry.model_validate(e) for e in raw]


def load_results(path: Path) -> list[dict[str, Any]]:
    """Load non-blank JSONL rows as dicts (blank padding lines skipped)."""
    rows: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            rows.append(json.loads(ln))
    return rows


def _infer_repo(entry_path: str) -> str:
    stem = Path(entry_path).stem
    return stem[len("res_") :] if stem.startswith("res_") else stem


def _infer_max_turns(rows: list[dict[str, Any]]) -> int | None:
    values = [r["max_turns"] for r in rows if r.get("max_turns") is not None]
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


class RepoRows:
    """One repo's classified outcomes within a single (model, mode, max_turns) group."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.outcomes: list[str] = []

    def add(self, rows: list[dict[str, Any]]) -> None:
        self.outcomes.extend(outcome_of(r) for r in rows)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def scorable_flags(self) -> list[bool]:
        """One bool per scorable row: True if it passed."""
        return [o == "pass" for o in self.outcomes if o not in NON_SCORABLE]

    @property
    def scorable(self) -> int:
        return len(self.scorable_flags)

    @property
    def passed(self) -> int:
        return sum(self.scorable_flags)

    def counts(self) -> dict[str, int]:
        c = Counter(self.outcomes)
        return {name: c.get(name, 0) for name in OUTCOME_ORDER}


def _mean(xs: list[bool]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _percentile_ci(
    samples: list[float], alpha: float
) -> tuple[float | None, float | None]:
    if not samples:
        return (None, None)
    s = sorted(samples)
    lo_idx = int((alpha / 2) * len(s))
    hi_idx = min(len(s) - 1, int((1 - alpha / 2) * len(s)))
    return (s[lo_idx], s[hi_idx])


def bootstrap_micro_ci(
    per_repo: dict[str, RepoRows],
    *,
    n_boot: int,
    alpha: float,
    seed: int | None,
) -> tuple[float | None, float | None]:
    """Resample all scorable rows pooled across repos, recompute pass/scorable."""
    pooled: list[bool] = []
    for r in per_repo.values():
        pooled.extend(r.scorable_flags)
    if not pooled:
        return (None, None)
    rng = random.Random(seed)
    n = len(pooled)
    samples = [_mean(rng.choices(pooled, k=n)) for _ in range(n_boot)]
    return _percentile_ci(samples, alpha)


def bootstrap_macro_ci(
    per_repo: dict[str, RepoRows],
    *,
    n_boot: int,
    alpha: float,
    seed: int | None,
) -> tuple[float | None, float | None]:
    """Stratified (per-repo) bootstrap: resample each repo's scorable rows within that
    repo, recompute its pass rate, then average across repos with >=1 scorable row —
    repeated `n_boot` times. This preserves the repo structure the macro average
    depends on (unlike pooling, which would just reproduce the micro CI)."""
    repo_flags = [r.scorable_flags for r in per_repo.values() if r.scorable_flags]
    if not repo_flags:
        return (None, None)
    rng = random.Random(seed)
    samples = []
    for _ in range(n_boot):
        repo_means = [_mean(rng.choices(flags, k=len(flags))) for flags in repo_flags]
        samples.append(statistics.mean(repo_means))
    return _percentile_ci(samples, alpha)


class GroupResult(BaseModel):
    model: str
    mode: str
    max_turns: int | None
    total: int
    outcomes: dict[str, int]
    scorable: int
    micro_pass: int
    micro_rate: float
    micro_ci_lo: float | None
    micro_ci_hi: float | None
    macro_n_repos: int
    macro_rate: float | None
    macro_ci_lo: float | None
    macro_ci_hi: float | None
    per_repo: dict[str, dict[str, int | float]]


def aggregate(
    entries: list[ManifestEntry],
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int | None = None,
    base_dir: Path | None = None,
) -> list[GroupResult]:
    """Group manifest entries by (model, mode, max_turns) — inferring max_turns from
    each file's rows when the manifest omits it — and compute micro + macro stats."""
    # first pass: load rows + resolve each entry's effective max_turns
    loaded: list[tuple[ManifestEntry, list[dict[str, Any]], int | None]] = []
    for e in entries:
        p = Path(e.path) if base_dir is None else base_dir / e.path
        rows = load_results(p)
        turns = e.max_turns if e.max_turns is not None else _infer_max_turns(rows)
        loaded.append((e, rows, turns))

    groups: dict[tuple[str, str, int | None], dict[str, RepoRows]] = defaultdict(dict)
    for e, rows, turns in loaded:
        repo = e.repo or _infer_repo(e.path)
        key = (e.model, e.mode, turns)
        per_repo = groups[key]
        if repo not in per_repo:
            per_repo[repo] = RepoRows(repo)
        per_repo[repo].add(rows)

    results: list[GroupResult] = []
    for (model, mode, turns), per_repo in sorted(
        groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or -1)
    ):
        total = sum(r.total for r in per_repo.values())
        outcomes: dict[str, int] = dict.fromkeys(OUTCOME_ORDER, 0)
        for r in per_repo.values():
            for name, n in r.counts().items():
                outcomes[name] += n
        scorable = sum(r.scorable for r in per_repo.values())
        micro_pass = sum(r.passed for r in per_repo.values())
        micro_rate = micro_pass / scorable if scorable else 0.0
        micro_lo, micro_hi = bootstrap_micro_ci(
            per_repo, n_boot=n_boot, alpha=alpha, seed=seed
        )
        repo_rates = [
            (repo, r.passed / r.scorable) for repo, r in per_repo.items() if r.scorable
        ]
        macro_rate = (
            statistics.mean(rate for _, rate in repo_rates) if repo_rates else None
        )
        macro_lo, macro_hi = bootstrap_macro_ci(
            per_repo, n_boot=n_boot, alpha=alpha, seed=seed
        )
        per_repo_out = {
            repo: {
                "total": r.total,
                "scorable": r.scorable,
                "pass": r.passed,
                "rate": (r.passed / r.scorable) if r.scorable else 0.0,
            }
            for repo, r in sorted(per_repo.items())
        }
        results.append(
            GroupResult(
                model=model,
                mode=mode,
                max_turns=turns,
                total=total,
                outcomes=outcomes,
                scorable=scorable,
                micro_pass=micro_pass,
                micro_rate=micro_rate,
                micro_ci_lo=micro_lo,
                micro_ci_hi=micro_hi,
                macro_n_repos=len(repo_rates),
                macro_rate=macro_rate,
                macro_ci_lo=macro_lo,
                macro_ci_hi=macro_hi,
                per_repo=per_repo_out,
            )
        )
    return results


def _pct(x: float | None) -> str:
    return f"{100 * x:.1f}%" if x is not None else "n/a"


def to_markdown(results: list[GroupResult]) -> str:
    lines = [
        "| model | mode | max_turns | n | scorable | micro PASS | micro CI | macro PASS | macro CI | repos |",
        "|---|---|--:|--:|--:|--:|---|--:|---|--:|",
    ]
    for g in results:
        micro_ci = f"[{_pct(g.micro_ci_lo)}, {_pct(g.micro_ci_hi)}]"
        macro_ci = (
            f"[{_pct(g.macro_ci_lo)}, {_pct(g.macro_ci_hi)}]"
            if g.macro_rate is not None
            else "n/a"
        )
        lines.append(
            f"| {g.model} | {g.mode} | {g.max_turns if g.max_turns is not None else 'n/a'} "
            f"| {g.total} | {g.scorable} | {_pct(g.micro_rate)} ({g.micro_pass}/{g.scorable}) "
            f"| {micro_ci} | {_pct(g.macro_rate)} | {macro_ci} | {g.macro_n_repos} |"
        )
    lines.append("")
    lines.append("Outcome breakdown:")
    lines.append("")
    header = "| model | mode | max_turns | " + " | ".join(OUTCOME_ORDER) + " |"
    sep = "|---|---|--:|" + "--:|" * len(OUTCOME_ORDER)
    lines.append(header)
    lines.append(sep)
    for g in results:
        counts = " | ".join(str(g.outcomes[name]) for name in OUTCOME_ORDER)
        lines.append(
            f"| {g.model} | {g.mode} | {g.max_turns if g.max_turns is not None else 'n/a'} | {counts} |"
        )
    return "\n".join(lines) + "\n"


def to_json(results: list[GroupResult]) -> str:
    return json.dumps([g.model_dump() for g in results], indent=2) + "\n"


@app.command()
def run(
    manifest: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="JSON list of {path, model, mode, max_turns?, repo?} entries.",
        ),
    ],
    out_json: Annotated[
        Path | None, typer.Option("--out-json", help="machine-readable aggregate")
    ] = None,
    out_md: Annotated[
        Path | None, typer.Option("--out-md", help="markdown table for the paper")
    ] = None,
    n_boot: Annotated[int, typer.Option("--n-boot", help="bootstrap resamples")] = 2000,
    alpha: Annotated[
        float, typer.Option("--alpha", help="CI significance level (0.05 = 95% CI)")
    ] = 0.05,
    seed: Annotated[
        int | None, typer.Option("--seed", help="bootstrap RNG seed (reproducible CIs)")
    ] = None,
) -> None:
    """Aggregate a set of `ablate-baseline` result JSONLs into micro + macro PASS."""
    entries = load_manifest(manifest)
    results = aggregate(
        entries, n_boot=n_boot, alpha=alpha, seed=seed, base_dir=manifest.parent
    )
    md = to_markdown(results)
    typer.echo(md)
    if out_json:
        out_json.write_text(to_json(results), encoding="utf-8")
        typer.echo(f"wrote {out_json}")
    if out_md:
        out_md.write_text(md, encoding="utf-8")
        typer.echo(f"wrote {out_md}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()


__all__ = [
    "GroupResult",
    "ManifestEntry",
    "aggregate",
    "load_manifest",
    "to_json",
    "to_markdown",
]
