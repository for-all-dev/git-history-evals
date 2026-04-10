"""
Proof completion experiment using the Claude API.

Two conditions:
  A — experiments/admitted-proofs/   : full Admitted (write entire proof)
  B — experiments/experiments3/      : last 3 tactics removed (complete partial proof)

For each challenge the script:
  1. Reads the challenge file and meta.json
  2. Calls claude-sonnet-4-6 to fill the Admitted
  3. Writes the completed file to attempt.v
  4. Runs coqc to check correctness (best-effort — fiat-crypto deps may be missing)
  5. Logs all reasoning, responses, and verdicts to experiment-log.txt
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ── Config ───────────────────────────────────────────────────────────────────
BASE  = Path(__file__).parent
EXP_A = BASE / "admitted-proofs"
EXP_B = BASE / "experiments3"
LOG   = BASE / "experiment-log.txt"
MODEL          = "claude-sonnet-4-6"
COQC           = "coqc"
MAX_CHALLENGES = 3   # set to None to run all

client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env

# ── Prompts ──────────────────────────────────────────────────────────────────

SYS_PROMPT = """\
You are an expert Coq/Rocq proof engineer with deep knowledge of the \
fiat-crypto library (elliptic curve cryptography). Your task is to fill \
in missing proof tactics in Coq source files.

Rules:
- Return the COMPLETE modified .v file — nothing else, no markdown fences, \
  no explanation before or after.
- Replace every occurrence of `Admitted.` that belongs to the target \
  declaration with real tactics followed by `Qed.` or `Defined.` \
  (use `Qed.` unless the original used `Defined.`).
- Do NOT change anything outside the target proof block.
- Do NOT add new imports or axioms.
- If you genuinely cannot determine the proof, write `Admitted.` and leave \
  it — do not hallucinate incorrect tactics.
