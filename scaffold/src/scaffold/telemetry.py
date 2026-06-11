"""Logfire telemetry wiring shared by every LLM-calling entry point.

One helper, three guarantees (issue #89):

- **No-op without a token.** ``send_to_logfire="if-token-present"`` means a
  machine without ``LOGFIRE_TOKEN`` (env or ``.env``) runs exactly as before —
  telemetry never blocks or alters a mining/curation run.
- **Every LLM call traced when a token is present.** ``instrument_anthropic()``
  covers the raw Anthropic SDK clients used by the curator and the calibration
  loop (labeler, tier-1/tier-2 scoring, the writer conversation);
  ``instrument_pydantic_ai()`` covers the profiler agent.
- **Idempotent.** Safe to call from multiple entry points; the first call wins
  (one process is one CLI command, so its service name is the right label).

Call this *after* dotenv loading so a token in ``.env`` is honoured.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_configured = False


def configure_telemetry(service_name: str) -> None:
    """Configure Logfire tracing for this process; safe no-op without a token.

    *service_name* labels the traces (e.g. ``scaffold-curator``,
    ``scaffold-calibrator``, ``scaffold-profiler``). Repeated calls are
    ignored — the first entry point to configure wins.
    """
    global _configured
    if _configured:
        return
    try:
        import logfire

        logfire.configure(send_to_logfire="if-token-present", service_name=service_name)
        logfire.instrument_anthropic()
        logfire.instrument_pydantic_ai()
        _configured = True
    except Exception as exc:  # telemetry must never break a run
        logger.warning("logfire not configured: %s", exc)
