#!/usr/bin/env python3
"""Lemma introduction dates for the temporal holdout (issue #132, DATA-PREP half).

This is the extraction + join tooling only. It does NOT compute the contamination
gap against real eval results -- that correlation step is `temporal_holdout_split`
below, implemented and unit-tested against synthetic data, and documented as a hook
to run once #129/#130 land real `ablate-baseline` results. Do not fabricate numbers
by running it against anything but real result JSONL.

## What this does

For every (challenge, deleted lemma) pair mined into `artifacts/lean-ablate/*/challenges.jsonl`
(the corollary-leaves mode; NOT `lean-ablate-whole`, out of scope for #132 prep), find the
INTRODUCTION date of that lemma: the earliest commit in the source repo's git history whose
diff *adds a declaration line* for that lemma name, in that file. This is not the same as
"earliest commit mentioning the identifier" -- a commit can touch a lemma's *name* (e.g. a
usage, an import, a doc reference) long before the lemma itself is declared, and `git log -S`
alone conflates the two. See `find_introduction` for exactly how we disambiguate.

Method, per lemma:

1. `git log -S "<lemma>" --reverse --format=... -- <file>` -- restricts to commits that
   changed the *number of occurrences* of the literal string `<lemma>` in that file, oldest
   first. This is the right primitive (not `--diff-filter=A`, which is about whole-file
   additions, not line-level ones, and not `--follow`, which is about *file* renames, not
   *declaration* introduction).
2. For each candidate commit (oldest first), inspect its diff on that file and check whether
   an *added* line actually looks like a Lean declaration of that name (a `theorem` / `lemma`
   / `def` / `instance` / `abbrev` keyword, with optional `private`/`protected`/`scoped`/
   `local`/`noncomputable` modifiers, immediately followed by the identifier). Take the first
   commit that passes this check -- that is the introduction.
3. If no path-scoped candidate passes, retry step 1-2 with the path filter dropped (handles
   the lemma's file having been renamed/moved before its current path -- `--follow` does not
   help here because it follows one file's history, not an identifier across arbitrary moves).
4. If nothing passes the declaration check but `git log -S` still found *some* commit (path-
   scoped or, failing that, whole-repo), fall back to the earliest such commit as a best-effort
   date -- we have positive evidence the identifier exists there even if our regex heuristic
   didn't confirm a declaration line (e.g. multi-line signatures). This is logged, not silent.
5. If `git log -S` finds nothing at all, the row is emitted with an empty date (not dropped --
   dropping would silently understate the corpus, same lesson as the `malformed` pitfall
   documented in `pipeline/README.md`).

## Shallow / truncated history

`clone_repos.sh` does full clones, not shallow ones, so in practice this should not bite. We
still check per repo (`git rev-parse --is-shallow-repository`, plus a `rev-list --count HEAD
<= 1` guard for a repo that is technically not "shallow" but was locally re-initialised with
no history) and set `depth_limited=true` for every row of an affected repo rather than emit a
date we can't stand behind.

## Usage

    python3 pipeline/lemma_dates.py mine [--repo NAME ...] [--jobs N] [--out FILE]
    python3 pipeline/lemma_dates.py temporal-holdout --dates FILE --results FILE --cutoff ISO
    python3 pipeline/lemma_dates.py self-test

`mine` reads `artifacts/lean-ablate/*/challenges.jsonl` from the main checkout (these are
gitignored blob payloads -- present in a full working copy, not necessarily in every git
worktree) and the source checkouts under `data/lean/<repo>` (read-only: only `git log`/
`git show` plumbing, never anything that touches the working tree), and writes
`pipeline/lemma_dates.tsv`.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_TSV = REPO_ROOT / "pipeline" / "registry_all.tsv"
DEFAULT_OUT = REPO_ROOT / "pipeline" / "lemma_dates.tsv"

TSV_COLUMNS = [
    "challenge_id",
    "repo",
    "lemma",
    "introduction_date",
    "introduction_sha",
    "depth_limited",
]

# --------------------------------------------------------------------------------------
# Declaration-line heuristic
# --------------------------------------------------------------------------------------

_DECL_KEYWORDS = r"(?:theorem|lemma|def|instance|abbrev)"
_DECL_MODIFIER = r"(?:private|protected|scoped|local|noncomputable)\s+"


def _decl_regex(name: str) -> re.Pattern[str]:
    """A line looks like a declaration of `name` if, after optional Lean visibility/
    computability modifiers, a declaration keyword is immediately followed by `name` as a
    whole identifier. Deliberately conservative (false negatives -> we fall back to the
    raw pickaxe hit; false positives are rarer and would just misattribute the date to a
    slightly-wrong-but-nearby commit)."""
    ident = re.escape(name)
    return re.compile(rf"(?:^|\s)(?:{_DECL_MODIFIER})*{_DECL_KEYWORDS}\s+{ident}\b")


# --------------------------------------------------------------------------------------
# git plumbing (read-only: log / show only, never anything that writes)
# --------------------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


_git_root_cache: dict[str, Path | None] = {}


def find_git_root(start: Path) -> Path | None:
    """Walk up from `start` to the nearest ancestor containing `.git`.

    Deliberately walks from the FILE's own directory, not from the repo's registered `src`
    root: `src` is not always a git root or an ancestor of one. `symcrust`'s `src` is
    `data/lean/symcrust`, but its actual clone (and the `aeneas` sibling its lakefile
    path-requires) sits one level deeper at `data/lean/symcrust/SymCrypt` -- walking up from
    `src` overshoots past that nested `.git` and lands on the *monorepo's own* `.git`
    instead, silently searching the wrong history. Walking up from the file catches a git
    root at, above, OR below `src`."""
    key = str(start)
    cached = _git_root_cache.get(key)
    if cached is not None or key in _git_root_cache:
        return cached
    d = start.resolve()
    result: Path | None = None
    while True:
        if (d / ".git").exists():
            result = d
            break
        if d.parent == d:
            break
        d = d.parent
    _git_root_cache[key] = result
    return result


_depth_limited_cache: dict[str, bool] = {}


def repo_is_depth_limited(git_root: Path) -> bool:
    key = str(git_root)
    cached = _depth_limited_cache.get(key)
    if cached is not None:
        return cached
    shallow = _run_git(["rev-parse", "--is-shallow-repository"], git_root)
    if shallow.returncode == 0 and shallow.stdout.strip() == "true":
        _depth_limited_cache[key] = True
        return True
    count = _run_git(["rev-list", "--count", "HEAD"], git_root)
    try:
        n = int(count.stdout.strip())
        result = n <= 1
    except ValueError:
        result = True  # can't tell how deep the history is -> be conservative
    _depth_limited_cache[key] = result
    return result


def _log_dash_s(git_root: Path, lemma: str, rel_path: str | None) -> list[tuple[str, str]]:
    """[(sha, author_date_iso), ...] oldest-first, for commits that changed the number of
    occurrences of the literal string `lemma` (optionally scoped to one file)."""
    args = ["log", "-S", lemma, "--reverse", "--format=%H%x1f%aI"]
    if rel_path:
        args += ["--", rel_path]
    res = _run_git(args, git_root)
    if res.returncode != 0:
        return []
    out = []
    for line in res.stdout.splitlines():
        if "\x1f" not in line:
            continue
        sha, date = line.split("\x1f", 1)
        out.append((sha, date))
    return out


def _adds_declaration_line(
    git_root: Path, sha: str, rel_path: str | None, regex: re.Pattern[str]
) -> bool:
    args = ["show", "--unified=0", "--format=", sha]
    if rel_path:
        args += ["--", rel_path]
    res = _run_git(args, git_root)
    if res.returncode != 0:
        return False
    for line in res.stdout.splitlines():
        if line.startswith("+++") or not line.startswith("+"):
            continue
        if regex.search(line[1:]):
            return True
    return False


@dataclass
class Introduction:
    sha: str = ""
    date: str = ""

    @property
    def found(self) -> bool:
        return bool(self.sha)


_intro_cache: dict[tuple[str, str, str], Introduction] = {}


def find_introduction(git_root: Path, rel_path: str, lemma: str) -> Introduction:
    """The earliest commit that introduces a declaration of `lemma` in `rel_path` (or,
    failing that, anywhere in the repo). See module docstring for the algorithm."""
    key = (str(git_root), rel_path, lemma)
    cached = _intro_cache.get(key)
    if cached is not None:
        return cached

    regex = _decl_regex(lemma)

    path_candidates = _log_dash_s(git_root, lemma, rel_path)
    for sha, date in path_candidates:
        if _adds_declaration_line(git_root, sha, rel_path, regex):
            result = Introduction(sha=sha, date=date)
            _intro_cache[key] = result
            return result

    # Fallback: drop the path filter (handles the file having moved/been renamed before
    # reaching its current path; --follow tracks a *file*, not an *identifier*, so it does
    # not help here).
    wide_candidates = _log_dash_s(git_root, lemma, None)
    for sha, date in wide_candidates:
        if _adds_declaration_line(git_root, sha, None, regex):
            result = Introduction(sha=sha, date=date)
            _intro_cache[key] = result
            return result

    # Best effort: we found *some* commit that changed the occurrence count of the literal
    # name, just couldn't confirm a declaration line with the regex heuristic (e.g. a
    # multi-line signature). Still better evidence than nothing.
    for sha, date in path_candidates or wide_candidates:
        result = Introduction(sha=sha, date=date)
        _intro_cache[key] = result
        return result

    result = Introduction()
    _intro_cache[key] = result
    return result


# --------------------------------------------------------------------------------------
# repo resolution: challenge_id's `file_path` -> (git root, git-relative path)
# --------------------------------------------------------------------------------------


def load_registry(data_root: Path) -> dict[str, Path]:
    """`pipeline/registry_all.tsv`: `<repo name>\\t<src dir file_path is relative to>`.
    This is the same root the miner passed as `-d <root>` (the ablator's strip-prefix), so
    it is the authoritative base for resolving a challenge's `file_path` back to a real
    file on disk -- NOT `repos.tsv`'s `checkout` column, which for a couple of repos
    (`symcrust`, `hax-proof-libs-lean`) points at a workspace-wrapper directory that
    doesn't actually contain the mined files at that relative path.

    `data_root` is where `data/lean/...` actually lives on disk -- the *script's own*
    location (`REPO_ROOT`, used to find `registry_all.tsv` itself, which is git-tracked)
    can differ from this when running from a worktree: `data/lean/<repo>` checkouts are
    gitignored (multi-GB) and so are NOT materialised per-worktree, only in the shared main
    checkout. Pass `--data-root` to point at wherever they actually are."""
    out: dict[str, Path] = {}
    if not REGISTRY_TSV.exists():
        return out
    for line in REGISTRY_TSV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        name, src = line.split("\t", 1)
        out[name] = data_root / src
    return out


@dataclass
class RepoSrc:
    name: str
    src: Path  # dir `file_path` in challenges.jsonl is relative to


_repo_src_cache: dict[str, RepoSrc | None] = {}


def resolve_repo_src(
    name: str, registry: dict[str, Path], data_root: Path = REPO_ROOT
) -> RepoSrc | None:
    if name in _repo_src_cache:
        return _repo_src_cache[name]
    src = registry.get(name, data_root / "data" / "lean" / name)
    if not src.is_dir():
        # registry_all.tsv can be stale (e.g. SizzLean's registry src is
        # data/lean/etheorem, which doesn't exist on disk -- the checkout actually lives
        # at data/lean/SizzLean, its manifest-name-derived path). Fall back to that before
        # giving up.
        fallback = data_root / "data" / "lean" / name
        if fallback.is_dir():
            src = fallback
    rs = RepoSrc(name=name, src=src) if src.is_dir() else None
    _repo_src_cache[name] = rs
    return rs


def resolve_file_location(src: Path, file_path: str) -> tuple[Path, str] | None:
    """A challenge's `file_path` is relative to `src`; find the git root that actually
    owns the resulting file (which can be nested below, at, or above `src` -- see
    `find_git_root`) and return `(git_root, git-relative path)`, or `None` if the file
    isn't under any git checkout at all (a stale/missing registry entry)."""
    import os

    abs_file = (src / file_path).resolve()
    git_root = find_git_root(abs_file.parent)
    if git_root is None:
        return None
    return git_root, os.path.relpath(abs_file, git_root)


# --------------------------------------------------------------------------------------
# challenges.jsonl -> (challenge_id, repo, lemma, file_path) rows
# --------------------------------------------------------------------------------------


@dataclass
class ChallengeLemma:
    challenge_id: str
    repo: str
    lemma: str
    file_path: str


def iter_challenge_lemmas(
    challenges_dir: Path, only_repos: set[str] | None
):
    for jsonl_path in sorted(challenges_dir.glob("*/challenges.jsonl")):
        repo = jsonl_path.parent.name
        if only_repos and repo not in only_repos:
            continue
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                cid = obj.get("challenge_id")
                file_path = obj.get("file_path")
                if not cid or not file_path:
                    continue
                for d in obj.get("deleted_lemmas") or []:
                    name = d.get("name") if isinstance(d, dict) else None
                    if not name:
                        continue
                    yield ChallengeLemma(
                        challenge_id=cid, repo=repo, lemma=name, file_path=file_path
                    )


# --------------------------------------------------------------------------------------
# mine subcommand
# --------------------------------------------------------------------------------------


def cmd_mine(args: argparse.Namespace) -> None:
    data_root = Path(args.data_root)
    challenges_dir = Path(args.challenges_dir) if args.challenges_dir else data_root / "artifacts" / "lean-ablate"
    registry = load_registry(data_root)
    only_repos = set(args.repo) if args.repo else None

    rows = list(iter_challenge_lemmas(challenges_dir, only_repos))
    print(f"loaded {len(rows)} (challenge, lemma) pairs from {challenges_dir}", file=sys.stderr)
    if not rows:
        print("nothing to do (no challenges.jsonl found, or --repo matched nothing -- "
              "note challenges.jsonl blobs are gitignored and only present in a full "
              "working copy)", file=sys.stderr)

    repo_names = sorted({r.repo for r in rows})
    repo_src: dict[str, RepoSrc] = {}
    skipped_repos = []
    for name in repo_names:
        rs = resolve_repo_src(name, registry, data_root)
        if rs is None:
            skipped_repos.append(name)
        else:
            repo_src[name] = rs
    if skipped_repos:
        print(
            f"WARNING: no git checkout found for {len(skipped_repos)} repo(s), "
            f"skipping their rows: {skipped_repos}",
            file=sys.stderr,
        )

    # dedupe by (git root, git-relative path, lemma) -- the same lemma is deleted across
    # many ablation variants of the same file, so this collapses ~2x the row count into git
    # calls. Keyed by git root (not repo name): a repo's files can resolve to different git
    # roots than its `src` might suggest (see `find_git_root`), though in practice one repo
    # -> one root.
    work: dict[tuple[str, str, str], list[ChallengeLemma]] = {}
    git_roots: dict[tuple[str, str, str], Path] = {}
    skipped_files = 0
    for r in rows:
        rs = repo_src.get(r.repo)
        if rs is None:
            continue
        loc = resolve_file_location(rs.src, r.file_path)
        if loc is None:
            skipped_files += 1
            continue
        git_root, rel_path = loc
        key = (str(git_root), rel_path, r.lemma)
        work.setdefault(key, []).append(r)
        git_roots[key] = git_root
    if skipped_files:
        print(
            f"WARNING: {skipped_files} row(s) skipped, file not under any git checkout",
            file=sys.stderr,
        )

    print(f"{len(work)} unique (git root, file, lemma) lookups", file=sys.stderr)

    def _job(key: tuple[str, str, str]) -> tuple[tuple[str, str, str], Introduction]:
        _, rel_path, lemma = key
        return key, find_introduction(git_roots[key], rel_path, lemma)

    results: dict[tuple[str, str, str], Introduction] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        done = 0
        for key, intro in ex.map(_job, work.keys()):
            results[key] = intro
            done += 1
            if done % 1000 == 0:
                print(f"  ...{done}/{len(work)}", file=sys.stderr)

    out_rows = []
    for key, chal_list in work.items():
        _, rel_path, lemma = key
        intro = results[key]
        depth_limited = repo_is_depth_limited(git_roots[key])
        for c in chal_list:
            out_rows.append(
                {
                    "challenge_id": c.challenge_id,
                    "repo": c.repo,
                    "lemma": lemma,
                    "introduction_date": intro.date,
                    "introduction_sha": intro.sha,
                    "depth_limited": "true" if depth_limited else "false",
                }
            )
    out_rows.sort(key=lambda r: (r["repo"], r["challenge_id"], r["lemma"]))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TSV_COLUMNS, delimiter="\t")
        w.writeheader()
        w.writerows(out_rows)

    n_found = sum(1 for r in out_rows if r["introduction_date"])
    n_depth_limited = sum(1 for r in out_rows if r["depth_limited"] == "true")
    print(f"wrote {len(out_rows)} rows to {out_path}", file=sys.stderr)
    if out_rows:
        print(f"  dated: {n_found} ({n_found / len(out_rows):.1%})", file=sys.stderr)
    print(f"  depth_limited: {n_depth_limited}", file=sys.stderr)
    print(f"  not found: {len(out_rows) - n_found}", file=sys.stderr)


