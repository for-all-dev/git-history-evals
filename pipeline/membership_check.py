#!/usr/bin/env python3
"""Direct training-corpus membership check for every repo in `repos.tsv` (issue #137).

Replaces the stars/age correlation (see `docs/contamination.agents.md`) with a *direct*
measurement: does this origin URL appear in the corpus a documented training-data snapshot
was built from?

## Method

The Stack v1/v2 are not full-text searchable without downloading terabytes of parquet (v2 is
also gated behind a license click-through on HuggingFace, so `datasets-server` search 404s
without an accepted `HF_TOKEN`). But **The Stack v2 is built directly from a dated Software
Heritage (SWH) graph snapshot** ("3.28B files belonging to 104.2M GitHub repositories were
collected by traversing the Software Heritage 2023-09-06 graph dataset" — the-stack-v2 dataset
card), and SWH's public API records, per origin, the dated history of every crawl ("visit") it
has made. So instead of approximating membership through popularity (stars/age), we query SWH
directly for each repo's origin and visit history, and compare the *earliest* visit date against
the documented snapshot cutoffs:

- The Stack v1 cutoff: **2022-06** (files "downloaded from public GitHub repositories between
  November 2021 and June 2022" — the-stack v1 dataset card)
- The Stack v2 cutoff: **2023-09-06** (SWH graph snapshot date — the-stack-v2 dataset card)

`earliest_visit_date <= cutoff` is necessary but not sufficient for actual inclusion (SWH
crawling the repo by that date does not guarantee the-stack's own filtering/dedup/license
pipeline kept it), so results are reported as `likely_v1` / `likely_v2` / `not_in_swh` /
`too_recent` / `unknown`, not a hard yes/no. This is still strictly more direct than the
stars/age proxy it replaces: it is dated evidence about *this exact origin URL*, not a proxy
correlated with duplication count.

## Optional cross-check: AI2 infini-gram

`--infinigram` additionally queries api.infini-gram.io for the raw count of the repo's
`owner/name` slug string inside an open pretraining corpus (default: `v4_dolma-v1_7_llama`).
This is a weak, noisy secondary signal (a slug can appear in READMEs/link lists without the
*code* being duplicated, and non-appearance doesn't prove non-membership) — kept optional and
clearly labeled as such in the output, per the issue's "optionally cross-check" framing.

## Usage

    uv run --no-project python3 pipeline/membership_check.py [--infinigram] [--out membership.tsv]

Network access required. Every API call is wrapped so a transient failure records `UNKNOWN`
rather than crashing the run — see `_get_json`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOS_TSV = ROOT / "pipeline" / "repos.tsv"
DEFAULT_OUT = ROOT / "pipeline" / "membership.tsv"

SWH_API = "https://archive.softwareheritage.org/api/1"
INFINIGRAM_API = "https://api.infini-gram.io/"
INFINIGRAM_DEFAULT_INDEX = "v4_dolma-v1_7_llama"

STACK_V1_CUTOFF = date(2022, 6, 30)
STACK_V2_CUTOFF = date(2023, 9, 6)

USER_AGENT = "for-all-dev-git-history-evals-membership-check/1.0"


def _get_json(url: str, *, timeout: float = 20.0, method: str = "GET", data: bytes | None = None) -> tuple[dict | list | None, str | None]:
    """GET/POST `url`, return (parsed_json, None) on success or (None, error_str) on failure.

    Never raises — every caller treats a failure as UNKNOWN, per the issue's robustness
    requirement ("record UNKNOWN rather than crashing").
    """
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return json.loads(body), None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, "not_found"
        return None, f"http_error_{e.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"


def _normalize_github_url(url: str) -> str:
    """SWH indexes origins by exact URL. Strip `.git` suffix and trailing slash — the two
    variants that actually appear in repos.tsv (`cedar-spec` has `.git`)."""
    u = url.strip()
    if u.endswith(".git"):
        u = u[: -len(".git")]
    return u.rstrip("/")


@dataclass
class MembershipResult:
    name: str
    url: str
    swh_known: str = "UNKNOWN"  # yes / no / UNKNOWN
    earliest_visit_date: str = ""  # ISO date of the first full SWH visit, or ""
    latest_visit_date: str = ""
    num_visits: int = -1
    stack_v1_status: str = "UNKNOWN"  # likely_in / too_recent / not_in_swh / UNKNOWN
    stack_v2_status: str = "UNKNOWN"
    infinigram_index: str = ""
    infinigram_count: int = -1  # -1 = not queried / failed
    infinigram_approx: str = ""
    notes: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def check_swh(name: str, url: str, *, sleep: float) -> MembershipResult:
    r = MembershipResult(name=name, url=url)
    norm = _normalize_github_url(url)

    origin, err = _get_json(f"{SWH_API}/origin/{norm}/get/")
    time.sleep(sleep)
    if err == "not_found":
        r.swh_known = "no"
        r.stack_v1_status = "not_in_swh"
        r.stack_v2_status = "not_in_swh"
        return r
    if err is not None:
        r.swh_known = "UNKNOWN"
        r.stack_v1_status = "UNKNOWN"
        r.stack_v2_status = "UNKNOWN"
        r.note(f"origin lookup failed: {err}")
        return r

    r.swh_known = "yes"
    assert isinstance(origin, dict)

    visits, verr = _get_json(f"{SWH_API}/origin/{norm}/visits/?per_page=1000")
    time.sleep(sleep)
    if verr is not None:
        r.note(f"visits lookup failed: {verr}")
        r.stack_v1_status = "UNKNOWN"
        r.stack_v2_status = "UNKNOWN"
        return r
    assert isinstance(visits, list)

    dates: list[date] = []
    for v in visits:
        d = v.get("date")
        if not d:
            continue
        try:
            dates.append(datetime.fromisoformat(d.replace("Z", "+00:00")).date())
        except ValueError:
            continue

    r.num_visits = len(dates)
    if not dates:
        # known origin, but SWH has never completed a visit we could date (e.g. pending/failed)
        r.stack_v1_status = "not_in_swh"
        r.stack_v2_status = "not_in_swh"
        return r

    earliest, latest = min(dates), max(dates)
    r.earliest_visit_date = earliest.isoformat()
    r.latest_visit_date = latest.isoformat()

    r.stack_v1_status = "likely_in" if earliest <= STACK_V1_CUTOFF else "too_recent"
    r.stack_v2_status = "likely_in" if earliest <= STACK_V2_CUTOFF else "too_recent"
    return r


_SLUG_RE = re.compile(r"github\.com/([^/]+/[^/.]+)")


def check_infinigram(r: MembershipResult, *, index: str, sleep: float) -> None:
    m = _SLUG_RE.search(r.url)
    if not m:
        r.note("could not extract owner/name slug for infini-gram query")
        return
    slug = m.group(1)
    r.infinigram_index = index
    payload = json.dumps({"index": index, "query_type": "count", "query": slug}).encode()
    resp, err = _get_json(INFINIGRAM_API, method="POST", data=payload, timeout=30.0)
    time.sleep(sleep)
    if err is not None:
        r.note(f"infini-gram query failed: {err}")
        return
    assert isinstance(resp, dict)
    if "error" in resp:
        r.note(f"infini-gram error: {resp['error']}")
        return
    r.infinigram_count = int(resp.get("count", -1))
    r.infinigram_approx = str(resp.get("approx", ""))


def load_repos(tsv_path: Path) -> list[tuple[str, str]]:
    repos = []
    with open(tsv_path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            repos.append((row["name"], row["url"]))
    return repos


def macro_pass_rate_join_hook(membership_tsv: Path, results_jsonl: Path) -> dict[str, tuple[str, float]]:
    """TODO(#137 follow-up): correlate membership against pass rate, macro-averaged per repo.

    Blocked on a baseline run over enough of the 57-repo corpus to compute a per-repo macro
    pass rate (the only baseline we have today, `docs/leanstral-baseline-100.md`, covers 47
    repos at 2 problems each — noted in `docs/contamination.agents.md` as "a hint, not a
    result"; fabricating a wider table here is out of scope for this issue).

    Join key once such a run exists: `repo name` (the `name` column in `pipeline/repos.tsv` /
    this script's `membership.tsv`, which is also `challenge["repo"]` in ablator output and the
    `repo` field of `baselines/apply_ablate` result JSONL) -> macro-averaged pass rate for that
    repo (mean of per-challenge PASS/fail over that repo's *evaluated* challenges; "macro" so a
    repo with many challenges, e.g. `evm-asm`, does not dominate the correlation the way it does
    the raw corpus size, per the "macro-averaged per repo" warning in
    `docs/contamination.agents.md`'s "Corpus facts" section).

    Implementation sketch, once `results_jsonl` exists (one JSON object per solved challenge,
    with `repo` and an outcome field, matching `baselines/apply_ablate/record.py`):

        from collections import defaultdict
        outcomes = defaultdict(list)
        for line in open(results_jsonl):
            rec = json.loads(line)
            outcomes[rec["repo"]].append(1.0 if rec["outcome"] == "PASS" else 0.0)
        macro_pass_rate = {repo: sum(v) / len(v) for repo, v in outcomes.items()}
        # then join macro_pass_rate[name] against the stack_v1_status/stack_v2_status columns
        # of membership_tsv (e.g. Pearson r of pass_rate vs. an is-member indicator, or a
        # two-sample comparison of pass rate for likely_in vs. not_in_swh repos) and report
        # both counts per bucket (n is small - most of the 57 repos are post-2024, see
        # docs/contamination.agents.md's "Corpus facts") alongside the correlation.

    Returns {} today — intentionally not fabricated. Raises if called before a results file
    exists, so accidental use in a report script fails loudly instead of silently reporting
    zeros.
    """
    if not results_jsonl.exists():
        raise FileNotFoundError(
            f"{results_jsonl} does not exist yet — no baseline run covers enough of the "
            "57-repo corpus to compute a per-repo macro pass rate. See this function's "
            "docstring for the join key and implementation sketch once one does."
        )
    raise NotImplementedError("wire up once a corpus-wide baseline run exists")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos-tsv", type=Path, default=REPOS_TSV)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--infinigram", action="store_true", help="also cross-check AI2 infini-gram (slug substring count)")
    ap.add_argument("--infinigram-index", default=INFINIGRAM_DEFAULT_INDEX)
    ap.add_argument("--sleep", type=float, default=0.6, help="delay between API calls (SWH unauthenticated: 120/hr)")
    ap.add_argument("--limit", type=int, default=None, help="only process the first N repos (debugging)")
    args = ap.parse_args()

    repos = load_repos(args.repos_tsv)
    if args.limit:
        repos = repos[: args.limit]

    results: list[MembershipResult] = []
    for i, (name, url) in enumerate(repos, 1):
        print(f"[{i}/{len(repos)}] {name} ({url})", file=sys.stderr)
        r = check_swh(name, url, sleep=args.sleep)
        if args.infinigram:
            check_infinigram(r, index=args.infinigram_index, sleep=args.sleep)
        results.append(r)

    fields = [
        "name",
        "url",
        "swh_known",
        "earliest_visit_date",
        "latest_visit_date",
        "num_visits",
        "stack_v1_status",
        "stack_v2_status",
        "infinigram_index",
        "infinigram_count",
        "infinigram_approx",
        "notes",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(fields)
        for r in results:
            w.writerow(
                [
                    r.name,
                    r.url,
                    r.swh_known,
                    r.earliest_visit_date,
                    r.latest_visit_date,
                    r.num_visits,
                    r.stack_v1_status,
                    r.stack_v2_status,
                    r.infinigram_index,
                    r.infinigram_count,
                    r.infinigram_approx,
                    "; ".join(r.notes),
                ]
            )

    v2_in = sum(1 for r in results if r.stack_v2_status == "likely_in")
    v2_unknown = sum(1 for r in results if r.stack_v2_status == "UNKNOWN")
    print(
        f"\nwrote {len(results)} rows to {args.out}\n"
        f"Stack v2 (SWH <= {STACK_V2_CUTOFF}): likely_in={v2_in} "
        f"too_recent={sum(1 for r in results if r.stack_v2_status == 'too_recent')} "
        f"not_in_swh={sum(1 for r in results if r.stack_v2_status == 'not_in_swh')} "
        f"UNKNOWN={v2_unknown}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
