"""Apply a `solution_diff` to a challenge to recover the original solution.

The four ablators all emit `solution_diff` in one shared unified-diff dialect
(`rocq-ablator/lib/diff.ml`, `isabelle-ablator/rust/src/diff.rs`,
`lean-ablator/Ablator/Diff.lean`, `isabelle-ablator/scala/src/Diff.scala`). The
convention, by construction, is byte-exact and consumer-friendly:

* lines are `split("\n")` (a trailing newline yields a final "" element);
* `apply` re-joins with "\n", so the round-trip is exact;
* hunks are `@@ -oldStart,oldLen +newStart,newLen @@` with ` `/`-`/`+` prefixes;
* an empty diff ("") means challenge == solution.

Because we control producer and consumer, this avoids the unified-diff
"\\ No newline at end of file" edge case entirely. This is the canonical Python
`apply` reproduced in every ablator README.
"""

from __future__ import annotations

import difflib


def unified_or_empty(challenge: str, solution: str) -> str:
    """A human-readable unified diff challenge→solution ("" if identical).

    Used to *record* an agent's solution; for the byte-exact round-trip format the
    ablators emit, see `apply` below.
    """
    if challenge == solution:
        return ""
    return "".join(
        difflib.unified_diff(
            challenge.splitlines(keepends=True),
            solution.splitlines(keepends=True),
            "challenge",
            "solution",
        )
    )


def apply(challenge: str, diff: str) -> str:
    """Recover the solution by applying `diff` (a `solution_diff`) to `challenge`."""
    if not diff:
        return challenge
    a = challenge.split("\n")
    out: list[str] = []
    oi = 0
    for line in diff.split("\n"):
        if line[:2] == "@@":
            # "@@ -oldStart,oldLen +newStart,newLen @@" -> oldStart (1-based)
            start = int(line.split("-", 1)[1].split(",", 1)[0])
            while oi < start - 1:
                out.append(a[oi])
                oi += 1
        elif line == "":
            pass
        elif line[0] == " ":
            out.append(line[1:])
            oi += 1
        elif line[0] == "-":
            oi += 1
        elif line[0] == "+":
            out.append(line[1:])
    out += a[oi:]
    return "\n".join(out)
