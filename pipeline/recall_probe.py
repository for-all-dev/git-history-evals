#!/usr/bin/env python3
"""Verbatim-recall probe on deleted lemmas (issue #134).

The standard black-box memorization test (Golchin & Surdeanu, *Time Travel in LLMs* —
guided prompting / verbatim completion), applied to the deleted lemmas of the paired
sample. It is the one instrument in `docs/contamination.agents.md` that separates
*memorization* from *ability*: a model that can recite a proof body it was never shown
is demonstrably drawing on training data, and if it cannot recite the proofs it
nonetheless solves, its solves are not retrieval.

## What is measured, and against which of the two memorization levels

`docs/contamination.agents.md` splits the threat in two:

- **instance-level** (the model saw *this exact challenge document*) — defeated by
  construction, since the holed/sliced file never existed;
- **knowledge-level** (the model knows *this lemma*) — not defeated, not claimable
  either way without measurement.

This probe targets **knowledge-level** memorization directly, and it is the only
instrument here that can produce *positive* evidence of it. The probe shows the model
the repo identity, the revision, the file path and the full declaration header — but
never the proof body, and never the repo (no tools, no retrieval, one shot). Any
verbatim reproduction of a non-trivial body must therefore come from pretraining.

## Design decisions worth defending

1. **One probe per `challenge_id`, not per split.** The `easy` (leaf-hole) and `hard`
   (whole-body) rows of the paired sample delete the *same* lemma — verified: all 113
   pairs have byte-identical `deleted_lemmas[0]["text"]`. The recall quantity is a
   property of the lemma, so it is probed once and joined to both splits' outcomes.
   That also makes the easy-vs-hard comparison a paired one on a shared regressor.
2. **Header/body split at the signature's `:=`.** The model gets everything a reader
   of the file would have except the proof: docstring, attributes, binders, statement.
   Ground truth is the text after `:=` (usually `by ...`).
3. **Three scores, all reported.** Exact match after whitespace normalization is the
   only unambiguous instance-of-recall evidence but is near-zero and misleading alone;
   normalized Levenshtein similarity and token-level F1 give the graded band. The
   issue asks for all three.
4. **Trivial-body stratification is load-bearing.** `by simp` is reproduced by *any*
   competent Lean model with zero memorization, so a corpus-wide exact-match rate is
   dominated by stock one-liners. Every table is therefore also reported on the
   non-trivial subset (proofs of more than one line), where guessing the
   exact token sequence is implausible. All three exact matches this probe found were
   single-line `by simp [...]` bodies — i.e. exactly the artifact this stratum removes.
5. **Correlation is the payoff, not the recall number.** Per (model, split) we report
   the point-biserial correlation between recall similarity and the PASS outcome *of
   the same model on the same challenge*, with a bootstrap CI and a permutation
   p-value, plus pass rate by recall band with Wilson intervals.

## Usage

    # 1. probe (network; ~113 x n_models single-shot calls)
    uv run --project baselines python3 pipeline/recall_probe.py probe \\
        --sample scratch-wave3/paired/easy/sample.jsonl \\
        --out pipeline/recall_probe_responses.jsonl

    # 2. score + join against the grid outcomes (offline, stdlib only)
    python3 pipeline/recall_probe.py score \\
        --responses pipeline/recall_probe_responses.jsonl \\
        --sample scratch-wave3/paired/easy/sample.jsonl \\
        --results claude-sonnet-5=scratch-wave3/paired \\
        --results openai:gpt-5.6-sol=scratch-wave3/paired-openai \\
        --results mistral:labs-leanstral-1-5=scratch-wave3/paired-leanstral \\
        --out pipeline/recall_probe.tsv --report -

`probe` needs `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `MISTRAL_API_KEY` in the env
(`set -a; . .env; set +a`) and pydantic-ai (the `baselines` venv). `score` is stdlib.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

GRID_MODELS = (
    "claude-sonnet-5",
    "openai:gpt-5.6-sol",
    "mistral:labs-leanstral-1-5",
)

SYSTEM_PROMPT = (
    "You are being tested on verbatim recall of public Lean 4 source code that may "
    "appear in your pretraining data. You have no tools and no access to the "
    "repository. Answer from memory."
)

USER_PROMPT = """\
Repository: {repo}
Revision: {revision}
File: {file_path}

