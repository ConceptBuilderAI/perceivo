"""Invoke connected components through ports.

Available only inside orchestration components, not in ML service extensions
(training, inference, feature, manager). FSM components use it to trigger
state transitions and AGENT components use it to call tools.
"""

from typing import TYPE_CHECKING, Any, Optional, cast

from .._internal.context import _ExecutionContext

if TYPE_CHECKING:
    from ..types.artifact import Artifact


def _check_orchestration_context(ext: Any, function_name: str) -> None:
    """Check that we're in an orchestration context that supports port invocation."""
    if not hasattr(ext, "invoke_port"):
        raise RuntimeError(
            f"{function_name}() is only available in orchestrations. "
            f"It is not supported in ML service extensions (training, inference, etc.)."
        )


async def invoke(
    port_name: str,
    request_data: Optional[dict[str, Any]] = None,
    artifact: Optional["Artifact"] = None,
    artifacts: Optional[dict[str, "Artifact"]] = None,
) -> dict[str, Any]:
    """Invoke a connected component through a port.

    Triggers execution of the component connected to the given output port.
    Available only inside orchestration components.

    Args:
        port_name: Name of the output port to invoke.
        request_data: Optional data to pass to the connected component.
        artifact: Optional artifact to pass to the connected component.
        artifacts: Optional named artifacts dict to pass to the connected component.

    Returns:
        Dict containing the response from the connected component:
            - artifact: The response artifact (if any)
            - artifacts: Named artifacts dict from the component
            - response_data: Response data from the component

    Raises:
        RuntimeError: If called from an ML service extension (not an orchestration).

    Example:
        ```python
        # Call a tool from an agent
        result = await perceivo.flow.invoke(
            "weather_tool",
            request_data={"city": "London"}
        )
        temperature = result["response_data"]["temperature"]

        # Pass artifact to next component
        result = await perceivo.flow.invoke(
            "processor",
            artifact=perceivo.primitives.Artifact(type="text", data="Process this")
        )
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_orchestration_context(ext, "perceivo.flow.invoke")
    return cast(
        dict[str, Any],
        await ext.invoke_port(
            port_name=port_name,
            request_data=request_data or {},
            artifact=artifact,
            artifacts=artifacts or {},
        ),
    )


async def invoke_parallel(
    port_names: list[str],
    request_data: Optional[dict[str, Any]] = None,
    artifact: Optional["Artifact"] = None,
    artifacts: Optional[dict[str, "Artifact"]] = None,
    timeout_seconds: int = 300,
    failure_strategy: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    """Invoke multiple connected components in parallel.

    Triggers every listed port concurrently, for fan-out patterns. Available only
    inside orchestration components.

    Args:
        port_names: List of output port names to invoke.
        request_data: Optional data to pass to all connected components.
        artifact: Optional artifact to pass to all connected components.
        artifacts: Optional named artifacts dict to pass to all connected components.
        timeout_seconds: Maximum time to wait for all responses (default: 300).
        failure_strategy: How branch failures are handled. One of:
            - ``"wait_all"``: wait for every branch, then raise if any failed.
            - ``"fail_fast"``: raise on the first branch failure.
            - ``"ignore_failures"``: return the per-branch errors in the result
              instead of raising.
            ``None`` lets the orchestration engine apply its default,
            ``"wait_all"``.

    Returns:
        Dict mapping port names to their responses. Each response contains:
            - artifact: The response artifact (if any)
            - response_data: Response data from the component
            - error: Error message if the branch failed (only with
              ``failure_strategy="ignore_failures"``)

    Raises:
        RuntimeError: If called from an ML service extension (not an orchestration).

    Example:
        ```python
        # Invoke multiple tools in parallel
        results = await perceivo.flow.invoke_parallel(
            ["sentiment_tool", "entity_tool", "summary_tool"],
            artifact=Artifact(type="text", data=text_to_analyze)
        )

        sentiment = results["sentiment_tool"]["response_data"]["sentiment"]
        entities = results["entity_tool"]["response_data"]["entities"]
        summary = results["summary_tool"]["artifact"].data
        ```
    """
    ext = _ExecutionContext.get_ext()
    _check_orchestration_context(ext, "perceivo.flow.invoke_parallel")

    return cast(
        dict[str, dict[str, Any]],
        await ext.invoke_port_parallel(
            port_names=port_names,
            request_data=request_data or {},
            artifact=artifact,
            artifacts=artifacts or {},
            timeout_seconds=timeout_seconds,
            failure_strategy=failure_strategy,
        ),
    )
