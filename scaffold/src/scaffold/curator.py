"""LLM-based curation pipeline for filtering low-quality mined challenges.

Tiered approach: a cheap model (Haiku) handles the obvious accept/reject calls;
challenges it marks DEFER are escalated to a stronger model (Sonnet).  DEFER
from the stronger model becomes ``"borderline"`` in the final output.

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
import logging
import re

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

    verdict: str  # "accept" | "reject" | "defer" | "borderline"
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
# Async curation engine
# ---------------------------------------------------------------------------


async def _curate_one(
    challenge: EvalChallenge,
    client: AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
) -> CurationResult:
    """Send a single challenge to the LLM and return a CurationResult."""
    prompt = _build_curation_prompt(challenge)

    async with semaphore:
        response = await client.messages.create(
            model=model,
            max_tokens=150,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
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
) -> list[CurationResult]:
    """Run tiered LLM curation on a list of challenges.

    Tier 1 (cheap model) processes all challenges concurrently.  Any that
    return DEFER are escalated to tier 2 (stronger model).  DEFER from
    tier 2 becomes ``"borderline"`` in the final result.
    """
    if not challenges:
        return []

    client = AsyncAnthropic(max_retries=5)
    sem = asyncio.Semaphore(max_concurrent)

    # --- Tier 1 ---
    logger.info("Tier-1 curation: %d challenges with %s", len(challenges), tier1_model)
    tier1_tasks = [_curate_one(c, client, tier1_model, sem) for c in challenges]
    results: list[CurationResult] = list(await asyncio.gather(*tier1_tasks))

    # --- Tier 2: escalate DEFERs ---
    deferred = [
        (i, challenges[i]) for i, r in enumerate(results) if r.verdict == "defer"
    ]
    if deferred:
        logger.info(
            "Tier-2 escalation: %d deferred challenges with %s",
            len(deferred),
            tier2_model,
        )
        tier2_tasks = [_curate_one(ch, client, tier2_model, sem) for _, ch in deferred]
        tier2_results = await asyncio.gather(*tier2_tasks)
        for (idx, _), t2r in zip(deferred, tier2_results):
            # DEFER from tier-2 becomes "borderline" (no further escalation)
            if t2r.verdict == "defer":
                t2r = CurationResult(
                    verdict="borderline", model=t2r.model, rationale=t2r.rationale
                )
            results[idx] = t2r

    accept_n = sum(1 for r in results if r.verdict == "accept")
    reject_n = sum(1 for r in results if r.verdict == "reject")
    border_n = sum(1 for r in results if r.verdict == "borderline")
    logger.info(
        "Curation complete: %d accepted, %d rejected, %d borderline",
        accept_n,
        reject_n,
        border_n,
    )
    return results


def curate_challenges_sync(
    challenges: list[EvalChallenge],
    *,
    tier1_model: str = DEFAULT_TIER1_MODEL,
    tier2_model: str = DEFAULT_TIER2_MODEL,
    max_concurrent: int = 10,
) -> list[CurationResult]:
    """Synchronous wrapper around :func:`curate_challenges`."""
    return asyncio.run(
        curate_challenges(
            challenges,
            tier1_model=tier1_model,
            tier2_model=tier2_model,
            max_concurrent=max_concurrent,
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
