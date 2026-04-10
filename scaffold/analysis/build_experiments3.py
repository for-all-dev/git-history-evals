"""Build experiments3: same 20 challenges but with only the last 3 tactic
sentences removed and replaced by Admitted.

A Coq tactic sentence ends at a '.' that is:
  - NOT inside a comment  (* ... *)
  - NOT inside a string   " ... "
  - followed by whitespace or end-of-string
  - NOT part of a qualified name (letter/digit/_ immediately after)

This gives us complete, syntactically safe sentence boundaries.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

SRC  = Path('/Users/taibaabid/Desktop/spar/git-history-evals/experiments/admitted-proofs')
OUT  = Path('/Users/taibaabid/Desktop/spar/git-history-evals/experiments/experiments3')
LOG  = Path('/Users/taibaabid/Desktop/spar/git-history-evals/experiments/experiments3-build.txt')

# ---------------------------------------------------------------------------
# Coq sentence splitter
# ---------------------------------------------------------------------------

def split_coq_sentences(text: str) -> list[str]:
    """Split Coq source into a list of top-level sentences (tactics, etc.)
    Each returned string includes the terminating '.'.
    Whitespace between sentences is NOT included.
    """
    sentences: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        # ── comment  (* ... *)  (can nest) ──────────────────────────────────
        if text[i:i+2] == '(*':
            depth = 1
            buf.append('(*')
            i += 2
            while i < n and depth > 0:
                if text[i:i+2] == '(*':
                    depth += 1; buf.append('(*'); i += 2
                elif text[i:i+2] == '*)':
                    depth -= 1; buf.append('*)'); i += 2
                else:
                    buf.append(text[i]); i += 1
            continue

        # ── string literal  " ... " ──────────────────────────────────────────
        if ch == '"':
            buf.append(ch); i += 1
            while i < n and text[i] != '"':
                if text[i] == '\\':
                    buf.append(text[i]); i += 1
                buf.append(text[i]); i += 1
            if i < n:
                buf.append(text[i]); i += 1   # closing "
            continue

        # ── period ──────────────────────────────────────────────────────────
        if ch == '.':
            next_ch = text[i+1] if i+1 < n else ' '
            # Sentence terminator: '.' followed by whitespace or EOF
            # NOT a qualified name separator (next char is letter/digit/_)
            if next_ch in ' \t\n\r' or i+1 >= n:
                buf.append('.')
                sentence = ''.join(buf).strip()
                if sentence:
                    sentences.append(sentence)
                buf = []
                i += 1
                continue

        buf.append(ch)
        i += 1

    # anything left over
    tail = ''.join(buf).strip()
    if tail:
        sentences.append(tail)

    return sentences


def is_structural(s: str) -> bool:
    """True if the sentence is structural bookkeeping, not a tactic."""
    stripped = s.strip()
    return stripped in ('{', '}', '-', '+', '*', '--', '++', '**',
                        'Proof.', 'Qed.', 'Defined.', 'Admitted.') \
        or stripped.startswith('(*')


def remove_last_n_tactics(proof_body: str, n: int = 3) -> tuple[str, list[str]]:
    """Remove the last n tactic sentences from proof_body.

    Returns (new_body, removed_sentences).
    new_body ends with 'Admitted.' replacing the removed sentences + Qed/Defined.
    """
    sentences = split_coq_sentences(proof_body)

    # Split off trailing Qed/Defined/Admitted
    closer = None
    while sentences and is_structural(sentences[-1]) and \
          sentences[-1].strip() in ('Qed.', 'Defined.', 'Admitted.'):
        closer = sentences.pop()

    # Split off trailing structural noise  (closing braces)
    suffix_structural: list[str] = []
    while sentences and is_structural(sentences[-1]):
        suffix_structural.insert(0, sentences.pop())

    # Now remove the last n non-structural sentences
    removed: list[str] = []
    while len(removed) < n and sentences:
        last = sentences.pop()
        if not is_structural(last):
            removed.insert(0, last)
        else:
            suffix_structural.insert(0, last)

    # Rebuild: keep remaining sentences + structural suffix + Admitted.
    kept = sentences + suffix_structural
    # Reconstruct with same indentation style: join with newline, add Admitted
    # Find indentation of the first kept tactic for alignment
    indent = '  '
    for s in kept:
        m = re.match(r'^(\s+)', s)
        if m:
            indent = m.group(1)
            break

    new_body = '\n'.join(kept)
    if new_body and not new_body.endswith('\n'):
        new_body += '\n'
    new_body += f'{indent}Admitted.'

    return new_body, removed


# ---------------------------------------------------------------------------
# Apply to each challenge
# ---------------------------------------------------------------------------

OUT.mkdir(parents=True, exist_ok=True)

log_lines: list[str] = [
    "=" * 70,
    "EXPERIMENTS3 BUILD LOG",
    "Removed last 3 tactic sentences from each solution proof.",
    "=" * 70,
]

index_rows: list[tuple] = []
ok_count = 0

for slot in sorted(SRC.iterdir()):
    if not slot.is_dir():
        continue

    meta     = json.loads((slot / 'meta.json').read_text())
    decl     = meta['declaration']
    sol_text = (slot / 'solution.v').read_text()
    chal_text = (slot / 'challenge.v').read_text()

    log_lines.append(f"\n{'─'*60}")
    log_lines.append(f"Challenge: {slot.name}")
    log_lines.append(f"Declaration: {decl}")

    # ── Locate proof block in solution ──────────────────────────────────────
    decl_m = re.search(
        rf'\b(?:Theorem|Lemma|Definition|Fixpoint|Corollary|Proposition'
        rf'|Remark|Fact|Program\s+Definition|Program\s+Lemma)\s+{re.escape(decl)}\b',
        sol_text,
    )
    if not decl_m:
        log_lines.append("  STATUS: SKIP — declaration not found in solution.v")
        continue

    after_decl = sol_text[decl_m.start():]
    proof_m = re.search(r'\bProof\.\s*\n', after_decl)
    if not proof_m:
        log_lines.append("  STATUS: SKIP — Proof. not found after declaration")
        continue

    after_proof = after_decl[proof_m.end():]
    qed_m = re.search(r'^[ \t]*(Qed|Defined|Admitted)\.[ \t]*$', after_proof, re.MULTILINE)
    if not qed_m:
        log_lines.append("  STATUS: SKIP — Qed/Defined/Admitted not found")
        continue

    proof_body = after_proof[:qed_m.end()]  # includes the Qed.

    # ── Remove last 3 tactics ───────────────────────────────────────────────
    new_body, removed = remove_last_n_tactics(proof_body, n=3)

    if not removed:
        log_lines.append("  STATUS: SKIP — could not identify 3 removable tactics")
        continue

    log_lines.append(f"  Removed sentences ({len(removed)}):")
    for r in removed:
        log_lines.append(f"    >> {r[:120].strip()}")

    # ── Reconstruct solution.v with new proof ────────────────────────────────
    proof_start_in_sol = decl_m.start() + proof_m.start() + proof_m.end()
    proof_end_in_sol   = decl_m.start() + proof_m.start() + proof_m.end() + qed_m.end()

    challenge3_text = (
        sol_text[:proof_start_in_sol]
        + new_body
        + sol_text[proof_end_in_sol:]
    )

    # ── Write output folder ──────────────────────────────────────────────────
    out_slot = OUT / slot.name
    out_slot.mkdir(exist_ok=True)

    (out_slot / 'challenge3.v').write_text(challenge3_text)
    (out_slot / 'solution.v').write_text(sol_text)
    (out_slot / 'challenge.v').write_text(chal_text)   # full Admitted version
    (out_slot / 'meta.json').write_text((slot / 'meta.json').read_text())
    (out_slot / 'diff.txt').write_text((slot / 'diff.txt').read_text())

    log_lines.append(f"  STATUS: OK — wrote challenge3.v")
    index_rows.append((slot.name, decl, len(removed)))
    ok_count += 1

# ── index.txt ────────────────────────────────────────────────────────────────
index_lines = [
    "EXPERIMENTS3 — last 3 tactics removed, replaced with Admitted",
    "=" * 60,
    "Each folder contains:",
    "  challenge3.v  — solution with last 3 tactics → Admitted",
    "  solution.v    — full ground-truth proof",
    "  challenge.v   — original full-Admitted version",
    "  meta.json     — declaration name, task_id, instructions",
    "  diff.txt      — original git diff",
    "",
    f"  {'Folder':<45} {'Declaration':<40} Tactics removed",
    "-" * 100,
]
for folder, decl, n in index_rows:
    index_lines.append(f"  {folder:<45} {decl:<40} {n}")

(OUT / 'index.txt').write_text('\n'.join(index_lines) + '\n')

# ── summary in log ────────────────────────────────────────────────────────────
log_lines += [
    "",
    "=" * 70,
    f"SUMMARY: {ok_count}/20 challenges successfully built into experiments3.",
    f"Output: {OUT}",
]

log_text = '\n'.join(log_lines)
LOG.write_text(log_text)
print(log_text)