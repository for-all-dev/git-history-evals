"""Outcome/label reconstruction from `ablate-baseline` harness results.

The harness writes one `SolveResult` JSON row per challenge (see
`apply_ablate.solve.SolveResult`) but does NOT persist the human-readable outcome
class — only the boolean flags. We reconstruct the class with the exact precedence the
harness uses for its Logfire span (`apply_ablate.baseline`), so `succeeded` is the
binary PASS label and non-scorable rows can be dropped before training.
"""

from __future__ import annotations

from typing import Any

# precedence order used by the harness; first matching flag wins
_OUTCOME_ORDER: tuple[tuple[str, str], ...] = (
    ("succeeded", "pass"),
    ("dry_run", "dry_run"),
    ("trivial", "trivial"),
    ("malformed_challenge", "malformed"),
    ("tampered", "tampered"),
    ("gave_up", "gave_up"),
    ("turn_limit", "turn_limit"),
)

# outcome classes excluded from the trainable set (not scorable / no model was called)
NON_TRAINABLE: frozenset[str] = frozenset({"trivial", "malformed", "dry_run"})


def outcome_of(result: dict[str, Any]) -> str:
    """Human-readable outcome class for a SolveResult row."""
    for flag, name in _OUTCOME_ORDER:
        if result.get(flag):
            return name
    if result.get("error"):
        return "error"
    return "fail"


def label_of(result: dict[str, Any]) -> int:
    """Binary PASS(1)/FAIL(0) label."""
    return 1 if result.get("succeeded") else 0


def is_trainable(result: dict[str, Any]) -> bool:
    """Whether this row should enter the classifier's training set."""
    return outcome_of(result) not in NON_TRAINABLE
