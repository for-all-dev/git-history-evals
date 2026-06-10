"""Per-repo calibration of the curation prompt (issue #84).

The universal curation prompt in curator.py was hand-tuned on CompCert and
generalised poorly to fiat-crypto (~25% false-accept rate). This module
automates the manual tune-measure loop that produced the CompCert prompt, so
any repo gets its own calibrated prompt before the full curation run:

1. **Sample** — draw fresh challenges each iteration (500 uniform on the
   first; 200 uniform + 100 oversampled near the current prompt's defer zone
   afterwards). Re-randomising every iteration is the anti-overfitting
   mechanism; the oversampled tail concentrates labeling where the prompt is
   uncertain.
2. **Label** — the *decision* model (Opus-class) labels each sampled
   challenge accept/reject in independent, checkpointed calls. Labels are
   binary and accumulate across iterations.
3. **Evaluate** — run the tier-1 *cheap* model with each candidate criteria
   body over the sample, escalate the mid-zone to the *mid* model, then sweep
   accept/reject thresholds mechanically: minimise misclassifications on the
   uniform subset subject to a hard defer-rate cap. Unbiased rates come from
   the uniform subset only.
4. **Reflect** — a single *persistent* decision-model conversation (the
   "writer") sees every iteration's metrics and failure cases, plus the repo
   profile, and either declares convergence or emits up to three full
   replacement criteria bodies. The best 2-3 candidates survive between
   iterations and are re-evaluated on each fresh sample.
5. **Select** — after the loop, finalists are compared on the union of all
   labeled samples and the winner's criteria body + tuned thresholds are
   written as dataset-bundle artifacts (see write_artifacts).

The expensive state (ground-truth labels, tier-1 scores, tier-2 verdicts) is
checkpointed to a state directory; re-running after an interruption reuses it.
The writer conversation itself restarts fresh on a re-run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlock
from pydantic import BaseModel, Field

from scaffold.curator import (
    _build_curation_prompt,
    _parse_response,
    _parse_score_response,
    build_tier1_prompt,
    build_tier2_prompt,
)
from scaffold.model_roles import ModelRoles
from scaffold.models import EvalChallenge
from scaffold.profile import RepoProfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Threshold grids swept mechanically per evaluation. Tier-2 verdicts are
# pre-computed for every challenge whose score falls strictly inside the
# widest possible defer zone (min(ACCEPT_GRID), max(REJECT_GRID)), so the
# sweep can resolve every grid point exactly without further API calls.
ACCEPT_GRID: tuple[int, ...] = tuple(range(5, 45, 5))
REJECT_GRID: tuple[int, ...] = tuple(range(60, 100, 5))

_LABELER_MAX_TOKENS = 1500
_WRITER_MAX_TOKENS = 16000
_SCORER_MAX_TOKENS = 150
_FAILURE_CASE_CAP = 40
_FAILURE_DIFF_CHARS = 1500

# Universal core criteria: the repo-agnostic skeleton every calibrated prompt
# starts from. Deliberately free of Coq-specific bullet lists — those belong
# in the repo-specific section the writer drafts from the profile.
UNIVERSAL_CORE_CRITERIA = """\
You are a quality curator for proof engineering benchmark challenges mined \
from the git history of a formally verified codebase. Decide whether each \
challenge belongs in an evaluation dataset for AI proof synthesis.

DEFAULT TO ACCEPT. In formally verified codebases, most changes to proof and \
specification files are substantive, because definitions, types, and \
specifications carry proof obligations.

The following are NOT substantive (reject-worthy):
- Whitespace, formatting, or indentation changes
- Comment or documentation changes
- Import/module-inclusion changes with no other content
- License, copyright, or boilerplate header changes
- Auto-generated or mechanically derived file contents
- Deletion of truly inert code (e.g. an unused import or a comment). \
Deleting a definition, lemma, theorem, or proof body is NOT inert — these \
carry proof obligations and their removal is substantive proof engineering.

The following ARE substantive (accept-worthy):
- Any change inside a proof body, however mechanical
- New, modified, or deleted definitions, lemmas, theorems, or type-class \
instances
- Type, signature, statement, or specification changes
- Renamed or replaced identifiers inside definition bodies (API migrations \
change the proof engineering task)
- A new file containing any definitions, lemmas, or proofs"""

_LABELER_SYSTEM_TEMPLATE = """\
You are creating ground-truth quality labels for proof engineering benchmark \
challenges mined from the git history of {repo}, a formally verified codebase \
({proof_assistant}).

Each challenge presents the repository state before a commit; the commit's \
diff is the ground-truth solution. A challenge is VALUABLE when reproducing \
the change requires genuine proof engineering: writing or repairing proofs, \
adding or modifying definitions, lemmas, theorems, or specifications, or \
adapting proofs to changed statements. A challenge is NOT valuable when the \
change carries no proof obligations: cosmetic edits (whitespace, comments, \
formatting), import-only or license-header changes, auto-generated or \
mechanically derived boilerplate, or deletion of truly inert code.

Repo-specific context from automated profiling:
{notes}

