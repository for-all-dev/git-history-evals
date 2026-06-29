"""Logfire observability wiring (opt-in, graceful).

`init_logfire()` configures Logfire once and instruments pydantic-ai + the Anthropic
client, so every agent run shows up as a trace (the ReAct loop, each tool call, model
reasoning, token usage, latency). It is safe to call when Logfire is unauthenticated:
`send_to_logfire="if-token-present"` means no credentials → no-op (spans still work
locally), so the baseline runs unchanged whether or not you've run `logfire auth`.
"""

from __future__ import annotations

_INITIALISED = False


def init_logfire(service: str = "ablate-baseline") -> None:
    """Configure + instrument Logfire once; degrade silently if unavailable."""
    global _INITIALISED
    if _INITIALISED:
        return
    _INITIALISED = True
    try:
        import logfire
    except ImportError:  # pragma: no cover - logfire is a declared dep
        return
    # Only ships to the cloud when a token/auth is present; otherwise a local no-op.
    logfire.configure(
        service_name=service,
        send_to_logfire="if-token-present",
        console=False,
    )
    logfire.instrument_pydantic_ai()
    try:
        logfire.instrument_anthropic()
    except Exception:  # noqa: BLE001 - instrumentation is best-effort
        pass


def span(name: str, **attrs: object):
    """A Logfire span context manager (a harmless no-op if Logfire is absent)."""
    try:
        import logfire
    except ImportError:  # pragma: no cover
        from contextlib import nullcontext

        return nullcontext()
    return logfire.span(name, **attrs)  # ty: ignore[invalid-argument-type]


def log(message: str, **attrs: object) -> None:
    """Emit a Logfire info event (a no-op if Logfire is absent/unconfigured)."""
    try:
        import logfire
    except ImportError:  # pragma: no cover
        return
    logfire.info(message, **attrs)  # ty: ignore[invalid-argument-type]


def set_attrs(span_obj: object, **attrs: object) -> None:
    """Best-effort `set_attribute` on a Logfire span (ignores no-op/None spans)."""
    setter = getattr(span_obj, "set_attribute", None)
    if setter is None:
        return
    for k, v in attrs.items():
        setter(k, v)