"""

def make_prompt_full(decl: str, file_content: str) -> str:
    return (
        f"Fill in the complete proof for the declaration `{decl}` in the "
        f"Coq file below. The proof currently ends with `Admitted.` — replace "
        f"it with a complete tactic proof.\n\n"
        f"FILE CONTENT:\n{file_content}"
    )

def make_prompt_partial(decl: str, file_content: str) -> str:
    return (
        f"The proof for declaration `{decl}` in the Coq file below is "
        f"partially written. The last 3 tactic sentences have been removed "
        f"and replaced with `Admitted.`. Fill in those missing tactics to "
        f"complete the proof.\n\n"
        f"FILE CONTENT:\n{file_content}"
    )

# ── Helpers ──────────────────────────────────────────────────────────────────

def call_claude(prompt: str, label: str, log: list[str]) -> str:
    log.append(f"\n  [API call: {MODEL}]")
    t0 = time.time()
    for attempt in range(6):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                temperature=0,
                system=SYS_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except anthropic.RateLimitError as e:
            wait = 60 * (attempt + 1)
            log.append(f"  rate-limited (attempt {attempt+1}), waiting {wait}s…")
            print(f"  rate-limited (attempt {attempt+1}), waiting {wait}s…", flush=True)
            time.sleep(wait)
    else:
        raise RuntimeError("Exceeded retry limit for rate limiting")
    elapsed = time.time() - t0
    response = msg.content[0].text
    log.append(f"  elapsed: {elapsed:.1f}s  |  output tokens: {msg.usage.output_tokens}")
    return response


def strip_fences(text: str) -> str:
    """Remove markdown code fences and leading prose if the model added them."""
    text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()

    # Strip leading prose: find the first line that looks like Coq source.
    # Valid Coq file starts with: Require, From, Set, Unset, a comment (*,
    # or a declaration keyword (Lemma, Theorem, Definition, etc.)
    coq_start = re.compile(
        r"^(Require|From|Set|Unset|Local|Global|Import|Export|\(\*"
        r"|Lemma|Theorem|Definition|Fixpoint|CoFixpoint|Inductive"
        r"|CoInductive|Record|Class|Instance|Section|Module|End"
        r"|Corollary|Proposition|Remark|Fact|Notation|Ltac"
        r"|Program|Obligation|Next\s+Obligation)",
        re.MULTILINE,
    )
    m = coq_start.search(text)
    if m and m.start() > 0:
        text = text[m.start():]

    return text.strip()


def run_coqc(attempt_path: Path, log: list[str]) -> str:
    """Run coqc and return 'PASS', 'FAIL', or 'ERROR'."""
    try:
        result = subprocess.run(
            [COQC, str(attempt_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            log.append("  coqc: PASS ✓")
            return "PASS"
        else:
            short = (result.stderr or result.stdout)[:400].strip()
            log.append(f"  coqc: FAIL\n    {short}")
            return "FAIL"
    except subprocess.TimeoutExpired:
        log.append("  coqc: TIMEOUT (>120s)")
        return "TIMEOUT"
    except FileNotFoundError:
        log.append("  coqc: not found on PATH")
        return "ERROR"


def run_condition(slots: list[Path], condition: str,
                  prompt_fn, challenge_file: str, log_lines: list[str]) -> dict:
    results = {}
    count = 0
    for slot in sorted(slots):
        if not slot.is_dir():
            continue
        if MAX_CHALLENGES is not None and count >= MAX_CHALLENGES:
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
        log_lines.append(f"Instructions: {meta.get('instructions','')}")

        # Show the Admitted context (±10 lines around it)
        lines = content.splitlines()
        adm_idx = next((i for i, l in enumerate(lines) if "Admitted" in l), None)
        if adm_idx is not None:
            ctx_start = max(0, adm_idx - 8)
            ctx_end   = min(len(lines), adm_idx + 4)
            ctx = "\n".join(f"  {i+1:>4} | {lines[i]}" for i in range(ctx_start, ctx_end))
            log_lines.append(f"\nProof context around Admitted (lines {ctx_start+1}–{ctx_end}):")
            log_lines.append(ctx)

        # Call the API
        prompt   = prompt_fn(decl, content)
        response = call_claude(prompt, slot.name, log_lines)
        completed = strip_fences(response)

        # Write attempt.v
        attempt_path = slot / "attempt.v"
        attempt_path.write_text(completed)
        log_lines.append(f"\nModel response written to: attempt.v")

        # Show what the model filled in — diff the Admitted line(s)
        orig_lines = content.splitlines()
        new_lines  = completed.splitlines()
        adm_new = next((i for i, l in enumerate(new_lines) if "Admitted" in l), None)
        if adm_new is None:
            # Find where proof diverges
            for i, (ol, nl) in enumerate(zip(orig_lines, new_lines)):
                if ol != nl:
                    snippet = "\n".join(
                        f"  {j+1:>4} | {new_lines[j]}" for j in range(max(0,i-2), min(len(new_lines),i+8))
                    )
                    log_lines.append(f"\nModel filled proof (diverges at line {i+1}):")
                    log_lines.append(snippet)
                    break
        else:
            log_lines.append(f"\n  Model kept Admitted at line {adm_new+1} (could not complete)")

        # coqc check
        verdict = run_coqc(attempt_path, log_lines)
        results[slot.name] = verdict

    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    log_lines: list[str] = [
        "=" * 64,
        "PROOF COMPLETION EXPERIMENT — Claude API",
        f"Model: {MODEL}",
        f"Date : {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 64,
    ]

    # Condition B — last 3 tactics removed (easier)
    log_lines.append("\n\n" + "━"*64)
    log_lines.append("CONDITION B: Last 3 tactics removed (experiments3)")
    log_lines.append("━"*64)
    slots_b = sorted(EXP_B.iterdir())
    results_b = run_condition(
        slots_b, "B", make_prompt_partial, "challenge3.v", log_lines
    )

    # Condition A — full Admitted (harder)
    log_lines.append("\n\n" + "━"*64)
    log_lines.append("CONDITION A: Full Admitted (admitted-proofs)")
    log_lines.append("━"*64)
    slots_a = sorted(EXP_A.iterdir())
    results_a = run_condition(
        slots_a, "A", make_prompt_full, "challenge.v", log_lines
    )

    # Summary
    log_lines.append("\n\n" + "="*64)
    log_lines.append("SUMMARY")
    log_lines.append("="*64)

    for label, results in [("B (last 3 tactics)", results_b), ("A (full Admitted)", results_a)]:
        total = len(results)
        passes = sum(1 for v in results.values() if v == "PASS")
        fails  = sum(1 for v in results.values() if v == "FAIL")
        errors = sum(1 for v in results.values() if v in ("TIMEOUT","ERROR"))
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
    main()