"""
Coq proof utilities: tactic sentence parsing and proof block extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Tactics that constitute "automation" (machine-found vs human-guided)
AUTOMATION_TACTICS = frozenset([
    "auto", "eauto", "tauto", "intuition", "decide", "trivial",
    "omega", "lia", "linarith", "ring", "field", "norm_num",
    "firstorder", "solve_by_elim", "congruence",
    "reflexivity", "assumption", "contradiction",
    "exact", "discriminate",
])


@dataclass
class TacticSentence:
    text: str       # full text including leading whitespace
    start: int      # byte offset in the source string
    end: int        # byte offset past the terminating '.'


def split_coq_sentences(text: str) -> list[TacticSentence]:
    """
    Split Coq source text into sentences.

    A sentence ends with '.' followed by whitespace or EOF, where the '.' is
    not inside a (* comment *) or a "string" literal.
    Qualified names (Nat.add) are not sentence terminators because the '.'
    is followed by a non-whitespace character.
    """
    sentences: list[TacticSentence] = []
    n = len(text)
    i = 0
    comment_depth = 0
    in_string = False
    sentence_start = 0

    while i < n:
        c = text[i]

        if in_string:
            if c == '"':
                in_string = False
            i += 1
            continue

        if comment_depth > 0:
            if text[i:i+2] == "(*":
                comment_depth += 1
                i += 2
            elif text[i:i+2] == "*)":
                comment_depth -= 1
                i += 2
            else:
                i += 1
            continue

        if text[i:i+2] == "(*":
            comment_depth += 1
            i += 2
            continue

        if c == '"':
            in_string = True
            i += 1
            continue

        if c == ".":
            next_i = i + 1
            # Sentence terminator: '.' at EOF or followed by whitespace
            if next_i >= n or text[next_i] in " \t\n\r":
                sentences.append(TacticSentence(
                    text=text[sentence_start:next_i],
                    start=sentence_start,
                    end=next_i,
                ))
                # Skip the whitespace char that follows (don't double-count)
                sentence_start = next_i
                i = next_i
                continue

        i += 1

    # Trailing content without a terminating '.'
    tail = text[sentence_start:].strip()
    if tail:
        sentences.append(TacticSentence(
            text=text[sentence_start:],
            start=sentence_start,
            end=n,
        ))

    return sentences


def _find_proof_block(content: str, decl_name: str) -> tuple[int, int] | None:
    """
    Locate the proof block of `decl_name` in `content`.

    Returns (proof_kw_end, terminator_end) where:
      - proof_kw_end: position just after 'Proof.' (start of tactic body)
      - terminator_end: position just after Qed./Admitted./Defined.

    Returns None if the declaration or its proof block is not found.
    """
    # Find the declaration
    decl_pat = re.compile(
        r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Fact|Remark|Example"
        r"|Definition|Fixpoint|Program)\s+" + re.escape(decl_name) + r"\b",
        re.MULTILINE,
    )
    m_decl = decl_pat.search(content)
    if m_decl is None:
        return None

    search_from = m_decl.start()

    # Find 'Proof.' after the declaration
    m_proof = re.search(r"\bProof\s*\.", content[search_from:])
    if m_proof is None:
        return None

    proof_kw_end = search_from + m_proof.end()

    # Find the matching Qed./Admitted./Defined.
    term_pat = re.compile(r"\b(?:Qed|Admitted|Defined)\s*\.")
    m_term = term_pat.search(content[proof_kw_end:])
    if m_term is None:
        return None

    terminator_end = proof_kw_end + m_term.end()
    return proof_kw_end, terminator_end


def extract_proof_sentences(content: str, decl_name: str) -> list[TacticSentence] | None:
    """
    Return the tactic sentences for `decl_name`'s proof in `content`,
    including the final Qed./Admitted./Defined. sentence.

    Returns None if the proof block is not found.
    """
    span = _find_proof_block(content, decl_name)
    if span is None:
        return None

    proof_kw_end, terminator_end = span
    body = content[proof_kw_end:terminator_end]
    sentences = split_coq_sentences(body)

    # Re-anchor offsets to the full content
    result = []
    for s in sentences:
        if s.text.strip():
            result.append(TacticSentence(
                text=s.text,
                start=proof_kw_end + s.start,
                end=proof_kw_end + s.end,
            ))
    return result if result else None


def remove_last_n_tactics(content: str, decl_name: str, n: int) -> str | None:
    """
    Remove the last `n` tactic sentences (excluding the terminal Qed./Admitted.)
    from `decl_name`'s proof in `content`, replacing them with 'Admitted.'.

    Returns the modified content, or None if the proof could not be parsed.
    """
    span = _find_proof_block(content, decl_name)
    if span is None:
        return None

    proof_kw_end, terminator_end = span
    body = content[proof_kw_end:terminator_end]
    sentences = [s for s in split_coq_sentences(body) if s.text.strip()]

    if not sentences:
        return None

    # The last sentence is Qed./Admitted./Defined. — keep it separate
    terminal = sentences[-1]
    tactics = sentences[:-1]

    if n > len(tactics):
        n = len(tactics)

    if n == 0:
        return content  # nothing to remove

    # The cut point: remove the last `n` tactic sentences
    keep = tactics[:-n]

    if keep:
        cut_offset = keep[-1].end  # end of last kept tactic, relative to body
    else:
        cut_offset = 0  # remove all tactics

    # Determine indentation from the first removed tactic
    removed_first = tactics[-n]
    indent_match = re.match(r"(\s*)", removed_first.text)
    indent = indent_match.group(1) if indent_match else "  "
    indent = indent.lstrip("\n")  # keep only spaces/tabs, not newlines

    replacement = f"\n{indent}Admitted."

    new_body = body[:cut_offset] + replacement + "\n"
    return content[:proof_kw_end] + new_body + content[terminator_end:]


# ── Metrics helpers ───────────────────────────────────────────────────────────

_TACTIC_NAME_RE = re.compile(r"^\s*([a-zA-Z_]\w*)")


def count_tactics(sentences: list[TacticSentence]) -> int:
    """Number of tactic sentences, excluding the terminal keyword."""
    return sum(
        1 for s in sentences
        if not re.match(r"\s*(?:Qed|Admitted|Defined)\s*\.", s.text)
    )


def automation_ratio(sentences: list[TacticSentence]) -> float:
    """Fraction of tactic sentences whose leading keyword is an automation tactic."""
    non_terminal = [
        s for s in sentences
        if not re.match(r"\s*(?:Qed|Admitted|Defined)\s*\.", s.text)
    ]
    if not non_terminal:
        return 0.0
    auto_count = sum(
        1 for s in non_terminal
        if (m := _TACTIC_NAME_RE.match(s.text)) and m.group(1) in AUTOMATION_TACTICS
    )
    return auto_count / len(non_terminal)


def unique_tactic_types(sentences: list[TacticSentence]) -> int:
    """Number of distinct leading tactic keywords."""
    names = set()
    for s in sentences:
        if re.match(r"\s*(?:Qed|Admitted|Defined)\s*\.", s.text):
            continue
        m = _TACTIC_NAME_RE.match(s.text)
        if m:
            names.add(m.group(1))
    return len(names)


def max_bullet_depth(content: str) -> int:
    """Estimate maximum proof nesting depth from { } brackets and bullet counts."""
    depth = 0
    max_depth = 0
    for c in content:
        if c == "{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif c == "}":
            depth = max(0, depth - 1)
    return max_depth


def tactic_edit_distance(human_sentences: list[TacticSentence],
                          llm_sentences: list[TacticSentence]) -> int:
    """
    Levenshtein edit distance on tactic-name sequences (not full text),
    excluding terminal keywords.
    """
    def names(sents: list[TacticSentence]) -> list[str]:
        result = []
        for s in sents:
            if re.match(r"\s*(?:Qed|Admitted|Defined)\s*\.", s.text):
                continue
            m = _TACTIC_NAME_RE.match(s.text)
            result.append(m.group(1) if m else "?")
        return result

    a = names(human_sentences)
    b = names(llm_sentences)

    # Standard DP edit distance
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[j] = prev[j-1]
            else:
                dp[j] = 1 + min(prev[j], dp[j-1], prev[j-1])
    return dp[n]