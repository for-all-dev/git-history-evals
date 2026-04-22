"""In-container per-slot ReAct driver.

This is the Python entry point executed inside each per-SHA Docker container
(issue #15). It iterates the slots described by ``RUN_CONFIG_JSON`` in the
environment, runs the pydantic-ai agent (``agent/agent.py`` + ``agent/tools.py``)
against each challenge, computes the same ``ProofMetrics`` the single-shot
baseline computes so the two runs are schema-comparable, and appends one
``ExperimentResult`` JSON line per slot to ``<results>/<RUN_ID>/agent.jsonl``.

The docker/orchestration layers (#16/#18/#19/#20/#23) are responsible for
producing ``RUN_CONFIG_JSON`` and providing the mounted ``repo_dir``; this
module only consumes them.

Design mirrors ``experiments/run_experiment.py``:
- The same meta.json keys (``declaration``, ``file_path``, ``commit_hash``).
- The same ``compute_proof_metrics`` + PASS-only ``vo_bytes`` /
  ``compile_time_s`` / ``assumptions`` population rules.
- A per-SHA human-reference compile cache so solution.v is compiled at most
  once per container invocation.
- ``tactic_edit_distance`` + ``normalized_edit_distance`` computed exactly
  as in the baseline.

Key differences: the agent edits files directly inside the per-container
``repo_dir`` checkout, so after each slot we ``git -C repo_dir checkout --
<rel_target>`` to restore the starting tree before the next slot.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai.usage import UsageLimits

# The experiments/ root is on sys.path in-container and via conftest.py in
# tests, so sibling imports work as they do in run_experiment.py.
from agent.agent import AgentVerdict, make_agent
from agent.deps import AgentDeps
from agent.tools import register_tools
from metrics import ExperimentResult, ProofMetrics
from proof_utils import (
    _find_proof_block,
    automation_ratio,
    count_tactics,
    extract_proof_sentences,
    max_bullet_depth,
    tactic_edit_distance,
    unique_tactic_types,
)
from shared.compile import (
    _get_coq_flags,
    print_assumptions,
    run_make_target,
    vo_bytes,
)
from shared.prompts import make_prompt_full, make_prompt_partial


# ── Per-SHA human-reference compile cache ────────────────────────────────────
# Keyed by (commit, rel_target_str) so a single container run compiles
# solution.v at most once per (SHA, file) pair. Mirrors the
# ``_human_metrics_cache`` in run_experiment.py.
_human_metrics_cache: dict[tuple[str, str], dict[str, Any] | None] = {}


@dataclass
class _SlotOutcome:
    """Internal bundle returned by ``_run_slot_core``.

    Exposes the raw agent-run artifacts the caller needs to both (a) build
    the public ``ExperimentResult`` and (b) write a transcript. ``run_one``
    consumes this via ``.result`` only; ``main`` also reads ``.log`` and
    ``.verdict``.
    """

    result: ExperimentResult
    log: list[str]
    verdict: AgentVerdict | None


# ── Proof-metrics helper (mirrors run_experiment.compute_proof_metrics) ──────

def _compute_proof_metrics(content: str, decl: str) -> ProofMetrics | None:
    """Reproduce run_experiment.compute_proof_metrics without importing it.

    Keeping a local copy avoids pulling the baseline's anthropic client /
    typer CLI into the agent's import graph (which would require network
    libraries at import time and break the tests/smoke path).
    """
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
        ends_with_admitted=bool(re.search(r"\bAdmitted\s*\.", proof_body)),
    )


# ── Git reset helper ──────────────────────────────────────────────────────────

def _git_reset(repo_dir: Path, rel_target: Path) -> None:
    """Reset ``rel_target`` in ``repo_dir`` to HEAD.

    Best-effort: if repo_dir is not a git worktree (e.g. in a test harness)
    or the file is untracked, we silently ignore the failure so the runner
    can continue with the next slot.
    """
    try:
        subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", "--", str(rel_target)],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


# ── Post-run compile + metrics (mirrors baseline's ``_compile_in_repo``) ─────

def _post_run_compile(
    repo_dir: Path,
    rel_target: Path,
    decl: str,
) -> dict[str, Any]:
    """Recompile ``rel_target`` after the agent finishes and derive PASS-only metrics.

    Returns a dict with keys ``verdict`` (PASS/FAIL/TIMEOUT/ERROR),
    ``vo_bytes``, ``compile_time_s``, ``assumptions``.

    The agent's own ``compile`` tool may have run earlier in the turn; we
    re-run here so the metrics reflect the final state the agent left on
    disk (the last ``write_proof`` + ``compile`` pair is not guaranteed to
    be the same turn the run terminates on).
    """
    try:
        result = run_make_target(repo_dir, rel_target)
    except Exception:
        return {
            "verdict": "ERROR",
            "vo_bytes": None,
            "compile_time_s": None,
            "assumptions": None,
        }

    if result.ok:
        size = vo_bytes(repo_dir, rel_target)
        try:
            axioms = print_assumptions(repo_dir, rel_target, decl)
        except Exception:
            axioms = None
        return {
            "verdict": "PASS",
            "vo_bytes": size,
            "compile_time_s": round(result.elapsed_s, 3),
            "assumptions": axioms,
        }

    if result.exit_code == 124:
        verdict = "TIMEOUT"
    elif result.exit_code == 127:
        verdict = "ERROR"
    else:
        verdict = "FAIL"
    return {
        "verdict": verdict,
        "vo_bytes": None,
        "compile_time_s": None,
        "assumptions": None,
    }


def _compile_human(
    commit: str,
    repo_dir: Path,
    rel_target: Path,
    solution_content: str,
    decl: str,
) -> dict[str, Any] | None:
    """Compile solution.v into ``repo_dir`` once per (commit, rel_target).

    Caches the outcome by ``(commit, str(rel_target))`` — a given container
    runs against a single SHA but may touch multiple files, so the compound
    key keeps the semantics obvious. Resets the repo file afterwards so the
    next slot does not inherit human-solution contents.
    """
    key = (commit, str(rel_target))
    if key in _human_metrics_cache:
        return _human_metrics_cache[key]

    target_path = repo_dir / rel_target
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(solution_content)
        outcome = _post_run_compile(repo_dir, rel_target, decl)
    finally:
        _git_reset(repo_dir, rel_target)

    _human_metrics_cache[key] = outcome
    return outcome


# ── Core per-slot runner (shared by run_one and main) ────────────────────────

def _run_slot_core(
    slot_dir: Path,
    condition: str,
    deletion_size: int,
    challenge_file: str,
    repo_dir: Path,
    model: str,
    max_turns: int,
) -> _SlotOutcome:
    """Drive one (slot × deletion_size) run. Used by both public entry points.

    Steps:
      1. Load meta.json, find rel_target, build AgentDeps.
      2. Build the initial user prompt via shared/prompts.py (full for
         condition A, partial for condition B).
      3. make_agent + register_tools; run_sync with a request_limit cap.
      4. Recompile the final attempt + compute ProofMetrics for both
         ``solution.v`` (human) and the agent's attempt (LLM).
      5. tactic_edit_distance + normalized_edit_distance.
      6. Reset the in-repo file so the next slot starts clean.
    """
    slot_dir = Path(slot_dir)
    repo_dir = Path(repo_dir)

    meta = json.loads((slot_dir / "meta.json").read_text())
    decl: str = meta["declaration"]
    rel_target = Path(meta["file_path"])
    commit: str = meta.get("commit_hash", "")

    challenge_path = slot_dir / challenge_file
    content = challenge_path.read_text()

    attempt_name = (
        "attempt.v" if deletion_size == -1 else f"attempt_del{deletion_size}.v"
    )
    attempt_path = slot_dir / attempt_name

    # Populate coq_flags from the project file so future tool additions can
    # reach for them without re-reading _CoqProject. Matches how the baseline
    # sees the project.
    try:
        coq_flags = _get_coq_flags(repo_dir)
    except Exception:
        coq_flags = []

    deps = AgentDeps(
        slot_dir=slot_dir,
        repo_dir=repo_dir,
        rel_target=rel_target,
        decl=decl,
        attempt_path=attempt_path,
        coq_flags=coq_flags,
    )

    if deletion_size == -1:
        initial_prompt = make_prompt_full(decl, content)
    else:
        initial_prompt = make_prompt_partial(decl, content, deletion_size)

    agent = make_agent(model)
    register_tools(agent)

    verdict_obj: AgentVerdict | None = None
    agent_error: str | None = None
    input_tokens = 0
    output_tokens = 0
    n_requests = 0
    t0 = time.monotonic()
    try:
        run_result = agent.run_sync(
            initial_prompt,
            deps=deps,
            usage_limits=UsageLimits(request_limit=max_turns),
        )
        verdict_obj = run_result.output
        usage = run_result.usage()
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        n_requests = int(getattr(usage, "requests", 0) or 0)
    except Exception as e:  # noqa: BLE001 - surface any failure, don't crash the loop
        agent_error = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0

    # Recompile the final attempt so PASS-only fields are derived from the
    # exact bytes sitting on disk when the agent terminated.
    if agent_error is not None:
        outcome: dict[str, Any] = {
            "verdict": "ERROR",
            "vo_bytes": None,
            "compile_time_s": None,
            "assumptions": None,
        }
    else:
        outcome = _post_run_compile(repo_dir, rel_target, decl)
    verdict = outcome["verdict"]

    # Proof metrics: on the FINAL contents of the attempt path if it exists
    # (agent's last write_proof), else fall back to challenge content.
    if attempt_path.exists():
        final_content = attempt_path.read_text()
    else:
        final_content = content
    llm_m = _compute_proof_metrics(final_content, decl)

    solution_path = slot_dir / "solution.v"
    human_m: ProofMetrics | None = None
    solution_content: str | None = None
    if solution_path.exists():
        solution_content = solution_path.read_text()
        human_m = _compute_proof_metrics(solution_content, decl)

    # On PASS, populate compile-derived fields on llm_metrics and, via the
    # per-SHA cache, on human_metrics.
    if verdict == "PASS" and llm_m is not None:
        llm_m.vo_bytes = outcome["vo_bytes"]
        llm_m.compile_time_s = outcome["compile_time_s"]
        llm_m.assumptions = outcome["assumptions"]
        llm_m.n_assumptions = (
            len(outcome["assumptions"]) if outcome["assumptions"] is not None else None
        )

        if human_m is not None and solution_content is not None and commit:
            human_outcome = _compile_human(
                commit, repo_dir, rel_target, solution_content, decl,
            )
            if human_outcome is not None and human_outcome["verdict"] == "PASS":
                human_m.vo_bytes = human_outcome["vo_bytes"]
                human_m.compile_time_s = human_outcome["compile_time_s"]
                human_m.assumptions = human_outcome["assumptions"]
                human_m.n_assumptions = (
                    len(human_outcome["assumptions"])
                    if human_outcome["assumptions"] is not None else None
                )

    # Similarity
    ted: int | None = None
    ned: float | None = None
    if human_m and llm_m and solution_content is not None:
        h_sents = extract_proof_sentences(solution_content, decl) or []
        l_sents = extract_proof_sentences(final_content, decl) or []
        if h_sents and l_sents:
            ted = tactic_edit_distance(h_sents, l_sents)
            denom = max(human_m.tactic_count, llm_m.tactic_count)
            ned = round(ted / denom, 4) if denom > 0 else 0.0

    # Reset the in-repo file so the next slot starts from a clean tree.
    _git_reset(repo_dir, rel_target)

    # Agent-loop bookkeeping
    agent_n_turns: int | None
    agent_give_up_reason: str | None
    if verdict_obj is not None:
        agent_n_turns = verdict_obj.n_turns or n_requests or None
        if verdict_obj.give_up_reason:
            agent_give_up_reason = verdict_obj.give_up_reason
        elif verdict_obj.succeeded:
            agent_give_up_reason = "compile_success"
        else:
            agent_give_up_reason = "completed_without_success"
    else:
        agent_n_turns = n_requests or None
        agent_give_up_reason = agent_error or "run_failed"

    res = ExperimentResult(
        challenge_id=slot_dir.name,
        declaration=decl,
        deletion_size=deletion_size,
        condition=condition,  # type: ignore[arg-type]
        verdict=verdict,       # type: ignore[arg-type]
        inference_time_s=round(elapsed, 2),
        output_tokens=output_tokens,
        mode="agent",
        agent_n_turns=agent_n_turns,
        agent_give_up_reason=agent_give_up_reason,
        agent_total_input_tokens=input_tokens,
        agent_total_output_tokens=output_tokens,
        human_metrics=human_m,
        llm_metrics=llm_m,
        tactic_edit_distance=ted,
        normalized_edit_distance=ned,
    )
    return _SlotOutcome(result=res, log=list(deps.log), verdict=verdict_obj)


def run_one(
    slot_dir: Path,
    condition: str,
    deletion_size: int,
    challenge_file: str,
    repo_dir: Path,
    model: str,
    max_turns: int,
) -> ExperimentResult:
    """Run the ReAct agent on a single slot and return a comparable row.

    Public entry point for issue #15's acceptance criteria. Delegates to
    ``_run_slot_core`` and discards the transcript-side artifacts.
    """
    return _run_slot_core(
        slot_dir=slot_dir,
        condition=condition,
        deletion_size=deletion_size,
        challenge_file=challenge_file,
        repo_dir=repo_dir,
        model=model,
        max_turns=max_turns,
    ).result


# ── Transcript writer ─────────────────────────────────────────────────────────

def _write_transcript(
    transcripts_dir: Path,
    slot_name: str,
    deletion_size: int,
    log: list[str],
    verdict_obj: AgentVerdict | None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Persist the agent's per-slot trail to ``<transcripts>/<slot>_d<size>.json``.

    The payload is the ``AgentDeps.log`` list the tools appended to, plus the
    ``AgentVerdict`` fields (or ``None`` if the run crashed before producing
    one). ``extra`` is folded in as-is so callers can record error info.
    """
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    path = transcripts_dir / f"{slot_name}_d{deletion_size}.json"
    payload: dict[str, Any] = {
        "slot": slot_name,
        "deletion_size": deletion_size,
        "log": list(log),
        "verdict": verdict_obj.model_dump() if verdict_obj is not None else None,
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


# ── main: the container entry point ──────────────────────────────────────────

def _results_base() -> Path:
    """Return the results mount root, parametrised for the smoke test.

    Defaults to ``/results`` (the docker volume mount point); tests and local
    smoke runs can override via ``RESULTS_BASE`` so we don't need root to
    write to ``/``.
    """
    return Path(os.environ.get("RESULTS_BASE", "/results"))


def _append_jsonl(path: Path, row: ExperimentResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(row.model_dump_json() + "\n")


def _log_exception(logfile: Path, context: str, exc: BaseException) -> None:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open("a") as fh:
        fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n")
        fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        fh.write("\n")


def _resolve_slot_dir(base_experiments: Path, slot_entry: dict[str, Any]) -> Path:
    """Resolve a slot entry from RUN_CONFIG_JSON to an on-disk directory.

    Accepts either an explicit ``slot_dir`` (agent-runner convention) or the
    orchestrate/lib.sh shape ``{condition, slot, deletion_size, challenge_file}``
    — in the latter case we derive the dir from
    ``<experiments_base>/admitted-proofs`` (condition A) or
    ``<experiments_base>/experiments3`` (condition B).
    """
    if "slot_dir" in slot_entry:
        return Path(slot_entry["slot_dir"])
    condition = slot_entry["condition"]
    slot_name = slot_entry["slot"]
    sub = "admitted-proofs" if condition == "A" else "experiments3"
    return base_experiments / sub / slot_name


def main() -> int:
    """Container entry point. Returns a process exit code.

    Reads ``RUN_CONFIG_JSON`` from the environment. Writes per-slot rows to
    ``<RESULTS_BASE>/<RUN_ID>/agent.jsonl``, transcripts to
    ``<RESULTS_BASE>/<RUN_ID>/transcripts/<slot>_d<size>.json``, and catches
    per-slot exceptions to ``<RESULTS_BASE>/<RUN_ID>/run.log``.
    """
    raw_config = os.environ.get("RUN_CONFIG_JSON")
    if not raw_config:
        sys.stderr.write(
            "agent/runner.py: RUN_CONFIG_JSON is not set. "
            "This entry point is meant to run inside the per-SHA container; "
            "set RUN_CONFIG_JSON to a JSON object describing the run. "
            "See experiments/orchestrate/lib.sh:run_config_json.\n"
        )
        return 2
    try:
        config = json.loads(raw_config)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"agent/runner.py: RUN_CONFIG_JSON is not valid JSON: {e}\n")
        return 2

    run_id = (
        config.get("run_id")
        or os.environ.get("RUN_ID")
        or time.strftime("%Y%m%d-%H%M%S")
    )
    model = config.get("model") or os.environ.get("MODEL") or "anthropic:claude-sonnet-4-6"
    max_turns = int(config.get("max_turns") or os.environ.get("MAX_TURNS") or 20)
    slots: list[dict[str, Any]] = config.get("slots") or []

    repo_dir = Path(
        config.get("repo_dir")
        or os.environ.get("FIAT_CRYPTO_DIR")
        or "/data/fiat-crypto"
    )
    # Experiments root — slots from lib.sh only reference slot names, not
    # full paths, so we need a base to resolve them against. Defaults to the
    # parent of this file's package (experiments/).
    base_experiments = Path(
        config.get("experiments_base")
        or os.environ.get("EXPERIMENTS_BASE")
        or Path(__file__).resolve().parent.parent
    )

    results_dir = _results_base() / run_id
    transcripts_dir = results_dir / "transcripts"
    jsonl_path = results_dir / "agent.jsonl"
    logfile = results_dir / "run.log"

    results_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    with logfile.open("a") as fh:
        fh.write(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] agent runner starting "
            f"run_id={run_id} model={model} max_turns={max_turns} "
            f"n_slots={len(slots)} repo_dir={repo_dir}\n"
        )

    for entry in slots:
        try:
            slot_dir = _resolve_slot_dir(base_experiments, entry)
            condition = entry["condition"]
            deletion_size = int(entry.get("deletion_size", -1))
            challenge_file = entry.get("challenge_file") or (
                "challenge.v" if deletion_size == -1 else f"challenge{deletion_size}.v"
            )
            outcome = _run_slot_core(
                slot_dir=slot_dir,
                condition=condition,
                deletion_size=deletion_size,
                challenge_file=challenge_file,
                repo_dir=repo_dir,
                model=model,
                max_turns=max_turns,
            )
            _append_jsonl(jsonl_path, outcome.result)
            _write_transcript(
                transcripts_dir,
                slot_name=slot_dir.name,
                deletion_size=deletion_size,
                log=outcome.log,
                verdict_obj=outcome.verdict,
            )
        except Exception as e:  # noqa: BLE001 - per-slot isolation
            _log_exception(
                logfile,
                f"slot={entry!r} failed",
                e,
            )
            continue

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
