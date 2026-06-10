"""Tests for the per-repo curation-prompt calibration loop.

All tests stub the LLM seams (`_complete`, `_writer_call`) — no API calls.
The stub derives deterministic behavior from the challenge file path:
``good-*.v`` is substantive (tier-1 score 5, ground truth accept),
``junk-*.v`` is non-substantive (score 95, reject), and ``amb-*.v`` is
ambiguous (score 50 → escalates to tier-2, which accepts; ground truth
accept).
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from scaffold.calibrate import (
    CalibrationConfig,
    CalibrationError,
    PromptCalibrator,
    UNIVERSAL_CORE_CRITERIA,
    _CONVERGED_RE,
    _profile_context,
    _prompt_id,
    _resolve_verdict,
    _VARIANT_RE,
    load_calibration,
    sweep_thresholds,
    write_artifacts,
)
from scaffold.curator import build_tier1_prompt, build_tier2_prompt
from scaffold.model_roles import ModelRoles
from scaffold.models import EvalChallenge
from scaffold.profile import HoleMarker, RepoProfile

_FILE_RE = re.compile(r"\*\*File\*\*: (\S+)")


def _make_profile() -> RepoProfile:
    return RepoProfile(
        proof_assistant="coq",
        proof_file_globs=["**/*.v"],
        exclude_globs=["gen/**"],
        hole_markers=[HoleMarker(regex=r"\bAdmitted\b", kind="admitted")],
        declaration_patterns=[r"^\s*Lemma\s+(\w+)"],
        domain_terms=["widget", "gadget"],
        notes="Test repo: files under gen/ are auto-generated.",
    )


def _make_challenge(task_id: str, file_path: str) -> EvalChallenge:
    return EvalChallenge(
        task_id=task_id,
        repo="test-repo",
        proof_assistant="coq",
        commit_hash="abc123",
        parent_hash="def456",
        commit_message=f"Change {file_path}",
        file_path=file_path,
        challenge_file_content="Lemma foo : True. Admitted.",
        solution_file_content="Lemma foo : True. Proof. trivial. Qed.",
        diff=f"--- a/{file_path}\n+++ b/{file_path}\n-Admitted.\n+trivial. Qed.\n",
        instructions="Fill in the proof.",
    )


def _make_challenges(n_good: int, n_junk: int, n_amb: int) -> list[EvalChallenge]:
    challenges = []
    for i in range(n_good):
        challenges.append(_make_challenge(f"t-good-{i}", f"src/good-{i}.v"))
    for i in range(n_junk):
        challenges.append(_make_challenge(f"t-junk-{i}", f"src/junk-{i}.v"))
    for i in range(n_amb):
        challenges.append(_make_challenge(f"t-amb-{i}", f"src/amb-{i}.v"))
    return challenges


class StubCalibrator(PromptCalibrator):
    """Calibrator with deterministic in-process LLMs."""

    def __init__(self, *args, writer_replies: list[str], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._writer_replies = list(writer_replies)
        self.n_label_calls = 0

    async def _writer_call(self, user_text: str) -> str:
        self.writer_messages.append({"role": "user", "content": user_text})
        if not self._writer_replies:
            raise AssertionError("Writer stub ran out of scripted replies")
        reply = self._writer_replies.pop(0)
        self.writer_messages.append({"role": "assistant", "content": reply})
        self._save_writer_transcript()  # mirror the real seam's persistence
        return reply

    async def _complete(self, *, system, user, model, semaphore, max_tokens):
        match = _FILE_RE.search(user)
        assert match is not None, "challenge prompt missing file path"
        kind = match.group(1).rsplit("/", 1)[-1].split("-")[0]
        if model == self.models.decision:
            self.n_label_calls += 1
            label = "REJECT" if kind == "junk" else "ACCEPT"
            return f"Detailed analysis here.\nLABEL: {label}\nRATIONALE: stub"
        if model == self.models.cheap:
            score = {"good": 5, "junk": 95, "amb": 50}[kind]
            return f"SCORE: {score}\nRATIONALE: stub tier-1"
        return "VERDICT: ACCEPT\nRATIONALE: stub tier-2"


_SEED_REPLY = "<repo_section>Reject changes under gen/ — auto-generated.</repo_section>"
_VARIANTS_REPLY = (
    "Analysis of failures...\n"
    "<variant>VARIANT ONE criteria body. Reject generated files.</variant>\n"
    "<variant>VARIANT TWO criteria body. Reject boilerplate.</variant>"
)
_CONVERGED_REPLY = "<converged>Error rates are at the noise floor.</converged>"


def _make_calibrator(tmp_path, *, writer_replies, challenges=None, config=None):
    return StubCalibrator(
        challenges or _make_challenges(44, 12, 4),
        _make_profile(),
        repo="test-repo",
        state_dir=tmp_path / "state",
        models=ModelRoles(cheap="stub-cheap", mid="stub-mid", decision="stub-decision"),
        config=config
        or CalibrationConfig(
            max_iterations=3,
            first_sample_size=20,
            uniform_per_iteration=10,
            oversample_per_iteration=5,
            oversample_pool_size=100,  # > corpus → the pool is every unlabeled task
            pool_size=2,
            defer_cap=0.5,
            seed=0,
        ),
        writer_replies=writer_replies,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestResolveVerdict:
    def test_boundaries(self) -> None:
        assert _resolve_verdict(20, 20, 85, None) == "accept"
        assert _resolve_verdict(85, 20, 85, None) == "reject"

    def test_midzone_uses_tier2(self) -> None:
        assert _resolve_verdict(50, 20, 85, "reject") == "reject"
        assert _resolve_verdict(50, 20, 85, "accept") == "accept"

    def test_midzone_defer_or_missing_counts_as_accept(self) -> None:
        assert _resolve_verdict(50, 20, 85, "defer") == "accept"
        assert _resolve_verdict(50, 20, 85, None) == "accept"


class TestSweepThresholds:
    def test_perfect_separation(self) -> None:
        scores = {f"g{i}": 0.0 for i in range(8)} | {"j1": 100.0, "j2": 100.0}
        labels = {f"g{i}": "accept" for i in range(8)} | {
            "j1": "reject",
            "j2": "reject",
        }
        result = sweep_thresholds(scores, {}, labels, list(scores))
        assert result.n_misclassified == 0
        assert result.meets_defer_cap
        assert result.defer_rate == 0.0

    def test_midzone_resolved_by_tier2(self) -> None:
        scores = {
            "g1": 0.0,
            "g2": 0.0,
            "g3": 0.0,
            "g4": 0.0,
            "g5": 0.0,
            "g6": 0.0,
            "g7": 0.0,
            "g8": 0.0,
            "g9": 0.0,
            "m1": 50.0,
        }
        labels = dict.fromkeys(scores, "accept")
        result = sweep_thresholds(scores, {"m1": "accept"}, labels, list(scores))
        assert result.n_misclassified == 0
        assert result.meets_defer_cap  # 1/10 defers
        assert result.defer_rate == pytest.approx(0.1)

    def test_defer_cap_unreachable(self) -> None:
        scores = {f"m{i}": 50.0 for i in range(4)} | {f"g{i}": 0.0 for i in range(6)}
        labels = dict.fromkeys(scores, "accept")
        tier2 = {f"m{i}": "accept" for i in range(4)}
        result = sweep_thresholds(scores, tier2, labels, list(scores), defer_cap=0.10)
        assert not result.meets_defer_cap  # 40% always defers
        assert result.n_misclassified == 0

    def test_misclassification_counted(self) -> None:
        # A junk challenge the prompt confidently accepts is always an error.
        scores = {"g1": 0.0, "bad": 0.0, "j1": 100.0}
        labels = {"g1": "accept", "bad": "reject", "j1": "reject"}
        result = sweep_thresholds(scores, {}, labels, list(scores))
        assert result.n_misclassified == 1

    def test_only_uniform_ids_count(self) -> None:
        scores = {"g1": 0.0, "oversampled-junk": 0.0}
        labels = {"g1": "accept", "oversampled-junk": "reject"}
        result = sweep_thresholds(scores, {}, labels, ["g1"])
        assert result.n_misclassified == 0


class TestPromptId:
    def test_stable_and_distinct(self) -> None:
        assert _prompt_id("abc") == _prompt_id("abc")
        assert _prompt_id("abc") != _prompt_id("abd")
        assert _prompt_id("abc").startswith("p-")


class TestProfileContext:
    def test_includes_curation_relevant_fields(self) -> None:
        ctx = _profile_context(_make_profile(), "test-repo")
        assert "test-repo" in ctx
        assert "coq" in ctx
        assert "gen/**" in ctx
        assert "auto-generated" in ctx  # notes
        assert "widget" in ctx  # domain terms


class TestWriterTagParsing:
    def test_variant_blocks(self) -> None:
        found = _VARIANT_RE.findall(_VARIANTS_REPLY)
        assert len(found) == 2
        assert "VARIANT ONE" in found[0]

    def test_variant_with_attributes(self) -> None:
        found = _VARIANT_RE.findall('<variant name="x">body</variant>')
        assert found == ["body"]

    def test_converged(self) -> None:
        match = _CONVERGED_RE.search(_CONVERGED_REPLY)
        assert match is not None
        assert "noise floor" in match.group(1)


# ---------------------------------------------------------------------------
# Full loop (stubbed LLMs)
# ---------------------------------------------------------------------------


class TestCalibrationLoop:
    def test_full_loop_converges(self, tmp_path) -> None:
        calibrator = _make_calibrator(
            tmp_path, writer_replies=[_SEED_REPLY, _VARIANTS_REPLY, _CONVERGED_REPLY]
        )
        report = calibrator.run()

        # Iteration 1: 500-capped-to-20 uniform, no oversample; writer produced variants.
        assert len(report.iterations) == 2
        first, second = report.iterations
        assert len(first.sample_uniform) == 20
        assert first.sample_oversampled == []
        assert first.writer_action == "variants"
        # Iteration 2: 10 uniform + 5 oversampled; writer converged.
        assert len(second.sample_uniform) == 10
        assert len(second.sample_oversampled) == 5
        assert second.writer_action == "converged"
        assert "noise floor" in second.writer_note

        # Seed + two variants registered; winner among them.
        assert len(report.prompts) == 3
        assert report.winner_prompt_id in report.prompts
        seed_criteria = report.prompts[
            next(p for p, o in report.prompt_origins.items() if o.startswith("seed"))
        ]
        assert seed_criteria.startswith(UNIVERSAL_CORE_CRITERIA)
        assert "gen/" in seed_criteria  # repo section made it in

        # All sampled challenges labeled exactly once, binary labels.
        assert report.n_labeled == 35
        assert report.n_labeled == calibrator.n_label_calls
        assert report.n_label_accept + report.n_label_reject == report.n_labeled

        # The stub models are perfectly consistent → zero error on the pool.
        winner = next(
            e
            for e in report.final_evaluations
            if e.prompt_id == report.winner_prompt_id
        )
        assert winner.n_misclassified == 0
        assert winner.false_accept_rate in (0.0, None)
        assert winner.false_reject_rate in (0.0, None)

    def test_oversample_targets_defer_zone(self, tmp_path) -> None:
        calibrator = _make_calibrator(
            tmp_path, writer_replies=[_SEED_REPLY, _VARIANTS_REPLY, _CONVERGED_REPLY]
        )
        report = calibrator.run()
        oversampled = report.iterations[1].sample_oversampled
        # The ambiguous (score-50) challenges sit in the defer zone, so every
        # one still unlabeled after the uniform draws must be picked before
        # any extreme-score challenge (the pool covers the whole corpus).
        prior_draws = set(report.iterations[0].sample_uniform) | set(
            report.iterations[1].sample_uniform
        )
        remaining_ambs = {
            t
            for t in calibrator.task_ids
            if t.startswith("t-amb") and t not in prior_draws
        }
        assert remaining_ambs <= set(oversampled)

    def test_artifacts_round_trip(self, tmp_path) -> None:
        calibrator = _make_calibrator(
            tmp_path, writer_replies=[_SEED_REPLY, _VARIANTS_REPLY, _CONVERGED_REPLY]
        )
        report = calibrator.run()
        out = write_artifacts(report, tmp_path / "curation")

        assert (out / "criteria.txt").exists()
        assert (out / "calibration.json").exists()
        loaded = load_calibration(out)
        criteria = report.prompts[report.winner_prompt_id]
        assert loaded.tier1_system_prompt == build_tier1_prompt(criteria)
        assert loaded.tier2_system_prompt == build_tier2_prompt(criteria)
        assert loaded.accept_threshold == report.accept_threshold
        assert loaded.reject_threshold == report.reject_threshold
        # The report's prompt lineage is preserved for reproducibility.
        saved = json.loads((out / "calibration.json").read_text())
        assert saved["prompts"].keys() == report.prompts.keys()

    def test_rerun_of_completed_run_is_free(self, tmp_path) -> None:
        calibrator = _make_calibrator(
            tmp_path, writer_replies=[_SEED_REPLY, _VARIANTS_REPLY, _CONVERGED_REPLY]
        )
        report = calibrator.run()

        # A re-run after completion needs no writer turns, no labels, and no
        # scoring — everything replays from the persisted state.
        resumed = _make_calibrator(tmp_path, writer_replies=[])
        assert len(resumed.labels) == report.n_labeled
        assert resumed.scores  # tier-1 cache reloaded
        rerun = resumed.run()
        assert resumed.n_label_calls == 0
        assert rerun.n_labeled == report.n_labeled
        assert rerun.winner_prompt_id == report.winner_prompt_id
        assert len(rerun.iterations) == len(report.iterations)

    def test_crash_mid_iteration_resumes_without_relabeling(self, tmp_path) -> None:
        # The writer stub runs out of replies during iteration 2's turn —
        # after the iteration-2 sample was drawn and labeled.
        crashed = _make_calibrator(
            tmp_path, writer_replies=[_SEED_REPLY, _VARIANTS_REPLY]
        )
        with pytest.raises(AssertionError, match="scripted replies"):
            crashed.run()
        labels_at_crash = len(crashed.labels)
        assert labels_at_crash == 35  # both iterations' samples were labeled

        resumed = _make_calibrator(tmp_path, writer_replies=[_CONVERGED_REPLY])
        report = resumed.run()

        # No re-labeling: iteration 2 reused its persisted sample draw.
        assert resumed.n_label_calls == 0
        assert report.n_labeled == labels_at_crash
        assert len(report.iterations) == 2
        assert report.iterations[0].writer_action == "variants"
        assert report.iterations[1].writer_action == "converged"
        # The resumed writer conversation continued from the persisted
        # transcript: seed + retry-free iteration 1 + iteration 2 turns.
        assert len(resumed.writer_messages) == 6
        # Seed prompt and both variants survived the crash.
        assert len(report.prompts) == 3

    def test_converges_on_first_iteration(self, tmp_path) -> None:
        calibrator = _make_calibrator(
            tmp_path, writer_replies=[_SEED_REPLY, _CONVERGED_REPLY]
        )
        report = calibrator.run()
        assert len(report.iterations) == 1
        assert report.iterations[0].writer_action == "converged"
        assert len(report.prompts) == 1  # seed only

    def test_writer_seed_retry_then_failure(self, tmp_path) -> None:
        calibrator = _make_calibrator(
            tmp_path, writer_replies=["no tags here", "still no tags"]
        )
        with pytest.raises(CalibrationError):
            calibrator.run()

    def test_writer_variant_retry_succeeds(self, tmp_path) -> None:
        calibrator = _make_calibrator(
            tmp_path,
            writer_replies=[
                _SEED_REPLY,
                "oops, forgot the tags",
                _VARIANTS_REPLY,
                _CONVERGED_REPLY,
            ],
        )
        report = calibrator.run()
        assert report.iterations[0].writer_action == "variants"

    def test_pool_exhaustion_stops_loop(self, tmp_path) -> None:
        # 10 challenges, first sample takes all of them; iteration 2 finds none.
        calibrator = _make_calibrator(
            tmp_path,
            writer_replies=[_SEED_REPLY, _VARIANTS_REPLY],
            challenges=_make_challenges(8, 2, 0),
            config=CalibrationConfig(
                max_iterations=3,
                first_sample_size=10,
                uniform_per_iteration=10,
                oversample_per_iteration=5,
                oversample_pool_size=10,
                pool_size=2,
                defer_cap=0.5,
                seed=0,
            ),
        )
        report = calibrator.run()
        assert report.iterations[-1].writer_action == "exhausted"
        assert report.n_labeled == 10
        assert report.winner_prompt_id in report.prompts


# ---------------------------------------------------------------------------
# Evaluation internals
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_rates_computed_on_uniform_subset_only(self, tmp_path) -> None:
        calibrator = _make_calibrator(tmp_path, writer_replies=[])
        pid = calibrator._register_prompt("criteria body", "test")
        uniform = ["t-good-0", "t-good-1", "t-junk-0"]
        oversampled = ["t-junk-1", "t-junk-2"]
        provenance = dict.fromkeys(uniform, "uniform") | dict.fromkeys(
            oversampled, "oversampled"
        )

        async def _go():
            await calibrator._label_tasks([*uniform, *oversampled], provenance)
            return await calibrator._evaluate(pid, uniform, oversampled)

        evaluation = asyncio.run(_go())
        assert evaluation.n == 5
        assert evaluation.n_uniform == 3
        assert evaluation.n_misclassified == 0
        assert evaluation.misclassification_rate == 0.0
        assert evaluation.n_errors == 0
