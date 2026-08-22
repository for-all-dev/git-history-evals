"""Shared visual style: palette, rcParams, and deterministic PDF/PGF saving.

Design targets (see comms/vericode-workshop/figures/README.md):
- single-column width (~3.4in) at a NeurIPS-column font size (8-9pt)
- Okabe-Ito colorblind-safe palette
- model identity gets the SAME color in every figure that encodes it
- no timestamps embedded in output, so two runs are byte-identical
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("pdf")  # headless; also the backend savefig(...pdf) below uses
import matplotlib.pyplot as plt

# --- Okabe-Ito colorblind-safe palette -------------------------------------------------
OKABE_ITO = {
    "black": "#000000",
    "orange": "#E69F00",
    "sky_blue": "#56B4E9",
    "bluish_green": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "vermillion": "#D55E00",
    "reddish_purple": "#CC79A7",
    "gray": "#999999",
}

# --- Canonical model identity: order, display label, and color -------------------------
# This order and color assignment is used by EVERY figure that plots per-model series or
# bars, so a reader can track a model by color across the whole figure set.
MODEL_ORDER = [
    "claude-sonnet-5",
    "openai:gpt-5.6-sol",
    "mistral:labs-leanstral-1-5",
]

MODEL_LABELS = {
    "claude-sonnet-5": "Claude Sonnet 5",
    "openai:gpt-5.6-sol": "GPT-5.6-sol",
    "mistral:labs-leanstral-1-5": "Leanstral 1.5",
}

MODEL_COLORS = {
    "claude-sonnet-5": OKABE_ITO["blue"],
    "openai:gpt-5.6-sol": OKABE_ITO["vermillion"],
    "mistral:labs-leanstral-1-5": OKABE_ITO["bluish_green"],
}

# --- Mode identity: the aggregate JSON's "leaves"/"whole" are the paper's easy/hard ----
MODE_ORDER = ["leaves", "whole"]
MODE_LABELS = {"leaves": "easy", "whole": "hard"}

# pipeline/temporal_holdout.tsv spells model names without the pydantic-ai provider
# prefix (e.g. "gpt-5.6-sol", not "openai:gpt-5.6-sol"). Map those onto the canonical
# MODEL_ORDER keys so every figure shares one color/label per model.
TSV_MODEL_ALIASES = {
    "claude-sonnet-5": "claude-sonnet-5",
    "gpt-5.6-sol": "openai:gpt-5.6-sol",
    "leanstral-1-5": "mistral:labs-leanstral-1-5",
}

# --- Outcome taxonomy colors (semantic, independent of model color assignment) ---------
# pass/tampered/turn_limit/gave_up/error/fail cover every non-excluded outcome;
# "excluded" (malformed + any dry_run/trivial/context_exceeded) is hatched, not colored
# in the model palette, to keep it visually distinct from a real outcome.
OUTCOME_ORDER = ["pass", "tampered", "turn_limit", "gave_up", "error", "fail"]
OUTCOME_LABELS = {
    "pass": "pass",
    "tampered": "tampered",
    "turn_limit": "turn limit",
    "gave_up": "gave up",
    "error": "error",
    "fail": "fail",
}
OUTCOME_COLORS = {
    "pass": OKABE_ITO["bluish_green"],
    "tampered": OKABE_ITO["vermillion"],
    "turn_limit": OKABE_ITO["sky_blue"],
    "gave_up": OKABE_ITO["yellow"],
    "error": OKABE_ITO["reddish_purple"],
    "fail": OKABE_ITO["orange"],
}
EXCLUDED_COLOR = OKABE_ITO["gray"]
EXCLUDED_HATCH = "//"

# --- Sizing: NeurIPS single column is ~3.4in wide -------------------------------------
COLUMN_WIDTH_IN = 3.4
COLUMN_HEIGHT_IN = 2.4  # a reasonable default aspect for a bar chart

# A fixed epoch (1970-01-01T00:00:00Z, i.e. SOURCE_DATE_EPOCH=0) so PDF CreationDate/
# ModDate never vary run to run. matplotlib's pdf backend metadata dict wants an actual
# datetime.datetime for these two keys (not a raw PDF date string).
_FIXED_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def set_rcparams() -> None:
    """Fixed, deterministic rcParams — call once before building any figure."""
    plt.rcParams.update(
        {
            "font.size": 8,
            "axes.titlesize": 9,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
            "axes.linewidth": 0.6,
            "axes.edgecolor": "#333333",
            "axes.grid": True,
            "grid.linewidth": 0.4,
            "grid.color": "#dddddd",
            "grid.alpha": 1.0,
            "axes.axisbelow": True,
            "legend.frameon": False,
            "legend.borderaxespad": 0.2,
            "savefig.transparent": False,
            "path.simplify": False,  # deterministic vector output, no simplification jitter
            "svg.hashsalt": "vericode-figures",  # no-op for pdf backend, harmless elsewhere
            # .pgf output: use pdflatex (near-universally installed) rather than the
            # default xelatex, and skip fontspec/system-font lookup (pgf.rcfonts), which
            # requires xelatex/lualatex and isn't needed for these plots' plain labels.
            "pgf.texsystem": "pdflatex",
            "pgf.rcfonts": False,
        }
    )


def new_figure(width_in: float = COLUMN_WIDTH_IN, height_in: float = COLUMN_HEIGHT_IN):
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    return fig, ax


def save_figure(fig: Any, out_path: Path, *, also_pgf: bool = True) -> list[Path]:
    """Save fig as PDF (and, if trivial, PGF) with no embedded timestamp.

    Two runs over the same COMMITTED inputs must produce byte-identical PDFs — the
    matplotlib pdf backend embeds a CreationDate/ModDate by default (today's date), which
    we override with a fixed epoch via the `metadata` kwarg (the SOURCE_DATE_EPOCH=0
    convention, applied through matplotlib's own hook rather than the env var, which the
    pdf backend does not read).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = []

    pdf_path = out_path.with_suffix(".pdf")
    fig.savefig(
        pdf_path,
        format="pdf",
        bbox_inches="tight",
        pad_inches=0.02,
        metadata={"CreationDate": _FIXED_EPOCH, "ModDate": _FIXED_EPOCH},
    )
    written.append(pdf_path)

    if also_pgf:
        pgf_path = out_path.with_suffix(".pgf")
        try:
            fig.savefig(pgf_path, format="pgf", bbox_inches="tight", pad_inches=0.02)
            written.append(pgf_path)
        except Exception as exc:  # noqa: BLE001 - .pgf is best-effort, LaTeX setup varies
            print(f"  (skipped .pgf for {out_path.name}: {exc})")

    plt.close(fig)
    return written
