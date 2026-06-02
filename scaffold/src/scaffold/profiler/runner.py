"""Drive the calibration agent end-to-end and persist a RepoProfile.

``build_profile(repo_path, ...)`` wires Logfire, constructs the agent, attaches
the host tools, runs one calibration pass, stamps provenance, **checkpoints the
calibrated profile + transcript to disk**, then runs the full Phase-1 miner and
materializes a dataset version directory. The CLI ``profile`` command is a thin
wrapper over this.

Calibration is the expensive part (many LLM round-trips); the mine is cheap and
re-runnable. So the profile is checkpointed the instant ``run_sync`` returns —
*before* the (also slow, also fallible) full-history mine — so a crash in
materialization, a library error, or a laptop dying never discards the LLM work.
On success the checkpoint is removed (the version dir holds the canonical copy);
on failure it survives and is recoverable via ``scaffold materialize``.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scaffold.profile import RepoProfile

from .prompts import SYS_PROMPT_PROFILER
from .deps import ProfilerDeps

logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Load the nearest ``.env`` (walking up from CWD) into ``os.environ``.

    pydantic-ai reads ``ANTHROPIC_API_KEY`` from the environment, but this repo
    keeps the key in a ``.env`` at the monorepo root (one dir above ``scaffold``).
    Existing env vars win (python-dotenv's default ``override=False``).
    """
    from dotenv import find_dotenv, load_dotenv

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
        logger.info("Loaded environment from %s", dotenv_path)


@dataclass
class ProfileBuildResult:
    profile: RepoProfile
    version: str
    dataset_path: Path
    manifest_hash: str
    n_challenges: int
    promoted: bool
    transcript: list[str]


def _configure_logfire() -> None:
    """Wire Logfire so pydantic-ai + CodeMode spans register (user requirement).

    ``send_to_logfire='if-token-present'`` keeps this a no-op when no token is
    configured, so calibration never blocks on telemetry.
    """
    try:
        import logfire

        logfire.configure(
            send_to_logfire="if-token-present", service_name="scaffold-profiler"
        )
        logfire.instrument_pydantic_ai()
    except Exception as exc:  # logfire is optional at runtime
        logger.warning("logfire not configured: %s", exc)


def _git_remote(repo_path: Path) -> str:
    from scaffold.git_walker import _run_git

    res = _run_git(repo_path, "config", "--get", "remote.origin.url", check=False)
    return res.stdout.strip()


def _checkpoint_calibration(
    *,
    profile: RepoProfile,
    transcript: list[str],
    repo_path: Path,
    tag: str,
    artifacts_root: Path | None,
    created_at: str,
) -> Path:
    """Persist the calibrated profile + transcript before the full-history mine.

    Writes ``<artifacts>/<repo>-eval/_checkpoints/<tag>-<stamp>/{profile.json,
    transcript.txt}`` so the expensive LLM calibration survives any failure in
    the subsequent (slow, fallible) mine/materialize step. The stamp is derived
    from ``created_at`` so successive runs don't clobber a prior recoverable
    profile. Recover a stranded checkpoint with::

        scaffold materialize <repo> -p <checkpoint>/profile.json --tag <tag>

    Returns the checkpoint directory.
    """
    from scaffold.dataset import _monorepo_root
    from scaffold.profile import save_profile

    root = (
        Path(artifacts_root)
        if artifacts_root
        else _monorepo_root(repo_path) / "artifacts"
    )
    # 2026-05-29T02:09:35.12+00:00 -> 20260529T020935 (filesystem-safe, sortable)
    stamp = created_at.replace("-", "").replace(":", "")[:15]
    ckpt = root / f"{repo_path.name}-eval" / "_checkpoints" / f"{tag}-{stamp}"
    ckpt.mkdir(parents=True, exist_ok=True)
    save_profile(profile, ckpt / "profile.json")
    if transcript:
        (ckpt / "transcript.txt").write_text("\n".join(transcript) + "\n")
    return ckpt


