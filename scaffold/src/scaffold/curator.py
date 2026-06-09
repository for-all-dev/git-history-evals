"""LLM-based curation pipeline for filtering low-quality mined challenges.

Tiered approach: a cheap model (Haiku) handles the obvious accept/reject calls;
challenges it marks DEFER are escalated to a stronger model (Sonnet).  DEFER
from the stronger model becomes ``"borderline"`` in the final output.

The pipeline is **fault-tolerant**: individual API errors are caught and
recorded rather than crashing the batch, results are checkpointed to disk as
they complete, and runs can be resumed from a checkpoint after interruption.

The curation is **challenge-type agnostic** — it inspects the fields already
present on every ``EvalChallenge`` (diff, commit_message, instructions,
file_path) and works equally well on proof_complete, proof_add, proof_optimise,
and spec_change challenges.

Usage:
    from scaffold.curator import curate_challenges_sync, apply_curation
    results = curate_challenges_sync(challenges)
    accepted, rejected, borderline = apply_curation(challenges, results)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from scaffold.models import EvalChallenge

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIER1_MODEL = "claude-haiku-4-5"
DEFAULT_TIER2_MODEL = "claude-sonnet-4-5"
_MAX_DIFF_CHARS = 8000

# Substrings in API error messages that indicate a permanent failure —
# no point retrying or continuing with remaining tasks.
_PERMANENT_ERROR_PATTERNS = ["credit balance", "billing"]

_SYSTEM_PROMPT = """\
You are a quality curator for proof engineering benchmark challenges mined \
from the git history of a formally verified codebase. Decide whether each \
challenge belongs in an evaluation dataset for AI proof synthesis.

DEFAULT TO ACCEPT. In verified codebases like CompCert, seL4, or fiat-crypto, \
most changes to proof files (.v, .thy, .lean) are substantive because \
definitions, types, and specifications carry proof obligations.

REJECT only when the ENTIRE diff consists of:
- Whitespace, formatting, or indentation changes
- Comment or documentation changes
- Adding, removing, or modifying Import/Require lines with no other changes
- License, copyright, or boilerplate header changes
- Deletion of unused code with no replacement

ACCEPT if the diff contains ANY of the following, regardless of how small:
- Any change inside a proof body (between Proof. and Qed./Defined.), \
including mechanical tactic updates like omega->lia — tactic selection is \
part of the proof engineering task
- New or modified Definition, Fixpoint, Lemma, Theorem, or Instance
- New constructors added to inductive types
- New match arms in pattern matches
- Type, signature, or specification changes
- Extraction directives (Extract Constant, Extract Inductive)
- A new file containing Proof./Qed. blocks with tactic scripts

When in doubt, ACCEPT. The dataset can tolerate a few marginal challenges \
but should not lose genuine proof engineering work.

Respond with exactly two lines:
VERDICT: ACCEPT | REJECT | DEFER
RATIONALE: <one sentence explaining your decision>

Use DEFER only when you genuinely cannot tell — the challenge is ambiguous \
enough to warrant a stronger model's evaluation."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class CurationResult(BaseModel):
    """Result of curating a single challenge."""

    verdict: str  # "accept" | "reject" | "defer" | "borderline" | "error"
    model: str
    rationale: str


# ---------------------------------------------------------------------------
# Prompt / response helpers
# ---------------------------------------------------------------------------


def _build_curation_prompt(challenge: EvalChallenge) -> str:
    """Assemble the user-message prompt from an EvalChallenge's fields."""
    diff = challenge.diff
    if len(diff) > _MAX_DIFF_CHARS:
        diff = (
            diff[:_MAX_DIFF_CHARS]
            + f"\n... [truncated, {len(challenge.diff)} chars total]"
        )

    return (
        f"## Challenge\n"
        f"- **Repository**: {challenge.repo}\n"
        f"- **Proof assistant**: {challenge.proof_assistant}\n"
        f"- **File**: {challenge.file_path}\n"
        f"- **Commit message**: {challenge.commit_message}\n"
        f"- **Instructions**: {challenge.instructions}\n"
        f"\n## Diff\n```\n{diff}\n```"
    )


_VERDICT_RE = re.compile(r"VERDICT:\s*(ACCEPT|REJECT|DEFER)", re.IGNORECASE)
_RATIONALE_RE = re.compile(r"RATIONALE:\s*(.+)", re.IGNORECASE)


def _parse_response(text: str) -> tuple[str, str]:
    """Extract ``(verdict, rationale)`` from the model's response text.

    Returns ``("defer", ...)`` if parsing fails — the challenge will be
    escalated to the stronger model rather than silently dropped.
    """
    verdict_match = _VERDICT_RE.search(text)
    rationale_match = _RATIONALE_RE.search(text)

    verdict = verdict_match.group(1).lower() if verdict_match else "defer"
    rationale = (
        rationale_match.group(1).strip()
        if rationale_match
        else text.strip()[:200] or "Could not parse curation response"
    )
    return verdict, rationale


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------