# --------------------------------------------------------------------------------------
# temporal_holdout_split -- the correlation-against-results HOOK
#
# NOT run against real numbers by this change. `pipeline/lemma_dates.tsv` exists today;
# `ablate-baseline` results at scale (#129/#130) do not yet. This function is the documented
# join point: once a results JSONL exists (one JSON object per challenge, with a
# `challenge_id` and a pass/fail outcome -- the `ablate-baseline --out` schema), call this
# with the model's stated training cutoff to get the pre/post pass-rate split, macro-averaged
# per repo per issue #132's acceptance criteria (`evm-asm` is 34% of the corpus; a
# micro-average would mostly measure one repo).
# --------------------------------------------------------------------------------------


@dataclass
class HoldoutSide:
    label: str
    n_challenges: int = 0
    # repo -> (n_pass, n_total)
    per_repo: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def macro_pass_rate(self) -> float | None:
        """Mean of per-repo pass rates (each repo weighted equally, not each challenge --
        see #132's `evm-asm` confound)."""
        rates = [n_pass / n_total for n_pass, n_total in self.per_repo.values() if n_total > 0]
        return mean(rates) if rates else None


@dataclass
class HoldoutResult:
    cutoff: str
    pre: HoldoutSide
    post: HoldoutSide
    # rows excluded because we couldn't trust the date (repo history was depth-limited, or
    # the lemma's introduction date was never found)
    excluded_undated: int = 0
    # result rows with no challenge_id, or a challenge_id absent from lemma_dates.tsv
    unmatched_results: int = 0
    # lemma_dates.tsv challenges with no matching row in the results file (results run
    # hasn't covered them yet, or used a different sample)
    challenges_without_results: int = 0


