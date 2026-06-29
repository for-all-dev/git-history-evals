"""Prover backend registry, keyed by a record's `proof_assistant`."""

from __future__ import annotations

from apply_ablate.provers.base import CheckResult, Prover, run
from apply_ablate.provers.coq import CoqProver
from apply_ablate.provers.isabelle import IsabelleProver
from apply_ablate.provers.lean import LeanProver

_REGISTRY: dict[str, Prover] = {
    p.key: p for p in (CoqProver(), IsabelleProver(), LeanProver())
}


def get_prover(assistant: str) -> Prover:
    """Return the backend for a (normalised) `proof_assistant` key."""
    key = assistant.strip().lower()
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"no prover backend for {assistant!r}; have {sorted(_REGISTRY)}"
        ) from None


__all__ = ["CheckResult", "Prover", "get_prover", "run"]
