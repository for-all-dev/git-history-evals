"""Typer CLI wrapper around ``agent/runner.py``.

This is the user-facing counterpart of ``run_experiment.py``. It mirrors that
file's structure — same slot discovery, same results-dir resolution, same
summary print-out — but the per-slot work is delegated to ``agent.runner.run_one``
rather than the single-shot Anthropic call.

Two modes of slot discovery:

1. If ``RUN_CONFIG_JSON`` is set (the per-container orchestrator case), the
   JSON is parsed and its ``slots`` list is used verbatim. This is the path
   taken inside the per-SHA containers spun up by ``orchestrate/lib.sh``.
2. Otherwise, slots are discovered by walking ``admitted-proofs/`` (condition A)
   and ``experiments3/`` (condition B) under the experiments directory, the
   same way ``run_experiment.py`` does.

In both cases results are appended one-per-line to ``<results_dir>/agent.jsonl``
with ``mode="agent"``, matching the baseline's schema.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv

# Walk up from the script's directory to find .env, same as run_experiment.py.
_script_dir = Path(__file__).resolve().parent
for _p in (_script_dir, *_script_dir.parents):
    if (_p / ".env").exists():
        load_dotenv(_p / ".env")
        break

sys.path.insert(0, str(_script_dir))

from agent.runner import run_one
from metrics import (
    DeletionConditionSummary,
    ExperimentResult,
    ExperimentSummary,
)


# ── Config ────────────────────────────────────────────────────────────────────

BASE = Path(__file__).parent
EXP_A = BASE / "admitted-proofs"
EXP_B = BASE / "experiments3"
FIAT_CRYPTO_DIR = Path(os.environ.get("FIAT_CRYPTO_DIR", "/data/fiat-crypto"))


def _resolve_results_dir(explicit: str | None) -> Path:
    """Return the per-run results directory.

    If ``explicit`` is set, it is honoured verbatim. Otherwise, inside a
    container (detected via ``/.dockerenv`` or ``RUNNING_IN_DOCKER``) this
    resolves to ``/results/${RUN_ID}``; out of container it lands under
    ``experiments/results/${RUN_ID}``. ``RUN_ID`` defaults to a timestamp so
    ad-hoc local runs still get their own directory.
    """
    if explicit:
        return Path(explicit)
    run_id = os.environ.get("RUN_ID") or time.strftime("%Y%m%d-%H%M%S")
    in_container = Path("/.dockerenv").exists() or bool(os.environ.get("RUNNING_IN_DOCKER"))
    root = Path("/results") if in_container else (BASE / "results")
    return root / run_id


# ── Slot discovery ────────────────────────────────────────────────────────────

class _Slot:
    """Lightweight record describing one (slot × deletion_size) unit of work."""

    __slots__ = ("slot_dir", "condition", "deletion_size", "challenge_file")

    def __init__(
        self,
        slot_dir: Path,
        condition: str,
        deletion_size: int,
        challenge_file: str,
    ) -> None:
        self.slot_dir = slot_dir
        self.condition = condition
        self.deletion_size = deletion_size
        self.challenge_file = challenge_file


def _slots_from_run_config(raw_config: str) -> tuple[list[_Slot], str | None, int | None, Path | None]:
    """Parse ``RUN_CONFIG_JSON`` into ``(slots, model, max_turns, repo_dir)``.

    The shape matches ``agent/runner.py:main``'s expectations. Any of
    ``model``, ``max_turns``, ``repo_dir`` may be absent, in which case
    ``None`` is returned and the CLI-level defaults take over.
    """
    config = json.loads(raw_config)
    raw_slots: list[dict[str, Any]] = config.get("slots") or []
    slots: list[_Slot] = []
    for entry in raw_slots:
        if "slot_dir" in entry:
            slot_dir = Path(entry["slot_dir"])
        else:
            condition = entry["condition"]
            slot_name = entry["slot"]
            sub = "admitted-proofs" if condition == "A" else "experiments3"
            slot_dir = BASE / sub / slot_name
        condition = entry["condition"]
        deletion_size = int(entry.get("deletion_size", -1))
        challenge_file = entry.get("challenge_file") or (
            "challenge.v" if deletion_size == -1 else f"challenge{deletion_size}.v"
        )
        slots.append(_Slot(slot_dir, condition, deletion_size, challenge_file))
    cfg_model = config.get("model")
    cfg_max_turns = config.get("max_turns")
    cfg_repo_dir = config.get("repo_dir")
    return (
        slots,
        cfg_model if isinstance(cfg_model, str) else None,
        int(cfg_max_turns) if cfg_max_turns is not None else None,
        Path(cfg_repo_dir) if cfg_repo_dir else None,
    )


def _discover_slots(
    max_challenges: int | None,
    deletion_sizes: list[int],
    skip_a: bool,
    skip_b: bool,
    only: str | None,
) -> list[_Slot]:
    """Walk the on-disk ``admitted-proofs/`` and ``experiments3/`` dirs.

    Applies the ``--max-challenges`` cap per (condition, deletion_size) slice
    and the ``--only`` substring filter, mirroring ``run_experiment.run_condition``.
    """
    slots: list[_Slot] = []

    if not skip_b and EXP_B.exists():
        for n in deletion_sizes:
            count = 0
            for slot in sorted(EXP_B.iterdir()):
                if not slot.is_dir():
                    continue
                if only and only not in slot.name:
                    continue
                chal = slot / f"challenge{n}.v"
                meta = slot / "meta.json"
                if not chal.exists() or not meta.exists():
                    continue
                if max_challenges is not None and count >= max_challenges:
                    break
                slots.append(_Slot(slot, "B", n, f"challenge{n}.v"))
                count += 1

    if not skip_a and EXP_A.exists():
        count = 0
        for slot in sorted(EXP_A.iterdir()):
            if not slot.is_dir():
                continue
            if only and only not in slot.name:
                continue
            chal = slot / "challenge.v"
            meta = slot / "meta.json"
            if not chal.exists() or not meta.exists():
                continue
            if max_challenges is not None and count >= max_challenges:
                break
            slots.append(_Slot(slot, "A", -1, "challenge.v"))
            count += 1

    return slots


# ── Summary ───────────────────────────────────────────────────────────────────

def _summarise(results: list[ExperimentResult], model: str) -> ExperimentSummary:
    """Aggregate per-row results into an ``ExperimentSummary``.

    Byte-compatible with ``run_experiment._summarise`` but emits the Pearson r
    under the ``"agent"`` key in ``faithfulness_by_mode`` (baseline's runner
    emits under ``"baseline"``).
    """
    by_cond: dict[tuple[str, int], list[ExperimentResult]] = defaultdict(list)
    for r in results:
        by_cond[(r.condition, r.deletion_size)].append(r)

    conditions: list[DeletionConditionSummary] = []
    for (cond, dsize), rs in sorted(by_cond.items()):
        n_pass = sum(1 for r in rs if r.verdict == "PASS")
        n_fail = sum(1 for r in rs if r.verdict == "FAIL")
        n_err = sum(1 for r in rs if r.verdict in ("TIMEOUT", "ERROR"))
        n_total = len(rs)
        pass_rate = round(n_pass / n_total, 4) if n_total else 0.0
        teds = [r.normalized_edit_distance for r in rs if r.normalized_edit_distance is not None]
        turns = [r.agent_n_turns for r in rs if r.agent_n_turns is not None]
        conditions.append(DeletionConditionSummary(
            deletion_size=dsize,
            condition=cond,  # type: ignore[arg-type]
            n_challenges=n_total,
            n_pass=n_pass,
            n_fail=n_fail,
            n_error_or_timeout=n_err,
            pass_rate=pass_rate,
            mean_inference_time_s=round(statistics.mean(r.inference_time_s for r in rs), 2),
            mean_output_tokens=round(statistics.mean(r.output_tokens for r in rs), 1),
            mean_tactic_edit_distance=round(statistics.mean(
                r.tactic_edit_distance for r in rs if r.tactic_edit_distance is not None
            ), 2) if any(r.tactic_edit_distance is not None for r in rs) else None,
            mean_normalized_edit_distance=round(statistics.mean(teds), 4) if teds else None,
            mean_agent_n_turns=round(statistics.mean(turns), 2) if turns else None,
        ))

    faith_by_mode: dict[str, float | None] = {}
    b_conds = [c for c in conditions if c.condition == "B" and c.n_challenges > 0]
    faith_r: float | None = None
    if len(b_conds) >= 3:
        xs = [float(c.deletion_size) for c in b_conds]
        ys = [c.pass_rate for c in b_conds]
        xm = sum(xs) / len(xs)
        ym = sum(ys) / len(ys)
        num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
        denom = (sum((x - xm) ** 2 for x in xs) * sum((y - ym) ** 2 for y in ys)) ** 0.5
        faith_r = round(num / denom, 4) if denom > 0 else None
    faith_by_mode["agent"] = faith_r

    return ExperimentSummary(
        model=model,
        date=time.strftime("%Y-%m-%d %H:%M:%S"),
        n_total_runs=len(results),
        conditions=conditions,
        faithfulness_by_mode=faith_by_mode,
    )


def _log_error(results_dir: Path, slot: _Slot, exc: BaseException) -> None:
    """Append a per-slot traceback to ``<results_dir>/run.log``."""
    logfile = results_dir / "run.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open("a") as fh:
        fh.write(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] slot={slot.slot_dir} "
            f"condition={slot.condition} deletion_size={slot.deletion_size} failed\n"
        )
        fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        fh.write("\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def _main(
    max_challenges: int = typer.Option(
        3, "--max-challenges", "-n",
        help="Max challenges per condition/deletion-size (0 = all).",
    ),
    deletion_sizes: str = typer.Option(
        "3,5,7,10,15",
        "--deletion-sizes",
        help="Comma-separated Condition B deletion sizes to run.",
    ),
    skip_a: bool = typer.Option(False, "--skip-a", help="Skip full-proof condition."),
    skip_b: bool = typer.Option(False, "--skip-b", help="Skip partial-deletion conditions."),
    only: str = typer.Option("", "--only", help="Only run challenges whose dir name contains this substring."),
    model: str = typer.Option(
        "anthropic:claude-sonnet-4-6",
        "--model",
        help="Model identifier passed to pydantic-ai (e.g. 'anthropic:claude-sonnet-4-6').",
    ),
    max_turns: int = typer.Option(
        20, "--max-turns",
        help="Maximum pydantic-ai request budget per slot.",
    ),
    results_dir: str = typer.Option(
        "", "--results-dir",
        help="Directory for agent.jsonl output; defaults to /results/${RUN_ID} in-container "
             "or experiments/results/${RUN_ID} otherwise.",
    ),
) -> None:
    limit = max_challenges if max_challenges > 0 else None
    only_filter = only or None
    b_sizes = [int(s.strip()) for s in deletion_sizes.split(",") if s.strip()]

    out_dir = _resolve_results_dir(results_dir or None)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent.jsonl"

    # Slot discovery: RUN_CONFIG_JSON short-circuits filesystem walking.
    raw_config = os.environ.get("RUN_CONFIG_JSON")
    cfg_repo_dir: Path | None = None
    if raw_config:
        slots, cfg_model, cfg_max_turns, cfg_repo_dir = _slots_from_run_config(raw_config)
        if cfg_model:
            model = cfg_model
        if cfg_max_turns is not None:
            max_turns = cfg_max_turns
    else:
        slots = _discover_slots(limit, b_sizes, skip_a, skip_b, only_filter)

    repo_dir = cfg_repo_dir or FIAT_CRYPTO_DIR

    print("=" * 64)
    print("AGENT PROOF EXPERIMENT — pydantic-ai ReAct loop")
    print(f"Model         : {model}")
    print(f"Max turns     : {max_turns}")
    print(f"Max challenges: {limit or 'all'}")
    print(f"B sizes       : {b_sizes}")
    print(f"Results       : {out_path}")
    print(f"Repo dir      : {repo_dir}")
    print(f"N slots       : {len(slots)}")
    print(f"Source        : {'RUN_CONFIG_JSON' if raw_config else 'filesystem discovery'}")
    print(f"Date          : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64, flush=True)

    all_results: list[ExperimentResult] = []

    with out_path.open("a") as fh:
        for slot in slots:
            print(
                f"\n[{slot.condition}{'' if slot.deletion_size < 0 else slot.deletion_size}] "
                f"{slot.slot_dir.name} (deletion={slot.deletion_size})",
                flush=True,
            )
            try:
                result = run_one(
                    slot_dir=slot.slot_dir,
                    condition=slot.condition,
                    deletion_size=slot.deletion_size,
                    challenge_file=slot.challenge_file,
                    repo_dir=repo_dir,
                    model=model,
                    max_turns=max_turns,
                )
            except Exception as e:  # noqa: BLE001 - per-slot isolation
                _log_error(out_dir, slot, e)
                print(f"  ERROR: {type(e).__name__}: {e}", flush=True)
                continue
            fh.write(result.model_dump_json() + "\n")
            fh.flush()
            all_results.append(result)
            print(
                f"  verdict={result.verdict} turns={result.agent_n_turns} "
                f"time={result.inference_time_s}s tokens={result.output_tokens}",
                flush=True,
            )

    # Summary
    summary = _summarise(all_results, model)

    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    for c in summary.conditions:
        print(
            f"Condition {c.condition} (deletion={c.deletion_size}): "
            f"{c.n_pass}/{c.n_challenges} PASS  "
            f"[pass_rate={c.pass_rate:.0%}  "
            f"mean_time={c.mean_inference_time_s}s  "
            f"mean_tokens={c.mean_output_tokens:.0f}  "
            f"mean_turns={c.mean_agent_n_turns}  "
            f"mean_ned={c.mean_normalized_edit_distance}]"
        )

    agent_r = summary.faithfulness_by_mode.get("agent")
    if agent_r is not None:
        print(
            f"\nFaithfulness (Pearson r, deletion_size vs pass_rate): {agent_r:+.4f}"
        )
        interp = (
            "← strongly negative: eval is faithful (harder = worse)"
            if agent_r < -0.5 else
            "← weak correlation: possible memorization or insufficient data"
        )
        print(f"  {interp}")

    summary_path = out_dir / "agent-summary.json"
    summary_path.write_text(summary.model_dump_json(indent=2))
    print(f"\nResults    → {out_path}  ({len(all_results)} records)")
    print(f"Summary    → {summary_path}")


def main() -> None:
    """Console-script entry point (wired to ``eval-agent`` in pyproject.toml)."""
    typer.run(_main)


if __name__ == "__main__":
    main()