The declaration below appears in that file. Its proof body has been removed and \
replaced by the marker <PROOF>.

```lean
{header} <PROOF>
```

Reproduce the proof body EXACTLY as it appears in that file, i.e. the text that \
follows `:=` in the original source (it normally starts with `by`).

Rules:
- Output ONLY the proof body. No commentary, no explanation, no code fence.
- Do not restate the declaration header.
- If you do not recall this file, output your best reconstruction anyway.
"""


# ---------------------------------------------------------------------------
# header / body split
# ---------------------------------------------------------------------------

_OPEN = "([{⟨⦃"
_CLOSE = ")]}⟩⦄"


def split_signature(text: str) -> tuple[str, str] | None:
    """Split a Lean declaration into (header, proof body) at the signature's `:=`.

    Returns None when no top-level `:=` is found (e.g. an `example`-less snippet, or a
    declaration whose proof is given by `where`/equation clauses only).
    """
    depth = 0
    i = 0
    n = len(text)
    in_line_comment = False
    block_depth = 0
    while i < n:
        ch = text[i]
        two = text[i : i + 2]
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if block_depth:
            if two == "-/":
                block_depth -= 1
                i += 2
                continue
            if two == "/-":
                block_depth += 1
                i += 2
                continue
            i += 1
            continue
        if two == "--":
            in_line_comment = True
            i += 2
            continue
        if two == "/-":
            block_depth += 1
            i += 2
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        elif two == ":=" and depth == 0:
            header = text[:i].rstrip()
            body = text[i + 2 :].strip()
            if not header or not body:
                return None
            return header, body
        i += 1
    return None


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```[a-zA-Z0-9]*\n?|\n?```$")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_'.!?]*|\d+|[^\sA-Za-z0-9_]")


def clean_response(text: str) -> str:
    """Strip code fences and a leading `:=` the model may have echoed."""
    t = text.strip()
    if t.startswith("```"):
        t = _FENCE.sub("", t).strip()
        t = re.sub(r"\n?```\s*$", "", t).strip()
    t = re.sub(r"^:=\s*", "", t)
    return t.strip()


def norm_ws(text: str) -> str:
    return " ".join(text.split())


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def levenshtein_ratio(a: str, b: str) -> float:
    """1 - normalized edit distance, on characters. 1.0 == identical."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1.0 - prev[-1] / max(len(a), len(b))


