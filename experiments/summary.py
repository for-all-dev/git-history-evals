"""
Cross-run aggregator for ExperimentResult JSONL files.

Reads one or more JSONL files produced by run_experiment.py (or the eventual
agent runner) and emits:
  * a JSON summary with per-(mode, deletion_size) aggregate metrics,
  * a `baseline_vs_agent` section with per-deletion-size deltas, and
  * an optional Markdown rendering with three tables (baseline, agent,
    baseline-vs-agent).

The drift columns (mean_vo_bytes_ratio, mean_compile_time_ratio,
mean_n_assumptions_diff, mean_proof_{chars,lines}_ratio,
mean_tactic_count_ratio) directly answer the "more/less <perf characteristic>
than the original human data" question. Pearson r per drift metric vs
deletion_size lives under `drift_faithfulness` as a per-mode honesty check
against memorization. See issues #13 and #25.

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


# Drift metrics we want to report on each per-(mode, deletion_size) row plus
# under drift_faithfulness. Each entry is (output_key, kind, llm_field,
# human_field). Kind is "ratio" (per-record llm/human then mean the ratios,
# skip when either side is missing or denominator is 0) or "diff" (per-record
# llm − human then mean the diffs, skip when either side is missing).
_DRIFT_FIELDS: list[tuple[str, str, str, str]] = [
    ("mean_vo_bytes_ratio",      "ratio", "vo_bytes",      "vo_bytes"),
    ("mean_compile_time_ratio",  "ratio", "compile_time_s", "compile_time_s"),
    ("mean_n_assumptions_diff",  "diff",  "n_assumptions", "n_assumptions"),
    ("mean_proof_chars_ratio",   "ratio", "proof_chars",   "proof_chars"),
    ("mean_proof_lines_ratio",   "ratio", "proof_lines",   "proof_lines"),
    ("mean_tactic_count_ratio",  "ratio", "tactic_count",  "tactic_count"),
]


def _drift_value(
    rs: list[dict[str, Any]],
    kind: str,
    llm_field: str,
    human_field: str,
) -> float | None:
    """Compute the per-group drift statistic across a list of records.

    For "ratio": skip a record if either side is missing/None or denominator
    is 0; otherwise append llm/human and return the mean of the ratios.
    For "diff": skip a record if either side is missing/None; otherwise append
    llm − human and return the mean.
    """
    vals: list[float] = []
    for r in rs:
        llm = _nested(r, "llm_metrics", llm_field)
        human = _nested(r, "human_metrics", human_field)
        if llm is None or human is None:
            continue
        llm_f = float(llm)
        human_f = float(human)
        if kind == "ratio":
            if human_f == 0:
                continue
            vals.append(llm_f / human_f)
        elif kind == "diff":
            vals.append(llm_f - human_f)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown drift kind: {kind!r}")
    return _mean(vals)


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

        # Compile / VO metrics come from llm_metrics; the human_ratio column
        # kept for backward compatibility with issue #13's output shape.
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

        row: dict[str, Any] = {
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
        }

        # Drift metrics: per-record llm-vs-human ratio/diff, then mean.
        for out_key, kind, llm_field, human_field in _DRIFT_FIELDS:
            row[out_key] = _drift_value(rs, kind, llm_field, human_field)

        # Agent-only column; None for baseline rows so downstream consumers
        # don't have to guard against KeyError.
        if mode == "agent":
            n_turns = [
                float(r["agent_n_turns"]) for r in rs
                if r.get("agent_n_turns") is not None
            ]
            row["mean_agent_n_turns"] = _mean(n_turns)
        else:
            row["mean_agent_n_turns"] = None

        group_summaries.append(row)

    # Per-mode Pearson r between deletion_size and pass_rate.
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in group_summaries:
        by_mode[g["mode"]].append(g)

    faithfulness: dict[str, float | None] = {}
    for mode, gs in by_mode.items():
        xs = [float(g["deletion_size"]) for g in gs if g["n_total"] > 0]
        ys = [float(g["pass_rate"]) for g in gs if g["n_total"] > 0]
        faithfulness[mode] = _pearson_r(xs, ys)

    # Per-metric Pearson r across deletion sizes, per mode. For each drift
    # metric (plus pass_rate and normalized_edit_distance, which are the two
    # headline non-drift signals) we emit {metric: {mode: r|None}}.
    drift_metric_keys: list[str] = [k for k, _, _, _ in _DRIFT_FIELDS]
    drift_metric_keys.extend(["pass_rate", "mean_normalized_edit_distance"])
    drift_faithfulness: dict[str, dict[str, float | None]] = {}
    for metric in drift_metric_keys:
        drift_faithfulness[metric] = {}
        for mode, gs in by_mode.items():
            pairs: list[tuple[float, float]] = [
                (float(g["deletion_size"]), float(g[metric]))
                for g in gs if g.get(metric) is not None and g["n_total"] > 0
            ]
            if len(pairs) >= 3:
                xs = [p[0] for p in pairs]
                ys = [p[1] for p in pairs]
                drift_faithfulness[metric][mode] = _pearson_r(xs, ys)
            else:
                drift_faithfulness[metric][mode] = None

    # Baseline-vs-agent per deletion_size: only emit rows where BOTH modes
    # have at least one result at that deletion_size.
    baseline_by_dsize = {g["deletion_size"]: g for g in by_mode.get("baseline", [])}
    agent_by_dsize    = {g["deletion_size"]: g for g in by_mode.get("agent", [])}
    shared_dsizes = sorted(set(baseline_by_dsize) & set(agent_by_dsize))
    baseline_vs_agent: list[dict[str, Any]] = []
    for dsize in shared_dsizes:
        b = baseline_by_dsize[dsize]
        a = agent_by_dsize[dsize]
        if b["n_total"] == 0 or a["n_total"] == 0:
            continue
        baseline_vs_agent.append({
            "deletion_size": dsize,
            "baseline_n_total": b["n_total"],
            "agent_n_total":    a["n_total"],
            "delta_pass_rate":  round(a["pass_rate"] - b["pass_rate"], 4),
            "delta_normalized_edit_distance": _delta(
                a.get("mean_normalized_edit_distance"),
                b.get("mean_normalized_edit_distance"),
            ),
            "delta_mean_agent_n_turns": a.get("mean_agent_n_turns"),
        })

    return {
        "n_total_runs": len(records),
        "groups": group_summaries,
        "faithfulness_by_mode": faithfulness,
        "drift_faithfulness": drift_faithfulness,
        "baseline_vs_agent": baseline_vs_agent,
    }


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 4)


# ── Rendering ─────────────────────────────────────────────────────────────────

# Columns shared by both baseline and agent tables.
_BASE_COLUMNS: list[tuple[str, str]] = [
    ("deletion_size",                 "deletion_size"),
    ("n_total",                       "n_total"),
    ("n_pass",                        "n_pass"),
    ("pass_rate",                     "pass_rate"),
    ("mean_inference_time_s",         "mean_inference_time_s"),
    ("mean_output_tokens",            "mean_output_tokens"),
    ("mean_normalized_edit_distance", "mean_norm_edit_dist"),
    ("mean_vo_bytes",                 "mean_vo_bytes"),
    ("mean_vo_bytes_human_ratio",     "mean_vo_ratio"),
    ("mean_compile_time_s",           "mean_compile_s"),
    ("mean_n_assumptions",            "mean_n_assumptions"),
    ("mean_vo_bytes_ratio",           "vo_bytes_ratio"),
    ("mean_compile_time_ratio",       "compile_time_ratio"),
    ("mean_n_assumptions_diff",       "n_assumptions_diff"),
    ("mean_proof_chars_ratio",        "proof_chars_ratio"),
    ("mean_proof_lines_ratio",        "proof_lines_ratio"),
    ("mean_tactic_count_ratio",       "tactic_count_ratio"),
]

# Backward-compat alias for tests / external callers that imported _COLUMNS.
_COLUMNS: list[tuple[str, str]] = _BASE_COLUMNS

# Agent table adds the turn-count column.
_AGENT_COLUMNS: list[tuple[str, str]] = _BASE_COLUMNS + [
    ("mean_agent_n_turns", "mean_agent_n_turns"),
]

_CMP_COLUMNS: list[tuple[str, str]] = [
    ("deletion_size",                    "deletion_size"),
    ("baseline_n_total",                 "baseline_n"),
    ("agent_n_total",                    "agent_n"),
    ("delta_pass_rate",                  "Δpass_rate"),
    ("delta_normalized_edit_distance",   "Δnorm_edit_dist"),
    ("delta_mean_agent_n_turns",         "Δmean_agent_n_turns"),
]


def _fmt_cell(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1000 else f"{v:.1f}"
    return str(v)


def _render_mode_table(
    out: list[str],
    mode: str,
    rows: list[dict[str, Any]],
    columns: list[tuple[str, str]],
    faithfulness_r: float | None,
) -> None:
    out.append(f"## mode = `{mode}`")
    out.append("")
    headers = [label for _, label in columns]
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for g in sorted(rows, key=lambda x: x["deletion_size"]):
        row = [_fmt_cell(g.get(key)) for key, _ in columns]
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    out.append(
        "Faithfulness correlation (Pearson r, deletion_size vs pass_rate): "
        f"**{_fmt_cell(faithfulness_r)}**"
    )
    out.append("")


def render_markdown(summary: dict[str, Any]) -> str:
    """Render three Markdown tables: baseline, agent, baseline-vs-agent.

    Also includes a drift-faithfulness block listing Pearson r per metric per
    mode.
    """
    groups_by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in summary["groups"]:
        groups_by_mode[g["mode"]].append(g)

    out: list[str] = []
    out.append("# Experiment summary")
    out.append("")
    out.append(f"Total runs: **{summary['n_total_runs']}**")
    out.append("")

    # Baseline table (if present).
    if "baseline" in groups_by_mode:
        _render_mode_table(
            out,
            "baseline",
            groups_by_mode["baseline"],
            _BASE_COLUMNS,
            summary["faithfulness_by_mode"].get("baseline"),
        )

    # Agent table (if present). Rendered even when baseline is absent.
    if "agent" in groups_by_mode:
        _render_mode_table(
            out,
            "agent",
            groups_by_mode["agent"],
            _AGENT_COLUMNS,
            summary["faithfulness_by_mode"].get("agent"),
        )

    # Any other modes (future-proofing) render with base columns.
    for mode in sorted(m for m in groups_by_mode if m not in ("baseline", "agent")):
        _render_mode_table(
            out,
            mode,
            groups_by_mode[mode],
            _BASE_COLUMNS,
            summary["faithfulness_by_mode"].get(mode),
        )

    # Baseline-vs-agent comparison table.
    out.append("## baseline vs agent")
    out.append("")
    cmp_rows = summary.get("baseline_vs_agent") or []
    if cmp_rows:
        headers = [label for _, label in _CMP_COLUMNS]
        out.append("| " + " | ".join(headers) + " |")
        out.append("|" + "|".join(["---"] * len(headers)) + "|")
        for row in sorted(cmp_rows, key=lambda x: x["deletion_size"]):
            cells = [_fmt_cell(row.get(key)) for key, _ in _CMP_COLUMNS]
            out.append("| " + " | ".join(cells) + " |")
    else:
        out.append("_No shared deletion sizes between baseline and agent runs._")
    out.append("")

    # Per-metric drift faithfulness (Pearson r vs deletion_size, per mode).
    drift_faith = summary.get("drift_faithfulness") or {}
    if drift_faith:
        out.append("## drift faithfulness (Pearson r vs deletion_size)")
        out.append("")
        modes = sorted({m for per_mode in drift_faith.values() for m in per_mode})
        headers = ["metric"] + modes
        out.append("| " + " | ".join(headers) + " |")
        out.append("|" + "|".join(["---"] * len(headers)) + "|")
        for metric in sorted(drift_faith):
            cells = [metric] + [
                _fmt_cell(drift_faith[metric].get(mode)) for mode in modes
            ]
            out.append("| " + " | ".join(cells) + " |")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="summary.py",
        description=(
            "Aggregate ExperimentResult JSONL files into a per-(mode, deletion_size) "
            "summary with drift metrics, baseline-vs-agent comparison, and Pearson-r "
            "faithfulness scores."
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
