"""
Cross-run aggregator for ExperimentResult JSONL files.

Reads one or more JSONL files produced by run_experiment.py (or the eventual
agent runner) and emits:
  * a JSON summary with per-(mode, deletion_size) aggregate metrics, and
  * an optional Markdown table (one table per mode).

This is the first cut — see issue #13. Drift-and-cross-mode comparisons live
in the follow-up (#25). Depends only on stdlib + fields already defined in
experiments/metrics.py (schema extended in #4).

Usage:
    python3 summary.py --inputs <glob> [--out summary.json] [--markdown summary.md]
"""

from __future__ import annotations

import argparse
import glob as globlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


# ── Loading ───────────────────────────────────────────────────────────────────

def _iter_records(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    """Yield one decoded dict per non-empty JSON line across all input files."""
    for p in paths:
        with p.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    print(
                        f"warning: skipping malformed line {p}:{lineno}: {exc}",
                        file=sys.stderr,
                    )


def _expand_inputs(glob_pattern: str) -> list[Path]:
    """Expand a shell-style glob into a sorted list of existing files."""
    matches = sorted(
        Path(m) for m in globlib.glob(glob_pattern, recursive=True)
        if Path(m).is_file()
    )
    return matches


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 4) if xs else None


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r between two equal-length sequences.

    Formula copied verbatim from run_experiment.py (lines 466–470). Returns
    None when there are fewer than 3 points or when the denominator is zero.
    """
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    xm = sum(xs) / len(xs)
    ym = sum(ys) / len(ys)
    num   = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    denom = (sum((x - xm) ** 2 for x in xs) * sum((y - ym) ** 2 for y in ys)) ** 0.5
    return round(num / denom, 4) if denom > 0 else None


def _nested(rec: dict[str, Any], *path: str) -> Any:
    """Safe nested-dict lookup; returns None if any key is missing or None."""
    cur: Any = rec
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


# ── Aggregation ───────────────────────────────────────────────────────────────

def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Group records by (mode, deletion_size) and compute summary metrics."""
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        mode = rec.get("mode", "baseline")
        dsize = rec.get("deletion_size")
        if dsize is None:
            continue
        groups[(mode, int(dsize))].append(rec)

    group_summaries: list[dict[str, Any]] = []
    for (mode, dsize), rs in sorted(groups.items()):
        n_total = len(rs)
        n_pass = sum(1 for r in rs if r.get("verdict") == "PASS")
        pass_rate = round(n_pass / n_total, 4) if n_total else 0.0

        inference_times = [
            float(r["inference_time_s"]) for r in rs if r.get("inference_time_s") is not None
        ]
        output_tokens = [
            float(r["output_tokens"]) for r in rs if r.get("output_tokens") is not None
        ]
        n_eds = [
            float(r["normalized_edit_distance"])
            for r in rs if r.get("normalized_edit_distance") is not None
        ]

        # Compile / VO metrics come from llm_metrics; human ratio compares LLM
        # vs ground-truth human vo_bytes to flag suspiciously-small artifacts.
        vo_bytes_vals: list[float] = []
        vo_ratio_vals: list[float] = []
        compile_time_vals: list[float] = []
        n_assumptions_vals: list[float] = []
        for r in rs:
            vo = _nested(r, "llm_metrics", "vo_bytes")
            if vo is not None:
                vo_bytes_vals.append(float(vo))
                human_vo = _nested(r, "human_metrics", "vo_bytes")
                if human_vo is not None and float(human_vo) > 0:
                    vo_ratio_vals.append(float(vo) / float(human_vo))
            ctime = _nested(r, "llm_metrics", "compile_time_s")
            if ctime is not None:
                compile_time_vals.append(float(ctime))
            nassum = _nested(r, "llm_metrics", "n_assumptions")
            if nassum is not None:
                n_assumptions_vals.append(float(nassum))

        group_summaries.append({
            "mode": mode,
            "deletion_size": dsize,
            "n_total": n_total,
            "n_pass": n_pass,
            "pass_rate": pass_rate,
            "mean_inference_time_s": _mean(inference_times),
            "mean_output_tokens": _mean(output_tokens),
            "mean_normalized_edit_distance": _mean(n_eds),
            "mean_vo_bytes": _mean(vo_bytes_vals),
            "mean_vo_bytes_human_ratio": _mean(vo_ratio_vals),
            "mean_compile_time_s": _mean(compile_time_vals),
            "mean_n_assumptions": _mean(n_assumptions_vals),
        })

    # Per-mode Pearson r between deletion_size and pass_rate.
    faithfulness: dict[str, float | None] = {}
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in group_summaries:
        by_mode[g["mode"]].append(g)
    for mode, gs in by_mode.items():
        xs = [float(g["deletion_size"]) for g in gs if g["n_total"] > 0]
        ys = [float(g["pass_rate"]) for g in gs if g["n_total"] > 0]
        faithfulness[mode] = _pearson_r(xs, ys)

    return {
        "n_total_runs": len(records),
        "groups": group_summaries,
        "faithfulness_by_mode": faithfulness,
    }


