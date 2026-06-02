"""RepoProfile — the declarative spec that drives the deterministic miner.

Everything the miner currently hardcodes for Coq/fiat-crypto (hole markers,
declaration patterns, commit-message signal banks, tactic vocabulary, tactic
groups, domain terms, build/exclude config) lives here as *data* instead of
module-level constants. A profile is either hand-authored or synthesised by the
calibration agent, cached to ``artifacts/<repo>-profile.json``, and consumed by
the profile-driven engine (git_walker / pattern_detector).

The model writes a profile as a JSON dict (it cannot construct pydantic objects
inside the CodeMode sandbox); ``RepoProfile.model_validate`` turns that into the
validated artifact. ``RepoProfile.compiled()`` returns a ``CompiledProfile`` with
every regex pre-compiled, so the engine never recompiles per commit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# Default order in which message-based commit classes are tried (highest wins).
# Mirrors the legacy classify_commit decision tree. proof_optimise is excluded:
# it is derived from the diff (net proof lines), not the message.
DEFAULT_CLASSIFICATION_PRIORITY: list[str] = [
    "infra",
    "proof_complete",
    "spec_change",
    "proof_new",
    "proof_add",
    "refactor",
    "fix",
]


def _check_regex(pattern: str, field_name: str, flags: int = 0) -> str:
    """Try to compile *pattern*; raise ``ValueError`` with *field_name* context on failure."""
    try:
        re.compile(pattern, flags)
    except re.error as e:
        raise ValueError(
            f"invalid regex in {field_name}: {e.msg} in pattern {pattern!r}"
        ) from e
    return pattern


class HoleMarker(BaseModel):
    """A regex that matches an incomplete-proof placeholder, with its kind label.

    ``kind`` is free-form (e.g. 'sorry', 'admitted', 'admit', 'oops',
    'placeholder') so a new proof assistant can introduce its own marker kinds
    without touching an enum.
    """

    regex: str = Field(
        description="Regex matching the hole marker as it appears in source."
    )
    kind: str = Field(
        description="Label for this hole kind, e.g. 'sorry' or 'admitted'."
    )

    @field_validator("regex")
    @classmethod
    def _validate_regex(cls, v: str) -> str:
        return _check_regex(v, "hole_markers.regex")


class Provenance(BaseModel):
    """How a profile was produced — for auditability and reproducibility."""

    generated_by: str = Field(
        default="hand",
        description="'hand' (lifted from constants) | 'agent' (calibration agent) | other.",
    )
    model: str = Field(default="", description="Model string if agent-generated.")
    sampled_commits: list[str] = Field(
        default_factory=list,
        description="SHAs the agent sampled while calibrating (audit trail).",
    )
    created_at: str = Field(default="", description="ISO-8601 timestamp, if recorded.")
    repo_url: str = Field(default="", description="Source repo remote URL.")
    notes: str = Field(default="", description="Free-text provenance notes.")


class RepoProfile(BaseModel):
    """Declarative, repo-specific spec consumed by the deterministic miner.

    A strict generalisation of the legacy ``RepoMetadata.discovered_patterns`` +
    the module-level constant banks in pattern_detector.py / git_walker.py /
    analyzers/*. Every field here replaces something that used to be hardcoded.
    """

    proof_assistant: str = Field(
        description="Free-form: 'coq', 'isabelle', 'lean4', 'fstar', 'agda', ..."
    )
    proof_file_globs: list[str] = Field(
        description="fnmatch-style globs identifying proof/spec files, e.g. '*.v', 'src/**/*.thy'.",
    )
    exclude_globs: list[str] = Field(
        default_factory=list,
        description="Globs / directory names to exclude (vendored deps, build dirs).",
    )

    # --- build ---------------------------------------------------------------
    build_files: dict[str, str] = Field(
        default_factory=dict,
        description="Map of build-marker filename -> build command (e.g. '_CoqProject' -> 'coq_makefile').",
    )
    build_commands: list[str] = Field(
        default_factory=list,
        description="Canonical build command(s) for the repo, if known.",
    )

    # --- proof structure -----------------------------------------------------
    hole_markers: list[HoleMarker] = Field(
        description="Regexes (with kind labels) marking incomplete proofs.",
    )
    declaration_patterns: list[str] = Field(
        description="Regexes matching theorem/lemma/def declarations; group 1 must be the name.",
    )

    # --- commit-message classification --------------------------------------
    commit_signals: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Map of commit-class name -> list of regex signals whose presence votes "
            "for that class. Replaces the legacy _*_SIGNALS banks. Keys are free-form."
        ),
    )
    proof_context_regex: str = Field(
        default="",
        description="Regex marking proof-relevant context; disambiguates fix/refactor/infra from proof work.",
    )
    classification_priority: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CLASSIFICATION_PRIORITY),
        description="Order in which commit classes are tried (highest priority first).",
    )

    # --- tactic / proof-style analysis --------------------------------------
    tactic_vocabulary: list[str] = Field(
        default_factory=list,
        description="Tactic names; the engine compiles a boundary-aware regex from these.",
    )
    tactic_groups: dict[str, str] = Field(
        default_factory=dict,
        description="Map of tactic name -> behavioural group label.",
    )
    proof_style_signals: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of style name -> regex (e.g. 'term_mode', 'ssreflect'). "
            "'tactic_mode'/'mixed' are derived by the engine, not stored here."
        ),
    )

    # --- keyword extraction --------------------------------------------------
    structural_terms: list[str] = Field(
        default_factory=list,
        description="Proof-structure vocabulary (lemma, theorem, proof, qed, ...) for keyword extraction.",
    )
    domain_terms: list[str] = Field(
        default_factory=list,
        description="Repo/domain-specific terms (e.g. crypto: x25519, montgomery) for keyword extraction.",
    )

    notes: str = Field(
        default="",
        description="Repo-specific git conventions discovered (squash-merge, Change-Id, [wip] prefixes, ...).",
    )
    provenance: Provenance = Field(default_factory=Provenance)

    @field_validator("declaration_patterns")
    @classmethod
    def _validate_declaration_patterns(cls, v: list[str]) -> list[str]:
        for i, p in enumerate(v):
            _check_regex(p, f"declaration_patterns[{i}]", re.MULTILINE)
        return v

    @field_validator("commit_signals")
    @classmethod
    def _validate_commit_signals(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, sigs in v.items():
            for i, s in enumerate(sigs):
                _check_regex(s, f"commit_signals.{name}[{i}]", re.IGNORECASE)
        return v

    @field_validator("proof_context_regex")
    @classmethod
    def _validate_proof_context_regex(cls, v: str) -> str:
        if v:
            _check_regex(v, "proof_context_regex", re.IGNORECASE)
        return v

    @field_validator("proof_style_signals")
    @classmethod
    def _validate_proof_style_signals(cls, v: dict[str, str]) -> dict[str, str]:
        for name, rx in v.items():
            _check_regex(rx, f"proof_style_signals.{name}", re.MULTILINE)
        return v

    def compiled(self) -> CompiledProfile:
        """Pre-compile every regex once; the engine uses this hot-path view."""
        return CompiledProfile.from_profile(self)


# ---------------------------------------------------------------------------
# Compiled view — built once per mining run, used per commit.
# ---------------------------------------------------------------------------


def _word_alternation(terms: list[str]) -> str:
    """Build a `\\b(a|b|c)\\b` alternation, longest-first to avoid prefix shadowing."""
    if not terms:
        return r"(?!x)x"  # never-matches sentinel
    ordered = sorted({t for t in terms if t}, key=lambda s: len(s), reverse=True)
    return r"\b(" + "|".join(re.escape(t) for t in ordered) + r")\b"


@dataclass
class CompiledProfile:
    """A RepoProfile with all regexes compiled and ready for per-commit use."""

    profile: RepoProfile
    hole_res: list[tuple[re.Pattern[str], str]]
    declaration_res: list[re.Pattern[str]]
    commit_signal_res: dict[str, re.Pattern[str]]
    proof_context_re: re.Pattern[str] | None
    tactic_re: re.Pattern[str]
    proof_style_res: dict[str, re.Pattern[str]]
    keyword_re: re.Pattern[str]
    tactic_groups_lower: dict[str, str]

    @classmethod
    def from_profile(cls, profile: RepoProfile) -> CompiledProfile:
        # Defense-in-depth: wrap each section so a bad regex names the field.
        # The pydantic validators on RepoProfile catch most issues at
        # deserialization time; these try/excepts guard against profiles built
        # programmatically without going through model_validate().
        try:
            hole_res = [(re.compile(h.regex), h.kind) for h in profile.hole_markers]
        except re.error as e:
            raise ValueError(f"invalid regex in hole_markers: {e}") from e
        try:
            declaration_res = [
                re.compile(p, re.MULTILINE) for p in profile.declaration_patterns
            ]
        except re.error as e:
            raise ValueError(f"invalid regex in declaration_patterns: {e}") from e

        # Validate individual commit signals before joining with "|".
        commit_signal_res: dict[str, re.Pattern[str]] = {}
        for name, sigs in profile.commit_signals.items():
            if not sigs:
                continue
            for i, sig in enumerate(sigs):
                try:
                    re.compile(sig, re.IGNORECASE)
                except re.error as e:
                    raise ValueError(
                        f"invalid regex in commit_signals.{name}[{i}]: {e}"
                    ) from e
            commit_signal_res[name] = re.compile("|".join(sigs), re.IGNORECASE)

        try:
            proof_context_re = (
                re.compile(profile.proof_context_regex, re.IGNORECASE)
                if profile.proof_context_regex
                else None
            )
        except re.error as e:
            raise ValueError(f"invalid regex in proof_context_regex: {e}") from e
        # Boundary-aware tactic regex: a tactic at a tactic position. Group 1 = name.
        ordered_tactics = sorted(
            {t for t in profile.tactic_vocabulary if t},
            key=lambda s: len(s),
            reverse=True,
        )
        tactic_alt = "|".join(re.escape(t) for t in ordered_tactics) or r"(?!x)x"
        tactic_re = re.compile(
            r"(?:^|[\s;|{(])(" + tactic_alt + r")(?:\s|[.;()\[\]{]|$)",
            re.IGNORECASE | re.MULTILINE,
        )
        try:
            proof_style_res = {
                name: re.compile(rx, re.MULTILINE)
                for name, rx in profile.proof_style_signals.items()
            }
        except re.error as e:
            raise ValueError(f"invalid regex in proof_style_signals: {e}") from e
        # Keyword extraction matches over the union of hole kinds + structural + tactic + domain.
        hole_kinds = [h.kind for h in profile.hole_markers]
        keyword_terms = (
            hole_kinds
            + profile.structural_terms
            + profile.tactic_vocabulary
            + profile.domain_terms
        )
        keyword_re = re.compile(_word_alternation(keyword_terms), re.IGNORECASE)
        # Normalize tactic_groups keys to lowercase so lookups from the
        # lowercased tactic tags (git_walker diff extraction) always match.
        tactic_groups_lower = {k.lower(): v for k, v in profile.tactic_groups.items()}
        return cls(
            profile=profile,
            hole_res=hole_res,
            declaration_res=declaration_res,
            commit_signal_res=commit_signal_res,
            proof_context_re=proof_context_re,
            tactic_re=tactic_re,
            proof_style_res=proof_style_res,
            keyword_re=keyword_re,
            tactic_groups_lower=tactic_groups_lower,
        )


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def load_profile(path: str | Path) -> RepoProfile:
    """Load and validate a RepoProfile from a JSON file."""
    return RepoProfile.model_validate_json(Path(path).read_text())


def save_profile(profile: RepoProfile, path: str | Path) -> None:
    """Write a RepoProfile to a JSON file (pretty-printed, stable key order)."""
    Path(path).write_text(json.dumps(profile.model_dump(), indent=2) + "\n")
