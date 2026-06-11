"""LLM-based curation pipeline for filtering low-quality mined challenges.

Tiered approach: a cheap model (Haiku) scores each challenge on a 0-100
rejection-confidence scale.  Scores below `accept_threshold` are auto-accepted,
scores above `reject_threshold` are auto-rejected, and scores in between are
escalated to a stronger model (Sonnet) for a categorical verdict.  DEFER from
the stronger model becomes ``"borderline"`` in the final output.

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

from scaffold.model_roles import DEFAULT_CHEAP_MODEL, DEFAULT_MID_MODEL
from scaffold.models import EvalChallenge

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIER1_MODEL = DEFAULT_CHEAP_MODEL
DEFAULT_TIER2_MODEL = DEFAULT_MID_MODEL
DEFAULT_ACCEPT_THRESHOLD = 20
DEFAULT_REJECT_THRESHOLD = 85
_MAX_DIFF_CHARS = 8000

# Substrings in API error messages that indicate a permanent failure —
# no point retrying or continuing with remaining tasks.
_PERMANENT_ERROR_PATTERNS = ["credit balance", "billing"]

# Tier 1 prompt: asks for a numeric rejection-confidence score (0-100).
# This produces a continuous signal that we threshold externally, giving
# us mechanical control over the defer zone — unlike categorical DEFER
# which Haiku ignores entirely.
_TIER1_SYSTEM_PROMPT = """\
You are a quality curator for proof engineering benchmark challenges mined \
from the git history of a formally verified codebase. Decide whether each \
challenge belongs in an evaluation dataset for AI proof synthesis.

DEFAULT TO ACCEPT. In verified codebases like CompCert, seL4, or fiat-crypto, \
most changes to proof files (.v, .thy, .lean) are substantive because \
definitions, types, and specifications carry proof obligations.

The following are NOT substantive (reject-worthy):
- Whitespace, formatting, or indentation changes
- Comment or documentation changes
- Adding, removing, or modifying Import/Require lines with no other changes
- License, copyright, or boilerplate header changes
- Deletion of truly inert code (e.g. removing an unused Import or deleting \
a comment). IMPORTANT: deleting a Definition, Lemma, Theorem, Fixpoint, \
Instance, or proof body is NOT "inert code deletion" — these carry proof \
obligations and their removal is substantive proof engineering.

The following ARE substantive (accept-worthy):
- Any change inside a proof body (between Proof. and Qed./Defined.), \
including mechanical tactic updates like omega->lia
- New, modified, or deleted Definition, Fixpoint, Lemma, Theorem, or Instance
- New or modified Parameter, Hypothesis, Axiom, or Variable declarations
- New constructors added to inductive types or new match arms
- Type, signature, or specification changes — including universe changes \
like Set->Type or Prop->Type in Record, Variable, or Module Type declarations
- Changes to Instance or Hint visibility (e.g. Instance -> Global Instance, \
Hint Resolve -> Global Hint Resolve)
- Changes to Arguments or Implicit declarations
- Renamed or replaced identifiers inside definition or axiom bodies \
(e.g. Psucc->Pos.succ, Zmax->Z.max, beq_nat->Nat.eqb) — these are \
API migrations that change the proof engineering task
- Notation, scope, or binding changes (Declare Scope, Bind Scope, \
Notation binder kinds)
- Extraction directives (Extract Constant, Extract Inductive)
- A new file containing any definitions, lemmas, or Proof./Qed. blocks

Rate the challenge on a scale from 0 to 100, where:
- 0 = definitely should be ACCEPTED (clearly substantive proof engineering)
- 100 = definitely should be REJECTED (clearly non-substantive)

Respond with exactly two lines:
SCORE: <integer 0-100>
RATIONALE: <one sentence explaining your assessment>"""

# Tier 2 prompt: categorical verdict for Sonnet on deferred challenges.
# Sonnet is capable of categorical reasoning, so we use the same detailed
# criteria but ask for ACCEPT/REJECT/DEFER directly.
_TIER2_SYSTEM_PROMPT = """\
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
- Deletion of truly inert code (e.g. removing an unused Import or deleting \
a comment). IMPORTANT: deleting a Definition, Lemma, Theorem, Fixpoint, \
Instance, or proof body is NOT "inert code deletion" — these carry proof \
obligations and their removal is substantive proof engineering.

