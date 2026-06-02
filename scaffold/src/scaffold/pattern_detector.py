"""Commit classification and enrichment — fully profile-driven.

Every pattern (commit-message signal banks, proof-context regex, tactic
vocabulary, tactic groups, keyword terms) comes from a ``CompiledProfile``
instead of module-level constants. The classification decision-tree *structure*
lives here; the regexes it consults live in the profile.
"""

from __future__ import annotations

import logging
from pathlib import Path

from scaffold.models import CommitClass, CommitRecord
from scaffold.profile import CompiledProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------


def extract_keywords(subject: str, body: str, compiled: CompiledProfile) -> list[str]:
    """Extract proof-relevant keywords from subject and body text."""
    text = f"{subject} {body}"
    seen: set[str] = set()
    result: list[str] = []
    for m in compiled.keyword_re.findall(text):
        low = m.lower()
        if low not in seen:
            seen.add(low)
            result.append(low)
    return result


# ---------------------------------------------------------------------------
# Tactic groups
# ---------------------------------------------------------------------------


def assign_tactic_groups(
    tactic_tags: list[str], compiled: CompiledProfile
) -> list[str]:
    """Map a list of tactic names to their unique behavioural groups."""
    groups_map = compiled.tactic_groups_lower
    seen: set[str] = set()
    groups: list[str] = []
    for t in tactic_tags:
        g = groups_map.get(t.lower())
        if g and g not in seen:
            seen.add(g)
            groups.append(g)
    return groups


# ---------------------------------------------------------------------------
# Commit classification
# ---------------------------------------------------------------------------


def classify_commit(record: CommitRecord, compiled: CompiledProfile) -> CommitClass:
    """Classify a CommitRecord using the profile's signal banks.

    Priority order (highest wins) — structure preserved from the original
    hand-tuned classifier; only the regexes are now profile-supplied:
      1. infra          — dependency bumps / CI are almost never proof-relevant
      2. proof_complete — a hole was removed / proof closed
      3. spec_change    — theorem statement was changed
      4. proof_new      — new lemma/theorem added with a proof
      5. proof_add      — partial proof work (goals still open)
      6. refactor / fix (on proof files -> proof_add; else their own class)
      7. other
    """
    sig = compiled.commit_signal_res
    ctx = compiled.proof_context_re

    def has(name: str, text: str) -> bool:
        p = sig.get(name)
        return bool(p and p.search(text))

    def has_ctx(text: str) -> bool:
        return bool(ctx and ctx.search(text))

    text = f"{record.message_subject} {record.message_body}".lower()
    subject = record.message_subject.lower()

    # 1. Infrastructure noise — check subject only to avoid body false positives.
    if has("infra", subject) and not has_ctx(subject):
        return CommitClass.infra

    # 2. proof_complete — explicit hole closure language.
    if has("proof_complete", text) and has_ctx(text):
        return CommitClass.proof_complete

    # Structural heuristic for body-free commits: net deletions in proof files
    # with proof-context subject => likely a hole was removed.
    if (
        record.touches_proof_files
        and record.deletions > record.insertions
        and not record.message_body
        and has_ctx(text)
        and not has("infra", text)
    ):
        return CommitClass.proof_complete

    # 3. spec_change — statement-level edits.
    if has("spec_change", text) and record.touches_proof_files:
        return CommitClass.spec_change

    # 4. proof_new — new lemma or theorem added.
    if has("proof_new", text) and record.touches_proof_files:
        return CommitClass.proof_new

    # 5. proof_add — partial or incremental proof work.
    if has("proof_add", text) and record.touches_proof_files:
        return CommitClass.proof_add

    # 6. refactor / fix touching proof files -> proof_add.
    if record.touches_proof_files:
        if has("refactor", text) or has("fix", text):
            return CommitClass.proof_add

    # 7. refactor / fix on non-proof files stay as their own class.
    if has("refactor", text):
        return CommitClass.refactor
    if has("fix", text):
        return CommitClass.fix

    # 8. Any remaining proof-file-touching commit -> proof_add.
    if record.touches_proof_files:
        return CommitClass.proof_add

    return CommitClass.other


def enrich_record(record: CommitRecord, compiled: CompiledProfile) -> CommitRecord:
    """Return a copy with commit_class and keywords populated (message-only)."""
    kw = extract_keywords(record.message_subject, record.message_body, compiled)
    cls = classify_commit(record, compiled)
    return record.model_copy(
        update={"commit_class": cls, "keywords": kw, "class_confidence": "heuristic"}
    )


def enrich_record_with_diff(
    record: CommitRecord,
    repo_path: str | Path,
    compiled: CompiledProfile,
) -> CommitRecord:
    """Enrich a record using the actual proof-file diffs for this commit.

    Authoritative second pass: supersedes the message-heuristic class for any
    commit that touches proof files.

      1. hole net-removed in diff              -> proof_complete
      2. net_proof_lines < 0 (proof shrank)    -> proof_optimise
      3. net_proof_lines >= 0 (grew/unchanged) -> proof_add
         (tactic_tags & proof_style populated)

    proof_new / spec_change (message-only, semantic) are kept when already set.
    """
    from scaffold.git_walker import analyze_proof_diff

    if not record.proof_files_changed:
        return record

    parent = record.parent_hashes[0] if record.parent_hashes else ""
    diff_data = analyze_proof_diff(
        repo_path,
        parent,
        record.hash,
        record.proof_files_changed,
        compiled,
    )

    if diff_data["sorry_removed"]:
        new_class = CommitClass.proof_complete
    elif diff_data["net_proof_lines"] < 0:
        new_class = CommitClass.proof_optimise
    else:
        new_class = CommitClass.proof_add

    if record.commit_class in (CommitClass.proof_new, CommitClass.spec_change):
        new_class = record.commit_class

    return record.model_copy(
        update={
            "commit_class": new_class,
            "class_confidence": "diff",
            "diff_sorry_removed": diff_data["sorry_removed"],
            "diff_net_proof_lines": diff_data["net_proof_lines"],
            "tactic_tags": diff_data["tactic_tags"],
            "proof_style": diff_data["proof_style"],
        }
    )