def load_lemma_dates(dates_tsv: Path | str) -> dict[str, dict]:
    """challenge_id -> {repo, min_introduction_date, depth_limited}.

    A challenge can delete more than one lemma (multiple rows share `challenge_id` in
    `lemma_dates.tsv`); we take the MINIMUM (earliest) introduction date across them for the
    contamination classification. Rationale: contamination risk is set by whichever deleted
    lemma the model is *most* likely to have memorized, i.e. the oldest one -- if any one of
    a challenge's deleted lemmas predates the cutoff, the challenge cannot be trusted as
    "post-cutoff clean" even if the others are newer.
    """
    per_challenge: dict[str, dict] = {}
    with open(dates_tsv, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f, delimiter="\t")
        for row in r:
            cid = row["challenge_id"]
            entry = per_challenge.setdefault(
                cid, {"repo": row["repo"], "dates": [], "depth_limited": False}
            )
            entry["depth_limited"] = entry["depth_limited"] or (row["depth_limited"] == "true")
            date = row["introduction_date"]
            if date:
                entry["dates"].append(date)
    out = {}
    for cid, entry in per_challenge.items():
        out[cid] = {
            "repo": entry["repo"],
            "min_introduction_date": min(entry["dates"]) if entry["dates"] else None,
            "depth_limited": entry["depth_limited"],
        }
    return out


