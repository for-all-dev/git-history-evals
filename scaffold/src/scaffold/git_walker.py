"""Git history walker — mines proof engineering repos for eval challenges.

Uses raw git subprocess calls for performance on large repos (no GitPython).
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import re

from scaffold.analyzers.base import ProofAnalyzer
from scaffold.models import (
    CommitRecord,
    EvalChallenge,
    MiningResult,
    RepoMetadata,
)

logger = logging.getLogger(__name__)

# git log format: hash, parent hash, author, date (ISO), subject
_LOG_FORMAT = "%H%x00%P%x00%an%x00%aI%x00%s"
_LOG_SEP = "\x00"

# Separators for dump_commits — all plain ASCII, safe as subprocess args.
# git expands %xNN escapes in its output, so the format string itself is clean.
_COMMIT_SEP = "\x1e"  # ASCII Record Separator — between commits in output
_FIELD_SEP = "\x01"  # ASCII SOH — between header fields in output
_META_END = "\x1f"  # ASCII Unit Separator — between header and numstat block

# git log format for dump_commits (uses %xNN escapes, not literal bytes):
_DUMP_FORMAT = "%x1e%H%x01%P%x01%an%x01%ae%x01%aI%x01%s%x01%b%x1f"


@dataclass
class RawCommit:
    hash: str
    parent_hash: str
    author: str
    date: str
    message: str


def _run_git(
    repo_path: str | Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a git command in the given repo."""
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def iter_commits(
    repo_path: str | Path,
    start_ref: str = "HEAD",
    max_commits: int | None = None,
) -> list[RawCommit]:
    """List commits from start_ref backwards."""
    cmd = ["log", f"--format={_LOG_FORMAT}", start_ref]
    if max_commits is not None:
        cmd.append(f"-n{max_commits}")

    result = _run_git(repo_path, *cmd)
    commits: list[RawCommit] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(_LOG_SEP)
        if len(parts) < 5:
            continue
        parent = parts[1].split()[0] if parts[1] else ""
        commits.append(
            RawCommit(
                hash=parts[0],
                parent_hash=parent,
                author=parts[2],
                date=parts[3],
                message=parts[4],
            )
        )
    return commits


def get_file_at_commit(
    repo_path: str | Path, commit_hash: str, file_path: str
) -> str | None:
    """Get file content at a specific commit without checkout."""
    result = _run_git(
        repo_path, "show", f"{commit_hash}:{file_path}", check=False
    )
    if result.returncode != 0:
        return None
    return result.stdout


def get_diff_text(
    repo_path: str | Path, parent_hash: str, child_hash: str, file_path: str
) -> str:
    """Get unified diff for a single file between two commits."""
    result = _run_git(
        repo_path,
        "diff",
        parent_hash,
        child_hash,
        "--",
        file_path,
        check=False,
    )
    return result.stdout