ACCEPT if the diff contains ANY of the following, regardless of how small:
- Any change inside a proof body (between Proof. and Qed./Defined.), \
including mechanical tactic updates like omega->lia — tactic selection is \
part of the proof engineering task
- New, modified, or deleted Definition, Fixpoint, Lemma, Theorem, or Instance
- New or modified Parameter, Hypothesis, Axiom, or Variable declarations
- New constructors added to inductive types or new match arms
- Type, signature, or specification changes — including universe changes \
like Set->Type or Prop->Type in Record, Variable, or Module Type declarations
- Changes to Instance or Hint visibility (e.g. Instance -> Global Instance, \
Hint Resolve -> Global Hint Resolve)
- Changes to Arguments or Implicit declarations
- Renamed or replaced identifiers inside definition or axiom bodies \
(e.g. Psucc->Pos.succ, Zmax->Z.max, beq_nat->Nat.eqb)
- Notation, scope, or binding changes (Declare Scope, Bind Scope, \
Notation binder kinds)
- Extraction directives (Extract Constant, Extract Inductive)
- A new file containing any definitions, lemmas, or Proof./Qed. blocks

When in doubt, ACCEPT. The dataset can tolerate a few marginal challenges \
but should not lose genuine proof engineering work.

Respond with exactly two lines:
VERDICT: ACCEPT | REJECT | DEFER
RATIONALE: <one sentence explaining your decision>

Before choosing REJECT, double-check: does the diff touch ANY Definition, \
Lemma, Theorem, Instance, Hint, Parameter, proof body, type annotation, \
identifier rename, or specification? If so, ACCEPT. If you are not sure, \
use DEFER."""


# ---------------------------------------------------------------------------
# Calibrated-prompt assembly
# ---------------------------------------------------------------------------
#
# The calibration loop (scaffold/calibrate.py) writes a single *criteria body*
# per repo — the role framing plus accept/reject criteria, without any output
# format instructions.  The two builders below append the tier-specific output
# format, so the tier-1 and tier-2 prompts always share identical criteria.

TIER1_OUTPUT_FORMAT = """\
Rate the challenge on a scale from 0 to 100, where:
- 0 = definitely should be ACCEPTED (clearly substantive proof engineering)
- 100 = definitely should be REJECTED (clearly non-substantive)

Respond with exactly two lines:
SCORE: <integer 0-100>
RATIONALE: <one sentence explaining your assessment>"""

TIER2_OUTPUT_FORMAT = """\
Respond with exactly two lines:
VERDICT: ACCEPT | REJECT | DEFER
RATIONALE: <one sentence explaining your decision>

