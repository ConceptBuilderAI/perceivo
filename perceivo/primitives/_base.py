"""
Base classes for the orchestration primitives.

These are the base classes that component authors extend to create
their orchestration components. Each component type has a specific
method to implement. Import them from :mod:`perceivo.primitives`.

Example:
    ```python
    import perceivo
    from perceivo.primitives import Agent, AgentInput, AgentOutput, Artifact, ArtifactType

    class MyAgent(Agent):
        async def run(self, input: AgentInput) -> AgentOutput:
            response = await perceivo.managed_service.ai.invoke(
                perceivo.types.AIService.CHAT,
                model="gpt-4o",
                messages=[{"role": "user", "content": "Translate this..."}],
            )
            text = response["choices"][0]["message"]["content"]
            return AgentOutput(
                artifact=Artifact(type=ArtifactType.TEXT, data=text),
                conversation_log="Translation completed"
            )
    ```
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

_MUST_IMPLEMENT_RUN = "Subclass must implement run()"

if TYPE_CHECKING:
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


class Start(ABC):
    """
    Base class for START components.

    START components are entry points for orchestrations. They receive
    initial input and produce the first artifact in the processing chain.

    Implement the `start()` method to define your component's behavior.

    Example:
        ```python
        class MyStart(perceivo.primitives.Start):
            async def start(self, input: perceivo.primitives.StartInput) -> perceivo.primitives.StartOutput:
                # Initialize processing
                initial_data = input.request_data.get("prompt", "")
                return perceivo.primitives.StartOutput(
                    artifact=perceivo.primitives.Artifact(type="text", data=initial_data)
                )
        ```
    """

    # setup() is an optional override hook, intentionally concrete (not abstract).
    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook called once before the first run in persistent mode.

        Override this to perform expensive initialization such as downloading
        ML model weights, loading models into GPU memory, or establishing
        persistent connections. Work done here persists via module-level
        caching (component instances are recreated per invocation, but the
        Python process and module globals persist).

        Has full SDK access: perceivo.secrets, perceivo.params, perceivo.log, etc.
        """
        pass

    @abstractmethod
    async def start(self, input: "StartInput") -> "StartOutput":
        """
        Execute the START component.

        Args:
            input: The start input containing initial artifact and request data.

        Returns:
            StartOutput with the initial artifact for the orchestration.
        """
        raise NotImplementedError("Subclass must implement start()")

    # Engine entry point: the platform engine and MCP service call this
    # underscore method; do not remove.
    async def _start(self, input: "StartInput") -> "StartOutput":
        """Engine entry point. Delegates to start()."""
        return await self.start(input)


class Agent(ABC):
    """
    Base class for AGENT components.

    AGENT components are the primary processing units in an orchestration.
    They typically call LLMs, process data, and make decisions.

    Implement the `run()` method to define your component's behavior.

    Example:
        ```python
        class TranslatorAgent(perceivo.primitives.Agent):
            async def run(self, input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
                text = input.artifact.data
                response = await perceivo.managed_service.ai.invoke(
                    perceivo.types.AIService.CHAT,
                    model="gpt-4o",
                    messages=[{"role": "user", "content": f"Translate to French: {text}"}],
                    system_prompt="You are a professional translator."
                )
                reply = response["choices"][0]["message"]["content"]
                return perceivo.primitives.AgentOutput(
                    artifact=perceivo.primitives.Artifact(type=perceivo.primitives.ArtifactType.TEXT, data=reply),
                    conversation_log=perceivo.log.conversation.ConversationMessage(
                        role="agent",
                        parts=[
                            perceivo.log.conversation.ContentPart(
                                "text/plain", f"Translated {len(text)} characters", name="summary"
                            ),
                        ],
                    ),
                )
        ```
    """

    # setup() is an optional override hook, intentionally concrete (not abstract).
    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook. See Start.setup() for full documentation."""
        pass

    @abstractmethod
    async def run(self, input: "AgentInput") -> "AgentOutput":
        """
        Execute the AGENT component.

        Args:
            input: The agent input containing artifact and request data.

        Returns:
            AgentOutput with processed artifact and optional conversation log.
        """
        raise NotImplementedError(_MUST_IMPLEMENT_RUN)

    # Engine entry point: the platform engine and MCP service call this
    # underscore method; do not remove.
    async def _run(self, input: "AgentInput") -> "AgentOutput":
        """Engine entry point. Delegates to run()."""
        return await self.run(input)


class End(ABC):
    """
    Base class for END components.

    END components are the final nodes in an orchestration. They format
    and return the final result that will be displayed in the UI.

    Implement the `end()` method to define your component's behavior.

    Example:
        ```python
        class MyEnd(perceivo.primitives.End):
            async def end(self, input: perceivo.primitives.EndInput) -> perceivo.primitives.EndOutput:
                # Format final output
                result = input.artifact.data
                return perceivo.primitives.EndOutput(
                    artifact=perceivo.primitives.Artifact(type="text", data=f"Final: {result}")
                )
        ```
    """

    # setup() is an optional override hook, intentionally concrete (not abstract).
    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook. See Start.setup() for full documentation."""
        pass

    @abstractmethod
    async def end(self, input: "EndInput") -> "EndOutput":
        """
        Execute the END component.

        Args:
            input: The end input containing artifact and response data.

        Returns:
            EndOutput with the final artifact for the orchestration.
        """
        raise NotImplementedError("Subclass must implement end()")

    # Engine entry point: the platform engine and MCP service call this
    # underscore method; do not remove.
    async def _end(self, input: "EndInput") -> "EndOutput":
        """Engine entry point. Delegates to end()."""
        return await self.end(input)


