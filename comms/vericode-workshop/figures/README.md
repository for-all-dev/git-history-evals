# vericode-figures

Deterministic figure regeneration for the VeriCodeGen workshop paper
(`comms/vericode-workshop/`).

```bash
cd comms/vericode-workshop/figures
uv sync
uv run figures
```

That's the whole workflow. `uv run figures` reads only committed (or
content-addressed-symlinked) inputs, iterates models/modes in a fixed order,
and never draws a random sample of its own — the bootstrap CIs it plots are
pre-computed upstream by `ablate-aggregate`. Two runs over the same inputs
produce **byte-identical** PDFs (see "Verifying determinism" below).

## What gets produced

Vector PDFs (plus a `.pgf` twin where matplotlib's pgf backend cooperates)
land in `out/`, which is gitignored — figures regenerate from inputs, they
don't get committed:

| file | figure | reads |
|---|---|---|
| `out/grid-pass.pdf` | (a) grouped bars, macro PASS per model x mode, bootstrap CI error bars | `data/aggregate-*.json` |
| `out/outcome-composition.pdf` | (b) stacked bars over the outcome taxonomy per model x mode, `malformed` hatched as excluded-from-denominator | `data/aggregate-*.json` |
| `out/tamper-by-model.pdf` | (c) tampered share of scorable rows per model x mode — the reward-hacking headline | `data/aggregate-*.json` |
| `out/temporal-holdout.pdf` | (d) pre vs post macro PASS per model per cutoff, pre-side n annotated | `pipeline/temporal_holdout.tsv` |
| `out/budget-curve.pdf` | (e) PASS vs turn budget | `pipeline/budget_curve.tsv` — **doesn't exist yet** (issue #131, in flight); the script prints a skip message and moves on rather than failing, so the same command starts producing this figure the day the TSV lands |

## Inputs and provenance

- `../data/aggregate-{claude-sonnet-5,gpt-5.6-sol,leanstral-1-5}.json` are
  **relative symlinks** into `scratch-wave3/` (gitignored, untracked) — not
  copies. See `../data/README.md` for the sha256 + row/field summary
  recorded at link time, the exact eval-run command chain, and why the
  symlinks are expected to dangle on a fresh clone. If a symlink dangles,
  `uv run figures` fails fast with `aggregate missing — run the evals or
  fetch results` and the resolved target path, rather than silently
  skipping a figure or crashing on a confusing JSON-parse error.
- `../../../pipeline/temporal_holdout.tsv` and `../../../pipeline/membership.tsv`
  are already committed on `master` and are read in place — `membership.tsv`
  isn't consumed by any figure yet, kept available for a future
  contamination-conditioned cut.
- `../../../pipeline/budget_curve.tsv` — see the table above.

## Design

- Publication-quality, single-column width (~3.4in), NeurIPS-column font
  sizing (8-9pt body, smaller ticks/legend).
- Okabe-Ito colorblind-safe palette. Model identity (Claude Sonnet 5 / GPT-5.6-sol /
  Leanstral 1.5) gets the **same color in every figure** that encodes model
  by color — see `src/vericode_figures/style.py` for the single source of
  truth (`MODEL_COLORS`, `MODEL_LABELS`, `MODEL_ORDER`).
- CIs are shown wherever the upstream aggregate actually provides one
  (macro PASS); `tamper-by-model` has none because `ablate-aggregate`
  doesn't currently emit a bootstrap CI for the tampered fraction.
- No gridlines heavier than the data, no redundant legends, spines trimmed.

## Verifying determinism

```bash
uv run figures && sha256sum out/*.pdf > /tmp/run1.sha256
rm -rf out
uv run figures && sha256sum out/*.pdf > /tmp/run2.sha256
diff /tmp/run1.sha256 /tmp/run2.sha256   # must be empty
```

This works because the pdf backend's `CreationDate`/`ModDate` metadata is
pinned to a fixed epoch (`D:19700101000000Z`) in `style.save_figure`
instead of the current wall-clock date matplotlib embeds by default, and
because nothing in this project reads the wall clock, a random seed, or
filesystem iteration order for anything that ends up in the image.

## Overleaf

This directory (and `../data/`) must **never** be pushed to the paper's
Overleaf project — see `../.olignore` and `../../README.md` for why and how
that's enforced. Only the `.tex`/`.sty` sources sync with Overleaf.

## Adding a figure

Drop a new module under `src/vericode_figures/plots/`, exporting a
`render(...) -> list[Path]` that calls `style.save_figure`, and wire it into
the `steps` list in `src/vericode_figures/cli.py`. Keep it deterministic:
constant iteration order, no wall-clock reads, no unseeded randomness — if a
figure needs a bootstrap CI, compute it upstream (in `ablate-aggregate` or
a TSV) rather than resampling inside this project.