def get_modified_files(
    repo_path: str | Path,
    parent_hash: str,
    child_hash: str,
    analyzer: ProofAnalyzer,
) -> list[str]:
    """Get list of proof files modified between parent and child commits."""
    result = _run_git(
        repo_path,
        "diff",
        "--name-only",
        "--diff-filter=M",
        parent_hash,
        child_hash,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [
        f
        for f in result.stdout.strip().splitlines()
        if analyzer.matches_file(f)
    ]


# ---------------------------------------------------------------------------
# Diff-based proof analysis
# ---------------------------------------------------------------------------

# Matches a sorry/Admitted/admit/oops as a standalone word (not in a comment
# that has been kept, so we check any occurrence in the raw diff line).
_HOLE_RE = re.compile(r"\b(sorry|Admitted|admit|oops)\b")

# Tactics that appear at the start of a tactic position (after whitespace /
# bullets / semicolons).  We scan every added line for these.
_TACTIC_RE = re.compile(
    r"(?:^|[\s;|{(])("
    # Core intro/elim
    r"intro[s]?|revert|clear|clearbody|rename|move"
    r"|destruct|case(?:_eq)?|induction|elim(?:type)?|inversion(?:_clear)?"
    r"|injection|discriminate|constructor|econstructor"
    r"|left|right|split|exists|eexists"
    # Rewriting
    r"|rewrite|erewrite|setoid_rewrite|rewrite_strat|replace"
    r"|symmetry|transitivity|etransitivity|subst|congruence"
    # Application
    r"|apply|eapply|exact(?:_no_check)?|refine|change|convert"
    r"|rapply|lapply|specialize|generalize|instantiate"
    r"|pose|remember|set|assert|cut|enough|have|suff(?:ices)?"
    # Automation
    r"|auto|eauto|tauto|intuition|firstorder|trivial|easy|done"
    r"|decide|btauto|contradiction|absurd|exfalso|assumption"
    # Arithmetic solvers
    r"|omega|lia|lra|nia|nra|psatz|ring(?:_simplify|_nf)?"
    r"|field(?:_simplify)?|norm_num|zify|push_cast|pull_cast"
    r"|push_neg|pull_neg"
    # Simplification / reduction
    r"|simpl|cbn|cbv|lazy|vm_compute|native_compute"
    r"|unfold|fold|red|hnf|compute|delta|beta|iota|zeta"
    r"|norm_cast|simp"
    # ssreflect / mathcomp
    r"|by|congr|wlog|without_loss|reflect"
    # Ltac control
    r"|repeat|try|first|do|progress|timeout|once|solve|fail|idtac"
    r"|abstract|pattern"
    r")(?:\s|[.;()\[\]{]|$)",
    re.IGNORECASE | re.MULTILINE,
)

# Term-mode / lambda proof signals in added lines
_TERM_MODE_RE = re.compile(
    r"\bfun\s+\w"          # fun x =>  (lambda)
    r"|\bλ\s*\w"           # unicode lambda
    r"|\bmatch\s+\w"       # match expression
    r"|\bfix\s+\w"         # recursive definition
    r"|\blet\s+\w+\s*:="   # let binding
    r"|\bexist\s*[({]"     # dependent pair
    r"|\bconj\b"           # conjunction intro in term mode
)

# ssreflect-style signals: heavy use of / ; [] move: => in tactic position
_SSREFLECT_RE = re.compile(
    r"^\s*(?:move|case|elim|apply|rewrite|have|suff|set|pose)\s*[/:[\]]",
    re.MULTILINE,
)


def analyze_proof_diff(
    repo_path: str | Path,
    parent_hash: str,
    commit_hash: str,
    coq_files: list[str],
) -> dict:
    """Read the actual diff for .v files and return proof-content signals.

    Returns a dict with:
      sorry_removed      — bool: a hole word was net-removed (not just moved)
      net_proof_lines    — int:  added_lines - removed_lines across all .v files
      added_count        — int:  raw count of added lines
      removed_count      — int:  raw count of removed lines
      tactic_tags        — list[str]: unique tactics found in added lines
      proof_style        — list[str]: 'tactic_mode'|'term_mode'|'ssreflect'|'mixed'
    """
    empty: dict = {
        "sorry_removed": False,
        "net_proof_lines": 0,
        "added_count": 0,
        "removed_count": 0,
        "tactic_tags": [],
        "proof_style": [],
    }

    if not coq_files or not parent_hash:
        return empty

    result = _run_git(
        repo_path,
        "diff",
        parent_hash,
        commit_hash,
        "--",
        *coq_files,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return empty

    added: list[str] = []
    removed: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])

    # Net hole removal: count holes in removed vs added lines
    holes_removed = sum(1 for l in removed if _HOLE_RE.search(l))
    holes_added = sum(1 for l in added if _HOLE_RE.search(l))
    sorry_removed = holes_removed > holes_added  # net removal

    # Tactics from added lines
    tactic_hits = _TACTIC_RE.findall("\n".join(added))
    tactic_tags: list[str] = []
    seen: set[str] = set()
    for t in tactic_hits:
        low = t.lower().strip()
        if low and low not in seen:
            seen.add(low)
            tactic_tags.append(low)

    # Proof style detection on added lines
    added_text = "\n".join(added)
    styles: list[str] = []
    has_term = bool(_TERM_MODE_RE.search(added_text))
    has_ssr = bool(_SSREFLECT_RE.search(added_text))
    has_tactic = bool(tactic_tags)

    if has_ssr:
        styles.append("ssreflect")
    if has_term:
        styles.append("term_mode")
    if has_tactic and not has_ssr:
        styles.append("tactic_mode")
    if has_term and has_tactic:
        # Replace both with 'mixed'
        styles = [s for s in styles if s not in ("term_mode", "tactic_mode")]
        styles.append("mixed")
    if not styles:
        styles.append("unknown")

    return {
        "sorry_removed": sorry_removed,
        "net_proof_lines": len(added) - len(removed),
        "added_count": len(added),
        "removed_count": len(removed),
        "tactic_tags": tactic_tags,
        "proof_style": styles,
    }


def _make_task_id(repo_name: str, commit_hash: str, file_path: str) -> str:
    """Create a deterministic task ID."""
    raw = f"{repo_name}_{commit_hash[:12]}_{file_path}"
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{repo_name}_{commit_hash[:8]}_{suffix}"


def mine_commit(
    repo_path: str | Path,
    commit: RawCommit,
    analyzer: ProofAnalyzer,
    repo_name: str,
) -> list[EvalChallenge]:
    """Mine a single commit for eval challenges."""
    if not commit.parent_hash:
        return []

    modified = get_modified_files(
        repo_path, commit.parent_hash, commit.hash, analyzer
    )
    challenges: list[EvalChallenge] = []

    for fpath in modified:
        parent_content = get_file_at_commit(repo_path, commit.parent_hash, fpath)
        child_content = get_file_at_commit(repo_path, commit.hash, fpath)

        if parent_content is None or child_content is None:
            continue

        filled = analyzer.find_filled_holes(parent_content, child_content, fpath)
        if not filled:
            continue

        diff = get_diff_text(repo_path, commit.parent_hash, commit.hash, fpath)

        hole_descriptions = ", ".join(
            f"{h.kind.value} in {h.enclosing_decl or 'unknown'}" for h in filled
        )
        instructions = (
            f"Fill in the proof(s) marked with placeholder tactics "
            f"({hole_descriptions}) in {fpath}."
        )

        challenges.append(
            EvalChallenge(
                task_id=_make_task_id(repo_name, commit.hash, fpath),
                repo=repo_name,
                proof_assistant=analyzer.proof_assistant,
                commit_hash=commit.hash,
                parent_hash=commit.parent_hash,
                commit_message=commit.message,
                file_path=fpath,
                challenge_file_content=parent_content,
                solution_file_content=child_content,
                holes_filled=filled,
                diff=diff,
                instructions=instructions,
            )
        )

    return challenges