Analyze the challenge carefully — the diff, the commit message, and the file \
path — then commit to a binary judgment. There is no abstain option; pick the \
better-supported label.

End your response with exactly two lines:
LABEL: ACCEPT | REJECT
RATIONALE: <one sentence>"""

_WRITER_SYSTEM_TEMPLATE = """\
You are calibrating the curation prompt of a two-tier LLM pipeline that \
filters mined proof engineering benchmark challenges for {repo}.

How the pipeline works: a cheap tier-1 model ({cheap}) reads a criteria \
prompt and scores each challenge 0-100 (0 = clearly substantive, accept; \
100 = clearly non-substantive, reject). Scores at or below an accept \
threshold auto-accept; at or above a reject threshold auto-reject; scores in \
between escalate to a mid-tier model ({mid}) that applies the same criteria \
and returns a categorical verdict. After every evaluation the thresholds are \
re-tuned mechanically to minimise errors subject to a hard cap of \
{defer_cap:.0%} on the escalation rate — escalation is far more expensive, so \
a prompt that can only be accurate by deferring often will lose.

Your job is to write the shared *criteria body*: the role framing plus \
accept/reject criteria that both tiers read. Output-format instructions \
(score lines, verdict lines) are appended mechanically — NEVER include them.

You will see evaluation results against ground-truth labels produced \
independently by a stronger model, including the specific failing challenges. \
The evaluation sample is re-randomised every iteration, so chase recurring \
patterns, not individual cases. A well-calibrated prompt typically reaches \
roughly 5% false-accept and false-reject rates or better; treat that as a \
baseline to aim for, but use your judgment — some repos have genuinely \
ambiguous challenges, and pushing past the noise floor just overfits.

Remember the tier-1 model is small: concrete, recognisable patterns \
(file-name conventions, code shapes, commit-message idioms) outperform \
abstract principles.

Tag contract (text outside tags is treated as your analysis and ignored):
- When asked to draft the repo-specific section, reply with the section \
inside <repo_section>...</repo_section> tags.
- When asked for variants, reply with up to {n_variants} candidate criteria \
bodies, each inside its own <variant>...</variant> block. Each variant must \
be a COMPLETE standalone criteria body — it replaces the current prompt \
wholesale, so include everything it needs.
- If you judge the calibration converged — further changes would chase noise \
or overfit the samples — reply instead with <converged>a short \
justification</converged>."""

_REPO_SECTION_RE = re.compile(
    r"<repo_section(?:\s[^>]*)?>(.*?)</repo_section>", re.DOTALL | re.IGNORECASE
)
_VARIANT_RE = re.compile(
    r"<variant(?:\s[^>]*)?>(.*?)</variant>", re.DOTALL | re.IGNORECASE
)
_CONVERGED_RE = re.compile(
    r"<converged(?:\s[^>]*)?>(.*?)</converged>", re.DOTALL | re.IGNORECASE
)
_LABEL_RE = re.compile(r"LABEL:\s*(ACCEPT|REJECT)", re.IGNORECASE)
_LABEL_RATIONALE_RE = re.compile(r"RATIONALE:\s*(.+)", re.IGNORECASE)


class CalibrationError(RuntimeError):
    """Raised when the calibration loop cannot proceed."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class CalibrationConfig(BaseModel):
    """Tunable knobs for the calibration loop."""

    max_iterations: int = 5
    first_sample_size: int = 500
    uniform_per_iteration: int = 200
    oversample_per_iteration: int = 100
    oversample_pool_size: int = 800
    n_variants: int = 3
    pool_size: int = 3
    defer_cap: float = 0.10
    max_concurrent: int = 10
    label_max_concurrent: int = 4
    seed: int = 0


class GroundTruthLabel(BaseModel):
    """A binary ground-truth judgment for one challenge."""

    task_id: str
    label: str  # "accept" | "reject"
    rationale: str
    model: str
    provenance: str  # "uniform" | "oversampled"


class PromptEvaluation(BaseModel):
    """Metrics for one criteria body on one labeled sample.

    Rates are computed on the uniform subset only (the oversampled tail is
    deliberately biased toward the defer zone). ``false_accept_rate`` is
    P(ground truth = reject | pipeline accepted), ``false_reject_rate`` is
    P(ground truth = accept | pipeline rejected) — matching how quality was
    measured manually in PRs #82/#83. ``None`` means the denominator was 0.
    """

    prompt_id: str
    accept_threshold: int
    reject_threshold: int
    n: int
    n_uniform: int
    n_misclassified: int
    misclassification_rate: float | None
    false_accept_rate: float | None
    false_reject_rate: float | None
    defer_rate: float | None
    meets_defer_cap: bool
    n_errors: int = 0


class IterationRecord(BaseModel):
    """Everything that happened in one calibration iteration."""

    iteration: int
    sample_uniform: list[str]
    sample_oversampled: list[str]
    evaluations: list[PromptEvaluation]
    chosen_prompt_id: str
    pool: list[str]
    writer_action: str  # "variants" | "converged" | "exhausted"
    writer_note: str = ""