class Tool(ABC):
    """
    Base class for TOOL components.

    TOOL components provide utility functions like API calls, data
    transformations, or external service integrations. They are called
    by agents through tool ports.

    Implement the `run()` method to define your component's behavior.

    Example:
        ```python
        class WeatherTool(perceivo.primitives.Tool):
            async def run(self, input: perceivo.primitives.ToolInput) -> perceivo.primitives.ToolOutput:
                city = input.request_data.get("city", "London")
                # Call weather API...
                return perceivo.primitives.ToolOutput(
                    response_data={"temperature": 22, "conditions": "sunny"}
                )
        ```
    """

    # setup() is an optional override hook, intentionally concrete (not abstract).
    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook. See Start.setup() for full documentation."""
        pass

    @abstractmethod
    async def run(self, input: "ToolInput") -> "ToolOutput":
        """
        Execute the TOOL component.

        Args:
            input: The tool input containing optional artifact and request data.

        Returns:
            ToolOutput with optional modified artifact and response data.
        """
        raise NotImplementedError(_MUST_IMPLEMENT_RUN)

    # Engine entry point: the platform engine and MCP service call this
    # underscore method; do not remove.
    async def _run(self, input: "ToolInput") -> "ToolOutput":
        """Engine entry point. Delegates to run()."""
        return await self.run(input)


class FSM(ABC):
    """
    Base class for FSM (finite state machine) components.

    FSM components are state machines that contain multiple states
    (sub-components) and manage transitions between them. They enable
    complex workflows with branching logic.

    Implement the `run()` method to define your component's behavior.

    Example:
        ```python
        class ReviewFSM(perceivo.primitives.FSM):
            async def run(self, input: perceivo.primitives.FsmInput) -> perceivo.primitives.FsmOutput:
                # State machine logic handled by sub-components
                # This method is called when the FSM completes
                return perceivo.primitives.FsmOutput(
                    artifact=input.artifact
                )
        ```
    """

    # setup() is an optional override hook, intentionally concrete (not abstract).
    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook. See Start.setup() for full documentation."""
        pass

    @abstractmethod
    async def run(self, input: "FsmInput") -> "FsmOutput":
        """
        Execute the FSM component.

        Args:
            input: The FSM input containing artifact and request data.

        Returns:
            FsmOutput with the artifact from the final state.
        """
        raise NotImplementedError(_MUST_IMPLEMENT_RUN)

    # Engine entry point: the platform engine and MCP service call this
    # underscore method; do not remove.
    async def _run(self, input: "FsmInput") -> "FsmOutput":
        """Engine entry point. Delegates to run()."""
        return await self.run(input)


