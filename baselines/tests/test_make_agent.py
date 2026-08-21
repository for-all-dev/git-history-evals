"""No-network tests for `apply_ablate.solve.make_agent`'s provider routing (#153).

Each supported `<provider>:<name>` prefix must construct the matching pydantic-ai
model class without ever touching the network — API keys are dummy values and no
requests are made at construction time.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai.models import Model

from apply_ablate.solve import make_agent


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-dummy")


@pytest.mark.parametrize("model", ["claude-fable-5", "anthropic:claude-fable-5"])
def test_bare_and_anthropic_prefix_route_to_anthropic(model: str) -> None:
    from pydantic_ai.models.anthropic import AnthropicModel

    agent = make_agent(model)

    assert isinstance(agent.model, AnthropicModel)


def test_openai_prefix_routes_to_openai_chat_model() -> None:
    from pydantic_ai.models.openai import OpenAIChatModel

    agent = make_agent("openai:gpt-5.6-sol")

    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "gpt-5.6-sol"


def test_mistral_prefix_routes_to_mistral_model() -> None:
    from pydantic_ai.models.mistral import MistralModel

    agent = make_agent("mistral:labs-leanstral-1-5")

    assert isinstance(agent.model, MistralModel)
    assert agent.model.model_name == "labs-leanstral-1-5"


def test_unknown_prefix_falls_through_to_infer_model() -> None:
    """Any other known pydantic-ai prefix must reach the generic fall-through,
    not silently get routed to Anthropic (the original bug in #153)."""
    sentinel = MagicMock(spec=Model)

    with patch(
        "pydantic_ai.models.infer_model", return_value=sentinel
    ) as mock_infer_model:
        agent = make_agent("groq:llama-3.1-8b-instant")

    # `Agent.__init__` itself also passes the already-constructed model through
    # `infer_model` (a no-op for `Model` instances), so assert on the first call
    # rather than requiring exactly one.
    assert mock_infer_model.call_args_list[0].args == ("groq:llama-3.1-8b-instant",)
    assert agent.model is sentinel
