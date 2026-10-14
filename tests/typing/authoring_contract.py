"""Strict consumer examples for the installed ``py.typed`` contract."""

from __future__ import annotations

import perceivo
from perceivo import secrets


def echo_agent_input(input: perceivo.AgentInput) -> perceivo.AgentOutput:
    """Preserve a typed artifact while adding structured response data."""
    artifact: perceivo.Artifact = input.artifact
    return perceivo.AgentOutput(
        artifact=artifact,
        response_data={"request_keys": sorted(input.request_data)},
    )


def available_templates() -> list[dict[str, str]]:
    """Reach the typed lazy orchestration module through the root package."""
    return perceivo.orchestration.list_templates()


def make_policy_decision(input: perceivo.PolicyInput) -> perceivo.PolicyOutput:
    """Build a required caller-interpreted policy decision label."""
    decision = "continue" if input.request_data else "exit"
    return perceivo.PolicyOutput(next_state=decision, response_data={"decision": decision})


async def secret_fallback_types() -> tuple[str | None, str, str | None, str | int]:
    """Keep optional secret fallbacks visible to strict SDK consumers."""
    optional = await secrets.get("optional-secret")
    text_default = await secrets.get("optional-secret", "fallback")
    none_default = await secrets.get("optional-secret", None)
    integer_default = await secrets.get("optional-secret", 0)
    return optional, text_default, none_default, integer_default