class CalibrationReport(BaseModel):
    """Full record of a calibration run — stored as calibration.json."""

    repo: str
    created_at: str
    models: ModelRoles
    config: CalibrationConfig
    iterations: list[IterationRecord] = Field(default_factory=list)
    final_evaluations: list[PromptEvaluation] = Field(default_factory=list)
    winner_prompt_id: str = ""
    accept_threshold: int = 0
    reject_threshold: int = 0
    n_labeled: int = 0
    n_label_accept: int = 0
    n_label_reject: int = 0
    prompts: dict[str, str] = Field(default_factory=dict)
    prompt_origins: dict[str, str] = Field(default_factory=dict)
    api_calls: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Threshold sweep (pure, unit-testable)
# ---------------------------------------------------------------------------


def _resolve_verdict(
    score: float,
    accept_threshold: int,
    reject_threshold: int,
    tier2_verdict: str | None,
) -> str:
    """Final pipeline verdict for one challenge at the given thresholds.

    Tier-2 DEFER becomes "borderline", which is included in the materialized
    dataset, so it counts as an accept for error purposes.
    """
    if score <= accept_threshold:
        return "accept"
    if score >= reject_threshold:
        return "reject"
    if tier2_verdict in ("accept", "reject"):
        return tier2_verdict
    return "accept"  # defer/borderline/missing → included in dataset


class SweepResult(BaseModel):
    """Outcome of a mechanical threshold sweep for one prompt."""

    accept_threshold: int
    reject_threshold: int
    n_misclassified: int
    defer_rate: float | None
    meets_defer_cap: bool


def sweep_thresholds(
    scores: dict[str, float],
    tier2_verdicts: dict[str, str],
    labels: dict[str, str],
    uniform_ids: list[str],
    *,
    defer_cap: float = 0.10,
    accept_grid: tuple[int, ...] = ACCEPT_GRID,
    reject_grid: tuple[int, ...] = REJECT_GRID,
) -> SweepResult:
    """Pick thresholds minimising misclassifications subject to the defer cap.

    Only challenges present in both *scores* and *labels* and listed in
    *uniform_ids* contribute (oversampled challenges are biased, so they don't
    drive threshold choice). If no grid point satisfies the cap, the pair with
    the lowest defer rate (then fewest errors) is returned with
    ``meets_defer_cap=False``.
    """
    ids = [t for t in uniform_ids if t in scores and t in labels]
    candidates: list[tuple[bool, int, float, int, int]] = []
    for a in accept_grid:
        for r in reject_grid:
            n_mis = 0
            n_defer = 0
            for t in ids:
                s = scores[t]
                if a < s < r:
                    n_defer += 1
                verdict = _resolve_verdict(s, a, r, tier2_verdicts.get(t))
                effective = "accept" if verdict != "reject" else "reject"
                if effective != labels[t]:
                    n_mis += 1
            defer_rate = n_defer / len(ids) if ids else 0.0
            candidates.append((defer_rate <= defer_cap, n_mis, defer_rate, a, r))

    meeting = [c for c in candidates if c[0]]
    if meeting:
        _, n_mis, defer_rate, a, r = min(meeting, key=lambda c: (c[1], c[2]))
        meets = True
    else:
        _, n_mis, defer_rate, a, r = min(candidates, key=lambda c: (c[2], c[1]))
        meets = False
    return SweepResult(
        accept_threshold=a,
        reject_threshold=r,
        n_misclassified=n_mis,
        defer_rate=defer_rate if ids else None,
        meets_defer_cap=meets,
    )


def _prompt_id(criteria_body: str) -> str:
    """Stable content-addressed identifier for a criteria body."""
    return "p-" + hashlib.sha256(criteria_body.encode()).hexdigest()[:10]