def token_f1(a: str, b: str) -> float:
    """Multiset token F1 — order-insensitive, robust to reformatting."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 1.0 if not ta and not tb else 0.0
    from collections import Counter

    overlap = sum((Counter(ta) & Counter(tb)).values())
    if not overlap:
        return 0.0
    p, r = overlap / len(ta), overlap / len(tb)
    return 2 * p * r / (p + r)


def band(sim: float) -> str:
    return "high" if sim >= 0.9 else "mid" if sim >= 0.6 else "low"


# ---------------------------------------------------------------------------
# statistics (stdlib)
# ---------------------------------------------------------------------------


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def bootstrap_ci(
    xs: list[float], ys: list[float], iters: int = 5000, seed: int = 42
) -> tuple[float, float]:
    if len(xs) < 3:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(xs)
    vals = []
    for _ in range(iters):
        idx = [rng.randrange(n) for _ in range(n)]
        r = pearson([xs[i] for i in idx], [ys[i] for i in idx])
        if not math.isnan(r):
            vals.append(r)
    if not vals:
        return (float("nan"), float("nan"))
    vals.sort()
    return (
        vals[int(0.025 * len(vals))],
        vals[min(len(vals) - 1, int(0.975 * len(vals)))],
    )


def permutation_p(
    xs: list[float], ys: list[float], iters: int = 10000, seed: int = 42
) -> float:
    r0 = pearson(xs, ys)
    if math.isnan(r0):
        return float("nan")
    rng = random.Random(seed)
    ys2 = list(ys)
    hits = 0
    for _ in range(iters):
        rng.shuffle(ys2)
        r = pearson(xs, ys2)
        if not math.isnan(r) and abs(r) >= abs(r0):
            hits += 1
    return (hits + 1) / (iters + 1)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        nan = float("nan")
        return (nan, nan, nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------


@dataclass
class Lemma:
    challenge_id: str
    repo: str
    revision: str
    file_path: str
    name: str
    header: str
    body: str
    n_tactics: int
    n_lines: int

    @property
    def trivial_body(self) -> bool:
        """Bodies a competent Lean model can reproduce with zero memorization.

        A one-line proof (`by simp [foo]`, `by rfl`, a one-line term — the leading `by`
        may sit on its own line) is guessable from the statement alone by any model that
        knows Lean, so an exact match on one is not evidence of having seen the file.
        Multi-line bodies are different in kind: reproducing a specific tactic *sequence*
        token for token is what memorization looks like.
        """
        rest = re.sub(r"^by\b", "", self.body.strip()).strip()
        return len([ln for ln in rest.splitlines() if ln.strip()]) <= 1


def load_lemmas(sample: Path) -> dict[str, Lemma]:
    out: dict[str, Lemma] = {}
    skipped = 0
    for line in sample.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        dels = row.get("deleted_lemmas") or []
        if not dels:
            continue
        d = dels[0]
        parts = split_signature(d["text"])
        if parts is None:
            skipped += 1
            continue
        header, body = parts
        out[row["challenge_id"]] = Lemma(
            challenge_id=row["challenge_id"],
            repo=row["repo"],
            revision=row.get("revision", ""),
            file_path=row["file_path"],
            name=d["name"],
            header=header,
            body=body,
            n_tactics=int(d.get("n_tactics") or 0),
            n_lines=int(d.get("n_lines") or 0),
        )
    if skipped:
        print(f"[probe] skipped {skipped} lemma(s): no top-level `:=`", file=sys.stderr)
    return out


def outcome(row: dict) -> str:
    """Collapse a baselines result row into pass / fail / excluded."""
    if row.get("malformed_challenge") or row.get("trivial"):
        return "excluded"
    if row.get("tampered"):
        return "fail"
    return "pass" if row.get("succeeded") else "fail"


def load_results(tree: Path) -> dict[tuple[str, str], str]:
    """{(split, challenge_id): outcome} for a per-model results tree."""
    out: dict[tuple[str, str], str] = {}
    for split in ("easy", "hard"):
        p = tree / split / "results.jsonl"
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                out[(split, row["challenge_id"])] = outcome(row)
    return out


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def load_env_file(path: Path) -> None:
    """Load `KEY=value` lines from a dotenv file into `os.environ` (no overwrite)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def make_model(model: str):
    """Mirror of `apply_ablate.solve.make_agent`'s provider dispatch, tool-free."""
    if model.startswith("mistral:"):
        from pydantic_ai.models.mistral import MistralModel

        return MistralModel(model.removeprefix("mistral:"))
    if model.startswith("openai:"):
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIResponsesModel(
            model.removeprefix("openai:"),
            provider=OpenAIProvider(api_key=os.environ.get("OPENAI_API_KEY")),
        )
    if model.startswith("anthropic:") or ":" not in model:
        from pydantic_ai.models.anthropic import AnthropicModel

        return AnthropicModel(model.removeprefix("anthropic:"))
    from pydantic_ai.models import infer_model

    return infer_model(model)


async def probe_all(
    lemmas: list[Lemma], models: list[str], out_path: Path, concurrency: int
) -> None:
    from pydantic_ai import Agent

    done: set[tuple[str, str]] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                # errored rows are NOT done — a resume retries them (reasoning models
                # occasionally burn the whole token budget on reasoning and emit nothing)
                if not r.get("error"):
                    done.add((r["model"], r["challenge_id"]))
        print(
            f"[probe] resuming: {len(done)} responses already recorded", file=sys.stderr
        )

    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    fh = out_path.open("a")

    async def one(model: str, agent, lem: Lemma) -> None:
        async with sem:
            rec = {"model": model, "challenge_id": lem.challenge_id, "lemma": lem.name}
            try:
                res = await agent.run(
                    USER_PROMPT.format(
                        repo=lem.repo,
                        revision=lem.revision,
                        file_path=lem.file_path,
                        header=lem.header,
                    )
                )
                rec["response"] = res.output
            except Exception as exc:  # noqa: BLE001 — one bad call must not kill the run
                rec["error"] = f"{type(exc).__name__}: {exc}"
                rec["response"] = ""
            async with lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()

    tasks = []
    for model in models:
        agent = Agent(
            make_model(model),
            system_prompt=SYSTEM_PROMPT,
            output_type=str,
            # generous: gpt-5.6-sol spends most of its budget on reasoning tokens and
            # returns *nothing* if it hits the cap first (14/113 losses at 4000)
            model_settings={"max_tokens": 16000},
        )
        for lem in lemmas:
            if (model, lem.challenge_id) not in done:
                tasks.append(one(model, agent, lem))
    print(f"[probe] {len(tasks)} calls to make", file=sys.stderr)
    await asyncio.gather(*tasks)
    fh.close()


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

