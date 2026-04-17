# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "anthropic",
#     "python-dotenv",
#     "typer",
#     "pydantic",
# ]
# ///
"""
Proof completion experiment using the Claude API.

Two conditions:
  A — experiments/admitted-proofs/   : full Admitted (write entire proof)
  B — experiments/experiments3/      : last 3 tactics removed (complete partial proof)

For each challenge the script:
  1. Reads the challenge file and meta.json
  2. Calls claude-sonnet-4-6 to produce only the missing tactics
  3. Splices the tactics into the original file, writing attempt.v
  4. Clones fiat-crypto at the challenge's commit into /tmp, places a
     renamed copy of attempt.v (<stem>_challenge.v, all declarations
     suffixed with '1'), then runs coqc with the _CoqProject flags
  5. Logs all reasoning, responses, and verdicts to experiment-log.txt
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import anthropic
import typer
from dotenv import load_dotenv
from pydantic import BaseModel

# Walk up from the script's directory to find .env
_script_dir = Path(__file__).resolve().parent
for _p in (_script_dir, *_script_dir.parents):
    if (_p / ".env").exists():
        load_dotenv(_p / ".env")
        break

# ── Config ───────────────────────────────────────────────────────────────────
BASE             = Path(__file__).parent
EXP_A            = BASE / "admitted-proofs"
EXP_B            = BASE / "experiments3"
LOG              = BASE / "experiment-log.txt"
MODEL            = "claude-sonnet-4-6"
COQC             = "coqc"
FIAT_CRYPTO_DIR  = Path(os.environ.get("FIAT_CRYPTO_DIR", "/data/fiat-crypto"))

client = anthropic.Anthropic()


# ── Structured output schema ──────────────────────────────────────────────────

class ProofAttempt(BaseModel):
    tactics: str    # only the tactic lines that replace Admitted., ending with Qed. or Admitted.


# ── Prompts ──────────────────────────────────────────────────────────────────

SYS_PROMPT = """\
You are an expert Coq proof engineer. Your task is to supply the missing \
tactic lines that complete a proof.

Output ONLY the tactic lines themselves — do not repeat any surrounding \
Lemma/Theorem/Definition header, do not include the opening `Proof.`, \
and do not include anything outside the proof body.

