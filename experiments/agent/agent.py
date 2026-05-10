"""pydantic-ai Agent factory and structured `AgentVerdict` output schema.

The ReAct loop for proof completion is driven by a single `pydantic_ai.Agent`
parameterised on `AgentDeps` (see `agent/deps.py`, issue #6) and producing an
`AgentVerdict` as its structured final output. Tool registration (issue #11)
and the actual run loop (issue #15) live elsewhere; this module only wires up
the agent's type signature and system prompt.

Import must be side-effect-free: constructing the `Agent` object does not
touch the network, and no client is instantiated until a caller actually
invokes `Agent.run(...)`.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agent.deps import AgentDeps
from shared.prompts import SYS_PROMPT_AGENT


class AgentVerdict(BaseModel):
    """Structured final output returned by the ReAct agent."""

    succeeded: bool = Field(description="Whether the final compile passed.")
    final_tactics: str = Field(
        description="The proof body the agent settled on (or `Admitted.` on give-up)."
    )
    give_up_reason: str | None = Field(
        default=None,
        description="If the agent gave up, a short explanation; otherwise None.",
    )
    n_turns: int = Field(description="Number of tool-call rounds the agent took.")


def make_agent(
    model: str = "anthropic:claude-sonnet-4-6",
) -> Agent[AgentDeps, AgentVerdict]:
    """Build a fresh pydantic-ai Agent for the ReAct proof-completion loop.

    The returned agent has no tools registered — see `agent/tools.py` (#11).

    For Anthropic-prefixed models, swap the bare model string for an
    ``AnthropicModel`` whose underlying ``AsyncAnthropic`` client is
    configured with ``max_retries=8``. The Anthropic SDK then retries
    429/408/409/5xx with exponential backoff (and respects ``Retry-After``
    headers), which is exactly what the 17-SHA orchestrator hits when
    multiple agent loops cluster against the 2M tok/min org limit. Override
    the retry budget via ``ANTHROPIC_MAX_RETRIES``; set to 0 in tests.

    For non-Anthropic models we fall through to pydantic-ai's
    ``defer_model_check=True`` path, which delays provider instantiation
    until the first ``Agent.run(...)`` so importing this module remains
    network-free.
    """
    if model.startswith("anthropic:"):
        model_obj = _make_anthropic_model_with_retries(
            model.removeprefix("anthropic:")
        )
        return Agent(
            model_obj,
            deps_type=AgentDeps,
            output_type=AgentVerdict,
            system_prompt=SYS_PROMPT_AGENT,
        )
    return Agent(
        model,
        deps_type=AgentDeps,
        output_type=AgentVerdict,
        system_prompt=SYS_PROMPT_AGENT,
        defer_model_check=True,
    )


def _make_anthropic_model_with_retries(model_name: str):
    """Build an ``AnthropicModel`` whose client retries 429/5xx with backoff.

    Imports are local so the non-Anthropic branch (and unit tests that mock
    ``Agent``) don't pay the cost of resolving pydantic-ai providers.
    """
    from anthropic import AsyncAnthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    max_retries = int(os.environ.get("ANTHROPIC_MAX_RETRIES", "8"))
    client = AsyncAnthropic(max_retries=max_retries)
    return AnthropicModel(model_name, provider=AnthropicProvider(anthropic_client=client))