class Policy(ABC):
    """
    Base class for POLICY components.

    POLICY components are stateless decision makers that determine
    state transitions within an FSM. They analyze the current state
    and decide which state to transition to next.

    Implement the `run()` method to define your component's behavior.

    Example:
        ```python
        class SentimentPolicy(perceivo.primitives.Policy):
            async def run(self, input: perceivo.primitives.PolicyInput) -> perceivo.primitives.PolicyOutput:
                sentiment = float(input.request_data.get("sentiment", 0.0))

                if sentiment > 0.5:
                    return perceivo.primitives.PolicyOutput(next_state="positive_response")
                else:
                    return perceivo.primitives.PolicyOutput(next_state="negative_response")
        ```
    """

    # setup() is an optional override hook, intentionally concrete (not abstract).
    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook. See Start.setup() for full documentation."""
        pass

    @abstractmethod
    async def run(self, input: "PolicyInput") -> "PolicyOutput":
        """
        Execute the POLICY component.

        Args:
            input: The policy input containing artifact, request data,
                and conversation log reference.

        Returns:
            PolicyOutput specifying the next state to transition to.
        """
        raise NotImplementedError(_MUST_IMPLEMENT_RUN)

    # Engine entry point: the platform engine and MCP service call this
    # underscore method; do not remove.
    async def _run(self, input: "PolicyInput") -> "PolicyOutput":
        """Engine entry point. Delegates to run()."""
        return await self.run(input)


class Adapter(ABC):
    """
    Base class for ADAPTER components.

    ADAPTER components transform data between incompatible component
    schemas. They receive input from one component, apply field
    mappings, and return the transformed data as ``response_data`` to the
    engine. An ADAPTER has no output port (its ``out`` port budget is 0); it
    writes through its STATE ports.

    Implement the `run()` method to define your component's behavior.

    Example:
        ```python
        class MyAdapter(perceivo.primitives.Adapter):
            async def run(self, input: perceivo.primitives.AdapterInput) -> perceivo.primitives.AdapterOutput:
                data = input.request_data
                transformed = {"output_field": data.get("input_field")}
                return perceivo.primitives.AdapterOutput(
                    response_data=transformed,
                    artifact=input.artifact
                )
        ```
    """

    # setup() is an optional override hook, intentionally concrete (not abstract).
    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook. See Start.setup() for full documentation."""
        pass

    @abstractmethod
    async def run(self, input: "AdapterInput") -> "AdapterOutput":
        """
        Execute the ADAPTER component.

        Args:
            input: The adapter input containing artifact and request data.

        Returns:
            AdapterOutput with transformed data.
        """
        raise NotImplementedError(_MUST_IMPLEMENT_RUN)

    # Engine entry point: the platform engine and MCP service call this
    # underscore method; do not remove.
    async def _run(self, input: "AdapterInput") -> "AdapterOutput":
        """Engine entry point. Delegates to run()."""
        return await self.run(input)


class Review(ABC):
    """
    Base class for REVIEW components.

    A REVIEW node is where a run asks a human. The platform engine creates
    the review request when the run reaches the node (a component never
    creates one) and runs no component code there. The reviewer sees the
    items an upstream node sends; else a preceding FSM's branch artifacts;
    else the run's named artifacts, when there is more than one; else the
    incoming artifact. A required review pauses the run, and the reviewer's
    answers become the node's response data. An optional one does not wait:
    its response data is ``review_request_id``, ``review_status`` "pending"
    and ``review_required`` False. The component declares the node's ports.
    The local runner has no reviewer, so it runs the component and approves
    automatically; response data the component returns overrides the fixed
    local decision, which lets a test stand in for a reviewer's answers.

    Implement the `run()` method to define your component's behavior.

    Example:
        ```python
        class ApproveSummary(perceivo.primitives.Review):
            async def run(self, input: perceivo.primitives.ReviewInput) -> perceivo.primitives.ReviewOutput:
                return perceivo.primitives.ReviewOutput(artifact=input.artifact)
        ```
    """

    # setup() is an optional override hook, intentionally concrete (not abstract).
    async def setup(self) -> None:  # noqa: B027
        """Optional warmup hook. See Start.setup() for full documentation."""
        pass

    @abstractmethod
    async def run(self, input: "ReviewInput") -> "ReviewOutput":
        """
        Execute the REVIEW component.

        Args:
            input: The review input containing the artifact under review and
                request data.

        Returns:
            ReviewOutput with the artifact under review and any response data.
        """
        raise NotImplementedError(_MUST_IMPLEMENT_RUN)

    # The platform engine's entry point, matching the other primitives.
    async def _run(self, input: "ReviewInput") -> "ReviewOutput":
        """Engine entry point. Delegates to run()."""
        return await self.run(input)