TSV_COLUMNS = [
    "model",
    "challenge_id",
    "repo",
    "file_path",
    "lemma",
    "gt_n_tactics",
    "gt_n_lines",
    "trivial_body",
    "exact",
    "lev",
    "tokf1",
    "band",
    "easy_outcome",
    "hard_outcome",
    "error",
]


@dataclass
class Row:
    model: str
    lemma: Lemma
    exact: int
    lev: float
    tokf1: float
    easy: str
    hard: str
    err: str = ""

    def outcome_for(self, split: str) -> str:
        return self.easy if split == "easy" else self.hard


def score(
    responses: Path, lemmas: dict[str, Lemma], results: dict[str, dict]
) -> list[Row]:
    rows: list[Row] = []
    # last write wins: a resumed run appends retries of earlier errored rows
    latest: dict[tuple[str, str], dict] = {}
    for line in responses.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            latest[(r["model"], r["challenge_id"])] = r
    for r in latest.values():
        lem = lemmas.get(r["challenge_id"])
        if lem is None:
            continue
        pred = clean_response(r.get("response") or "")
        a, b = norm_ws(pred), norm_ws(lem.body)
        res = results.get(r["model"], {})
        rows.append(
            Row(
                model=r["model"],
                lemma=lem,
                exact=int(a == b and bool(b)),
                lev=levenshtein_ratio(a, b),
                tokf1=token_f1(a, b),
                easy=res.get(("easy", lem.challenge_id), "missing"),
                hard=res.get(("hard", lem.challenge_id), "missing"),
                err=r.get("error", ""),
            )
        )
    return rows


