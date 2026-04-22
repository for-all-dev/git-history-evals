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

    `defer_model_check=True` delays provider instantiation until the first
    `Agent.run(...)` call, so building the agent (and therefore importing
    this module) never touches the network or requires an API key.
    """
    return Agent(
        model,
        deps_type=AgentDeps,
        output_type=AgentVerdict,
        system_prompt=SYS_PROMPT_AGENT,
        defer_model_check=True,
    )
