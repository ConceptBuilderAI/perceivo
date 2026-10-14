"""Define the orchestration primitives.

The eight node types an orchestration graph is built from, their decorators,
and the input and output types their signatures are written against::

    Start End Agent Tool FSM Policy Adapter Review

A component is written either as a decorated ``async def``::

    from perceivo.primitives import AgentInput, AgentOutput, Artifact, agent

    @agent
    async def shout(input: AgentInput) -> AgentOutput:
        return AgentOutput(artifact=Artifact(type="text", data=str(input.artifact.data).upper()))

or as a subclass of the base class::

    from perceivo.primitives import Agent, AgentInput, AgentOutput

    class Shout(Agent):
        async def run(self, input: AgentInput) -> AgentOutput:
            ...

The root package re-exports each decorator (``perceivo.start``,
``perceivo.end``, ``perceivo.agent``, ``perceivo.tool``, ``perceivo.fsm``,
``perceivo.policy``, ``perceivo.adapter`` and ``perceivo.review``) as the same
object as its counterpart here, so ``@perceivo.agent`` and ``@agent`` are
interchangeable. Only the decorators are re-exported: the base classes and the
input/output types live here and nowhere else.
"""

from ..types.artifact import Artifact, ArtifactType
from ..types.inputs import (
    AdapterInput,
    AgentInput,
    EndInput,
    FsmInput,
    PolicyInput,
    ReviewInput,
    StartInput,
    ToolInput,
)
from ..types.outputs import (
    AdapterOutput,
    AgentOutput,
    EndOutput,
    FsmOutput,
    PolicyOutput,
    ReviewOutput,
    StartOutput,
    ToolOutput,
)
from ._base import FSM, Adapter, Agent, End, Policy, Review, Start, Tool
from ._decorators import adapter, agent, end, fsm, policy, review, start, tool

__all__ = [
    "FSM",
    "Adapter",
    "AdapterInput",
    "AdapterOutput",
    "Agent",
    "AgentInput",
    "AgentOutput",
    "Artifact",
    "ArtifactType",
    "End",
    "EndInput",
    "EndOutput",
    "FsmInput",
    "FsmOutput",
    "Policy",
    "PolicyInput",
    "PolicyOutput",
    "Review",
    "ReviewInput",
    "ReviewOutput",
    "Start",
    "StartInput",
    "StartOutput",
    "Tool",
    "ToolInput",
    "ToolOutput",
    "adapter",
    "agent",
    "end",
    "fsm",
    "policy",
    "review",
    "start",
    "tool",
]
