"""
Input types for perceivo SDK components.

Each component type has a corresponding input type that defines what data
it receives when invoked.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from ..log.conversation import ConversationMessage
from .artifact import Artifact


@dataclass
class StartInput:
    """
    Input to START components.

    START components are entry points for orchestrations. They receive
    the initial input from the user or calling system.

    Attributes:
        artifact: Optional initial artifact (may be None for orchestrations
            that don't require input data). Not a lookup in ``artifacts``,
            which may hold no ``"main"``.
        request_data: Additional request parameters as key-value pairs.
        artifacts: Named artifacts dict. Keys are artifact names, values are
            Artifact objects. No ``"main"`` key when the upstream node returned
            named artifacts without one.
    """

    artifact: Optional[Artifact] = None
    request_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class AgentInput:
    """
    Input to AGENT components.

    AGENT components are the primary processing units in an orchestration.
    They typically call LLMs, process data, and make decisions.

    Attributes:
        artifact: The main artifact from the previous component, or None when no
            node upstream produced one. Not a lookup in ``artifacts``,
            which may hold no ``"main"``.
        request_data: Additional request parameters as key-value pairs.
        artifacts: Named artifacts dict. Keys are artifact names, values are
            Artifact objects. No ``"main"`` key when the upstream node returned
            named artifacts without one.
    """

    artifact: Optional[Artifact]
    request_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class EndInput:
    """
    Input to END components.

    END components are the final nodes in an orchestration. They format
    and return the final result.

    Attributes:
        artifact: The main artifact from the previous component, or None when no
            node upstream produced one. Not a lookup in ``artifacts``,
            which may hold no ``"main"``.
        response_data: Response data accumulated through the orchestration.
        artifacts: Named artifacts dict.
    """

    artifact: Optional[Artifact]
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class ToolInput:
    """
    Input to TOOL components.

    TOOL components provide utility functions like API calls, data
    transformations, or external service integrations.

    Attributes:
        artifact: Optional artifact (tools may not require input data).
            Not a lookup in ``artifacts``,
            which may hold no ``"main"``.
        request_data: Request parameters for the tool.
        artifacts: Named artifacts dict.
    """

    artifact: Optional[Artifact] = None
    request_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class FsmInput:
    """
    Input to FSM components.

    FSM components are state machines that contain multiple states
    (sub-components) and manage transitions between them.

    Attributes:
        artifact: The main artifact from the previous component, or None when no
            node upstream produced one. Not a lookup in ``artifacts``,
            which may hold no ``"main"``.
        request_data: Request parameters passed to the FSM.
        artifacts: Named artifacts dict.
    """

    artifact: Optional[Artifact]
    request_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class PolicyInput:
    """
    Input to POLICY components.

    POLICY components are stateless decision makers that determine
    state transitions within an FSM. They analyze the current state
    and decide which state to transition to next.

    Attributes:
        artifact: The main artifact from the current state, or None when no
            node upstream produced one. Not a lookup in ``artifacts``,
            which may hold no ``"main"``.
        request_data: Request parameters.
        conversation_log: The conversation so far, as
            :class:`~perceivo.log.conversation.ConversationMessage` entries,
            when the runtime supplies one; ``None`` otherwise.
        artifacts: Named artifacts dict.
    """

    artifact: Optional[Artifact]
    request_data: dict[str, Any] = field(default_factory=dict)
    conversation_log: Optional[list[ConversationMessage]] = None
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class ReviewInput:
    """
    Input to REVIEW components (human-in-the-loop).

    REVIEW components pause execution and wait for human input before
    continuing. Used for approval workflows, content moderation, etc.

    Attributes:
        artifact: Optional artifact to present to the reviewer.
            Not a lookup in ``artifacts``,
            which may hold no ``"main"``.
        request_data: Additional context for the review.
        artifacts: Named artifacts dict.
    """

    artifact: Optional[Artifact] = None
    request_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)


@dataclass
class AdapterInput:
    """
    Input to ADAPTER components.

    ADAPTER components transform data between incompatible component schemas.
    They receive input from one component and return the transformed data
    as ``response_data`` to the engine. An ADAPTER has no output port; it
    writes through its STATE ports.

    Attributes:
        artifact: Optional artifact from the previous component.
        request_data: Request parameters to transform.
        artifacts: Named artifacts dict.
    """

    artifact: Optional[Artifact] = None
    request_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
