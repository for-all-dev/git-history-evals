"""pydantic-ai Agent factory for the RepoProfile calibration agent.

Built on ``pydantic-ai-harness``'s ``CodeMode`` (the only shipped harness
capability): the exploration + self-validation tools are sandboxed behind a
single ``run_code`` tool, so the model writes Python that calls them as
functions and collapses what would be dozens of tool round-trips into one or
two. The final ``RepoProfile`` is produced as a *native* structured-output call
— it is intentionally excluded from ``CodeMode(tools=...)`` because the model
cannot construct a pydantic object inside the Monty sandbox.

Construction is side-effect-free (no network, no client) until a caller invokes
``agent.run_sync(...)``; tool registration happens separately in
``register_tools`` (see ``tools.py``) so the agent's wiring and its host
functions can be reasoned about independently.
"""

from __future__ import annotations

import os
from typing import Any, cast

from pydantic_ai import Agent
from pydantic_ai_harness import CodeMode

from scaffold.profile import RepoProfile

from .prompts import SYS_PROMPT_PROFILER
from .tools import TOOL_NAMES

# Anthropic prompt-caching for the calibration loop. The agent re-sends a large,
# static prefix on every step — the CodeMode ``run_code`` tool schema (rendered
# first) and the system prompt — plus a transcript that grows across the dozens
# of tool round-trips a calibration takes. We cache the tool definitions and
# instructions (fixed breakpoints) and pass the top-level ``anthropic_cache``
# flag, which moves a breakpoint forward over the growing message history. After
# the first step the prefix re-reads at ~0.1x instead of full price.
#
# These are ``AnthropicModelSettings`` keys (a plain dict to avoid importing the
# anthropic model module at construction time — see the lazy import below);
# non-anthropic models ignore the ``anthropic_``-prefixed keys. Typed ``Any``
# because the keys are an ``AnthropicModelSettings`` TypedDict that we don't want
# to import at module load (see the lazy anthropic import below).
_CACHE_SETTINGS: Any = {
    "anthropic_cache_tool_definitions": True,
    "anthropic_cache_instructions": True,
    "anthropic_cache": True,
}


def make_profiler_agent(
    model: str = "anthropic:claude-sonnet-4-6",
) -> Agent[None, RepoProfile]:
    """Build the calibration Agent (no tools attached yet — see register_tools).

    For ``anthropic:`` models the bare string is swapped for an
    ``AnthropicModel`` whose ``AsyncAnthropic`` client retries 429/5xx with
    exponential backoff (``ANTHROPIC_MAX_RETRIES``, default 8) — the same
    treatment ``experiments/agent`` uses. Other model strings fall through to
    pydantic-ai's ``defer_model_check=True`` so import stays network-free.
    """
    code_mode = CodeMode(tools=list(TOOL_NAMES))
    # pydantic-ai's Agent overloads drop `output_type` from the inferred return
    # type when `capabilities=` is passed (it resolves to Agent[None, str]); the
    # runtime output_type is honoured, so cast back to the real public type.
    if model.startswith("anthropic:"):
        model_obj = _make_anthropic_model_with_retries(model.removeprefix("anthropic:"))
        agent = Agent(
            model_obj,
            capabilities=[code_mode],
            output_type=RepoProfile,
            system_prompt=SYS_PROMPT_PROFILER,
            model_settings=_CACHE_SETTINGS,
        )
    else:
        agent = Agent(
            model,
            capabilities=[code_mode],
            output_type=RepoProfile,
            system_prompt=SYS_PROMPT_PROFILER,
            defer_model_check=True,
            model_settings=_CACHE_SETTINGS,
        )
    return cast("Agent[None, RepoProfile]", agent)


def _make_anthropic_model_with_retries(model_name: str):
    """Build an ``AnthropicModel`` whose client retries 429/5xx with backoff."""
    from anthropic import AsyncAnthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    max_retries = int(os.environ.get("ANTHROPIC_MAX_RETRIES", "8"))
    client = AsyncAnthropic(max_retries=max_retries)
    return AnthropicModel(
        model_name, provider=AnthropicProvider(anthropic_client=client)
    )