def build_profile(
    repo_path: Path,
    *,
    model: str = "anthropic:claude-sonnet-4-6",
    tag: str = "agent",
    promote: bool = False,
    artifacts_root: Path | None = None,
    request_limit: int = 80,
    test_commits: int = 1500,
) -> ProfileBuildResult:
    """Run the calibration agent over ``repo_path`` and materialize a dataset version.

    Args:
        repo_path: the proof-engineering repo to calibrate against.
        model: pydantic-ai model string (bare ``anthropic:...`` etc.).
        tag: human-readable version tag (e.g. 'agentic_1'); dir is <tag>-<short_hash>.
        promote: if True, bless this dataset by symlinking the <repo>-eval/profile.json
            that mine-all reads at this version's profile.
        artifacts_root: where to write datasets (default: <monorepo>/artifacts).
        request_limit: core ``UsageLimits`` request cap for the agent run.
        test_commits: how many recent commits ``test_profile`` samples per call.
    """
    from pydantic_ai.usage import UsageLimits

    from .agent import make_profiler_agent
    from .tools import register_tools

    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists() and not repo_path.joinpath(".git").is_file():
        raise FileNotFoundError(f"{repo_path} is not a git repository")

    _load_dotenv()
    _configure_logfire()

    deps = ProfilerDeps(repo_path=repo_path, default_test_commits=test_commits)
    agent = make_profiler_agent(model)
    register_tools(agent, deps)

    prompt = (
        f"Calibrate a RepoProfile for the proof-engineering repository at "
        f"`{repo_path}` (its files are git-tracked; use the tools to explore). "
        f"Discover its proof assistant, file globs, hole markers, declaration "
        f"patterns, commit-message conventions, and tactic vocabulary, validating "
        f"with test_profile, then emit the final RepoProfile."
    )

    result = agent.run_sync(
        prompt, usage_limits=UsageLimits(request_limit=request_limit)
    )
    profile = result.output

    # Stamp provenance host-side (the sandbox has no wall clock and the model
    # shouldn't be trusted to record its own model id / sampled commits).
    created_at = datetime.now(timezone.utc).isoformat()
    profile.provenance.generated_by = "agent"
    profile.provenance.model = model
    profile.provenance.created_at = created_at
    if not profile.provenance.repo_url:
        profile.provenance.repo_url = _git_remote(repo_path)
    # Audit trail: which SHAs the tools touched this run.
    sampled = [
        line.split()[1]
        for line in deps.log
        if line.startswith(("git_show", "git_diff")) and len(line.split()) > 1
    ]
    profile.provenance.sampled_commits = sorted({s for s in sampled if len(s) >= 7})[
        :50
    ]

    # CHECKPOINT before the mine: calibration (the costly LLM work) is done, so
    # land it on disk now. If the mine below crashes — or the machine dies — this
    # survives and the profile is recoverable; only a clean success removes it.
    transcript = list(deps.log)
    checkpoint = _checkpoint_calibration(
        profile=profile,
        transcript=transcript,
        repo_path=repo_path,
        tag=tag,
        artifacts_root=artifacts_root,
        created_at=created_at,
    )
    logger.info(
        "Checkpointed calibrated profile -> %s "
        "(recover with: scaffold materialize %s -p %s --tag %s)",
        checkpoint,
        repo_path.name,
        checkpoint / "profile.json",
        tag,
    )

    # Build prompt text for hashing: system + user.
    prompt_text = SYS_PROMPT_PROFILER + "\n\n" + prompt

    # Run the full mine to get challenges and materialize the dataset version.
    from scaffold.dataset import mine_and_materialize, promote_profile

    try:
        dv = mine_and_materialize(
            profile=profile,
            repo_path=repo_path,
            tag=tag,
            miner_kind="agent",
            artifacts_root=artifacts_root,
            model=model,
            prompt_text=prompt_text,
            transcript=transcript,
        )
    except Exception:
        logger.error(
            "Mining failed after calibration; calibrated profile preserved at %s "
            "(recover with: scaffold materialize %s -p %s --tag %s)",
            checkpoint,
            repo_path.name,
            checkpoint / "profile.json",
            tag,
        )
        raise

    # Mine + materialize succeeded: the version dir now holds the canonical
    # profile.json + transcript.txt, so the checkpoint is redundant — drop it.
    shutil.rmtree(checkpoint, ignore_errors=True)

    # Conditionally bless this dataset (symlink <repo>-eval/profile.json -> it).
    promoted = False
    if promote:
        blessed_path = promote_profile(dv)
        logger.info("Promoted profile (symlink) -> %s", blessed_path)
        promoted = True

    return ProfileBuildResult(
        profile=profile,
        version=dv.version,
        dataset_path=dv.path,
        manifest_hash=dv.manifest_hash,
        n_challenges=dv.n_challenges,
        promoted=promoted,
        transcript=list(deps.log),
    )
