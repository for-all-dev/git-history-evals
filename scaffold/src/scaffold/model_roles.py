"""Model-role configuration shared by the curator and the prompt calibrator.

The pipeline uses three model *roles* rather than hardcoded model IDs, so it
stays runnable when today's models are retired:

- ``cheap``    — high-volume scoring (curation tier 1); currently Haiku.
- ``mid``      — escalation verdicts on ambiguous cases (tier 2); currently Sonnet.
- ``decision`` — ground-truth labeling and prompt writing during calibration;
                 currently Opus.

Roles resolve, in order, from: an explicit path, the ``SCAFFOLD_MODEL_ROLES``
environment variable, a ``model-roles.json`` found by walking upward from the
working directory (the monorepo root ships one), and finally the built-in
defaults below.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_CHEAP_MODEL = "claude-haiku-4-5"
DEFAULT_MID_MODEL = "claude-sonnet-4-6"
DEFAULT_DECISION_MODEL = "claude-opus-4-8"

CONFIG_FILENAME = "model-roles.json"


class ModelRoles(BaseModel):
    """The three model roles used across curation and calibration."""

    cheap: str = DEFAULT_CHEAP_MODEL
    mid: str = DEFAULT_MID_MODEL
    decision: str = DEFAULT_DECISION_MODEL


def _find_upwards(filename: str, start: Path) -> Path | None:
    """Return the first `filename` found in `start` or any of its ancestors."""
    start = start.resolve()
    for directory in [start, *start.parents]:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def load_model_roles(path: Path | None = None) -> ModelRoles:
    """Resolve the model roles from config, falling back to built-in defaults.

    An explicitly passed *path* must exist and parse; the implicit sources
    (env var, found config file) fall back to defaults with a warning if
    unreadable, so a malformed config never breaks a mining run.
    """
    if path is not None:
        return ModelRoles.model_validate(json.loads(Path(path).read_text()))

    env_path = os.environ.get("SCAFFOLD_MODEL_ROLES")
    candidate = (
        Path(env_path) if env_path else _find_upwards(CONFIG_FILENAME, Path.cwd())
    )
    if candidate is None:
        return ModelRoles()
    try:
        return ModelRoles.model_validate(json.loads(candidate.read_text()))
    except (OSError, ValueError) as exc:
        logger.warning("Could not load model roles from %s: %s", candidate, exc)
        return ModelRoles()