Use DEFER only when you genuinely cannot tell whether the challenge is \
substantive."""


def build_tier1_prompt(criteria_body: str) -> str:
    """Render a tier-1 (numeric scoring) system prompt from a criteria body."""
    return f"{criteria_body.rstrip()}\n\n{TIER1_OUTPUT_FORMAT}"


def build_tier2_prompt(criteria_body: str) -> str:
    """Render a tier-2 (categorical verdict) system prompt from a criteria body."""
    return f"{criteria_body.rstrip()}\n\n{TIER2_OUTPUT_FORMAT}"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class CurationResult(BaseModel):
    """Result of curating a single challenge."""

    verdict: str  # "accept" | "reject" | "defer" | "borderline" | "error"
    model: str
    rationale: str
    score: float | None = None  # tier-1 rejection confidence (0-100)


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


_SCORE_RE = re.compile(r"SCORE:\s*(\d+)", re.IGNORECASE)
_VERDICT_RE = re.compile(r"VERDICT:\s*(ACCEPT|REJECT|DEFER)", re.IGNORECASE)
_RATIONALE_RE = re.compile(r"RATIONALE:\s*(.+)", re.IGNORECASE)


def _parse_score_response(text: str) -> tuple[float, str]:
    """Extract ``(score, rationale)`` from a tier-1 numeric response.

    Returns ``(50.0, ...)`` if parsing fails — placing the challenge in the
    defer zone so it escalates to the stronger model.
    """
    score_match = _SCORE_RE.search(text)
    rationale_match = _RATIONALE_RE.search(text)

    score = float(score_match.group(1)) if score_match else 50.0
    score = max(0.0, min(100.0, score))

    rationale = (
        rationale_match.group(1).strip()
        if rationale_match
        else text.strip()[:200] or "Could not parse curation response"
    )
    return score, rationale


def _score_to_verdict(
    score: float,
    accept_threshold: int = DEFAULT_ACCEPT_THRESHOLD,
    reject_threshold: int = DEFAULT_REJECT_THRESHOLD,
) -> str:
    """Convert a numeric rejection-confidence score to a verdict.

    - score <= accept_threshold → ``"accept"``
    - score >= reject_threshold → ``"reject"``
    - otherwise → ``"defer"``
    """
    if score <= accept_threshold:
        return "accept"
    if score >= reject_threshold:
        return "reject"
    return "defer"


def _parse_response(text: str) -> tuple[str, str]:
    """Extract ``(verdict, rationale)`` from a tier-2 categorical response.

    Returns ``("defer", ...)`` if parsing fails — the challenge will be
    marked as borderline rather than silently dropped.
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
    entry: dict = {
        "task_id": task_id,
        "tier": tier,
        "verdict": result.verdict,
        "model": result.model,
        "rationale": result.rationale,
    }
    if result.score is not None:
        entry["score"] = result.score
    line = json.dumps(entry)
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
    system_prompt: str,
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
            # Cache the curation system prompt across the batch. NB: the tier-1
            # (~600-token) and tier-2 prompts sit below the per-model cacheable
            # minimum (Haiku 4096, Sonnet 2048 tokens), so this is a no-op on the
            # built-in prompts and won't show cache reads. It starts paying off
            # only if a calibrated criteria body grows past the threshold; the
            # varying per-challenge diff is the user message, so the system
            # prompt is the one stable prefix worth marking.
            response = await client.messages.create(
                model=model,
                max_tokens=150,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                cache_control={"type": "ephemeral"},
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

    return CurationResult(
        verdict="pending",  # caller applies thresholds or parses verdict
        model=model,
        rationale=response.content[0].text if response.content else "",
    )


async def curate_challenges(
    challenges: list[EvalChallenge],
    *,
    tier1_model: str = DEFAULT_TIER1_MODEL,
    tier2_model: str = DEFAULT_TIER2_MODEL,
    accept_threshold: int = DEFAULT_ACCEPT_THRESHOLD,
    reject_threshold: int = DEFAULT_REJECT_THRESHOLD,
    max_concurrent: int = 10,
    checkpoint_path: Path | None = None,
    tier1_system_prompt: str = _TIER1_SYSTEM_PROMPT,
    tier2_system_prompt: str = _TIER2_SYSTEM_PROMPT,
) -> list[CurationResult]:
    """Run tiered LLM curation on a list of challenges.

    Tier 1 (cheap model) scores all challenges on a 0-100 rejection-confidence
    scale.  Scores <= *accept_threshold* are auto-accepted, scores >=
    *reject_threshold* are auto-rejected, and scores in between are escalated
    to tier 2 (stronger model) for a categorical ACCEPT/REJECT/DEFER verdict.
    DEFER from tier 2 becomes ``"borderline"`` in the final result.

    If *checkpoint_path* is given, results are written to a JSONL checkpoint
    file as each completes.  On restart with the same path, completed entries
    are loaded and their challenges skipped — pass the same checkpoint to
    resume an interrupted run.

    The system prompts default to the universal prompts above; pass
    *tier1_system_prompt* / *tier2_system_prompt* to use repo-calibrated
    prompts (see scaffold/calibrate.py and build_tier{1,2}_prompt).
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
                verdict=cp["verdict"],
                model=cp["model"],
                rationale=cp["rationale"],
                score=cp.get("score"),
            )
        else:
            # Final result from checkpoint (accept/reject/borderline)
            results[i] = CurationResult(
                verdict=cp["verdict"],
                model=cp["model"],
                rationale=cp["rationale"],
                score=cp.get("score"),
            )

    # --- Open checkpoint for appending ---
    checkpoint_fh = None
    if checkpoint_path:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_fh = open(checkpoint_path, "a")  # noqa: SIM115

    try:
        # --- Tier 1: numeric scoring ---
        if todo_tier1:
            from_cp = len(challenges) - len(todo_tier1)
            logger.info(
                "Tier-1 scoring: %d challenges with %s (thresholds: accept<=%d, reject>=%d)%s",
                len(todo_tier1),
                tier1_model,
                accept_threshold,
                reject_threshold,
                f" ({from_cp} loaded from checkpoint)" if from_cp else "",
            )

            async def _tier1_task(idx: int, ch: EvalChallenge) -> None:
                raw = await _curate_one(
                    ch, client, tier1_model, sem, tier1_system_prompt, abort
                )
                if raw.verdict == "error":
                    results[idx] = raw
                else:
                    score, rationale = _parse_score_response(raw.rationale)
                    verdict = _score_to_verdict(
                        score, accept_threshold, reject_threshold
                    )
                    results[idx] = CurationResult(
                        verdict=verdict,
                        model=raw.model,
                        rationale=rationale,
                        score=score,
                    )
                if checkpoint_fh and results[idx] is not None:
                    _append_checkpoint(
                        checkpoint_fh,
                        ch.task_id,
                        results[idx],
                        tier=1,  # type: ignore[arg-type]
                    )

            await asyncio.gather(*[_tier1_task(i, c) for i, c in todo_tier1])
        else:
            logger.info(
                "Tier-1: all %d challenges loaded from checkpoint",
                len(challenges),
            )

        # --- Tier 2: escalate DEFERs with categorical prompt ---
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
                raw = await _curate_one(
                    ch, client, tier2_model, sem, tier2_system_prompt, abort
                )
                if raw.verdict == "error":
                    results[idx] = raw
                else:
                    verdict, rationale = _parse_response(raw.rationale)
                    if verdict == "defer":
                        verdict = "borderline"
                    # Preserve the tier-1 score on the result
                    tier1_score = (
                        results[idx].score if results[idx] is not None else None
                    )
                    results[idx] = CurationResult(
                        verdict=verdict,
                        model=raw.model,
                        rationale=rationale,
                        score=tier1_score,
                    )
                if checkpoint_fh and results[idx] is not None:
                    _append_checkpoint(
                        checkpoint_fh,
                        ch.task_id,
                        results[idx],
                        tier=2,  # type: ignore[arg-type]
                    )

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
    defer_n = sum(1 for r in final_results if r.verdict == "defer")
    error_n = sum(1 for r in final_results if r.verdict == "error")
    logger.info(
        "Curation complete: %d accepted, %d rejected, %d borderline, %d errors"
        + (f" ({defer_n} still deferred)" if defer_n else ""),
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
    accept_threshold: int = DEFAULT_ACCEPT_THRESHOLD,
    reject_threshold: int = DEFAULT_REJECT_THRESHOLD,
    max_concurrent: int = 10,
    checkpoint_path: Path | None = None,
    tier1_system_prompt: str = _TIER1_SYSTEM_PROMPT,
    tier2_system_prompt: str = _TIER2_SYSTEM_PROMPT,
) -> list[CurationResult]:
    """Synchronous wrapper around :func:`curate_challenges`."""
    return asyncio.run(
        curate_challenges(
            challenges,
            tier1_model=tier1_model,
            tier2_model=tier2_model,
            accept_threshold=accept_threshold,
            reject_threshold=reject_threshold,
            max_concurrent=max_concurrent,
            checkpoint_path=checkpoint_path,
            tier1_system_prompt=tier1_system_prompt,
            tier2_system_prompt=tier2_system_prompt,
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