End with `Qed.` (or `Defined.` if the original used `Defined.`).
If you genuinely cannot determine the proof, write just `Admitted.`.
Do not hallucinate tactics.
"""

def make_prompt_full(decl: str, file_content: str) -> str:
    return (
        f"The proof of `{decl}` is replaced entirely by `Admitted.`.\n"
        f"Write the complete tactic proof body (tactics only, ending with Qed.).\n\n"
        f"FILE:\n{file_content}"
    )

def make_prompt_partial(decl: str, file_content: str) -> str:
    return (
        f"The last 3 tactic sentences of the proof of `{decl}` have been removed "
        f"and replaced with `Admitted.`.\n"
        f"Write only those missing tactic lines (ending with Qed.) to complete the proof.\n\n"
        f"FILE:\n{file_content}"
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def patch_admitted(content: str, decl: str, tactics: str) -> str:
    """Replace the first standalone Admitted. after `decl` with the supplied tactics."""
    decl_pos = content.find(decl)
    if decl_pos == -1:
        return content

    search_region = content[decl_pos:]
    m = re.search(r'(?m)^\s*Admitted\.\s*$', search_region)
    if m is None:
        return content

    abs_start = decl_pos + m.start()
    abs_end   = decl_pos + m.end()

    indent = re.match(r'(\s*)', content[abs_start:]).group(1)
    replacement = "\n".join(
        indent + line if line.strip() else line
        for line in tactics.strip().splitlines()
    )
    return content[:abs_start] + replacement + content[abs_end:]


def call_claude(prompt: str, log: list[str]) -> ProofAttempt:
    log.append(f"\n  [API call: {MODEL}]")
    t0 = time.time()
    for attempt in range(6):
        try:
            response = client.messages.parse(
                model=MODEL,
                max_tokens=4096,
                temperature=0,
                system=SYS_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_format=ProofAttempt,
            )
            break
        except anthropic.RateLimitError:
            wait = 60 * (attempt + 1)
            log.append(f"  rate-limited (attempt {attempt+1}), waiting {wait}s…")
            print(f"  rate-limited (attempt {attempt+1}), waiting {wait}s…", flush=True)
            time.sleep(wait)
    else:
        raise RuntimeError("Exceeded retry limit for rate limiting")

    elapsed = time.time() - t0
    log.append(f"  elapsed: {elapsed:.1f}s  |  output tokens: {response.usage.output_tokens}")

    result = response.parsed_output
    if result is None:
        log.append("  WARNING: structured output returned None")
        return ProofAttempt(tactics="Admitted.")
    return result


# ── fiat-crypto checkout & coqc ───────────────────────────────────────────────

def _patch_compat(content: str) -> str:
    """Patch known Coq 8.19 incompatibilities from old challenge files."""
    # Omega module removed in 8.18+; the omega tactic is still built-in
    content = re.sub(r'(?m)^\s*Require\s+Import\s+Omega\.\s*$\n?', '', content)
    # SearchAbout removed in 8.17; replaced by Search
    content = re.sub(r'\bSearchAbout\b', 'Search', content)
    return content

def _get_coq_flags(repo_dir: Path) -> list[str]:
    """Parse _CoqProject for -R / -Q / -I flags."""
    cp = repo_dir / "_CoqProject"
    if not cp.exists():
        return []
    flags: list[str] = []
    for line in cp.read_text().splitlines():
        parts = line.strip().split()
        if parts and parts[0] in ("-R", "-Q", "-I") and len(parts) >= 3:
            flags.extend(parts[:3])
    return flags

def _checkout_commit(commit: str, log: list[str]) -> Path | None:
    """Clone fiat-crypto locally at a specific commit into /tmp. Returns the path."""
    if not FIAT_CRYPTO_DIR.exists():
        log.append(f"  fiat-crypto not mounted at {FIAT_CRYPTO_DIR}")
        return None

    tmpdir = Path(f"/tmp/fc_{commit[:8]}")
    if tmpdir.exists():
        shutil.rmtree(tmpdir)

    try:
        subprocess.run(
            ["git", "clone", "--local", "--shared", "--no-checkout",
             str(FIAT_CRYPTO_DIR), str(tmpdir)],
            check=True, capture_output=True, timeout=120,
        )
        subprocess.run(
            ["git", "-C", str(tmpdir), "checkout", commit, "--quiet"],
            check=True, capture_output=True, timeout=120,
        )
    except subprocess.CalledProcessError as e:
        log.append(f"  git checkout failed: {e.stderr.decode()[:200]}")
        return None

    return tmpdir


def run_coqc_challenge(attempt_path: Path, meta: dict, log: list[str]) -> str:
    """
    Place a renamed copy of attempt.v in a fresh checkout of fiat-crypto at
    the challenge's commit, then run coqc with the repo's load-path flags.
    """
    commit    = meta["commit_hash"]
    file_path = Path(meta["file_path"])   # e.g. src/Galois/BaseSystem.v

    log.append(f"  checking out fiat-crypto @ {commit[:8]}…")
    repo = _checkout_commit(commit, log)
    if repo is None:
        return "ERROR"

    try:
        content           = attempt_path.read_text()
        challenge_content = _patch_compat(content)

        challenge_name = file_path.stem + "_challenge.v"
        challenge_path = repo / file_path.parent / challenge_name
        challenge_path.write_text(challenge_content)

        flags = _get_coq_flags(repo)

        log.append(f"  coqc flags: {' '.join(flags)}")
        log.append(f"  target: {file_path.parent / challenge_name}")

        result = subprocess.run(
            [COQC] + flags + [str(challenge_path)],
            capture_output=True, text=True, timeout=300,
            cwd=str(repo),
        )
        if result.returncode == 0:
            log.append("  coqc: PASS ✓")
            return "PASS"
        else:
            short = (result.stderr or result.stdout)[:500].strip()
            log.append(f"  coqc: FAIL\n    {short}")
            return "FAIL"

    except subprocess.TimeoutExpired:
        log.append("  coqc: TIMEOUT (>300s)")
        return "TIMEOUT"
    except FileNotFoundError:
        log.append(f"  coqc: not found at {COQC}")
        return "ERROR"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_condition(slots: list[Path], condition: str,
                  prompt_fn, challenge_file: str, log_lines: list[str],
                  max_challenges: int | None = None) -> dict:
    results = {}
    count = 0
    for slot in sorted(slots):
        if not slot.is_dir():
            continue
        if max_challenges is not None and count >= max_challenges:
            break
        meta_path = slot / "meta.json"
        chal_path = slot / challenge_file
        if not meta_path.exists() or not chal_path.exists():
            continue
        count += 1

        meta    = json.loads(meta_path.read_text())
        decl    = meta["declaration"]
        content = chal_path.read_text()

        log_lines.append(f"\n{'═'*64}")
        log_lines.append(f"[{condition}] {slot.name}")
        log_lines.append(f"Declaration : {decl}")
        log_lines.append(f"File        : {meta.get('file_path','')}")
        log_lines.append(f"Commit      : {meta.get('commit_hash','')[:12]}")

        # Show proof context around the target Admitted.
        decl_pos = content.find(decl)
        if decl_pos != -1:
            m = re.search(r'(?m)^\s*Admitted\.\s*$', content[decl_pos:])
            if m:
                abs_line = content[:decl_pos + m.start()].count('\n')
                lines = content.splitlines()
                ctx_start = max(0, abs_line - 6)
                ctx_end   = min(len(lines), abs_line + 3)
                ctx = "\n".join(f"  {i+1:>4} | {lines[i]}"
                                for i in range(ctx_start, ctx_end))
                log_lines.append(f"\nProof context (lines {ctx_start+1}–{ctx_end}):")
                log_lines.append(ctx)

        # Generate tactics
        prompt = prompt_fn(decl, content)
        proof  = call_claude(prompt, log_lines)

        log_lines.append(f"\nTactics returned:\n{proof.tactics.strip()}")

        # Splice tactics into original file → attempt.v
        completed    = patch_admitted(content, decl, proof.tactics)
        attempt_path = slot / "attempt.v"
        attempt_path.write_text(completed)
        log_lines.append(f"\nattempt.v written")

        # Compile against fiat-crypto at the challenge's commit
        verdict = run_coqc_challenge(attempt_path, meta, log_lines)
        results[slot.name] = verdict

    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main(
    max_challenges: int = typer.Option(3, "--max-challenges", "-n",
                                       help="Max challenges per condition (0 = all)"),
):
    limit = max_challenges if max_challenges > 0 else None

    log_lines: list[str] = [
        "=" * 64,
        "PROOF COMPLETION EXPERIMENT — Claude API",
        f"Model : {MODEL}",
        f"Max   : {limit or 'all'}",
        f"Date  : {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 64,
    ]

    # Condition B — last 3 tactics removed
    log_lines.append("\n\n" + "━"*64)
    log_lines.append("CONDITION B: Last 3 tactics removed (experiments3)")
    log_lines.append("━"*64)
    results_b = run_condition(
        list(EXP_B.iterdir()), "B", make_prompt_partial, "challenge3.v",
        log_lines, max_challenges=limit,
    )

    # Condition A — full Admitted
    log_lines.append("\n\n" + "━"*64)
    log_lines.append("CONDITION A: Full Admitted (admitted-proofs)")
    log_lines.append("━"*64)
    results_a = run_condition(
        list(EXP_A.iterdir()), "A", make_prompt_full, "challenge.v",
        log_lines, max_challenges=limit,
    )

    # Summary
    log_lines.append("\n\n" + "="*64)
    log_lines.append("SUMMARY")
    log_lines.append("="*64)

    for label, results in [("B (last 3 tactics)", results_b), ("A (full Admitted)", results_a)]:
        total  = len(results)
        passes = sum(1 for v in results.values() if v == "PASS")
        fails  = sum(1 for v in results.values() if v == "FAIL")
        errors = sum(1 for v in results.values() if v in ("TIMEOUT", "ERROR"))
        log_lines.append(f"\nCondition {label}:")
        log_lines.append(f"  Total   : {total}")
        log_lines.append(f"  PASS    : {passes}")
        log_lines.append(f"  FAIL    : {fails}")
        log_lines.append(f"  ERR/TIM : {errors}")
        log_lines.append(f"\n  Per-challenge:")
        for name, verdict in results.items():
            log_lines.append(f"    {name:<50} {verdict}")

    report = "\n".join(log_lines)
    LOG.write_text(report)
    print(report)
    print(f"\nFull log written to: {LOG}")


if __name__ == "__main__":
    typer.run(main)