def _extract_pass(result_row: dict) -> bool | None:
    """Best-effort pass/fail extraction from an `ablate-baseline --out` JSONL row. Accepts
    either a boolean `pass` field or a PASS/... `outcome`/`status` string (PASS is the only
    passing value; trivial/malformed/turn-limit/harness-err all count as not-pass, matching
    how `ablate-baseline` reports its own summary)."""
    if "pass" in result_row and isinstance(result_row["pass"], bool):
        return result_row["pass"]
    outcome = result_row.get("outcome") or result_row.get("status")
    if isinstance(outcome, str):
        return outcome.strip().upper() == "PASS"
    return None


def temporal_holdout_split(
    dates_tsv: Path | str, results_jsonl: Path | str, cutoff: str
) -> HoldoutResult:
    """Partition challenges into pre/post `cutoff` (ISO 8601 string, compared lexicographically
    against `introduction_date` -- both are `%aI`-style ISO 8601 with an explicit UTC offset,
    which sorts correctly as long as `cutoff` uses the same offset convention, e.g. end it in
    `+00:00` or `Z`... actually pass `+00:00`, since `%aI` never emits a bare `Z`), and compute
    per-side macro-averaged pass rates from `results_jsonl` (one JSON object per line, an
    `ablate-baseline --out` file: `challenge_id` + `pass`/`outcome`).

    Rows whose date could not be established (`depth_limited`, or `git log -S` found nothing)
    are excluded from both sides rather than guessed into one -- see `HoldoutResult.excluded_undated`.

    This is the join hook for issue #132 step 4 ("compare pass rates ... macro-averaged per
    repo"). It does not run against real data as part of this change -- #129/#130 haven't
    produced `results_jsonl` yet.
    """
    dates = load_lemma_dates(dates_tsv)
    pre = HoldoutSide(label=f"introduced <= {cutoff}")
    post = HoldoutSide(label=f"introduced > {cutoff}")
    excluded_undated = 0
    unmatched_results = 0
    seen_challenge_ids: set[str] = set()

    with open(results_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cid = row.get("challenge_id")
            passed = _extract_pass(row)
            if cid is None or passed is None:
                unmatched_results += 1
                continue
            entry = dates.get(cid)
            if entry is None:
                unmatched_results += 1
                continue
            seen_challenge_ids.add(cid)
            if entry["depth_limited"] or entry["min_introduction_date"] is None:
                excluded_undated += 1
                continue
            side = pre if entry["min_introduction_date"] <= cutoff else post
            side.n_challenges += 1
            repo = entry["repo"]
            n_pass, n_total = side.per_repo.get(repo, (0, 0))
            side.per_repo[repo] = (n_pass + (1 if passed else 0), n_total + 1)

    challenges_without_results = len(dates) - len(seen_challenge_ids)

    return HoldoutResult(
        cutoff=cutoff,
        pre=pre,
        post=post,
        excluded_undated=excluded_undated,
        unmatched_results=unmatched_results,
        challenges_without_results=challenges_without_results,
    )


def cmd_temporal_holdout(args: argparse.Namespace) -> None:
    result = temporal_holdout_split(args.dates, args.results, args.cutoff)
    report = {
        "cutoff": result.cutoff,
        "pre": {
            "n_challenges": result.pre.n_challenges,
            "n_repos": len(result.pre.per_repo),
            "macro_pass_rate": result.pre.macro_pass_rate,
        },
        "post": {
            "n_challenges": result.post.n_challenges,
            "n_repos": len(result.post.per_repo),
            "macro_pass_rate": result.post.macro_pass_rate,
        },
        "excluded_undated": result.excluded_undated,
        "unmatched_results": result.unmatched_results,
        "challenges_without_results": result.challenges_without_results,
    }
    print(json.dumps(report, indent=2))


# --------------------------------------------------------------------------------------
# self-test: synthetic data only, no real repos/results touched. Run with
# `python3 pipeline/lemma_dates.py self-test`.
# --------------------------------------------------------------------------------------


def _self_test() -> None:
    import tempfile

    # -- declaration regex --
    regex = _decl_regex("foo_bar")
    assert regex.search("theorem foo_bar : True := trivial")
    assert regex.search("private lemma foo_bar (x : Nat) : x = x := rfl")
    assert regex.search("@[simp] theorem foo_bar := by rfl")
    assert regex.search("noncomputable def foo_bar : Nat := 0")
    assert not regex.search("  exact foo_bar h1 h2")
    assert not regex.search("-- see foo_bar above")
    assert not regex.search("instance : Foo foo_bar := inferInstance")
    print("declaration regex: OK")

    # -- temporal_holdout_split --
    tmp = Path(tempfile.mkdtemp(prefix="lemma_dates_selftest_"))
    dates_tsv = tmp / "lemma_dates.tsv"
    with dates_tsv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TSV_COLUMNS, delimiter="\t")
        w.writeheader()
        w.writerows(
            [
                {
                    "challenge_id": "c1",
                    "repo": "repoA",
                    "lemma": "foo",
                    "introduction_date": "2024-01-01T00:00:00+00:00",
                    "introduction_sha": "aaa",
                    "depth_limited": "false",
                },
                {
                    "challenge_id": "c2",
                    "repo": "repoA",
                    "lemma": "bar",
                    "introduction_date": "2026-03-01T00:00:00+00:00",
                    "introduction_sha": "bbb",
                    "depth_limited": "false",
                },
                {
                    "challenge_id": "c3",
                    "repo": "repoB",
                    "lemma": "baz",
                    "introduction_date": "2026-05-01T00:00:00+00:00",
                    "introduction_sha": "ccc",
                    "depth_limited": "false",
                },
                # c3b: same challenge, a second deleted lemma introduced EARLIER -> min()
                # should pull c3 into the pre-cutoff side despite baz being post-cutoff.
                {
                    "challenge_id": "c3",
                    "repo": "repoB",
                    "lemma": "baz_dep",
                    "introduction_date": "2023-01-01T00:00:00+00:00",
                    "introduction_sha": "ccc0",
                    "depth_limited": "false",
                },
                {
                    "challenge_id": "c4",
                    "repo": "repoB",
                    "lemma": "qux",
                    "introduction_date": "",
                    "introduction_sha": "",
                    "depth_limited": "true",
                },
            ]
        )
    results_jsonl = tmp / "results.jsonl"
    with results_jsonl.open("w", encoding="utf-8") as f:
        for row in [
            {"challenge_id": "c1", "outcome": "PASS"},
            {"challenge_id": "c2", "outcome": "FAIL"},
            {"challenge_id": "c3", "outcome": "PASS"},
            {"challenge_id": "c4", "outcome": "PASS"},
            {"challenge_id": "c-missing-from-dates", "outcome": "PASS"},
        ]:
            f.write(json.dumps(row) + "\n")

    result = temporal_holdout_split(dates_tsv, results_jsonl, cutoff="2025-01-01T00:00:00+00:00")

    # c1 (2024, pre) and c3 (min date 2023 via baz_dep, pre) land pre-cutoff; c2 (2026) lands post.
    # c4 is depth_limited -> excluded. c-missing-from-dates -> unmatched.
    assert result.pre.n_challenges == 2, result.pre
    assert result.post.n_challenges == 1, result.post
    assert result.pre.per_repo == {"repoA": (1, 1), "repoB": (1, 1)}, result.pre.per_repo
    assert result.post.per_repo == {"repoA": (0, 1)}, result.post.per_repo
    assert result.pre.macro_pass_rate == 1.0, result.pre.macro_pass_rate
    assert result.post.macro_pass_rate == 0.0, result.post.macro_pass_rate
    assert result.excluded_undated == 1, result.excluded_undated
    assert result.unmatched_results == 1, result.unmatched_results
    assert result.challenges_without_results == 0, result.challenges_without_results
    print("temporal_holdout_split: OK")

    print("all self-tests passed")


