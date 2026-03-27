"""Pattern detector — repo analysis pre-pass and commit classification.

Hybrid approach: fast heuristics for known patterns, optional LLM for ambiguous cases.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from scaffold.analyzers import detect_proof_assistant, get_analyzer
from scaffold.models import CommitClass, CommitRecord, ProofAssistant, RepoMetadata

logger = logging.getLogger(__name__)

# Known build systems per proof assistant
_BUILD_FILES: dict[str, tuple[ProofAssistant, str]] = {
    "Makefile": (ProofAssistant.coq, "make"),
    "_CoqProject": (ProofAssistant.coq, "coq_makefile"),
    "lakefile.lean": (ProofAssistant.lean4, "lake build"),
    "lakefile.toml": (ProofAssistant.lean4, "lake build"),
    "ROOT": (ProofAssistant.isabelle, "isabelle build"),
}

# Paths to commonly exclude (generated files, vendored deps, etc.)
_COMMON_EXCLUDES = [
    "vendor",
    "third_party",
    "external",
    "_build",
    "build",
    ".build",
    "node_modules",
    "__pycache__",
]

# ---------------------------------------------------------------------------
# Keyword banks for heuristic classification
# ---------------------------------------------------------------------------

# Signals that an existing sorry/Admitted/oops was *removed* (proof completed)
_COMPLETE_SIGNALS = [
    r"\bclose[ds]?\b",
    r"\bfill(?:ed|ing)?\b",
    r"\bfinish(?:ed|ing)?\b",
    r"\bsolv(?:e[ds]?|ing)\b",
    r"\bremov(?:e[ds]?|ing)\s+(?:sorry|admitted|oops)\b",
    r"\bno\s+(?:more\s+)?sorry\b",
    r"\bno\s+(?:more\s+)?admitted\b",
    r"\bqed\b",
    r"\bproof\s+complete\b",
    r"\bcomplete[ds]?\s+proof\b",
    r"\bresolv(?:e[ds]?|ing)\s+(?:goal|proof|sorry|admitted)\b",
]

# Signals that *new* proof content (lemma/theorem + proof) was added
_NEW_PROOF_SIGNALS = [
    r"\badd(?:ed|ing)?\s+(?:lemma|theorem|corollary|proposition|fact)\b",
    r"\bnew\s+(?:lemma|theorem|corollary|proof)\b",
    r"\bprov(?:e[ds]?|ing)\s+\w+\b",
    r"\bproof\s+of\b",
    r"\blemma\b",
    r"\btheorem\b",
    r"\bcorollary\b",
]

# Signals of partial proof work (adds to proof without necessarily closing it)
_ADD_PROOF_SIGNALS = [
    r"\bwip\b",
    r"\bwork\s+in\s+progress\b",
    r"\bpartial\b",
    r"\bprogress\b",
    r"\bmore\s+(?:cases|tactic|steps)\b",
    r"\btactic\b",
    r"\binduction\b",
    r"\brewrite\b",
    r"\bsimplif\b",
    r"\bapply\b",
    r"\bauto\b",
    r"\bomega\b",
    r"\bring\b",
    r"\bdestructur\b",
    r"\bcase\s+split\b",
    r"\bimprove\s+proof\b",
    r"\bclean\s+up\s+proof\b",
]

# Signals that a theorem *statement* (spec) changed
_SPEC_SIGNALS = [
    r"\bspecif(?:ication|y|ied)\b",
    r"\bstatement\b",
    r"\bgenerali[sz]e\b",
    r"\bstrengthen\b",
    r"\bweaken\b",
    r"\bprecondition\b",
    r"\bpostcondition\b",
    r"\bhypothes[ie]s\b",
    r"\bchanged?\s+(?:spec|statement|type|signature)\b",
    r"\bupdat(?:e[ds]?|ing)\s+(?:spec|statement|type)\b",
    r"\brelax\b",
    r"\brefin(?:e[ds]?|ing)\b",
]

# Signals of infrastructure / noise (dependency bumps, CI, build)
_INFRA_SIGNALS = [
    r"\bbump\b",
    r"\bupgrad(?:e[ds]?|ing)\b",
    r"\bdependabot\b",
    r"\bdependency\b",
    r"\bdependencies\b",
    r"\bci\b",
    r"\bgithub\s+actions?\b",
    r"\bdocker\b",
    r"\bmakefile\b",
    r"\bnix\b",
    r"\bopam\b",
    r"\bsubmodule\b",
    r"\bchore\b",
    r"\brelease\b",
    r"\bversion\b",
]

# Signals of refactoring (no proof content change)
_REFACTOR_SIGNALS = [
    r"\brefactor\b",
    r"\brename\b",
    r"\bmov(?:e[ds]?|ing)\b",
    r"\breorganiz\b",
    r"\bclean(?:\s+up|up)?\b",
    r"\bextract\b",
    r"\bsplit\b",
    r"\bmerge\b",
    r"\bdedup\b",
    r"\bdeduplicat\b",
]

# Signals of a bug fix (non-proof)
_FIX_SIGNALS = [
    r"\bfix(?:es|ed|ing)?\b",
    r"\bbug\b",
    r"\bpatch\b",
    r"\bcorrect\b",
    r"\brepair\b",
    r"\bregression\b",
    r"\bissue\b",
    r"\bworkaround\b",
]

# ---------------------------------------------------------------------------
# Tactic groups — behavioural classification of individual tactics
# ---------------------------------------------------------------------------

# Maps every tracked tactic name (lowercase) to its behavioural group.
# A tactic that could belong to multiple groups is assigned to its primary role.
_TACTIC_GROUPS: dict[str, str] = {
    # --- rewrite_reduce: transform the goal syntactically -------------------
    "rewrite":            "rewrite_reduce",
    "erewrite":           "rewrite_reduce",
    "setoid_rewrite":     "rewrite_reduce",
    "rewrite_strat":      "rewrite_reduce",
    "replace":            "rewrite_reduce",
    "symmetry":           "rewrite_reduce",
    "transitivity":       "rewrite_reduce",
    "etransitivity":      "rewrite_reduce",
    "subst":              "rewrite_reduce",
    "simpl":              "rewrite_reduce",
    "cbn":                "rewrite_reduce",
    "cbv":                "rewrite_reduce",
    "lazy":               "rewrite_reduce",
    "vm_compute":         "rewrite_reduce",
    "native_compute":     "rewrite_reduce",
    "compute":            "rewrite_reduce",
    "unfold":             "rewrite_reduce",
    "fold":               "rewrite_reduce",
    "red":                "rewrite_reduce",
    "hnf":                "rewrite_reduce",
    "delta":              "rewrite_reduce",
    "beta":               "rewrite_reduce",
    "iota":               "rewrite_reduce",
    "zeta":               "rewrite_reduce",
    "change":             "rewrite_reduce",
    "convert":            "rewrite_reduce",
    "simp":               "rewrite_reduce",
    "ring_nf":            "rewrite_reduce",
    "ring_simplify":      "rewrite_reduce",
    "field_simplify":     "rewrite_reduce",
    "norm_cast":          "rewrite_reduce",
    "push_cast":          "rewrite_reduce",
    "pull_cast":          "rewrite_reduce",
    "push_neg":           "rewrite_reduce",
    "pull_neg":           "rewrite_reduce",
    "pattern":            "rewrite_reduce",
    # --- arithmetic_algebra: decide algebraic/numeric equalities ------------
    "ring":               "arithmetic_algebra",
    "field":              "arithmetic_algebra",
    "norm_num":           "arithmetic_algebra",
    "zify":               "arithmetic_algebra",
    # --- contradiction_solver: close goals by inconsistency or decision -----
    "omega":              "contradiction_solver",
    "lia":                "contradiction_solver",
    "lra":                "contradiction_solver",
    "nia":                "contradiction_solver",
    "nra":                "contradiction_solver",
    "psatz":              "contradiction_solver",
    "contradiction":      "contradiction_solver",
    "absurd":             "contradiction_solver",
    "discriminate":       "contradiction_solver",
    "exfalso":            "contradiction_solver",
    "tauto":              "contradiction_solver",
    "btauto":             "contradiction_solver",
    "intuition":          "contradiction_solver",
    "firstorder":         "contradiction_solver",
    "decide":             "contradiction_solver",
    "congruence":         "contradiction_solver",
    # --- application: backward reasoning — unify goal with lemma ------------
    "apply":              "application",
    "eapply":             "application",
    "rapply":             "application",
    "lapply":             "application",
    "exact":              "application",
    "exact_no_check":     "application",
    "refine":             "application",
    "assumption":         "application",
    "auto":               "application",
    "eauto":              "application",
    "trivial":            "application",
    "easy":               "application",
    "specialize":         "application",
    # --- case_induction: structural decomposition — splits into subgoals ----
    "induction":          "case_induction",
    "destruct":           "case_induction",
    "case":               "case_induction",
    "case_eq":            "case_induction",
    "elim":               "case_induction",
    "elimtype":           "case_induction",
    "inversion":          "case_induction",
    "inversion_clear":    "case_induction",
    "injection":          "case_induction",
    "split":              "case_induction",
    "constructor":        "case_induction",
    "econstructor":       "case_induction",
    "left":               "case_induction",
    "right":              "case_induction",
    "exists":             "case_induction",
    "eexists":            "case_induction",
    # --- hypothesis_management: manipulate the proof context ----------------
    "intro":              "hypothesis_management",
    "intros":             "hypothesis_management",
    "revert":             "hypothesis_management",
    "clear":              "hypothesis_management",
    "clearbody":          "hypothesis_management",
    "rename":             "hypothesis_management",
    "move":               "hypothesis_management",
    "generalize":         "hypothesis_management",
    "instantiate":        "hypothesis_management",
    "pose":               "hypothesis_management",
    "remember":           "hypothesis_management",
    "set":                "hypothesis_management",
    "assert":             "hypothesis_management",
    "cut":                "hypothesis_management",
    "enough":             "hypothesis_management",
    "have":               "hypothesis_management",
    "suff":               "hypothesis_management",
    "suffices":           "hypothesis_management",
    # --- meta_tactical: orchestrate other tactics ---------------------------
    "repeat":             "meta_tactical",
    "try":                "meta_tactical",
    "first":              "meta_tactical",
    "do":                 "meta_tactical",
    "progress":           "meta_tactical",
    "timeout":            "meta_tactical",
    "once":               "meta_tactical",
    "solve":              "meta_tactical",
    "fail":               "meta_tactical",
    "idtac":              "meta_tactical",
    "by":                 "meta_tactical",
    "done":               "meta_tactical",
    "abstract":           "meta_tactical",
}


def assign_tactic_groups(tactic_tags: list[str]) -> list[str]:
    """Map a list of tactic names to their unique behavioural groups."""
    seen: set[str] = set()
    groups: list[str] = []
    for t in tactic_tags:
        g = _TACTIC_GROUPS.get(t.lower())
        if g and g not in seen:
            seen.add(g)
            groups.append(g)
    return groups


# ---------------------------------------------------------------------------
# Keyword extraction — proof-relevant terms to store per commit
# ---------------------------------------------------------------------------

# Coq / Rocq tactic and keyword vocabulary for keyword extraction.
# Covers: core Coq tactics, ssreflect/mathcomp, Ltac2, common automation,
# and fiat-crypto domain terms.
_PROOF_TERMS = re.compile(
    r"\b("
    # Proof holes / placeholders
    r"sorry|admitted|admit|oops"
    # Proof structure
    r"|lemma|theorem|corollary|proposition|remark|fact|definition"
    r"|example|instance|canonical|global|local|section|module|record"
    r"|proof|qed|defined|abort|end"
    # Core intro / elimination tactics
    r"|intro|intros|revert|clear|clearbody|rename|move"
    r"|destruct|case|case_eq|induction|inductive|elim|elimtype"
    r"|inversion|inversion_clear|injection|discriminate"
    r"|constructor|econstructor|left|right|split|exists|eexists"
    # Rewriting
    r"|rewrite|erewrite|setoid_rewrite|rewrite_strat"
    r"|replace|symmetry|transitivity|etransitivity"
    r"|subst|congruence"
    # Application / unification
    r"|apply|eapply|exact|refine|change|convert"
    r"|rapply|rapply|lapply|specialize|generalize|instantiate"
    r"|pose|remember|set|assert|cut|enough|have|suff|suffices"
    # Automation
    r"|auto|eauto|tauto|intuition|firstorder|trivial|easy"
    r"|decide|btauto|congruence|contradiction|absurd|exfalso"
    # Arithmetic / algebra solvers
    r"|omega|lia|lra|nia|nra|psatz|ring|field|ring_simplify|field_simplify"
    r"|norm_num|zify|push_cast|pull_cast|push_neg|pull_neg"
    # Simplification / reduction
    r"|simpl|cbn|cbv|lazy|vm_compute|native_compute"
    r"|unfold|fold|red|hnf|compute|delta|beta|iota|zeta"
    r"|simp|ring_nf|push_ring"
    # ssreflect / mathcomp tactics
    r"|move|case|elim|apply|exact|by|done|suff|have|pose|set"
    r"|rewrite|congr|wlog|without_loss|suffices"
    r"|reflect|iffP|appP|andP|orP|negP"
    # Ltac2 / modern tactics
    r"|induction|exact_no_check|assumption|solve|fail|idtac"
    r"|repeat|try|first|do|progress|timeout|once"
    r"|pattern|abstract|opaque|transparent"
    # Proof by reflection / decision procedures
    r"|decide_eq|reflect|boolean_reflect"
    # Structural
    r"|split|left|right|constructor|exists|eexists"
    r"|assumption|exact|exfalso|absurd"
    # fiat-crypto / bedrock2 specific
    r"|word|wordring|word_simpl|cancel|ring_simplify_subterms"
    r"|Felem|felem|montgomery|barrett"
    # Crypto domain terms
    r"|modular|prime|group|curve|lattice|hash|cipher|signature"
    r"|elliptic|montgomery|edwards|weierstrass|twisted"
    r"|x25519|x448|p256|p384|p521|secp256k1|curve25519"
    r"|bedrock|bedrock2|rupicola|fiat|crypto|cryptographic"
    r"|protocol|spec|specification|invariant|postcondition|precondition"
    r"|secp|ecdsa|ecdh|rsa|dh|dsa"
    r")\b",
    re.IGNORECASE,
)


def _compile(patterns: list[str]) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


_RE_COMPLETE = _compile(_COMPLETE_SIGNALS)
_RE_NEW = _compile(_NEW_PROOF_SIGNALS)
_RE_ADD = _compile(_ADD_PROOF_SIGNALS)
_RE_SPEC = _compile(_SPEC_SIGNALS)
_RE_INFRA = _compile(_INFRA_SIGNALS)
_RE_REFACTOR = _compile(_REFACTOR_SIGNALS)
_RE_FIX = _compile(_FIX_SIGNALS)

# Proof-context words — used to disambiguate fix/refactor from proof work
_RE_PROOF_CTX = re.compile(
    r"\b(proof|lemma|theorem|sorry|admitted|oops|tactic|coq|corollary)\b",
    re.IGNORECASE,
)


def extract_keywords(subject: str, body: str) -> list[str]:
    """Extract proof-relevant keywords from subject and body text."""
    text = f"{subject} {body}"
    matches = _PROOF_TERMS.findall(text)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        low = m.lower()
        if low not in seen:
            seen.add(low)
            result.append(low)
    return result


def classify_commit(record: CommitRecord) -> CommitClass:
    """Classify a CommitRecord using heuristic pattern matching.

    Priority order (highest wins):
      1. infra          — dependency bumps / CI are almost never proof-relevant
      2. proof_complete — a sorry was removed / proof closed
      3. spec_change    — theorem statement was changed
      4. proof_new      — new lemma/theorem added with a proof
      5. proof_add      — partial proof work (goals still open)
      6. refactor
      7. fix (with proof context -> proof_add; without -> fix)
      8. other
    """
    text = f"{record.message_subject} {record.message_body}".lower()
    subject = record.message_subject.lower()

    # 1. Infrastructure noise — check subject only to avoid body false positives.
    #    Merge commits often have a body sub-message like "* bump dependency X"
    #    even when the overall commit is proof work.
    if _RE_INFRA.search(subject) and not _RE_PROOF_CTX.search(subject):
        return CommitClass.infra

    # 2. proof_complete — explicit hole closure language
    if _RE_COMPLETE.search(text) and _RE_PROOF_CTX.search(text):
        return CommitClass.proof_complete

    # Structural heuristic for body-free commits: net deletions in proof files
    # with proof-context subject => likely a sorry was removed
    if (
        record.touches_proof_files
        and record.deletions > record.insertions
        and not record.message_body
        and _RE_PROOF_CTX.search(text)
        and not _RE_INFRA.search(text)
    ):
        return CommitClass.proof_complete

    # 3. spec_change — statement-level edits
    if _RE_SPEC.search(text) and record.touches_proof_files:
        return CommitClass.spec_change

    # 4. proof_new — new lemma or theorem added
    if _RE_NEW.search(text) and record.touches_proof_files:
        return CommitClass.proof_new

    # 5. proof_add — partial or incremental proof work
    if _RE_ADD.search(text) and record.touches_proof_files:
        return CommitClass.proof_add

    # 6. refactor / fix touching .v files → proof_add
    #    Any structural change to proof files is proof-relevant work.
    if record.touches_proof_files:
        if _RE_REFACTOR.search(text) or _RE_FIX.search(text):
            return CommitClass.proof_add

    # 7. refactor / fix on non-proof files stay as their own class
    if _RE_REFACTOR.search(text):
        return CommitClass.refactor
    if _RE_FIX.search(text):
        return CommitClass.fix

    # 8. Any remaining .v-touching commit → proof_add
    if record.touches_proof_files:
        return CommitClass.proof_add

    return CommitClass.other


def enrich_record(record: CommitRecord) -> CommitRecord:
    """Return a copy of record with commit_class and keywords populated (message-only)."""
    kw = extract_keywords(record.message_subject, record.message_body)
    cls = classify_commit(record)
    return record.model_copy(
        update={"commit_class": cls, "keywords": kw, "class_confidence": "heuristic"}
    )


def enrich_record_with_diff(
    record: CommitRecord,
    repo_path: str | Path,
) -> CommitRecord:
    """Enrich a record using the actual .v file diffs for this commit.

    This is the authoritative second-pass classifier.  It supersedes the
    message-heuristic class for any commit that touches .v files.

    Classification logic (applied only when coq_files_changed is non-empty):
      1. sorry/Admitted net-removed in diff   → proof_complete
      2. net_proof_lines < 0  (proof shrank)  → proof_optimise
      3. net_proof_lines >= 0 (proof grew or  → proof_add
         unchanged line count)                  (tactic_tags & proof_style populated)

    For commits that do NOT touch .v files the message-heuristic class is kept.
    """
    from scaffold.git_walker import analyze_proof_diff

    if not record.coq_files_changed:
        return record

    parent = record.parent_hashes[0] if record.parent_hashes else ""
    diff_data = analyze_proof_diff(
        repo_path,
        parent,
        record.hash,
        record.coq_files_changed,
    )

    # Determine diff-based class
    if diff_data["sorry_removed"]:
        new_class = CommitClass.proof_complete
    elif diff_data["net_proof_lines"] < 0:
        new_class = CommitClass.proof_optimise
    else:
        new_class = CommitClass.proof_add

    # Keep higher-signal message-based classes that the diff can't detect:
    # proof_new and spec_change require semantic understanding of declarations.
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


# ---------------------------------------------------------------------------
# Repo-level analysis
# ---------------------------------------------------------------------------


def detect_build_system(repo_path: str | Path) -> dict[str, str]:
    """Detect build files present in the repo root."""
    repo = Path(repo_path)
    found: dict[str, str] = {}
    for fname, (pa, cmd) in _BUILD_FILES.items():
        if (repo / fname).exists():
            found[fname] = cmd
    return found


def detect_exclude_paths(repo_path: str | Path) -> list[str]:
    """Detect directories that should be excluded from mining."""
    repo = Path(repo_path)
    excludes: list[str] = []
    for entry in os.scandir(repo):
        if entry.is_dir() and entry.name in _COMMON_EXCLUDES:
            excludes.append(entry.name)
    return excludes


def analyze_repo(
    repo_path: str | Path,
    llm_client: "anthropic.Anthropic | None" = None,
) -> RepoMetadata:
    """Run full heuristic analysis on a repository.

    Detects proof assistant, file extensions, build system, and paths to exclude.
    If llm_client is provided, also infers repo-specific proof-fill keywords from
    a sample of commit messages.
    """
    repo = Path(repo_path)
    name = repo.name

    pa = detect_proof_assistant(repo)
    if pa is None:
        logger.warning("Could not detect proof assistant for %s", name)
        pa = ProofAssistant.coq  # fallback

    analyzer = get_analyzer(pa)
    build_info = detect_build_system(repo)
    excludes = detect_exclude_paths(repo)
    url = _get_remote_url(repo)

    inferred_keywords: list[str] = []
    if llm_client is not None:
        messages = sample_commit_messages(repo, n=200)
        inferred_keywords = infer_proof_fill_keywords(messages, llm_client)
        if inferred_keywords:
            logger.info(
                "Inferred proof-fill keywords for %s: %s", name, inferred_keywords
            )

    metadata = RepoMetadata(
        name=name,
        url=url,
        local_path=str(repo),
        proof_assistant=pa,
        file_extensions=analyzer.file_extensions,
        exclude_paths=excludes,
        discovered_patterns={
            "build_files": build_info,
            "hole_markers": [p.pattern for p in analyzer.hole_markers],
            "inferred_fill_keywords": inferred_keywords,
        },
    )

    logger.info(
        "Analyzed %s: assistant=%s, extensions=%s",
        name,
        pa.value,
        analyzer.file_extensions,
    )
    return metadata


def _get_remote_url(repo_path: Path) -> str:
    """Try to extract the remote URL from git config."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def sample_commit_messages(repo_path: str | Path, n: int = 200) -> list[str]:
    """Return up to n recent commit messages from the repo."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", f"-n{n}", "--format=%s"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().splitlines() if line]


def infer_proof_fill_keywords(
    messages: list[str],
    client: "anthropic.Anthropic | None" = None,
) -> list[str]:
    """Use Claude to infer repo-specific proof-fill keywords from a sample of commit messages.

    Falls back to an empty list if no client is provided or the call fails.
    """
    if client is None or not messages:
        return []

    sample = "\n".join(f"- {m}" for m in messages[:100])
    prompt = (
        "Below are commit messages from a proof engineering repository.\n"
        "Identify words or short phrases that appear to signal that a proof was "
        "completed or a proof hole (sorry/Admitted/oops/admit) was filled in.\n"
        "Return ONLY a JSON array of lowercase strings, nothing else. "
        'Example: ["complete", "fill", "qed", "close proof"]\n\n'
        f"Commit messages:\n{sample}"
    )

    try:
        import json

        import anthropic

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        keywords = json.loads(text)
        if isinstance(keywords, list):
            return [str(k).lower() for k in keywords if isinstance(k, str)]
    except Exception as exc:
        logger.warning("LLM keyword inference failed: %s", exc)

    return []


# ---------------------------------------------------------------------------
# Legacy interface — kept for backward compatibility with existing callers
# ---------------------------------------------------------------------------


def classify_commit_message(
    message: str,
    extra_keywords: list[str] | None = None,
) -> str:
    """Classify a commit message subject line. Returns a CommitClass value string.

    Legacy single-string interface. Prefer classify_commit(record) for new
    callers, which uses the full record (body + file stats).
    """
    dummy = CommitRecord(
        hash="",
        date="",
        message_subject=message,
        message_body=" ".join(extra_keywords or []),
        touches_proof_files=True,
    )
    return classify_commit(dummy).value