def _profile_context(profile: RepoProfile, repo: str) -> str:
    """Render the curation-relevant slice of a RepoProfile for the writer."""
    lines = [
        f"- repo: {repo}",
        f"- proof assistant: {profile.proof_assistant}",
        f"- proof file globs: {', '.join(profile.proof_file_globs)}",
    ]
    if profile.exclude_globs:
        lines.append(
            f"- excluded paths (vendored/generated): {', '.join(profile.exclude_globs)}"
        )
    if profile.domain_terms:
        lines.append(f"- domain terms: {', '.join(profile.domain_terms[:40])}")
    if profile.notes:
        lines.append(f"- profiler notes: {profile.notes}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Calibrator
# ---------------------------------------------------------------------------


class PromptCalibrator:
    """Runs the calibration loop for one repo's mined challenges.

    All LLM access goes through three seams (`_complete`, `_writer_call`) so
    tests can run the full loop with stubbed models.
    """

    def __init__(
        self,
        challenges: list[EvalChallenge],
        profile: RepoProfile,
        *,
        repo: str,
        state_dir: Path,
        models: ModelRoles | None = None,
        config: CalibrationConfig | None = None,
    ) -> None:
        self.challenges = {c.task_id: c for c in challenges}
        self.task_ids = list(self.challenges)
        self.profile = profile
        self.repo = repo
        self.models = models or ModelRoles()
        self.config = config or CalibrationConfig()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(self.config.seed)

        self.labels: dict[str, GroundTruthLabel] = {}
        self.scores: dict[tuple[str, str], tuple[float, str]] = {}
        self.tier2: dict[tuple[str, str], tuple[str, str]] = {}
        self.prompts: dict[str, str] = {}
        self.prompt_origins: dict[str, str] = {}
        self.prompt_order: dict[str, int] = {}
        self.last_eval: dict[str, PromptEvaluation] = {}
        self.api_calls: dict[str, int] = {}
        self.writer_messages: list[MessageParam] = []

        self._client: AsyncAnthropic | None = None
        self._load_state()

    # -- state persistence ---------------------------------------------------

    def _load_state(self) -> None:
        for line in self._read_lines("ground_truth.jsonl"):
            entry = GroundTruthLabel.model_validate_json(line)
            self.labels[entry.task_id] = entry
        for line in self._read_lines("scores.jsonl"):
            e = json.loads(line)
            self.scores[(e["prompt_id"], e["task_id"])] = (e["score"], e["rationale"])
        for line in self._read_lines("tier2.jsonl"):
            e = json.loads(line)
            self.tier2[(e["prompt_id"], e["task_id"])] = (e["verdict"], e["rationale"])
        if self.labels or self.scores:
            logger.info(
                "Loaded calibration state: %d labels, %d cached scores",
                len(self.labels),
                len(self.scores),
            )

    def _read_lines(self, name: str) -> list[str]:
        path = self.state_dir / name
        if not path.exists():
            return []
        return [ln for ln in path.read_text().splitlines() if ln.strip()]

    def _append_jsonl(self, name: str, obj: dict) -> None:
        with open(self.state_dir / name, "a") as f:
            f.write(json.dumps(obj) + "\n")

    def _save_writer_transcript(self) -> None:
        (self.state_dir / "writer_transcript.json").write_text(
            json.dumps(self.writer_messages, indent=2)
        )

    # -- LLM seams -----------------------------------------------------------

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(max_retries=5)
        return self._client

    async def _complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        semaphore: asyncio.Semaphore,
        max_tokens: int,
    ) -> str | None:
        """One independent completion; returns None on API error."""
        try:
            async with semaphore:
                response = await self._get_client().messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
        except Exception as exc:
            logger.warning("API error (%s): %s", model, exc)
            return None
        self.api_calls[model] = self.api_calls.get(model, 0) + 1
        first = response.content[0] if response.content else None
        return first.text if isinstance(first, TextBlock) else ""

    async def _writer_call(self, user_text: str) -> str:
        """One turn of the persistent writer conversation."""
        self.writer_messages.append({"role": "user", "content": user_text})
        system = _WRITER_SYSTEM_TEMPLATE.format(
            repo=self.repo,
            cheap=self.models.cheap,
            mid=self.models.mid,
            defer_cap=self.config.defer_cap,
            n_variants=self.config.n_variants,
        )
        async with self._get_client().messages.stream(
            model=self.models.decision,
            max_tokens=_WRITER_MAX_TOKENS,
            system=system,
            messages=self.writer_messages,
        ) as stream:
            message = await stream.get_final_message()
        self.api_calls[self.models.decision] = (
            self.api_calls.get(self.models.decision, 0) + 1
        )
        text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
        self.writer_messages.append({"role": "assistant", "content": text})
        self._save_writer_transcript()
        return text

    # -- ground-truth labeling -------------------------------------------------

    async def _label_tasks(
        self, task_ids: list[str], provenance: dict[str, str]
    ) -> None:
        """Label *task_ids* with the decision model (skips already-labeled)."""
        todo = [t for t in task_ids if t not in self.labels]
        if not todo:
            return
        logger.info("Labeling %d challenges with %s", len(todo), self.models.decision)
        system = _LABELER_SYSTEM_TEMPLATE.format(
            repo=self.repo,
            proof_assistant=self.profile.proof_assistant,
            notes=self.profile.notes or "(none)",
        )
        sem = asyncio.Semaphore(self.config.label_max_concurrent)

        async def _one(task_id: str) -> None:
            user = _build_curation_prompt(self.challenges[task_id])
            text = await self._complete(
                system=system,
                user=user,
                model=self.models.decision,
                semaphore=sem,
                max_tokens=_LABELER_MAX_TOKENS,
            )
            label_match = _LABEL_RE.search(text) if text else None
            if text is not None and label_match is None:
                # One retry with an explicit format reminder.
                text = await self._complete(
                    system=system,
                    user=user + "\n\nEnd with exactly: LABEL: ACCEPT | REJECT",
                    model=self.models.decision,
                    semaphore=sem,
                    max_tokens=_LABELER_MAX_TOKENS,
                )
                label_match = _LABEL_RE.search(text) if text else None
            if text is None or label_match is None:
                logger.warning("Could not label %s — excluded from sample", task_id)
                return
            rationale_match = _LABEL_RATIONALE_RE.search(text)
            entry = GroundTruthLabel(
                task_id=task_id,
                label=label_match.group(1).lower(),
                rationale=rationale_match.group(1).strip() if rationale_match else "",
                model=self.models.decision,
                provenance=provenance.get(task_id, "uniform"),
            )
            self.labels[task_id] = entry
            self._append_jsonl("ground_truth.jsonl", entry.model_dump())

        await asyncio.gather(*[_one(t) for t in todo])

    # -- scoring / escalation ----------------------------------------------------

    async def _score_with(self, prompt_id: str, task_ids: list[str]) -> None:
        """Fill the tier-1 score cache for (prompt_id, task) pairs."""
        todo = [t for t in task_ids if (prompt_id, t) not in self.scores]
        if not todo:
            return
        system = build_tier1_prompt(self.prompts[prompt_id])
        sem = asyncio.Semaphore(self.config.max_concurrent)

        async def _one(task_id: str) -> None:
            text = await self._complete(
                system=system,
                user=_build_curation_prompt(self.challenges[task_id]),
                model=self.models.cheap,
                semaphore=sem,
                max_tokens=_SCORER_MAX_TOKENS,
            )
            if text is None:
                return
            score, rationale = _parse_score_response(text)
            self.scores[(prompt_id, task_id)] = (score, rationale)
            self._append_jsonl(
                "scores.jsonl",
                {
                    "prompt_id": prompt_id,
                    "task_id": task_id,
                    "score": score,
                    "rationale": rationale,
                },
            )

        await asyncio.gather(*[_one(t) for t in todo])

    async def _tier2_with(self, prompt_id: str, task_ids: list[str]) -> None:
        """Fill the tier-2 verdict cache for (prompt_id, task) pairs."""
        todo = [t for t in task_ids if (prompt_id, t) not in self.tier2]
        if not todo:
            return
        system = build_tier2_prompt(self.prompts[prompt_id])
        sem = asyncio.Semaphore(self.config.max_concurrent)

        async def _one(task_id: str) -> None:
            text = await self._complete(
                system=system,
                user=_build_curation_prompt(self.challenges[task_id]),
                model=self.models.mid,
                semaphore=sem,
                max_tokens=_SCORER_MAX_TOKENS,
            )
            if text is None:
                return
            verdict, rationale = _parse_response(text)
            self.tier2[(prompt_id, task_id)] = (verdict, rationale)
            self._append_jsonl(
                "tier2.jsonl",
                {
                    "prompt_id": prompt_id,
                    "task_id": task_id,
                    "verdict": verdict,
                    "rationale": rationale,
                },
            )

        await asyncio.gather(*[_one(t) for t in todo])

    # -- evaluation ---------------------------------------------------------------

    async def _evaluate(
        self, prompt_id: str, uniform_ids: list[str], oversampled_ids: list[str]
    ) -> PromptEvaluation:
        """Score + escalate + sweep thresholds for one prompt on one sample."""
        all_ids = [t for t in [*uniform_ids, *oversampled_ids] if t in self.labels]
        await self._score_with(prompt_id, all_ids)

        scores = {
            t: self.scores[(prompt_id, t)][0]
            for t in all_ids
            if (prompt_id, t) in self.scores
        }
        n_errors = len(all_ids) - len(scores)
        # Pre-compute tier-2 for everything inside the widest sweepable zone.
        midzone = [t for t, s in scores.items() if ACCEPT_GRID[0] < s < REJECT_GRID[-1]]
        await self._tier2_with(prompt_id, midzone)
        tier2_verdicts = {
            t: self.tier2[(prompt_id, t)][0]
            for t in midzone
            if (prompt_id, t) in self.tier2
        }
        labels = {t: self.labels[t].label for t in all_ids}

        sweep = sweep_thresholds(
            scores,
            tier2_verdicts,
            labels,
            [t for t in uniform_ids if t in self.labels],
            defer_cap=self.config.defer_cap,
        )

        # Conditional rates on the uniform subset at the chosen thresholds.
        uni = [t for t in uniform_ids if t in scores and t in labels]
        n_accepted = n_rejected = n_false_accept = n_false_reject = 0
        for t in uni:
            verdict = _resolve_verdict(
                scores[t],
                sweep.accept_threshold,
                sweep.reject_threshold,
                tier2_verdicts.get(t),
            )
            if verdict == "reject":
                n_rejected += 1
                if labels[t] == "accept":
                    n_false_reject += 1
            else:
                n_accepted += 1
                if labels[t] == "reject":
                    n_false_accept += 1

        evaluation = PromptEvaluation(
            prompt_id=prompt_id,
            accept_threshold=sweep.accept_threshold,
            reject_threshold=sweep.reject_threshold,
            n=len(scores),
            n_uniform=len(uni),
            n_misclassified=sweep.n_misclassified,
            misclassification_rate=sweep.n_misclassified / len(uni) if uni else None,
            false_accept_rate=n_false_accept / n_accepted if n_accepted else None,
            false_reject_rate=n_false_reject / n_rejected if n_rejected else None,
            defer_rate=sweep.defer_rate,
            meets_defer_cap=sweep.meets_defer_cap,
            n_errors=n_errors,
        )
        self.last_eval[prompt_id] = evaluation
        return evaluation

    @staticmethod
    def _ranking_key(e: PromptEvaluation) -> tuple:
        """Lower is better: respect the cap first, then errors, then defers."""
        return (
            not e.meets_defer_cap,
            e.n_misclassified,
            e.defer_rate if e.defer_rate is not None else 1.0,
        )

    # -- sampling -----------------------------------------------------------------

    def _draw_uniform(self, k: int) -> list[str]:
        unlabeled = [t for t in self.task_ids if t not in self.labels]
        return self.rng.sample(unlabeled, min(k, len(unlabeled)))

    async def _draw_sample(
        self, iteration: int, current_prompt_id: str
    ) -> tuple[list[str], list[str]]:
        """Draw this iteration's (uniform, oversampled) task ids."""
        if iteration == 1:
            return self._draw_uniform(self.config.first_sample_size), []

        uniform = self._draw_uniform(self.config.uniform_per_iteration)
        taken = set(uniform) | set(self.labels)
        remaining = [t for t in self.task_ids if t not in taken]
        pool = self.rng.sample(
            remaining, min(self.config.oversample_pool_size, len(remaining))
        )
        if not pool:
            return uniform, []

        await self._score_with(current_prompt_id, pool)
        last = self.last_eval.get(current_prompt_id)
        a = last.accept_threshold if last else 20
        r = last.reject_threshold if last else 85

        def zone_distance(task_id: str) -> float:
            entry = self.scores.get((current_prompt_id, task_id))
            if entry is None:
                return float("inf")
            s = entry[0]
            if a < s < r:
                return 0.0
            # Boundary scores auto-decide, so they rank strictly behind the
            # open defer interval (hence the +1).
            return (a - s + 1.0) if s <= a else (s - r + 1.0)

        ranked = sorted(pool, key=zone_distance)
        oversampled = [t for t in ranked if zone_distance(t) != float("inf")][
            : self.config.oversample_per_iteration
        ]
        return uniform, oversampled

    # -- writer turns ----------------------------------------------------------------

    def _register_prompt(self, criteria: str, origin: str) -> str:
        pid = _prompt_id(criteria)
        if pid not in self.prompts:
            self.prompts[pid] = criteria
            self.prompt_origins[pid] = origin
            self.prompt_order[pid] = len(self.prompt_order)
            (self.state_dir / "prompts.json").write_text(
                json.dumps(
                    {
                        p: {"origin": self.prompt_origins[p], "criteria": c}
                        for p, c in self.prompts.items()
                    },
                    indent=2,
                )
            )
        return pid

    async def _seed_prompt(self) -> str:
        """Writer turn 0: draft the repo-specific section from the profile."""
        user = (
            "## Task: draft the repo-specific criteria section\n\n"
            "Below is the universal core criteria body used for every repo, "
            f"followed by profiling data for {self.repo}. Draft a repo-specific "
            "section that will be appended to the core: concrete accept/reject "
            "guidance for THIS repo — auto-generated file patterns, boilerplate "
            "conventions, commit-message idioms that signal non-substantive "
            "work, and domain-specific cases the generic criteria miss.\n\n"
            f"## Universal core criteria\n{UNIVERSAL_CORE_CRITERIA}\n\n"
            f"## Repo profile\n{_profile_context(self.profile, self.repo)}\n\n"
            "Reply with the section inside <repo_section> tags."
        )
        reply = await self._writer_call(user)
        match = _REPO_SECTION_RE.search(reply)
        if match is None:
            reply = await self._writer_call(
                "Your reply contained no <repo_section> tags. Reply again with "
                "the repo-specific section inside <repo_section>...</repo_section>."
            )
            match = _REPO_SECTION_RE.search(reply)
        if match is None:
            raise CalibrationError("Writer did not produce a <repo_section> block")
        criteria = (
            f"{UNIVERSAL_CORE_CRITERIA}\n\n"
            f"Repo-specific guidance for {self.repo}:\n{match.group(1).strip()}"
        )
        return self._register_prompt(criteria, "seed (core + profile-derived section)")

    def _format_failures(self, prompt_id: str, sample_ids: list[str]) -> str:
        """Render the misclassified challenges for the writer, capped."""
        last = self.last_eval[prompt_id]
        failures: list[str] = []
        for task_id in sample_ids:
            entry = self.scores.get((prompt_id, task_id))
            label = self.labels.get(task_id)
            if entry is None or label is None:
                continue
            score, rationale = entry
            tier2 = self.tier2.get((prompt_id, task_id))
            verdict = _resolve_verdict(
                score,
                last.accept_threshold,
                last.reject_threshold,
                tier2[0] if tier2 else None,
            )
            effective = "accept" if verdict != "reject" else "reject"
            if effective == label.label:
                continue
            challenge = self.challenges[task_id]
            diff = challenge.diff[:_FAILURE_DIFF_CHARS]
            if len(challenge.diff) > _FAILURE_DIFF_CHARS:
                diff += f"\n... [truncated, {len(challenge.diff)} chars total]"
            via = (
                "tier-2"
                if last.accept_threshold < score < last.reject_threshold
                else "auto"
            )
            failures.append(
                f"### {task_id}\n"
                f"- ground truth: {label.label.upper()} — {label.rationale}\n"
                f"- pipeline: {verdict.upper()} (tier-1 score {score:.0f}, {via})\n"
                f"- tier-1 rationale: {rationale}\n"
                f"- file: {challenge.file_path}\n"
                f"- commit message: {challenge.commit_message[:200]}\n"
                f"- diff:\n```\n{diff}\n```"
            )
        shown = failures[:_FAILURE_CASE_CAP]
        header = f"{len(failures)} misclassified"
        if len(failures) > len(shown):
            header += f" (showing first {len(shown)})"
        return (
            f"{header}\n\n" + "\n\n".join(shown) if shown else "No misclassifications."
        )

    def _format_eval_table(self, evaluations: list[PromptEvaluation]) -> str:
        def pct(x: float | None) -> str:
            return f"{x:.1%}" if x is not None else "n/a"

        rows = [
            "| prompt | origin | thresholds | misclass | false-accept | false-reject | defer | cap ok |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for e in evaluations:
            rows.append(
                f"| {e.prompt_id} | {self.prompt_origins.get(e.prompt_id, '?')} "
                f"| <={e.accept_threshold} / >={e.reject_threshold} "
                f"| {pct(e.misclassification_rate)} ({e.n_misclassified}/{e.n_uniform}) "
                f"| {pct(e.false_accept_rate)} | {pct(e.false_reject_rate)} "
                f"| {pct(e.defer_rate)} | {'yes' if e.meets_defer_cap else 'NO'} |"
            )
        return "\n".join(rows)

    async def _writer_iteration_turn(
        self,
        iteration: int,
        evaluations: list[PromptEvaluation],
        best_id: str,
        sample_ids: list[str],
        n_uniform: int,
        n_oversampled: int,
    ) -> tuple[str, list[str] | str]:
        """Ask the writer to converge or produce variants.

        Returns ("converged", reason) or ("variants", [prompt_id, ...]).
        """
        labeled = [t for t in sample_ids if t in self.labels]
        n_acc = sum(1 for t in labeled if self.labels[t].label == "accept")
        user = (
            f"## Iteration {iteration} results\n\n"
            f"Fresh ground-truth sample: {n_uniform} uniform + {n_oversampled} "
            f"oversampled near the defer zone. Labels: {n_acc} accept / "
            f"{len(labeled) - n_acc} reject. Metrics below are computed on the "
            f"uniform subset only; thresholds were re-tuned mechanically per "
            f"prompt.\n\n"
            f"{self._format_eval_table(evaluations)}\n\n"
            f"## Failures of the current best prompt ({best_id})\n\n"
            f"{self._format_failures(best_id, sample_ids)}\n\n"
            "## Task\n\n"
            "Analyze the recurring failure patterns. Then either reply with "
            "<converged>justification</converged> if further changes would "
            f"chase noise or overfit, or produce up to {self.config.n_variants} "
            "improved variants — each a complete criteria body in its own "
            "<variant> tags. You may combine ideas from any candidates so far."
        )
        reply = await self._writer_call(user)
        converged = _CONVERGED_RE.search(reply)
        variants = _VARIANT_RE.findall(reply)
        if converged is None and not variants:
            reply = await self._writer_call(
                "Your reply contained neither <variant> nor <converged> tags. "
                "Reply again following the tag contract."
            )
            converged = _CONVERGED_RE.search(reply)
            variants = _VARIANT_RE.findall(reply)
        if converged is not None:
            return "converged", converged.group(1).strip()
        if not variants:
            raise CalibrationError("Writer produced neither variants nor convergence")
        ids = [
            self._register_prompt(v.strip(), f"iteration-{iteration} variant {i + 1}")
            for i, v in enumerate(variants[: self.config.n_variants])
        ]
        return "variants", ids

    # -- main loop -----------------------------------------------------------------

    async def _run(self) -> CalibrationReport:
        report = CalibrationReport(
            repo=self.repo,
            created_at=datetime.now(timezone.utc).isoformat(),
            models=self.models,
            config=self.config,
        )

        current = await self._seed_prompt()
        pool: list[str] = [current]

        for iteration in range(1, self.config.max_iterations + 1):
            uniform, oversampled = await self._draw_sample(iteration, current)
            if not uniform and not oversampled:
                logger.info(
                    "Challenge pool exhausted — stopping at iteration %d", iteration
                )
                report.iterations.append(
                    IterationRecord(
                        iteration=iteration,
                        sample_uniform=[],
                        sample_oversampled=[],
                        evaluations=[],
                        chosen_prompt_id=current,
                        pool=pool,
                        writer_action="exhausted",
                    )
                )
                break

            provenance = {t: "uniform" for t in uniform}
            provenance.update({t: "oversampled" for t in oversampled})
            await self._label_tasks([*uniform, *oversampled], provenance)
            sample_ids = [t for t in [*uniform, *oversampled] if t in self.labels]

            candidates = list(dict.fromkeys([current, *pool]))
            evaluations = [
                await self._evaluate(pid, uniform, oversampled) for pid in candidates
            ]
            best = min(
                evaluations,
                key=lambda e: (*self._ranking_key(e), self.prompt_order[e.prompt_id]),
            )

            action, payload = await self._writer_iteration_turn(
                iteration,
                evaluations,
                best.prompt_id,
                sample_ids,
                len(uniform),
                len(oversampled),
            )
            if action == "converged":
                current = best.prompt_id
                report.iterations.append(
                    IterationRecord(
                        iteration=iteration,
                        sample_uniform=uniform,
                        sample_oversampled=oversampled,
                        evaluations=evaluations,
                        chosen_prompt_id=current,
                        pool=pool,
                        writer_action="converged",
                        writer_note=str(payload),
                    )
                )
                logger.info("Writer declared convergence: %s", payload)
                break

            variant_ids = list(payload)
            for pid in variant_ids:
                evaluations.append(await self._evaluate(pid, uniform, oversampled))

            ranked = sorted(
                evaluations,
                key=lambda e: (*self._ranking_key(e), self.prompt_order[e.prompt_id]),
            )
            current = ranked[0].prompt_id
            pool = [e.prompt_id for e in ranked[: self.config.pool_size]]
            report.iterations.append(
                IterationRecord(
                    iteration=iteration,
                    sample_uniform=uniform,
                    sample_oversampled=oversampled,
                    evaluations=evaluations,
                    chosen_prompt_id=current,
                    pool=pool,
                    writer_action="variants",
                    writer_note=f"variants: {', '.join(variant_ids)}",
                )
            )
            logger.info(
                "Iteration %d: best %s (%d/%d misclassified, defer %s)",
                iteration,
                current,
                ranked[0].n_misclassified,
                ranked[0].n_uniform,
                f"{ranked[0].defer_rate:.1%}"
                if ranked[0].defer_rate is not None
                else "n/a",
            )

        # Final selection on the accumulated labeled pool.
        all_uniform = [t for t, e in self.labels.items() if e.provenance == "uniform"]
        all_oversampled = [
            t for t, e in self.labels.items() if e.provenance == "oversampled"
        ]
        finalists = list(dict.fromkeys([current, *pool]))
        report.final_evaluations = [
            await self._evaluate(pid, all_uniform, all_oversampled) for pid in finalists
        ]
        winner = min(
            report.final_evaluations,
            key=lambda e: (*self._ranking_key(e), self.prompt_order[e.prompt_id]),
        )
        report.winner_prompt_id = winner.prompt_id
        report.accept_threshold = winner.accept_threshold
        report.reject_threshold = winner.reject_threshold
        report.n_labeled = len(self.labels)
        report.n_label_accept = sum(
            1 for e in self.labels.values() if e.label == "accept"
        )
        report.n_label_reject = report.n_labeled - report.n_label_accept
        report.prompts = dict(self.prompts)
        report.prompt_origins = dict(self.prompt_origins)
        report.api_calls = dict(self.api_calls)
        return report

    def run(self) -> CalibrationReport:
        """Run the full calibration loop synchronously."""
        return asyncio.run(self._run())


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def write_artifacts(report: CalibrationReport, output_dir: Path) -> Path:
    """Write the calibrated prompts + report into *output_dir* (curation/).

    Layout:
        curation/criteria.txt      — winning criteria body
        curation/tier1_prompt.txt  — rendered tier-1 system prompt
        curation/tier2_prompt.txt  — rendered tier-2 system prompt
        curation/calibration.json  — full CalibrationReport
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    criteria = report.prompts[report.winner_prompt_id]
    (output_dir / "criteria.txt").write_text(criteria + "\n")
    (output_dir / "tier1_prompt.txt").write_text(build_tier1_prompt(criteria) + "\n")
    (output_dir / "tier2_prompt.txt").write_text(build_tier2_prompt(criteria) + "\n")
    (output_dir / "calibration.json").write_text(report.model_dump_json(indent=2))
    return output_dir


class CalibratedCuration(BaseModel):
    """The pieces of a calibration bundle that `scaffold curate` consumes."""

    tier1_system_prompt: str
    tier2_system_prompt: str
    accept_threshold: int
    reject_threshold: int


def load_calibration(curation_dir: Path) -> CalibratedCuration:
    """Load calibrated prompts + thresholds from a curation/ directory."""
    curation_dir = Path(curation_dir)
    report = json.loads((curation_dir / "calibration.json").read_text())
    return CalibratedCuration(
        tier1_system_prompt=(curation_dir / "tier1_prompt.txt")
        .read_text()
        .rstrip("\n"),
        tier2_system_prompt=(curation_dir / "tier2_prompt.txt")
        .read_text()
        .rstrip("\n"),
        accept_threshold=report["accept_threshold"],
        reject_threshold=report["reject_threshold"],
    )
