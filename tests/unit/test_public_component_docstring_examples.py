"""Offline execution checks for examples published from component base docstrings."""

from __future__ import annotations

import inspect
import re
import textwrap
from types import ModuleType
from typing import Any

import pytest

import perceivo
from perceivo.primitives import _base as base


def _python_examples(documented: object) -> list[str]:
    docstring = inspect.getdoc(documented)
    assert docstring is not None
    return [
        textwrap.dedent(match)
        for match in re.findall(r"```python\n(.*?)```", docstring, flags=re.DOTALL)
    ]


def _defined_class(example: str, name: str) -> type[Any]:
    namespace: dict[str, object] = {"perceivo": perceivo}
    exec(compile(example, "<public-docstring-example>", "exec"), namespace)
    value = namespace[name]
    assert isinstance(value, type)
    return value


@pytest.mark.parametrize(
    ("documented", "class_name"),
    ((base, "MyAgent"), (perceivo.primitives.Agent, "TranslatorAgent")),
)
async def test_agent_examples_select_chat_and_run_with_offline_mock(
    documented: ModuleType | type[Any],
    class_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    examples = _python_examples(documented)
    assert len(examples) == 1
    calls: list[tuple[perceivo.types.AIService | str, dict[str, object]]] = []

    async def fake_invoke(
        service: perceivo.types.AIService | str, **params: object
    ) -> dict[str, object]:
        calls.append((service, params))
        return {"choices": [{"message": {"content": "bonjour"}}]}

    monkeypatch.setattr(perceivo.managed_service.ai, "invoke", fake_invoke)
    agent_type = _defined_class(examples[0], class_name)
    result = await agent_type().run(
        perceivo.primitives.AgentInput(
            artifact=perceivo.primitives.Artifact(
                type=perceivo.primitives.ArtifactType.TEXT, data="hello"
            )
        )
    )

    assert result.artifact is not None
    assert result.artifact.data == "bonjour"
    assert calls
    assert calls[0][0] is perceivo.types.AIService.CHAT


async def test_policy_example_uses_supported_policy_input_only() -> None:
    examples = _python_examples(perceivo.primitives.Policy)
    assert len(examples) == 1
    policy_type = _defined_class(examples[0], "SentimentPolicy")

    positive = await policy_type().run(
        perceivo.primitives.PolicyInput(
            artifact=perceivo.primitives.Artifact(
                type=perceivo.primitives.ArtifactType.TEXT, data="input"
            ),
            request_data={"sentiment": 0.75},
        )
    )
    negative = await policy_type().run(
        perceivo.primitives.PolicyInput(
            artifact=perceivo.primitives.Artifact(
                type=perceivo.primitives.ArtifactType.TEXT, data="input"
            ),
            request_data={"sentiment": 0.25},
        )
    )

    assert positive.next_state == "positive_response"
    assert negative.next_state == "negative_response"


async def test_adapter_example_returns_response_data_without_an_output_port() -> None:
    # An ADAPTER's out-port budget is 0 (validator/_port_rules.py), so the
    # example must not invoke an "output" port; it returns response_data.
    examples = _python_examples(perceivo.primitives.Adapter)
    assert len(examples) == 1
    assert "perceivo.flow.invoke" not in examples[0]
    adapter_type = _defined_class(examples[0], "MyAdapter")
    result = await adapter_type().run(
        perceivo.primitives.AdapterInput(request_data={"input_field": "value"})
    )
    assert result.response_data == {"output_field": "value"}


def test_policy_output_documents_caller_interpreted_decision_label() -> None:
    docstring = inspect.getdoc(perceivo.primitives.PolicyOutput)
    assert docstring is not None
    assert "required decision label" in docstring
    assert "calling FSM interprets that label" in docstring
    assert "does not require the label to name a state or output port" in docstring
