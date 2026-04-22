"""
Progressive-deletion proof completion experiment using the Claude API.

Conditions
----------
A  (deletion_size = -1)  Full proof replaced with Admitted. (admitted-proofs/)
B3 (deletion_size =  3)  Last  3 tactics removed            (experiments3/)
B5 (deletion_size =  5)  Last  5 tactics removed            (experiments3/)
B7 (deletion_size =  7)  Last  7 tactics removed            (experiments3/)
B10 (deletion_size = 10) Last 10 tactics removed            (experiments3/)
B15 (deletion_size = 15) Last 15 tactics removed            (experiments3/)

For each challenge the script:
  1. Reads the challenge file and meta.json
  2. Calls claude-sonnet-4-6 to produce only the missing tactics
  3. Splices the tactics into the original file, writing attempt_delN.v
  4. Clones fiat-crypto at the challenge's commit into /tmp, places the
     attempt file at its original path in the repo, then drives the
     repo's Makefile via shared.compile.run_make_target
  5. Computes proof metrics (human vs LLM) and tactic edit distance
  6. Appends one JSON record per run to <results_dir>/baseline.jsonl
  7. Writes a human-readable log to experiment-log.txt
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import anthropic
import typer
from dotenv import load_dotenv
from pydantic import BaseModel

# Walk up from the script's directory to find .env
_script_dir = Path(__file__).resolve().parent
for _p in (_script_dir, *_script_dir.parents):
    if (_p / ".env").exists():
        load_dotenv(_p / ".env")
        break

sys.path.insert(0, str(_script_dir))
from proof_utils import (
    automation_ratio,
    count_tactics,
    extract_proof_sentences,
    max_bullet_depth,
    tactic_edit_distance,
    unique_tactic_types,
    _find_proof_block,
)
from metrics import (
    DeletionConditionSummary,
    ExperimentResult,
    ExperimentSummary,
    ProofMetrics,
)
from shared.splice import patch_admitted
from shared.prompts import (
    SYS_PROMPT_BASELINE,
    make_prompt_full,
    make_prompt_partial,
)
from shared.compile import (
    print_assumptions,
    run_make_target,
    vo_bytes,
)

# ── Config ────────────────────────────────────────────────────────────────────

BASE             = Path(__file__).parent
EXP_A            = BASE / "admitted-proofs"
EXP_B            = BASE / "experiments3"
LOG              = BASE / "experiment-log.txt"
MODEL            = "claude-sonnet-4-6"
FIAT_CRYPTO_DIR  = Path(os.environ.get("FIAT_CRYPTO_DIR", "/data/fiat-crypto"))


def _resolve_results_dir() -> Path:
    """Return the per-run results directory.

    Inside a container (detected via ``/.dockerenv`` or ``RUNNING_IN_DOCKER``),
    this is ``/results/${RUN_ID}``. Otherwise it is
    ``experiments/results/${RUN_ID}``. ``RUN_ID`` defaults to a timestamp if
    unset, so ad-hoc local runs still get their own directory.
    """
    run_id = os.environ.get("RUN_ID") or time.strftime("%Y%m%d-%H%M%S")
    in_container = Path("/.dockerenv").exists() or bool(os.environ.get("RUNNING_IN_DOCKER"))
    root = Path("/results") if in_container else (BASE / "results")
    out = root / run_id
    out.mkdir(parents=True, exist_ok=True)
    return out


RESULTS_DIR   = _resolve_results_dir()
RESULTS_JSONL = RESULTS_DIR / "baseline.jsonl"

client = anthropic.Anthropic()


# ── Structured output schema (Claude API) ─────────────────────────────────────

class ProofAttempt(BaseModel):
    tactics: str    # tactic lines replacing Admitted., ending with Qed. or Admitted.


def call_claude(prompt: str, log: list[str]) -> tuple[ProofAttempt, float, int]:
    """Returns (ProofAttempt, elapsed_seconds, output_tokens)."""
    log.append(f"\n  [API call: {MODEL}]")
    t0 = time.time()
    for attempt in range(6):
        try:
            response = client.messages.parse(
                model=MODEL,
                max_tokens=4096,
                temperature=0,
                system=SYS_PROMPT_BASELINE,
                messages=[{"role": "user", "content": prompt}],
                output_format=ProofAttempt,
            )
            break
        except anthropic.RateLimitError:
            wait = 60 * (attempt + 1)
            log.append(f"  rate-limited (attempt {attempt+1}), waiting {wait}s…")
            print(f"  rate-limited (attempt {attempt+1}), waiting {wait}s…", flush=True)
            time.sleep(wait)
    else:
        raise RuntimeError("Exceeded retry limit for rate limiting")

    elapsed = time.time() - t0
    tokens  = response.usage.output_tokens
    log.append(f"  elapsed: {elapsed:.1f}s  |  output tokens: {tokens}")

    result = response.parsed_output
    if result is None:
        log.append("  WARNING: structured output returned None")
        return ProofAttempt(tactics="Admitted."), elapsed, tokens
    return result, elapsed, tokens


# ── Proof metrics helpers ─────────────────────────────────────────────────────

def compute_proof_metrics(content: str, decl: str) -> ProofMetrics | None:
    sentences = extract_proof_sentences(content, decl)
    if sentences is None:
        return None
    span = _find_proof_block(content, decl)
    proof_body = content[span[0]:span[1]] if span else ""
    return ProofMetrics(
        tactic_count=count_tactics(sentences),
        automation_ratio=round(automation_ratio(sentences), 4),
        unique_tactic_types=unique_tactic_types(sentences),
        max_bullet_depth=max_bullet_depth(proof_body),
        proof_chars=len(proof_body),
        proof_lines=proof_body.count("\n"),
        ends_with_admitted=bool(re.search(r'\bAdmitted\s*\.', proof_body)),
    )


# ── fiat-crypto checkout & compile ───────────────────────────────────────────

def _checkout_commit(commit: str, log: list[str]) -> Path | None:
    if not FIAT_CRYPTO_DIR.exists():
        log.append(f"  fiat-crypto not mounted at {FIAT_CRYPTO_DIR}")
        return None
    tmpdir = Path(f"/tmp/fc_{commit[:8]}")
    if tmpdir.exists():
        shutil.rmtree(tmpdir)
    try:
        subprocess.run(
            ["git", "clone", "--local", "--shared", "--no-checkout",
             str(FIAT_CRYPTO_DIR), str(tmpdir)],
            check=True, capture_output=True, timeout=120,
        )
        subprocess.run(
            ["git", "-C", str(tmpdir), "checkout", commit, "--quiet"],
            check=True, capture_output=True, timeout=120,
        )
    except subprocess.CalledProcessError as e:
        log.append(f"  git checkout failed: {e.stderr.decode()[:200]}")
        return None
    return tmpdir


class _CompileOutcome(BaseModel):
    """Internal bundle returned by _compile_in_repo."""
    verdict: str
    vo_bytes: int | None = None
    compile_time_s: float | None = None
    assumptions: list[str] | None = None


def _compile_in_repo(
    repo: Path,
    rel_target: Path,
    source_content: str,
    decl: str | None,
    log: list[str],
) -> _CompileOutcome:
    """Write ``source_content`` to ``repo/rel_target`` and drive the repo Makefile.

    On success (``ok``), also populates vo_bytes, compile_time_s, and — if
    ``decl`` is provided — the axioms via ``print_assumptions``. Verdicts
    mirror the legacy mapping: PASS / FAIL / TIMEOUT / ERROR.
    """
    target_path = repo / rel_target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(source_content)

    log.append(f"  make target: {rel_target.with_suffix('.vo')}")
    try:
        result = run_make_target(repo, rel_target)
    except Exception as e:  # pragma: no cover - defensive
        log.append(f"  run_make_target raised: {e}")
        return _CompileOutcome(verdict="ERROR")

    if result.ok:
        log.append(f"  make: PASS ✓ ({result.elapsed_s:.1f}s)")
        size = vo_bytes(repo, rel_target)
        axioms: list[str] | None = None
        if decl:
            axioms = print_assumptions(repo, rel_target, decl)
        return _CompileOutcome(
            verdict="PASS",
            vo_bytes=size,
            compile_time_s=round(result.elapsed_s, 3),
            assumptions=axioms,
        )

    # Map non-ok outcomes to legacy verdicts.
    if result.exit_code == 124:
        log.append(f"  make: TIMEOUT")
        return _CompileOutcome(verdict="TIMEOUT")
    if result.exit_code == 127:
        log.append(f"  make: ERROR ({result.stderr[:200]})")
        return _CompileOutcome(verdict="ERROR")
    short = (result.stderr or result.stdout)[:2000].strip()
    log.append(f"  make: FAIL\n    {short}")
    return _CompileOutcome(verdict="FAIL")


# ── Human-proof compilation cache (keyed by commit SHA) ──────────────────────

_human_metrics_cache: dict[str, _CompileOutcome] = {}


def _compile_human_solution(
    commit: str,
    rel_target: Path,
    solution_content: str,
    decl: str,
    log: list[str],
) -> _CompileOutcome | None:
    """Compile solution.v once per SHA and cache the outcome."""
    cached = _human_metrics_cache.get(commit)
    if cached is not None:
        return cached

    log.append(f"  [human] compiling solution.v @ {commit[:8]}…")
    repo = _checkout_commit(commit, log)
    if repo is None:
        return None
    try:
        outcome = _compile_in_repo(repo, rel_target, solution_content, decl, log)
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    _human_metrics_cache[commit] = outcome
    return outcome


# ── Per-slot runner ───────────────────────────────────────────────────────────

def run_slot(
    slot: Path,
    condition: str,
    deletion_size: int,
    challenge_file: str,
    prompt_fn,
    log_lines: list[str],
    all_results: list[ExperimentResult],
) -> str:
    meta_path = slot / "meta.json"
    chal_path = slot / challenge_file
    if not meta_path.exists() or not chal_path.exists():
        return "SKIP"

    meta    = json.loads(meta_path.read_text())
    decl    = meta["declaration"]
    content = chal_path.read_text()
    rel_target = Path(meta["file_path"])
    commit = meta["commit_hash"]

    log_lines.append(f"\n{'═'*64}")
    log_lines.append(f"[{condition}] {slot.name}  (deletion={deletion_size})")
    log_lines.append(f"Declaration : {decl}")
    log_lines.append(f"File        : {meta.get('file_path','')}")
    log_lines.append(f"Commit      : {commit[:12]}")

    # Show context around the Admitted. placeholder
    decl_pos = content.find(decl)
    if decl_pos != -1:
        m = re.search(r'(?m)^\s*Admitted\.\s*$', content[decl_pos:])
        if m:
            abs_line = content[:decl_pos + m.start()].count('\n')
            lines = content.splitlines()
            ctx_start = max(0, abs_line - 6)
            ctx_end   = min(len(lines), abs_line + 3)
            ctx = "\n".join(f"  {i+1:>4} | {lines[i]}"
                            for i in range(ctx_start, ctx_end))
            log_lines.append(f"\nProof context (lines {ctx_start+1}–{ctx_end}):")
            log_lines.append(ctx)

    # Call Claude
    prompt = prompt_fn(decl, content)
    proof, elapsed, tokens = call_claude(prompt, log_lines)
    log_lines.append(f"\nTactics returned:\n{proof.tactics.strip()}")

    # Splice tactics → attempt file
    completed    = patch_admitted(content, decl, proof.tactics)
    attempt_name = f"attempt_del{deletion_size}.v" if deletion_size != -1 else "attempt.v"
    attempt_path = slot / attempt_name
    attempt_path.write_text(completed)
    log_lines.append(f"\nattempt written to {attempt_path.name}")

    # Compile the LLM attempt in a fresh checkout of the commit.
    log_lines.append(f"  checking out fiat-crypto @ {commit[:8]}…")
    repo = _checkout_commit(commit, log_lines)
    if repo is None:
        outcome = _CompileOutcome(verdict="ERROR")
    else:
        try:
            outcome = _compile_in_repo(repo, rel_target, completed, decl, log_lines)
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    verdict = outcome.verdict

    # Proof metrics
    solution_path = slot / "solution.v"
    human_m: ProofMetrics | None = None
    llm_m:   ProofMetrics | None = None
    ted: int | None = None
    ned: float | None = None

    if solution_path.exists():
        human_m = compute_proof_metrics(solution_path.read_text(), decl)

    llm_m = compute_proof_metrics(completed, decl)

    # On PASS, populate the artifact-drift fields on llm_metrics and,
    # via the per-SHA cache, on human_metrics.
    if verdict == "PASS" and llm_m is not None:
        llm_m.vo_bytes = outcome.vo_bytes
        llm_m.compile_time_s = outcome.compile_time_s
        llm_m.assumptions = outcome.assumptions
        llm_m.n_assumptions = len(outcome.assumptions) if outcome.assumptions is not None else None

        if human_m is not None and solution_path.exists():
            human_outcome = _compile_human_solution(
                commit, rel_target, solution_path.read_text(), decl, log_lines,
            )
            if human_outcome is not None and human_outcome.verdict == "PASS":
                human_m.vo_bytes = human_outcome.vo_bytes
                human_m.compile_time_s = human_outcome.compile_time_s
                human_m.assumptions = human_outcome.assumptions
                human_m.n_assumptions = (
                    len(human_outcome.assumptions)
                    if human_outcome.assumptions is not None else None
                )

    if human_m and llm_m and solution_path.exists():
        h_sents = extract_proof_sentences(solution_path.read_text(), decl) or []
        l_sents = extract_proof_sentences(completed, decl) or []
        if h_sents and l_sents:
            ted = tactic_edit_distance(h_sents, l_sents)
            denom = max(human_m.tactic_count, llm_m.tactic_count)
            ned = round(ted / denom, 4) if denom > 0 else 0.0

    result = ExperimentResult(
        challenge_id=slot.name,
        declaration=decl,
        deletion_size=deletion_size,
        condition=condition,  # type: ignore[arg-type]
        verdict=verdict,      # type: ignore[arg-type]
        inference_time_s=round(elapsed, 2),
        output_tokens=tokens,
        mode="baseline",
        human_metrics=human_m,
        llm_metrics=llm_m,
        tactic_edit_distance=ted,
        normalized_edit_distance=ned,
    )
    all_results.append(result)

    with RESULTS_JSONL.open("a") as fh:
        fh.write(result.model_dump_json() + "\n")

    return verdict


def run_condition(
    slots: list[Path],
    condition: str,
    deletion_size: int,
    challenge_file: str,
    prompt_fn,
    log_lines: list[str],
    all_results: list[ExperimentResult],
    max_challenges: int | None,
    only: str | None = None,
) -> dict[str, str]:
    results: dict[str, str] = {}
    count = 0
    for slot in sorted(slots):
        if not slot.is_dir():
            continue
        if only and only not in slot.name:
            continue
        if max_challenges is not None and count >= max_challenges:
            break
        verdict = run_slot(
            slot, condition, deletion_size, challenge_file,
            prompt_fn, log_lines, all_results,
        )
        if verdict != "SKIP":
            results[slot.name] = verdict
            count += 1
    return results


# ── Summary computation ───────────────────────────────────────────────────────

def _summarise(results: list[ExperimentResult]) -> ExperimentSummary:
    from collections import defaultdict
    import statistics

    by_cond: dict[tuple[str, int], list[ExperimentResult]] = defaultdict(list)
    for r in results:
        by_cond[(r.condition, r.deletion_size)].append(r)

    conditions: list[DeletionConditionSummary] = []
    for (cond, dsize), rs in sorted(by_cond.items()):
        n_pass    = sum(1 for r in rs if r.verdict == "PASS")
        n_fail    = sum(1 for r in rs if r.verdict == "FAIL")
        n_err     = sum(1 for r in rs if r.verdict in ("TIMEOUT", "ERROR"))
        n_total   = len(rs)
        pass_rate = round(n_pass / n_total, 4) if n_total else 0.0

        teds = [r.normalized_edit_distance for r in rs if r.normalized_edit_distance is not None]

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
        ))

    # Faithfulness: Pearson r between deletion_size and pass_rate for Condition B,
    # computed per-mode. This runner only emits mode="baseline", but #4's schema
    # stores the result in a dict keyed by mode.
    faith_by_mode: dict[str, float | None] = {}
    b_conds = [c for c in conditions if c.condition == "B" and c.n_challenges > 0]
    faith_r: float | None = None
    if len(b_conds) >= 3:
        xs = [float(c.deletion_size) for c in b_conds]
        ys = [c.pass_rate for c in b_conds]
        xm = sum(xs) / len(xs)
        ym = sum(ys) / len(ys)
        num   = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
        denom = (sum((x - xm)**2 for x in xs) * sum((y - ym)**2 for y in ys)) ** 0.5
        faith_r = round(num / denom, 4) if denom > 0 else None
    faith_by_mode["baseline"] = faith_r

    return ExperimentSummary(
        model=MODEL,
        date=time.strftime("%Y-%m-%d %H:%M:%S"),
        n_total_runs=len(results),
        conditions=conditions,
        faithfulness_by_mode=faith_by_mode,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def _main(
    max_challenges: int = typer.Option(
        3, "--max-challenges", "-n",
        help="Max challenges per condition/deletion-size (0 = all)",
    ),
    deletion_sizes: str = typer.Option(
        "3,5,7,10,15",
        "--deletion-sizes",
        help="Comma-separated Condition B deletion sizes to run",
    ),
    skip_condition_a: bool = typer.Option(False, "--skip-a", help="Skip full-proof condition"),
    skip_condition_b: bool = typer.Option(False, "--skip-b", help="Skip partial-deletion conditions"),
    only: str = typer.Option("", "--only", help="Only run challenges whose dir name contains this substring"),
) -> None:
    limit       = max_challenges if max_challenges > 0 else None
    only_filter = only or None
    b_sizes = [int(s.strip()) for s in deletion_sizes.split(",")]

    RESULTS_JSONL.write_text("")

    log_lines: list[str] = [
        "=" * 64,
        "PROGRESSIVE DELETION PROOF EXPERIMENT — Claude API",
        f"Model         : {MODEL}",
        f"Max challenges: {limit or 'all'}",
        f"B sizes       : {b_sizes}",
        f"Results dir   : {RESULTS_DIR}",
        f"Date          : {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 64,
    ]

    all_results: list[ExperimentResult] = []

    # Condition B: last N tactics removed
    if not skip_condition_b:
        for n in b_sizes:
            log_lines.append(f"\n\n{'━'*64}")
            log_lines.append(f"CONDITION B{n}: Last {n} tactics removed")
            log_lines.append("━"*64)
            run_condition(
                list(EXP_B.iterdir()),
                condition="B",
                deletion_size=n,
                challenge_file=f"challenge{n}.v",
                prompt_fn=lambda decl, content, _n=n: make_prompt_partial(decl, content, _n),
                log_lines=log_lines,
                all_results=all_results,
                max_challenges=limit,
                only=only_filter,
            )

    # Condition A: full proof replaced
    if not skip_condition_a:
        log_lines.append(f"\n\n{'━'*64}")
        log_lines.append("CONDITION A: Full proof replaced with Admitted.")
        log_lines.append("━"*64)
        run_condition(
            list(EXP_A.iterdir()),
            condition="A",
            deletion_size=-1,
            challenge_file="challenge.v",
            prompt_fn=make_prompt_full,
            log_lines=log_lines,
            all_results=all_results,
            max_challenges=limit,
            only=only_filter,
        )

    # Summary
    summary = _summarise(all_results)

    log_lines.append("\n\n" + "="*64)
    log_lines.append("SUMMARY")
    log_lines.append("="*64)

    for c in summary.conditions:
        log_lines.append(
            f"\nCondition {c.condition} (deletion={c.deletion_size}): "
            f"{c.n_pass}/{c.n_challenges} PASS  "
            f"[pass_rate={c.pass_rate:.0%}  "
            f"mean_time={c.mean_inference_time_s}s  "
            f"mean_tokens={c.mean_output_tokens:.0f}  "
            f"mean_ned={c.mean_normalized_edit_distance}]"
        )

    baseline_r = summary.faithfulness_by_mode.get("baseline")
    if baseline_r is not None:
        log_lines.append(
            f"\nFaithfulness (Pearson r, deletion_size vs pass_rate): "
            f"{baseline_r:+.4f}"
        )
        interp = (
            "← strongly negative: eval is faithful (harder = worse)"
            if baseline_r < -0.5 else
            "← weak correlation: possible memorization or insufficient data"
        )
        log_lines.append(f"  {interp}")

    report = "\n".join(log_lines)
    LOG.write_text(report)
    print(report)

    summary_path = BASE / "experiment-summary.json"
    summary_path.write_text(summary.model_dump_json(indent=2))
    print(f"\nFull log   → {LOG}")
    print(f"Results    → {RESULTS_JSONL}  ({len(all_results)} records)")
    print(f"Summary    → {summary_path}")


def main() -> None:
    """Console-script entry point (wired to ``eval-baseline`` in pyproject.toml)."""
    typer.run(_main)


if __name__ == "__main__":
    main()