def _load_checkpoint(path: Path) -> dict[str, dict]:
    """Load checkpoint JSONL → ``{task_id: {verdict, model, rationale, tier}}``."""
    entries: dict[str, dict] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entry = json.loads(line)
                    entries[entry["task_id"]] = entry
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load checkpoint %s: %s", path, exc)
    return entries


def _append_checkpoint(
    fh: object, task_id: str, result: CurationResult, tier: int
) -> None:
    """Append a single result to the open checkpoint file handle."""
    line = json.dumps(
        {
            "task_id": task_id,
            "tier": tier,
            "verdict": result.verdict,
            "model": result.model,
            "rationale": result.rationale,
        }
    )
    fh.write(line + "\n")  # type: ignore[union-attr]
    fh.flush()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Async curation engine
# ---------------------------------------------------------------------------


def _is_permanent_error(exc: Exception) -> bool:
    """Check if an API error indicates a permanent failure (e.g. billing)."""
    msg = str(exc).lower()
    return any(p in msg for p in _PERMANENT_ERROR_PATTERNS)


async def _curate_one(
    challenge: EvalChallenge,
    client: AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
    abort: asyncio.Event | None = None,
) -> CurationResult:
    """Send a single challenge to the LLM and return a CurationResult.

    Catches API errors and returns ``verdict="error"`` instead of raising,
    so a single failure doesn't crash the entire batch.  If *abort* is set
    (e.g. after a permanent billing error), returns immediately.
    """
    if abort and abort.is_set():
        return CurationResult(
            verdict="error",
            model=model,
            rationale="Skipped: pipeline aborted due to permanent API error",
        )

    prompt = _build_curation_prompt(challenge)

    try:
        async with semaphore:
            # Re-check after acquiring semaphore (may have been set while waiting)
            if abort and abort.is_set():
                return CurationResult(
                    verdict="error",
                    model=model,
                    rationale="Skipped: pipeline aborted due to permanent API error",
                )
            response = await client.messages.create(
                model=model,
                max_tokens=150,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
    except Exception as exc:
        if _is_permanent_error(exc):
            logger.error("Permanent API error, aborting remaining tasks: %s", exc)
            if abort:
                abort.set()
        else:
            logger.warning("API error for %s: %s", challenge.task_id, exc)
        return CurationResult(
            verdict="error",
            model=model,
            rationale=f"API error: {exc}",
        )

    text = response.content[0].text if response.content else ""
    verdict, rationale = _parse_response(text)
    return CurationResult(verdict=verdict, model=model, rationale=rationale)


async def curate_challenges(
    challenges: list[EvalChallenge],
    *,
    tier1_model: str = DEFAULT_TIER1_MODEL,
    tier2_model: str = DEFAULT_TIER2_MODEL,
    max_concurrent: int = 10,
    checkpoint_path: Path | None = None,
) -> list[CurationResult]:
    """Run tiered LLM curation on a list of challenges.

    Tier 1 (cheap model) processes all challenges concurrently.  Any that
    return DEFER are escalated to tier 2 (stronger model).  DEFER from
    tier 2 becomes ``"borderline"`` in the final result.

    If *checkpoint_path* is given, results are written to a JSONL checkpoint
    file as each completes.  On restart with the same path, completed entries
    are loaded and their challenges skipped — pass the same checkpoint to
    resume an interrupted run.
    """
    if not challenges:
        return []

    client = AsyncAnthropic(max_retries=5)
    sem = asyncio.Semaphore(max_concurrent)
    abort = asyncio.Event()

    # --- Load checkpoint if present ---
    checkpoint: dict[str, dict] = {}
    if checkpoint_path and checkpoint_path.exists():
        checkpoint = _load_checkpoint(checkpoint_path)
        if checkpoint:
            logger.info("Loaded checkpoint with %d entries", len(checkpoint))

    # Pre-fill results from checkpoint; identify what still needs work
    results: list[CurationResult | None] = [None] * len(challenges)
    todo_tier1: list[tuple[int, EvalChallenge]] = []

    for i, c in enumerate(challenges):
        cp = checkpoint.get(c.task_id)
        if cp is None or cp["verdict"] == "error":
            todo_tier1.append((i, c))
        elif cp["verdict"] == "defer" and cp.get("tier") == 1:
            # Tier 1 deferred — pre-fill so tier 2 picks it up below
            results[i] = CurationResult(
                verdict=cp["verdict"], model=cp["model"], rationale=cp["rationale"]
            )
        else:
            # Final result from checkpoint (accept/reject/borderline)
            results[i] = CurationResult(
                verdict=cp["verdict"], model=cp["model"], rationale=cp["rationale"]
            )

    # --- Open checkpoint for appending ---
    checkpoint_fh = None
    if checkpoint_path:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_fh = open(checkpoint_path, "a")  # noqa: SIM115

    try:
        # --- Tier 1 ---
        if todo_tier1:
            from_cp = len(challenges) - len(todo_tier1)
            logger.info(
                "Tier-1 curation: %d challenges with %s%s",
                len(todo_tier1),
                tier1_model,
                f" ({from_cp} loaded from checkpoint)" if from_cp else "",
            )

            async def _tier1_task(idx: int, ch: EvalChallenge) -> None:
                result = await _curate_one(ch, client, tier1_model, sem, abort)
                results[idx] = result
                if checkpoint_fh:
                    _append_checkpoint(checkpoint_fh, ch.task_id, result, tier=1)

            await asyncio.gather(*[_tier1_task(i, c) for i, c in todo_tier1])
        else:
            logger.info(
                "Tier-1: all %d challenges loaded from checkpoint",
                len(challenges),
            )

        # --- Tier 2: escalate DEFERs ---
        deferred = [
            (i, challenges[i])
            for i, r in enumerate(results)
            if r is not None and r.verdict == "defer"
        ]

        if deferred and not abort.is_set():
            logger.info(
                "Tier-2 escalation: %d deferred challenges with %s",
                len(deferred),
                tier2_model,
            )

            async def _tier2_task(idx: int, ch: EvalChallenge) -> None:
                result = await _curate_one(ch, client, tier2_model, sem, abort)
                if result.verdict == "defer":
                    result = CurationResult(
                        verdict="borderline",
                        model=result.model,
                        rationale=result.rationale,
                    )
                results[idx] = result
                if checkpoint_fh:
                    _append_checkpoint(checkpoint_fh, ch.task_id, result, tier=2)

            await asyncio.gather(*[_tier2_task(i, c) for i, c in deferred])
        elif deferred and abort.is_set():
            logger.warning(
                "Skipping tier-2 for %d deferred challenges (pipeline aborted)",
                len(deferred),
            )
    finally:
        if checkpoint_fh:
            checkpoint_fh.close()

    # --- Assemble final results ---
    final_results: list[CurationResult] = []
    for r in results:
        if r is None:
            r = CurationResult(
                verdict="error", model="unknown", rationale="No result produced"
            )
        final_results.append(r)

    accept_n = sum(1 for r in final_results if r.verdict == "accept")
    reject_n = sum(1 for r in final_results if r.verdict == "reject")
    border_n = sum(1 for r in final_results if r.verdict == "borderline")
    error_n = sum(1 for r in final_results if r.verdict == "error")
    logger.info(
        "Curation complete: %d accepted, %d rejected, %d borderline, %d errors",
        accept_n,
        reject_n,
        border_n,
        error_n,
    )
    if error_n:
        logger.warning(
            "%d challenges had errors — re-run with same checkpoint to retry",
            error_n,
        )
    return final_results


def curate_challenges_sync(
    challenges: list[EvalChallenge],
    *,
    tier1_model: str = DEFAULT_TIER1_MODEL,
    tier2_model: str = DEFAULT_TIER2_MODEL,
    max_concurrent: int = 10,
    checkpoint_path: Path | None = None,
) -> list[CurationResult]:
    """Synchronous wrapper around :func:`curate_challenges`."""
    return asyncio.run(
        curate_challenges(
            challenges,
            tier1_model=tier1_model,
            tier2_model=tier2_model,
            max_concurrent=max_concurrent,
            checkpoint_path=checkpoint_path,
        )
    )


# ---------------------------------------------------------------------------
# Post-curation annotation + filtering
# ---------------------------------------------------------------------------


def apply_curation(
    challenges: list[EvalChallenge],
    results: list[CurationResult],
) -> tuple[list[EvalChallenge], list[EvalChallenge], list[EvalChallenge]]:
    """Annotate challenges with curation fields and partition by verdict.

    Returns ``(accepted, rejected, borderline)``.  Rejected challenges are
    excluded from the materialized dataset; borderline challenges are included
    with their annotation so downstream consumers can filter further.
    """
    if len(challenges) != len(results):
        raise ValueError(
            f"challenges ({len(challenges)}) and results ({len(results)}) must have same length"
        )

    accepted: list[EvalChallenge] = []
    rejected: list[EvalChallenge] = []
    borderline: list[EvalChallenge] = []

    for challenge, result in zip(challenges, results):
        annotated = challenge.model_copy(
            update={
                "curation_verdict": result.verdict,
                "curation_model": result.model,
                "curation_rationale": result.rationale,
            }
        )
        if result.verdict == "accept":
            accepted.append(annotated)
        elif result.verdict == "reject":
            rejected.append(annotated)
        else:
            borderline.append(annotated)

    return accepted, rejected, borderline