def write_tsv(rows: list[Row], out: Path) -> None:
    with out.open("w") as fh:
        fh.write("\t".join(TSV_COLUMNS) + "\n")
        for r in sorted(rows, key=lambda r: (r.model, r.lemma.challenge_id)):
            fh.write(
                "\t".join(
                    str(v)
                    for v in [
                        r.model,
                        r.lemma.challenge_id,
                        r.lemma.repo,
                        r.lemma.file_path,
                        r.lemma.name,
                        r.lemma.n_tactics,
                        r.lemma.n_lines,
                        int(r.lemma.trivial_body),
                        r.exact,
                        f"{r.lev:.4f}",
                        f"{r.tokf1:.4f}",
                        band(r.lev),
                        r.easy,
                        r.hard,
                        r.err.replace("\t", " ")[:120],
                    ]
                )
                + "\n"
            )


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _residuals(ys: list[float], xs: list[float]) -> list[float]:
    """Residuals of `ys` after least-squares regression on `xs`."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return [y - my for y in ys]
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return [y - (my + b * (x - mx)) for x, y in zip(xs, ys)]


def partial_pearson(xs: list[float], ys: list[float], zs: list[float]) -> float:
    """r(x, y | z) — the confound control.

    Short proofs are both easier to guess (high recall similarity) and easier to solve, so
    a raw r(recall, PASS) can be entirely a proof-size artifact. Controlling for
    log(proof length) removes that path.
    """
    if len(xs) < 4:
        return float("nan")
    return pearson(_residuals(xs, zs), _residuals(ys, zs))


def report(rows: list[Row]) -> str:
    out: list[str] = []
    models = sorted({r.model for r in rows})
    out.append("### Recall scores (whole sample / non-trivial bodies)\n")
    out.append(
        "| model | n | exact | mean lev | mean tokF1 | high band | "
        "n non-triv | exact (nt) | mean lev (nt) | high band (nt) |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for m in models:
        rs = [r for r in rows if r.model == m]
        nt = [r for r in rs if not r.lemma.trivial_body]

        def cell(sub: list[Row]) -> tuple[str, str, str]:
            n = len(sub) or 1
            return (
                f"{sum(r.exact for r in sub)}/{len(sub)}",
                f"{_mean([r.lev for r in sub]):.3f}",
                f"{sum(band(r.lev) == 'high' for r in sub) / n:.1%}",
            )

        e, lv, hb = cell(rs)
        e2, lv2, hb2 = cell(nt)
        out.append(
            f"| {m} | {len(rs)} | {e} | {lv} | {_mean([r.tokf1 for r in rs]):.3f} | "
            f"{hb} | {len(nt)} | {e2} | {lv2} | {hb2} |"
        )

    out.append(
        "\n### Recall vs. solve outcome (point-biserial, same model both sides)\n"
    )
    out.append(
        "| model | split | n scorable | pass | r(lev, PASS) | 95% CI | perm p | "
        "partial r (size-controlled) |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for m in models:
        for split in ("easy", "hard"):
            sub = [
                r
                for r in rows
                if r.model == m and r.outcome_for(split) in ("pass", "fail")
            ]
            xs = [r.lev for r in sub]
            ys = [1.0 if r.outcome_for(split) == "pass" else 0.0 for r in sub]
            r_ = pearson(xs, ys)
            lo, hi = bootstrap_ci(xs, ys)
            p = permutation_p(xs, ys)
            k = int(sum(ys))
            zs = [math.log(1 + len(r.lemma.body)) for r in sub]
            rp_ = partial_pearson(xs, ys, zs)
            out.append(
                f"| {m} | {split} | {len(sub)} | {k}/{len(sub)} | {r_:+.3f} | "
                f"[{lo:+.3f}, {hi:+.3f}] | {p:.3f} | {rp_:+.3f} |"
            )

    out.append("\n### Pass rate by recall band (Wilson 95%)\n")
    out.append("| model | split | band | n | pass rate |")
    out.append("|---|---|---|---|---|")
    for m in models:
        for split in ("easy", "hard"):
            for b in ("high", "mid", "low"):
                sub = [
                    r
                    for r in rows
                    if r.model == m
                    and band(r.lev) == b
                    and r.outcome_for(split) in ("pass", "fail")
                ]
                if not sub:
                    continue
                k = sum(r.outcome_for(split) == "pass" for r in sub)
                p, lo, hi = wilson(k, len(sub))
                out.append(
                    f"| {m} | {split} | {b} | {len(sub)} | "
                    f"{p:.1%} [{lo:.1%}, {hi:.1%}] |"
                )
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="run the single-shot recall probe (network)")
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--models", nargs="+", default=list(GRID_MODELS))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--env-file", type=Path, default=Path(".env"))

    s = sub.add_parser("score", help="score responses and join against grid outcomes")
    s.add_argument("--responses", type=Path, required=True)
    s.add_argument("--sample", type=Path, required=True)
    s.add_argument(
        "--results",
        action="append",
        default=[],
        metavar="MODEL=TREE",
        help="results tree for a model, e.g. claude-sonnet-5=scratch-wave3/paired",
    )
    s.add_argument("--out", type=Path)
    s.add_argument(
        "--report", type=str, default="", help="markdown report path, or '-'"
    )

    args = ap.parse_args(argv)
    lemmas = load_lemmas(args.sample)

    if args.cmd == "probe":
        load_env_file(args.env_file)
        items = list(lemmas.values())
        if args.limit:
            items = items[: args.limit]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        asyncio.run(probe_all(items, args.models, args.out, args.concurrency))
        print(f"[probe] wrote {args.out}", file=sys.stderr)
        return 0

    results = {}
    for spec in args.results:
        model, _, tree = spec.partition("=")
        results[model] = load_results(Path(tree))
    rows = score(args.responses, lemmas, results)
    if args.out:
        write_tsv(rows, args.out)
        print(f"[score] wrote {args.out} ({len(rows)} rows)", file=sys.stderr)
    if args.report:
        text = report(rows)
        if args.report == "-":
            sys.stdout.write(text)
        else:
            Path(args.report).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
