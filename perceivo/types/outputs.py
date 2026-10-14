"""
Output types for perceivo SDK components.

Each component type has a corresponding output type that defines what data
it produces when execution completes.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from ..log.conversation import ConversationMessage
from .artifact import Artifact


@dataclass
class StartOutput:
    """
    Output from START components.

    START components produce the initial artifact that flows through
    the orchestration, or None for data-only orchestrations.

    Attributes:
        artifact: Optional artifact to pass to the next component.
        response_data: Optional response data.
        artifacts: Named artifacts dict. Keys are artifact names, values are
            Artifact objects. The "main" key corresponds to the artifact field.
    """

    artifact: Optional[Artifact] = None
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class AgentOutput:
    """
    Output from AGENT components.

    AGENT components are the primary processing units. Their output
    includes the processed artifact and structured conversation logs
    for multi-agent collaboration.

    Attributes:
        artifact: The processed artifact to pass to the next component.
        response_data: Optional response data.
        conversation_log: Structured conversation entry. SHOULD be provided
            by all agents. Contains typed ContentParts with:
            - A "summary" part describing what the agent did
            - Optional "analysis"/"metrics"/"response" parts with structured data
            - Optional "metadata" for token counts, model info, etc.

            This enables multi-agent collaboration where agents can read
            each other's structured conversation entries to build consensus.

    Example:
        ```python
        return perceivo.primitives.AgentOutput(
            artifact=perceivo.primitives.Artifact(type="text", data=result),
            conversation_log=perceivo.log.conversation.ConversationMessage(
                role="agent",
                parts=[
                    perceivo.log.conversation.ContentPart("text/plain", "Analyzed sentiment: positive", name="summary"),
                    perceivo.log.conversation.ContentPart("application/json", {"score": 0.85}, name="metrics"),
                ],
            )
        )
        ```
    """

    artifact: Artifact
    response_data: dict[str, Any] = field(default_factory=dict)
    conversation_log: Optional[ConversationMessage] = None
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class EndOutput:
    """
    Output from END components.

    END components are the final nodes. Their output becomes the
    orchestration's final result, displayed in the UI.

    Attributes:
        artifact: Optional final artifact (the orchestration result).
        response_data: Final response data.
        failed: Whether the orchestration run should finish as FAILED while
            retaining this output.
        failure_reason: Optional human-readable reason persisted with a failed
            run. Ignored unless `failed` is true.
    """

    artifact: Optional[Artifact] = None
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    failed: bool = False
    failure_reason: Optional[str] = None


@dataclass
class ToolOutput:
    """
    Output from TOOL components.

    TOOL components can optionally return a modified artifact that
    will be passed along the tool chain or back to the calling agent.

    Attributes:
        artifact: Optional modified artifact.
        response_data: Tool response data.
    """

    artifact: Optional[Artifact] = None
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class FsmOutput:
    """
    Output from FSM components.

    FSM components are state machines. Their output is produced
    when the FSM completes (reaches a terminal state).

    Attributes:
        artifact: Optional artifact from the final state.
        response_data: FSM response data.
    """

    artifact: Optional[Artifact] = None
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class PolicyOutput:
    """
    Output from POLICY components.

    POLICY components return a required decision label in ``next_state``.
    The calling FSM interprets that label according to its own control flow;
    the SDK does not require the label to name a state or output port.

    Attributes:
        next_state: Required decision label for the calling FSM to interpret.
        artifact: Optional modified artifact.
        response_data: Policy response data.

    Example:
        ```python
        # Simple decision
        return perceivo.primitives.PolicyOutput(next_state="continue")

        # Decision with caller-specific result data
        return perceivo.primitives.PolicyOutput(
            next_state="exit",
            response_data={"should_exit": True}
        )
        ```
    """

    next_state: str
    artifact: Optional[Artifact] = None
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class ReviewOutput:
    """
    Output from REVIEW components.

    REVIEW components pause for human input. The output contains
    the reviewer's responses once review is complete.

    Attributes:
        artifact: Not read by the engine. A REVIEW node passes on the artifact
            it was given, whatever this holds.
        response_data: Reviewer's survey answers and feedback.
    """

    artifact: Optional[Artifact] = None
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class AdapterOutput:
    """
    Output from ADAPTER components.

    Adapters transform data between incompatible schemas.
    They receive input from one component and hand the transformed
    ``response_data`` back to the engine, which passes it to the next node on
    the mainflow. An Adapter has no output port: the ADAPTER port budget allows
    zero, and the continuation is a node-level link, not a port.

    ``artifact`` is optional. When it is ``None`` the engine propagates ``None``
    downstream rather than reusing the inbound artifact.

    Attributes:
        artifact: Optional artifact to pass downstream.
        response_data: Transformed response data.
        artifacts: Named artifacts dict.
    """

    artifact: Optional[Artifact] = None
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
