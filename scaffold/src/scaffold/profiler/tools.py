"""Host-side tools for the CodeMode calibration agent.

``build_tools(deps)`` constructs the exploration + self-validation functions the
agent drives to synthesise a :class:`~scaffold.profile.RepoProfile`, closing them
over a single :class:`ProfilerDeps` (repo path + cost caps). ``register_tools``
attaches them to an agent. Splitting construction from registration keeps the
functions reachable for unit tests without an LLM.

Under CodeMode the model writes Python that *calls* these as functions inside a
restricted Monty sandbox (no subprocess, stdlib only); the callables themselves
dispatch HOST-side, where they have full ``git``/filesystem/engine access, and
marshal JSON-friendly results back. So the agent can loop, regex, and aggregate
in-sandbox while the heavy lifting (git subprocess, running the Phase-1 engine)
happens for real on the host.

The keystone is ``test_profile``: it validates a candidate profile dict and runs
the *actual* deterministic engine (``mine_repo`` / ``dump_commits`` /
``enrich_record`` / ``enrich_record_with_diff``) over a sample of the repo's
history, returning the mined-challenge count, commit-class distribution, and a
few worked examples — the feedback that lets the agent detect a repo's
conventions on the fly instead of one-shot guessing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING

from scaffold.git_walker import _run_git

from .deps import ProfilerDeps

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic_ai import Agent

    from scaffold.profile import RepoProfile

# Record/field separators for the structured git-log formats. NUL-delimited so
# commit subjects/bodies containing the separators can't corrupt the parse.
_FS = "\x00"  # between fields
_RS = "\x1e"  # between commits

# The tools the agent drives, in registration order. Exported so the agent can
# be built with ``CodeMode(tools=TOOL_NAMES)`` (sandboxing exactly these) before
# the closures are attached — the final RepoProfile output tool is deliberately
# NOT in this list, so it stays a native structured-output call (the model can't
# build a pydantic object inside the Monty sandbox).
TOOL_NAMES: list[str] = [
    "list_files",
    "read_file",
    "git_log",
    "sample_commits",
    "git_show",
    "git_diff",
    "count_regex",
    "test_profile",
]


def _resolve_under(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``, rejecting traversal outside ``root``."""
    root_resolved = root.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise ValueError(f"path {rel!r} escapes repo root {root_resolved}")
    return candidate


def _parse_commits(raw: str) -> list[dict]:
    """Parse a NUL/RS-delimited ``git log`` block into commit dicts."""
    out: list[dict] = []
    for block in raw.split(_RS):
        block = block.strip("\n")
        if not block:
            continue
        fields = block.split(_FS)
        if len(fields) < 5:
            continue
        h, author, date, subject = fields[0], fields[1], fields[2], fields[3]
        body = fields[4] if len(fields) > 4 else ""
        out.append(
            {
                "hash": h.strip(),
                "author": author,
                "date": date,
                "subject": subject,
                "body": body.strip(),
            }
        )
    return out


