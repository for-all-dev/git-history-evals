"""Drive the calibration agent end-to-end and persist a RepoProfile.

``build_profile(repo_path, ...)`` wires Logfire, constructs the agent, attaches
the host tools, runs one calibration pass, stamps provenance, then runs the full
Phase-1 miner and materializes a dataset version directory. The CLI ``profile``
command is a thin wrapper over this.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scaffold.profile import RepoProfile

from .prompts import SYS_PROMPT_PROFILER
from .deps import ProfilerDeps

logger = logging.getLogger(__name__)


def _load_dotenv(start: Path | None = None) -> Path | None:
    """Load the nearest ``.env`` (walking up from ``start``/CWD) into ``os.environ``.

    pydantic-ai reads ``ANTHROPIC_API_KEY`` from the environment, but this repo
    keeps the key in a ``.env`` at the monorepo root (one dir above ``scaffold``).
    Mirrors quali's ``load_env``: existing env vars win (``setdefault``), values
    have surrounding quotes stripped. Returns the file loaded, or None.
    """
    cur = (start or Path.cwd()).resolve()
    while True:
        candidate = cur / ".env"
        if candidate.is_file():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
            logger.info("Loaded environment from %s", candidate)
            return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent


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
        promote: if True, copy the profile to the blessed <repo>-eval/profile.json
            that mine-all uses.
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
    profile.provenance.generated_by = "agent"
    profile.provenance.model = model
    profile.provenance.created_at = datetime.now(timezone.utc).isoformat()
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

    # Compute monorepo root for artifacts placement.
    res = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    monorepo_root = (
        Path(res.stdout.strip())
        if res.stdout.strip()
        else repo_path.resolve().parents[1]
    )

    artifacts_root = (
        Path(artifacts_root) if artifacts_root else (monorepo_root / "artifacts")
    )
    miner_pkg_dir = Path(__file__).resolve().parents[1]  # .../src/scaffold

    # Build prompt text for hashing: system + user.
    prompt_text = SYS_PROMPT_PROFILER + "\n\n" + prompt

    # Run the full mine to get challenges.
    from scaffold.analyzers import ProfileAnalyzer
    from scaffold.git_walker import mine_repo

    analyzer = ProfileAnalyzer(profile.compiled())
    mined = mine_repo(repo_path, repo_path.name, analyzer)

    # Materialize the dataset version.
    from scaffold.dataset import materialize_dataset_version

    dv = materialize_dataset_version(
        profile=profile,
        result=mined,
        repo_path=repo_path,
        artifacts_root=artifacts_root,
        monorepo_root=monorepo_root,
        miner_pkg_dir=miner_pkg_dir,
        model=model,
        prompt_text=prompt_text,
        tag=tag,
        transcript=list(deps.log),
    )

    # Conditionally promote to the blessed profile.
    promoted = False
    if promote:
        blessed_path = artifacts_root / f"{repo_path.name}-eval" / "profile.json"
        blessed_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dv.path / "miner" / "profile.json", blessed_path)
        logger.info("Promoted profile to %s", blessed_path)
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
