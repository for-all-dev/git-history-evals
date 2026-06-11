"""Git history walker — mines proof engineering repos for eval challenges.

Uses raw git subprocess calls for performance on large repos (no GitPython).
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scaffold.analyzers import ProfileAnalyzer
from scaffold.models import (
    ChallengeType,
    CommitClass,
    CommitRecord,
    EvalChallenge,
    MiningResult,
)
from scaffold.profile import CompiledProfile

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
    """Run a git command in the given repo.

    Uses ``errors="replace"`` so that non-UTF-8 bytes (e.g. Latin-1 French
    comments in early CompCert history) are replaced with U+FFFD instead of
    raising ``UnicodeDecodeError``.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        check=check,
    )
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout.decode("utf-8", errors="replace"),
        stderr=result.stderr.decode("utf-8", errors="replace"),
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
    result = _run_git(repo_path, "show", f"{commit_hash}:{file_path}", check=False)
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
    analyzer: ProfileAnalyzer,
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
    return [f for f in result.stdout.strip().splitlines() if analyzer.matches_file(f)]


# ---------------------------------------------------------------------------
# Diff-based proof analysis
# ---------------------------------------------------------------------------


def analyze_proof_diff(
    repo_path: str | Path,
    parent_hash: str,
    commit_hash: str,
    proof_files: list[str],
    compiled: CompiledProfile,
) -> dict:
    """Read the actual diff for proof files and return proof-content signals.

    All language-specific patterns (hole markers, tactic vocabulary, proof-style
    signals) come from ``compiled``. Returns a dict with:
      sorry_removed      — bool: a hole word was net-removed (not just moved)
      net_proof_lines    — int:  added_lines - removed_lines across all proof files
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

    if not proof_files or not parent_hash:
        return empty

    result = _run_git(
        repo_path,
        "diff",
        parent_hash,
        commit_hash,
        "--",
        *proof_files,
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

    hole_res = compiled.hole_res

    def _has_hole(line: str) -> bool:
        return any(p.search(line) for p, _ in hole_res)

    # Net hole removal: count holes in removed vs added lines
    holes_removed = sum(1 for line in removed if _has_hole(line))
    holes_added = sum(1 for line in added if _has_hole(line))
    sorry_removed = holes_removed > holes_added  # net removal

    # Tactics from added lines
    tactic_hits = compiled.tactic_re.findall("\n".join(added))
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
    term_re = compiled.proof_style_res.get("term_mode")
    ssr_re = compiled.proof_style_res.get("ssreflect")
    has_term = bool(term_re and term_re.search(added_text))
    has_ssr = bool(ssr_re and ssr_re.search(added_text))
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
    analyzer: ProfileAnalyzer,
    repo_name: str,
) -> list[EvalChallenge]:
    """Mine a single commit for eval challenges."""
    if not commit.parent_hash:
        return []

    modified = get_modified_files(repo_path, commit.parent_hash, commit.hash, analyzer)
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
            f"{h.kind} in {h.enclosing_decl or 'unknown'}" for h in filled
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


# ---------------------------------------------------------------------------
# Declaration-aware parsing (for spec_change splicing)
# ---------------------------------------------------------------------------

# Default (Coq) proof-start and proof-end patterns. Profiles carry their own
# via proof_start_regex / proof_end_regex (compiled.proof_start_re /
# proof_end_re); these remain as fallbacks for callers without a profile.
_PROOF_START_RE = re.compile(r"^\s*Proof\b", re.MULTILINE)
_PROOF_END_RE = re.compile(r"^\s*(?:Qed|Defined|Admitted|Abort)\s*\.", re.MULTILINE)
# Coq declaration keyword that introduces a named theorem/lemma.
_COQ_DECL_RE = re.compile(
    r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Fact|Remark|Example"
    r"|Definition|Fixpoint|Program)\s+(\w+)",
    re.MULTILINE,
)


@dataclass
class DeclSpan:
    """A parsed declaration span with its statement and proof body."""

    name: str
    # Line offsets (0-based) in the source file.
    decl_start: int  # first line of the declaration
    proof_start: int | None  # line of ``Proof.`` (None if no explicit Proof)
    proof_end: int  # line of ``Qed.``/``Defined.``/``Admitted.``
    # Extracted text slices.
    statement: str  # from decl keyword up to (but not including) Proof.
    proof_body: str  # from Proof. through Qed./Defined.


def parse_decl_spans(
    content: str,
    declaration_res: list[re.Pattern[str]],
    proof_start_re: re.Pattern[str] | None = None,
    proof_end_re: re.Pattern[str] | None = None,
) -> list[DeclSpan]:
    """Parse top-level declaration spans from proof source content.

    Returns a list of ``DeclSpan`` objects sorted by position. Each span
    covers the declaration statement (up to the proof-start marker) and the
    proof body (proof start through the terminator). The proof-span regexes
    come from the profile (``proof_start_regex`` / ``proof_end_regex``);
    omitted, they fall back to the Coq patterns (``Proof.`` … ``Qed.``).

    Falls back to ``_COQ_DECL_RE`` if *declaration_res* is empty.
    """
    proof_start = proof_start_re or _PROOF_START_RE
    proof_end = proof_end_re or _PROOF_END_RE
    decl_patterns = declaration_res or [_COQ_DECL_RE]
    lines = content.splitlines()

    # Find all declaration start positions: (line_idx, name).
    decl_starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        for pat in decl_patterns:
            m = pat.search(line)
            if m:
                decl_starts.append((i, m.group(1)))
                break  # one match per line is enough

    if not decl_starts:
        return []

    spans: list[DeclSpan] = []
    for idx, (decl_line, name) in enumerate(decl_starts):
        # Search range: from this decl to the next decl (or EOF).
        end_bound = (
            decl_starts[idx + 1][0] if idx + 1 < len(decl_starts) else len(lines)
        )
        region = "\n".join(lines[decl_line:end_bound])

        # Find ``Proof.`` within the region.
        pm = proof_start.search(region)
        if pm:
            # Count newlines up to the match to get relative line offset.
            proof_line_rel = region[: pm.start()].count("\n")
            proof_line_abs = decl_line + proof_line_rel
            statement = "\n".join(lines[decl_line:proof_line_abs])
        else:
            proof_line_abs = None
            # No explicit Proof. — statement is the declaration line itself.
            statement = lines[decl_line]

        # Find the proof terminator.
        search_start = (proof_line_abs - decl_line) if proof_line_abs is not None else 0
        # Search after the Proof. line (or from the decl line).
        tail = "\n".join(lines[decl_line + search_start : end_bound])
        em = proof_end.search(tail)
        if em:
            end_line_rel = tail[: em.start()].count("\n")
            end_line_abs = decl_line + search_start + end_line_rel
        else:
            # No terminator found — skip this declaration.
            continue

        body_start = proof_line_abs if proof_line_abs is not None else decl_line
        proof_body = "\n".join(lines[body_start : end_line_abs + 1])

        spans.append(
            DeclSpan(
                name=name,
                decl_start=decl_line,
                proof_start=proof_line_abs,
                proof_end=end_line_abs,
                statement=statement,
                proof_body=proof_body,
            )
        )

    return spans


def splice_spec_change(
    parent_content: str,
    child_content: str,
    declaration_res: list[re.Pattern[str]],
    proof_start_re: re.Pattern[str] | None = None,
    proof_end_re: re.Pattern[str] | None = None,
) -> tuple[str, list[str]] | None:
    """Create a spec_change challenge by splicing new statement + old proof.

    Returns ``(spliced_content, changed_decl_names)`` or ``None`` if no
    statement changed between parent and child.
    """
    parent_spans = parse_decl_spans(
        parent_content, declaration_res, proof_start_re, proof_end_re
    )
    child_spans = parse_decl_spans(
        child_content, declaration_res, proof_start_re, proof_end_re
    )

    parent_by_name = {s.name: s for s in parent_spans}
    child_by_name = {s.name: s for s in child_spans}

    changed_names: list[str] = []
    for name, child_span in child_by_name.items():
        parent_span = parent_by_name.get(name)
        if parent_span is None:
            continue
        # Statement changed but same declaration exists in both.
        if child_span.statement.strip() != parent_span.statement.strip():
            changed_names.append(name)

    if not changed_names:
        return None

    # Build spliced content: start with child content, then for each changed
    # declaration, replace the child's proof body with the parent's proof body.
    child_lines = child_content.splitlines()
    # Work backwards so line indices stay valid.
    replacements: list[tuple[int, int, str]] = []
    for name in changed_names:
        child_span = child_by_name[name]
        parent_span = parent_by_name[name]
        if child_span.proof_start is None or parent_span.proof_start is None:
            continue
        # Replace from child's Proof. line through Qed. with parent's proof body.
        replacements.append(
            (child_span.proof_start, child_span.proof_end, parent_span.proof_body)
        )

    if not replacements:
        return None

    # Sort by start line descending so replacements don't shift indices.
    replacements.sort(key=lambda r: r[0], reverse=True)
    result_lines = list(child_lines)
    for start, end, body in replacements:
        result_lines[start : end + 1] = body.splitlines()

    return "\n".join(result_lines), changed_names


# ---------------------------------------------------------------------------
# New challenge formulations (proof_optimise, proof_add, spec_change)
# ---------------------------------------------------------------------------


def mine_proof_optimise_commit(
    repo_path: str | Path,
    record: CommitRecord,
    analyzer: ProfileAnalyzer,
    repo_name: str,
) -> list[EvalChallenge]:
    """Mine a proof_optimise commit: challenge is the longer proof, solution is shorter."""
    parent = record.parent_hashes[0] if record.parent_hashes else ""
    if not parent or not record.proof_files_changed:
        return []

    challenges: list[EvalChallenge] = []
    for fpath in record.proof_files_changed:
        parent_content = get_file_at_commit(repo_path, parent, fpath)
        child_content = get_file_at_commit(repo_path, record.hash, fpath)
        if parent_content is None or child_content is None:
            continue

        diff = get_diff_text(repo_path, parent, record.hash, fpath)
        instructions = (
            f"Simplify or optimize the proof(s) in {fpath}. "
            f"Produce a shorter or cleaner version that still compiles."
        )

        challenges.append(
            EvalChallenge(
                task_id=_make_task_id(repo_name, record.hash, fpath),
                repo=repo_name,
                proof_assistant=analyzer.proof_assistant,
                commit_hash=record.hash,
                parent_hash=parent,
                commit_message=record.message_subject,
                file_path=fpath,
                challenge_type=ChallengeType.proof_optimise,
                challenge_file_content=parent_content,
                solution_file_content=child_content,
                diff=diff,
                instructions=instructions,
            )
        )

    return challenges


def mine_proof_add_commit(
    repo_path: str | Path,
    record: CommitRecord,
    analyzer: ProfileAnalyzer,
    repo_name: str,
) -> list[EvalChallenge]:
    """Mine a proof_add commit: challenge is the file before, solution has proof added."""
    parent = record.parent_hashes[0] if record.parent_hashes else ""
    if not parent or not record.proof_files_changed:
        return []

    challenges: list[EvalChallenge] = []
    for fpath in record.proof_files_changed:
        parent_content = get_file_at_commit(repo_path, parent, fpath)
        child_content = get_file_at_commit(repo_path, record.hash, fpath)

        if child_content is None:
            continue

        diff = get_diff_text(repo_path, parent, record.hash, fpath)

        if parent_content is None:
            # New file added — challenge is to write the proof from scratch.
            instructions = f"Write the proof content for the declarations in {fpath}."
            challenge_content = ""
        else:
            instructions = (
                f"Write or extend the proof(s) in {fpath}. "
                f"Complete any unfinished proofs or add missing proof content."
            )
            challenge_content = parent_content

        challenges.append(
            EvalChallenge(
                task_id=_make_task_id(repo_name, record.hash, fpath),
                repo=repo_name,
                proof_assistant=analyzer.proof_assistant,
                commit_hash=record.hash,
                parent_hash=parent,
                commit_message=record.message_subject,
                file_path=fpath,
                challenge_type=ChallengeType.proof_add,
                challenge_file_content=challenge_content,
                solution_file_content=child_content,
                diff=diff,
                instructions=instructions,
            )
        )

    return challenges


def mine_spec_change_commit(
    repo_path: str | Path,
    record: CommitRecord,
    analyzer: ProfileAnalyzer,
    repo_name: str,
) -> list[EvalChallenge]:
    """Mine a spec_change commit: splice new statement + old proof as challenge."""
    parent = record.parent_hashes[0] if record.parent_hashes else ""
    if not parent or not record.proof_files_changed:
        return []

    compiled = analyzer._c
    challenges: list[EvalChallenge] = []

    for fpath in record.proof_files_changed:
        parent_content = get_file_at_commit(repo_path, parent, fpath)
        child_content = get_file_at_commit(repo_path, record.hash, fpath)
        if parent_content is None or child_content is None:
            continue

        result = splice_spec_change(
            parent_content,
            child_content,
            compiled.declaration_res,
            compiled.proof_start_re,
            compiled.proof_end_re,
        )
        if result is None:
            continue

        spliced_content, changed_names = result
        diff = get_diff_text(repo_path, parent, record.hash, fpath)
        decl_list = ", ".join(f"`{n}`" for n in changed_names)

        instructions = (
            f"The statement of {decl_list} in {fpath} was modified. "
            f"Adapt the proof to the new statement."
        )

        challenges.append(
            EvalChallenge(
                task_id=_make_task_id(repo_name, record.hash, fpath),
                repo=repo_name,
                proof_assistant=analyzer.proof_assistant,
                commit_hash=record.hash,
                parent_hash=parent,
                commit_message=record.message_subject,
                file_path=fpath,
                challenge_type=ChallengeType.spec_change,
                challenge_file_content=spliced_content,
                solution_file_content=child_content,
                diff=diff,
                instructions=instructions,
            )
        )

    return challenges


# ---------------------------------------------------------------------------
# Enriched mining — dispatch by commit class
# ---------------------------------------------------------------------------

# Map from CommitClass to the miner function that handles it.
_CLASS_MINERS = {
    CommitClass.proof_optimise: mine_proof_optimise_commit,
    CommitClass.proof_add: mine_proof_add_commit,
    CommitClass.proof_new: mine_proof_add_commit,  # same formulation as proof_add
    CommitClass.spec_change: mine_spec_change_commit,
}


def mine_from_enriched(
    repo_path: str | Path,
    repo_name: str,
    analyzer: ProfileAnalyzer,
    records: list[CommitRecord],
    classes: set[str] | None = None,
) -> MiningResult:
    """Mine challenges from pre-classified enriched commit records.

    Args:
        repo_path: Path to the git repo.
        repo_name: Human-readable repo name.
        analyzer: Profile-driven proof analyzer.
        records: Enriched CommitRecords (with commit_class set).
        classes: Optional set of commit class names to mine. If None, mines
            all supported classes (proof_optimise, proof_add, proof_new,
            spec_change). Pass ``{"proof_complete"}`` to include hole-filling
            via the existing ``mine_commit`` path.
    """
    target_classes = classes or {c.value for c in _CLASS_MINERS}
    all_challenges: list[EvalChallenge] = []

    for i, record in enumerate(records):
        if i % 200 == 0:
            logger.info("Enriched mining progress: %d/%d records", i, len(records))

        if record.commit_class.value not in target_classes:
            continue

        if record.commit_class == CommitClass.proof_complete:
            # Use existing hole-filling miner.
            parent = record.parent_hashes[0] if record.parent_hashes else ""
            if not parent:
                continue
            commit = RawCommit(
                hash=record.hash,
                parent_hash=parent,
                author=record.author,
                date=record.date,
                message=record.message_subject,
            )
            challenges = mine_commit(repo_path, commit, analyzer, repo_name)
            all_challenges.extend(challenges)
            continue

        miner_fn = _CLASS_MINERS.get(record.commit_class)
        if miner_fn is None:
            continue

        challenges = miner_fn(repo_path, record, analyzer, repo_name)
        if challenges:
            logger.info(
                "  %s [%s]: %d challenges",
                record.hash[:8],
                record.commit_class.value,
                len(challenges),
            )
            all_challenges.extend(challenges)

    return MiningResult(
        repo_name=repo_name,
        proof_assistant=analyzer.proof_assistant,
        total_commits_scanned=len(records),
        total_challenges=len(all_challenges),
        challenges=all_challenges,
    )


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
    compiled: CompiledProfile,
    start_ref: str = "HEAD",
    max_commits: int | None = None,
) -> list[CommitRecord]:
    """Walk every commit and return a flat CommitRecord for each one.

    A single ``git log --numstat`` call is used to avoid per-commit subprocess
    overhead across potentially thousands of commits. Proof-file membership is
    decided by the profile's ``proof_file_globs`` minus ``exclude_globs``.
    """
    analyzer = ProfileAnalyzer(compiled)

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

        proof_files = [f for f in all_files if analyzer.matches_file(f)]

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
                proof_files_changed=proof_files,
                touches_proof_files=bool(proof_files),
            )
        )

    logger.info("Extracted %d commit records from %s", len(records), repo_path)
    return records


def mine_repo(
    repo_path: str | Path,
    repo_name: str,
    analyzer: ProfileAnalyzer,
    max_commits: int | None = None,
    start_ref: str = "HEAD",
    dry_run: bool = False,
) -> MiningResult:
    """Main pipeline: walk commits, find proof-file diffs, detect filled holes.

    Args:
        repo_path: Path to the git repo.
        repo_name: Human-readable repo name (used in task IDs / output).
        analyzer: Profile-driven proof analyzer.
        max_commits: Limit number of commits to scan.
        start_ref: Git ref to start walking from.
        dry_run: If True, log candidates but don't build full challenges.
    """
    commits = iter_commits(repo_path, start_ref, max_commits)
    logger.info("Scanning %d commits in %s", len(commits), repo_name)

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

        challenges = mine_commit(repo_path, commit, analyzer, repo_name)
        if challenges:
            logger.info("  %s: %d challenges found", commit.hash[:8], len(challenges))
            all_challenges.extend(challenges)

    return MiningResult(
        repo_name=repo_name,
        proof_assistant=analyzer.proof_assistant,
        total_commits_scanned=len(commits),
        total_challenges=len(all_challenges),
        challenges=all_challenges,
    )