# ── Rendering ─────────────────────────────────────────────────────────────────

_COLUMNS: list[tuple[str, str]] = [
    ("deletion_size",                "deletion_size"),
    ("n_total",                      "n_total"),
    ("n_pass",                       "n_pass"),
    ("pass_rate",                    "pass_rate"),
    ("mean_inference_time_s",        "mean_inference_time_s"),
    ("mean_output_tokens",           "mean_output_tokens"),
    ("mean_normalized_edit_distance", "mean_norm_edit_dist"),
    ("mean_vo_bytes",                "mean_vo_bytes"),
    ("mean_vo_bytes_human_ratio",    "mean_vo_ratio"),
    ("mean_compile_time_s",          "mean_compile_s"),
    ("mean_n_assumptions",           "mean_n_assumptions"),
]


def _fmt_cell(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1000 else f"{v:.1f}"
    return str(v)


def render_markdown(summary: dict[str, Any]) -> str:
    """Render one Markdown table per mode plus a Pearson r block."""
    groups_by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in summary["groups"]:
        groups_by_mode[g["mode"]].append(g)

    out: list[str] = []
    out.append("# Experiment summary")
    out.append("")
    out.append(f"Total runs: **{summary['n_total_runs']}**")
    out.append("")

    for mode in sorted(groups_by_mode):
        out.append(f"## mode = `{mode}`")
        out.append("")
        headers = [label for _, label in _COLUMNS]
        out.append("| " + " | ".join(headers) + " |")
        out.append("|" + "|".join(["---"] * len(headers)) + "|")
        for g in sorted(groups_by_mode[mode], key=lambda x: x["deletion_size"]):
            row = [_fmt_cell(g[key]) for key, _ in _COLUMNS]
            out.append("| " + " | ".join(row) + " |")
        r = summary["faithfulness_by_mode"].get(mode)
        out.append("")
        out.append(f"Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **{_fmt_cell(r)}**")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="summary.py",
        description=(
            "Aggregate ExperimentResult JSONL files into a per-(mode, deletion_size) "
            "summary with Pearson-r faithfulness score."
        ),
    )
    p.add_argument(
        "--inputs", required=True,
        help="Glob pattern matching JSONL result files (e.g. 'results/**/*.jsonl').",
    )
    p.add_argument(
        "--out", default=None,
        help="Path to write the JSON summary (stdout if omitted).",
    )
    p.add_argument(
        "--markdown", default=None,
        help="Optional path to write a Markdown summary table.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    paths = _expand_inputs(args.inputs)
    if not paths:
        print(
            f"error: no input files matched glob {args.inputs!r}",
            file=sys.stderr,
        )
        return 1

    records = list(_iter_records(paths))
    summary = aggregate(records)

    rendered_json = json.dumps(summary, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(rendered_json + "\n", encoding="utf-8")
    else:
        print(rendered_json)

    if args.markdown:
        Path(args.markdown).write_text(render_markdown(summary), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