def _parse_numstat_line(line: str) -> tuple[int, int, str] | None:
    """Parse one --numstat line into (additions, deletions, filepath).

    Binary files are reported as '-\\t-\\tpath'; we record them as (0, 0, path).
    """
    parts = line.split("\t", 2)
    if len(parts) != 3:
        return None
    add_raw, del_raw, fpath = parts
    try:
        add = int(add_raw) if add_raw != "-" else 0
        sub = int(del_raw) if del_raw != "-" else 0
    except ValueError:
        return None
    return add, sub, fpath.strip()


def dump_commits(
    repo_path: str | Path,
    start_ref: str = "HEAD",
    max_commits: int | None = None,
) -> list[CommitRecord]:
    """Walk every commit and return a flat CommitRecord for each one.

    A single ``git log --numstat`` call is used to avoid per-commit subprocess
    overhead across potentially thousands of commits.
    """
    cmd = [
        "log",
        f"--format={_DUMP_FORMAT}",
        "--numstat",
        start_ref,
    ]
    if max_commits is not None:
        cmd.append(f"-n{max_commits}")

    result = _run_git(repo_path, *cmd)
    raw = result.stdout

    records: list[CommitRecord] = []

    # Each commit block starts with _COMMIT_SEP injected by the format string.
    # Split on it; first element is empty (output starts with the separator).
    blocks = raw.split(_COMMIT_SEP)

    for block in blocks:
        if not block.strip():
            continue

        # Header ends at _META_END; numstat lines follow.
        meta_part, _, stat_part = block.partition(_META_END)

        # Parse header fields.
        fields = meta_part.split(_FIELD_SEP)
        if len(fields) < 6:
            continue
        hash_, parents_raw, author, email, date, subject = fields[:6]
        body = fields[6].strip() if len(fields) > 6 else ""

        parent_hashes = [p for p in parents_raw.split() if p]

        # Parse --numstat lines.
        all_files: list[str] = []
        total_add = total_del = 0
        for line in stat_part.splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = _parse_numstat_line(line)
            if parsed is None:
                continue
            add, sub, fpath = parsed
            all_files.append(fpath)
            total_add += add
            total_del += sub

        coq_files = [f for f in all_files if f.endswith(".v")]

        records.append(
            CommitRecord(
                hash=hash_.strip(),
                parent_hashes=parent_hashes,
                author=author,
                author_email=email,
                date=date,
                message_subject=subject,
                message_body=body,
                files_changed_count=len(all_files),
                insertions=total_add,
                deletions=total_del,
                changed_files=all_files,
                coq_files_changed=coq_files,
                touches_proof_files=bool(coq_files),
            )
        )

    logger.info("Extracted %d commit records from %s", len(records), repo_path)
    return records


def mine_repo(
    metadata: RepoMetadata,
    analyzer: ProofAnalyzer,
    max_commits: int | None = None,
    start_ref: str = "HEAD",
    dry_run: bool = False,
) -> MiningResult:
    """Main pipeline: walk commits, find proof-file diffs, detect filled holes.

    Args:
        metadata: Repository metadata.
        analyzer: Language-specific proof analyzer.
        max_commits: Limit number of commits to scan.
        start_ref: Git ref to start walking from.
        dry_run: If True, log candidates but don't build full challenges.
    """
    repo_path = metadata.local_path
    commits = iter_commits(repo_path, start_ref, max_commits)
    logger.info("Scanning %d commits in %s", len(commits), metadata.name)

    all_challenges: list[EvalChallenge] = []

    for i, commit in enumerate(commits):
        if i % 100 == 0:
            logger.info("Progress: %d/%d commits", i, len(commits))

        if not commit.parent_hash:
            continue

        if dry_run:
            modified = get_modified_files(
                repo_path, commit.parent_hash, commit.hash, analyzer
            )
            if modified:
                logger.info(
                    "  [dry-run] %s: %d proof files modified",
                    commit.hash[:8],
                    len(modified),
                )
            continue

        challenges = mine_commit(repo_path, commit, analyzer, metadata.name)
        if challenges:
            logger.info(
                "  %s: %d challenges found", commit.hash[:8], len(challenges)
            )
            all_challenges.extend(challenges)

    return MiningResult(
        repo_name=metadata.name,
        proof_assistant=analyzer.proof_assistant,
        total_commits_scanned=len(commits),
        total_challenges=len(all_challenges),
        challenges=all_challenges,
    )