# --------------------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    p_mine = sub.add_parser("mine", help="extract lemma introduction dates -> lemma_dates.tsv")
    p_mine.add_argument("--repo", action="append", help="restrict to this repo (repeatable)")
    p_mine.add_argument("--jobs", type=int, default=16, help="parallel git subprocesses")
    p_mine.add_argument("--out", default=str(DEFAULT_OUT))
    p_mine.add_argument(
        "--data-root",
        default=str(REPO_ROOT),
        help="repo root under which data/lean/<repo> and artifacts/lean-ablate/ live "
        "(default: this script's own repo root -- override when running from a "
        "worktree, since both are gitignored and only materialised in the main checkout)",
    )
    p_mine.add_argument(
        "--challenges-dir",
        default=None,
        help="default: <data-root>/artifacts/lean-ablate",
    )
    p_mine.set_defaults(func=cmd_mine)

    p_th = sub.add_parser(
        "temporal-holdout", help="join lemma_dates.tsv against results -> pre/post pass rates"
    )
    p_th.add_argument("--dates", default=str(DEFAULT_OUT))
    p_th.add_argument("--results", required=True, help="ablate-baseline --out JSONL")
    p_th.add_argument("--cutoff", required=True, help="ISO 8601 date/datetime, e.g. 2025-06-01")
    p_th.set_defaults(func=cmd_temporal_holdout)

    p_test = sub.add_parser("self-test", help="run unit tests against synthetic data")
    p_test.set_defaults(func=lambda _args: _self_test())

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