def build_tools(deps: ProfilerDeps) -> dict[str, Callable]:
    """Construct the calibration tools as plain callables closing over ``deps``.

    Returns a ``{name: function}`` dict keyed by :data:`TOOL_NAMES`. Callers that
    want to drive an agent should use :func:`register_tools`; tests can call the
    functions here directly.
    """
    repo = deps.repo_path
    log_fmt = "%H%x00%an%x00%aI%x00%s%x00%b%x1e"

    def list_files(glob: str = "*", limit: int = 300) -> list[str]:
        """List git-tracked files whose path matches an fnmatch ``glob``.

        Use this to discover which extensions/directories hold proof sources
        (e.g. ``"*.v"``, ``"*.thy"``, ``"src/**/*.lean"``). Matching is
        case-sensitive fnmatch over the full repo-relative path. Returns at
        most ``limit`` paths.
        """
        res = _run_git(repo, "ls-files", check=False)
        files = [f for f in res.stdout.splitlines() if f]
        matched = [f for f in files if fnmatchcase(f, glob)]
        deps.log.append(f"list_files glob={glob!r} matched={len(matched)}/{len(files)}")
        return matched[: max(1, limit)]

    def read_file(path: str, start: int = 1, end: int = -1) -> str:
        """Read a 1-indexed, inclusive line slice of a working-tree file.

        ``end=-1`` reads to EOF. At most ``deps.max_read_lines`` lines are
        returned per call, so page through large files with successive calls.
        """
        resolved = _resolve_under(repo, path)
        if not resolved.is_file():
            raise FileNotFoundError(f"{path!r} is not a file under the repo")
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
        total = len(lines)
        s = max(1, start) - 1
        e = total if end == -1 else min(total, end)
        if e < s:
            e = s
        if e - s > deps.max_read_lines:
            e = s + deps.max_read_lines
        deps.log.append(f"read_file path={path!r} lines={s + 1}-{e}/{total}")
        return "".join(lines[s:e])

    def git_log(n: int = 50, grep: str = "") -> list[dict]:
        """Return up to ``n`` recent commits as ``{hash, author, date, subject, body}``.

        ``grep`` (if non-empty) is passed to ``git log --grep`` (a regex over
        the message) so you can probe how the team phrases, e.g., proof
        completions or spec changes.
        """
        cmd = ["log", f"--format={log_fmt}", f"-n{max(1, n)}"]
        if grep:
            cmd += ["--grep", grep, "--regexp-ignore-case", "-E"]
        res = _run_git(repo, *cmd, check=False)
        commits = _parse_commits(res.stdout)
        deps.log.append(f"git_log n={n} grep={grep!r} -> {len(commits)}")
        return commits

    def sample_commits(
        touching_glob: str = "", n: int = 40, grep: str = ""
    ) -> list[dict]:
        """Sample up to ``n`` commits, optionally only those touching ``touching_glob``.

        ``touching_glob`` is a git pathspec (e.g. ``"*.v"``) — handy to look
        only at commits that modified proof files. Returns the same dicts as
        ``git_log``. Combine with ``grep`` to find class-defining message
        conventions on real proof commits.
        """
        cmd = ["log", f"--format={log_fmt}", f"-n{max(1, n)}"]
        if grep:
            cmd += ["--grep", grep, "--regexp-ignore-case", "-E"]
        if touching_glob:
            cmd += ["--", touching_glob]
        res = _run_git(repo, *cmd, check=False)
        commits = _parse_commits(res.stdout)
        deps.log.append(
            f"sample_commits glob={touching_glob!r} n={n} grep={grep!r} -> {len(commits)}"
        )
        return commits

    def git_show(sha: str, path: str = "") -> str:
        """Show a commit. With ``path``, show that file's content at ``sha`` instead.

        Truncated to ``deps.max_read_lines`` lines. Use to inspect how a proof
        file looked at a given point, or to read a full commit message + diff.
        """
        target = f"{sha}:{path}" if path else sha
        res = _run_git(repo, "show", target, check=False)
        if res.returncode != 0:
            return f"<git show {target} failed: {res.stderr.strip()[:200]}>"
        lines = res.stdout.splitlines(keepends=True)
        deps.log.append(
            f"git_show {target} lines={min(len(lines), deps.max_read_lines)}"
        )
        return "".join(lines[: deps.max_read_lines])

    def git_diff(sha: str, path: str = "") -> str:
        """Return the unified diff a commit ``sha`` introduced (vs its first parent).

        With ``path`` (a pathspec like ``"*.v"``), restrict the diff to matching
        files. Truncated to ``deps.max_read_lines`` lines. This is the ground
        truth for what proof work a commit actually did (holes removed, tactics
        added) — sample a few to design ``hole_markers`` and ``tactic_vocabulary``.
        """
        cmd = ["diff", f"{sha}^!", "--unified=3"]
        if path:
            cmd += ["--", path]
        res = _run_git(repo, *cmd, check=False)
        if res.returncode != 0:
            return f"<git diff {sha} failed: {res.stderr.strip()[:200]}>"
        lines = res.stdout.splitlines(keepends=True)
        deps.log.append(
            f"git_diff {sha} path={path!r} lines={min(len(lines), deps.max_read_lines)}"
        )
        return "".join(lines[: deps.max_read_lines])

    def count_regex(pattern: str, scope: str = "*") -> dict:
        """Count matches of a regex across git-tracked files matching ``scope`` glob.

        Fast calibration primitive: returns ``{total_matches, files_with_match,
        files_scanned, files_total, error}``. Up to ``deps.max_files_scanned``
        matching files are read (reported via ``files_scanned`` vs
        ``files_total``). Use it to test, e.g., how often ``\\bAdmitted\\b``
        appears in ``*.v``, or which declaration keyword a repo favours.
        """
        try:
            rx = re.compile(pattern, re.MULTILINE)
        except re.error as exc:
            return {
                "total_matches": 0,
                "files_with_match": 0,
                "files_scanned": 0,
                "files_total": 0,
                "error": f"bad regex: {exc}",
            }
        res = _run_git(repo, "ls-files", check=False)
        files = [f for f in res.stdout.splitlines() if f and fnmatchcase(f, scope)]
        scanned = files[: deps.max_files_scanned]
        total = 0
        with_match = 0
        for f in scanned:
            try:
                text = (repo / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hits = len(rx.findall(text))
            if hits:
                with_match += 1
                total += hits
        deps.log.append(
            f"count_regex {pattern!r} scope={scope!r} -> {total} in {with_match} files"
        )
        return {
            "total_matches": total,
            "files_with_match": with_match,
            "files_scanned": len(scanned),
            "files_total": len(files),
            "error": "",
        }

    def test_profile(profile_json: dict, max_commits: int = 0) -> dict:
        """Validate a candidate RepoProfile and run the Phase-1 engine on a sample.

        Pass the full profile as a JSON dict (the same shape ``save_profile``
        writes). This:
          1. validates it against the ``RepoProfile`` schema (returns the
             pydantic error if malformed — fix and retry);
          2. runs ``mine_repo`` over the most recent ``max_commits`` commits
             (0 = ``deps.default_test_commits``) and reports how many eval
             challenges it mines, with a few worked examples (decl, hole kind,
             file, message);
          3. runs the message + diff enrichment passes and reports the
             commit-class distribution and the top tactic tags / groups.

        This is your feedback loop — iterate on the profile until challenges are
        being mined and the class distribution looks sane for a proof repo
        (a healthy mix of proof_complete / proof_add, infra separated out).
        NOTE: results are over a *sample* (recent history), so absolute counts
        are lower than a full-history run; use them comparatively.
        """
        from collections import Counter

        from scaffold.analyzers import ProfileAnalyzer
        from scaffold.git_walker import dump_commits, mine_repo
        from scaffold.pattern_detector import (
            assign_tactic_groups,
            enrich_record,
            enrich_record_with_diff,
        )
        from scaffold.profile import RepoProfile

        try:
            profile = RepoProfile.model_validate(profile_json)
        except Exception as exc:  # pydantic ValidationError or shape error
            return {"ok": False, "error": f"profile failed validation: {exc}"}

        n = max_commits if max_commits > 0 else deps.default_test_commits
        compiled = profile.compiled()
        analyzer = ProfileAnalyzer(compiled)

        try:
            mined = mine_repo(repo, repo.name, analyzer, max_commits=n)
        except Exception as exc:
            return {"ok": False, "error": f"mine_repo crashed: {exc}"}

        examples = []
        for ch in mined.challenges[:6]:
            holes = ", ".join(
                f"{h.kind}:{h.enclosing_decl or '?'}" for h in ch.holes_filled[:4]
            )
            examples.append(
                {
                    "file": ch.file_path,
                    "holes_filled": holes,
                    "commit_message": ch.commit_message[:120],
                }
            )

        # Classification + tactic signal over the same sample.
        records = dump_commits(repo, compiled, max_commits=n)
        proof_records = [r for r in records if r.touches_proof_files]
        enriched = []
        for r in proof_records:
            r = enrich_record(r, compiled)
            r = enrich_record_with_diff(r, repo, compiled)
            enriched.append(r)

        class_dist = Counter(r.commit_class.value for r in enriched)
        tactic_dist: Counter[str] = Counter()
        group_dist: Counter[str] = Counter()
        for r in enriched:
            tactic_dist.update(r.tactic_tags)
            group_dist.update(assign_tactic_groups(r.tactic_tags, compiled))

        deps.log.append(
            f"test_profile commits={n} challenges={mined.total_challenges} "
            f"proof_commits={len(proof_records)}"
        )
        return {
            "ok": True,
            "error": "",
            "commits_scanned": mined.total_commits_scanned,
            "challenges_mined": mined.total_challenges,
            "proof_file_commits": len(proof_records),
            "example_challenges": examples,
            "commit_class_distribution": dict(class_dist.most_common()),
            "top_tactic_tags": dict(tactic_dist.most_common(20)),
            "tactic_group_distribution": dict(group_dist.most_common()),
        }

    tools: dict[str, Callable] = {
        "list_files": list_files,
        "read_file": read_file,
        "git_log": git_log,
        "sample_commits": sample_commits,
        "git_show": git_show,
        "git_diff": git_diff,
        "count_regex": count_regex,
        "test_profile": test_profile,
    }
    assert set(tools) == set(TOOL_NAMES)
    return tools


def register_tools(agent: "Agent[None, RepoProfile]", deps: ProfilerDeps) -> list[str]:
    """Attach the calibration tools to ``agent``; return their names.

    The returned list is exactly what to hand ``CodeMode(tools=[...])`` so the
    tools are sandboxed while the final ``RepoProfile`` output tool stays native
    (the model can't construct a pydantic object inside the sandbox).
    """
    for fn in build_tools(deps).values():
        agent.tool_plain(fn)
    return list(TOOL_NAMES)
