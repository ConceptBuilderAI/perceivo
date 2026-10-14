"""Port invocation: ``perceivo.flow.invoke()`` and ``invoke_parallel()``.

Both functions take the executor explicitly: resolving a port needs the compiled
plan, the ContextVar holding the active node, and a recursive call back into node
execution. The executor binds them via ``functools.partial`` when it activates
itself as the invoke target.

Unconnected ports
-----------------
Invoking a port with nothing wired to it is a *contract*, not an internal error.
Components in the template library enumerate their declared state ports by
invoking each one and treating the failure as "this state is not wired", so the
exception's **type and message text are both load-bearing**. The engine raises::

    ValueError(f"No connection found for port {port_name} on node {node_id}")

and the Sequential Executor template enumerates its state ports with::

    except ValueError as e:
        if "No connection found" in str(e):
            break          # this state is not wired; stop enumerating

:func:`no_connection_error` is the single place the message is built.

Invoking an ambiguous port outside component execution raises ``RuntimeError``:
that condition is local-runner only and has no platform equivalent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from ._component_execution import apply_result_artifacts
from ._invoke_result import format_invoke_result, resolve_failure_strategy

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .executor import _OrchestrationExecutor

logger = logging.getLogger("perceivo.runner")

# The engine's exact message prefix; seeded components match on this substring.
NO_CONNECTION_PREFIX = "No connection found for port"

# Caller node id used when ``invoke_port`` runs outside component execution.
_UNKNOWN_NODE = "<unknown>"


def no_connection_error(port_name: str, node_id: str) -> ValueError:
    """Build the platform's unconnected-port exception, type and text.

    See the module docstring for why the wording is part of the contract.
    """
    return ValueError(f"{NO_CONNECTION_PREFIX} {port_name} on node {node_id}")


async def invoke_port(
    executor: _OrchestrationExecutor,
    port_name: str,
    request_data: Optional[dict[str, Any]] = None,
    artifact: Any = None,
    artifacts: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Handle perceivo.flow.invoke(): execute the target component in-process."""
    # As the engine builds an invoked node's artifacts: the caller's, with its
    # artifact as "main", or else the run's. The node's artifact is that "main".
    input_artifacts = dict(artifacts or {})
    if artifact and "main" not in input_artifacts:
        input_artifacts["main"] = artifact
    if not input_artifacts:
        input_artifacts = dict(executor._artifacts)
    input_artifact = input_artifacts.get("main") or artifact or executor._artifacts.get("main")
    return await invoke_target(
        executor,
        port_name=port_name,
        request_data=request_data,
        input_artifact=input_artifact,
        input_artifacts=input_artifacts,
    )


async def invoke_target(
    executor: _OrchestrationExecutor,
    *,
    port_name: str,
    request_data: Optional[dict[str, Any]],
    input_artifact: Any,
    input_artifacts: dict[str, Any],
) -> dict[str, Any]:
    """Run the node wired to ``port_name`` with the input an invoke resolved.

    The run's artifacts become ``input_artifacts`` while the node runs, then what
    the node's result leaves, as the engine applies an invoke to its run.
    """
    # Resolve against the component making the call. ContextVar keeps this
    # correct when parallel branches invoke same-named ports concurrently.
    source_node_id = executor._active_node_id.get()
    if source_node_id:
        source_shells = [executor._plan.shells_by_id[source_node_id]]
    else:
        # Internal/test callers outside component execution remain supported
        # only when the port name is unambiguous across the graph.
        source_shells = [
            shell
            for shell in executor._plan.shells_by_id.values()
            if shell.invoke_connections.get(port_name)
        ]
        if len(source_shells) > 1:
            raise RuntimeError(f"Port '{port_name}' is ambiguous without an active component.")

    target_node_id = None
    if source_shells:
        connections = source_shells[0].invoke_connections.get(port_name, [])
        if connections:
            target_node_id = connections[0].to_node_id

    if target_node_id is None:
        caller_node_id = source_node_id or (
            source_shells[0].node_id if source_shells else _UNKNOWN_NODE
        )
        raise no_connection_error(port_name, caller_node_id)

    if input_artifacts:
        replacement = dict(input_artifacts)
        executor._artifacts.clear()
        executor._artifacts.update(replacement)
    result = await executor._execute_node(target_node_id, input_artifact, request_data or {})
    apply_result_artifacts(executor._artifacts, result)
    # Return the platform-shaped invoke contract, not the raw node result.
    return format_invoke_result(result)


async def invoke_single_port(
    executor: _OrchestrationExecutor,
    port_name: str,
    request_data: Optional[dict[str, Any]] = None,
    artifact: Any = None,
    artifacts: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Handle perceivo.flow.invoke(): :func:`invoke_port` with FSM input forwarding.

    When the calling FSM runs with ``forward_input_to_states``, its own input is
    merged underneath *request_data*, so keys the caller passes win. The engine
    does this on a single invoke only and not on its parallel path, which is why
    :func:`invoke_port_parallel` calls :func:`invoke_target` directly.
    """
    source_node_id = executor._active_node_id.get()
    fsm_input = executor._fsm_forward_data.get(source_node_id) if source_node_id else None
    if fsm_input:
        request_data = {**fsm_input, **(request_data or {})}
    return await invoke_port(
        executor,
        port_name,
        request_data=request_data,
        artifact=artifact,
        artifacts=artifacts,
        **kwargs,
    )


async def _cancel_and_drain(tasks: set[asyncio.Task[Any]]) -> None:
    """Cancel every unfinished task and await the cancellations before returning.

    ``Task.cancel()`` only requests cancellation; a branch whose cancellation
    handler awaits anything is still running until it is awaited. Tasks that had
    already finished are read too, so their exceptions are retrieved.
    """
    unfinished = [task for task in tasks if not task.done()]
    for task in unfinished:
        task.cancel()
    await asyncio.gather(*unfinished, return_exceptions=True)
    for task in tasks:
        if not task.cancelled():
            task.exception()


async def _await_branches(
    branches: set[asyncio.Task[dict[str, Any]]],
    timeout_seconds: int,
) -> set[asyncio.Task[dict[str, Any]]]:
    """Await every branch under the deadline; return the ones still pending.

    If the awaiting task is cancelled, every unfinished branch is cancelled and
    drained before the cancellation propagates.
    """
    try:
        _, pending = await asyncio.wait(
            branches,
            timeout=timeout_seconds if timeout_seconds else None,
        )
    except asyncio.CancelledError:
        await _cancel_and_drain(branches)
        raise
    return pending


def _branch_outcome(
    task: asyncio.Task[dict[str, Any]],
    *,
    timed_out: bool,
    timeout_seconds: int,
) -> BaseException | dict[str, Any]:
    """What this branch produced: its result, or the exception standing for it.

    Returning the exception rather than raising it keeps the decision with the
    strategy, which is the only thing that knows whether a branch failure ends
    the call.
    """
    if timed_out:
        # The engine's per-branch text, verbatim, so a component that reads
        # ``result["error"]`` sees the same string locally and in-cluster.
        return TimeoutError(f"Timeout after {timeout_seconds}s")
    if task.cancelled():
        # ``Task.exception()`` raises ``CancelledError`` on a cancelled task, so check
        # ``cancelled()`` first.
        return RuntimeError("Branch cancelled")
    return task.exception() or task.result()


def _collect_branch_results(
    port_names: list[str],
    tasks: dict[str, asyncio.Task[dict[str, Any]]],
    pending: set[asyncio.Task[dict[str, Any]]],
    *,
    strategy: str,
    timeout_seconds: int,
) -> dict[str, dict[str, Any]]:
    """Turn the finished branches into the result map the strategy allows.

    The result map is built in ``port_names`` order rather than completion order.
    Every branch's outcome is read before anything is raised, so no task exception
    goes unretrieved; the exception raised is the first in ``port_names`` order.
    """
    outcomes: dict[str, BaseException | dict[str, Any]] = {
        name: _branch_outcome(
            tasks[name],
            timed_out=tasks[name] in pending,
            timeout_seconds=timeout_seconds,
        )
        for name in port_names
    }

    results: dict[str, dict[str, Any]] = {}
    first_error: Optional[BaseException] = None
    for name in port_names:
        outcome = outcomes[name]
        if not isinstance(outcome, BaseException):
            results[name] = outcome
            continue
        # fail_fast and wait_all both raise the first failing branch once every branch
        # has been read; ignore_failures records the error and continues.
        if first_error is None:
            first_error = outcome
        results[name] = {
            "artifact": None,
            "response_data": {},
            "error": str(outcome),
        }

    if strategy in ("fail_fast", "wait_all") and first_error is not None:
        raise first_error

    return results


async def invoke_port_parallel(
    executor: _OrchestrationExecutor,
    port_names: list[str],
    request_data: Optional[dict[str, Any]] = None,
    artifact: Any = None,
    artifacts: Optional[dict[str, Any]] = None,
    timeout_seconds: int = 300,
    failure_strategy: Optional[str] = None,
    **kwargs: Any,
) -> dict[str, dict[str, Any]]:
    """Handle perceivo.flow.invoke_parallel(): execute targets concurrently.

    Honors ``failure_strategy``: ``wait_all`` (the default) and ``fail_fast`` are
    fail-closed; only ``ignore_failures`` swallows branch failures.

    ``timeout_seconds`` bounds the whole call. Branches still running at the
    deadline are cancelled and recorded with the error ``"Timeout after {n}s"``,
    and the strategy then decides as for any other branch failure, so under
    ``fail_fast`` a timeout raises. A falsy ``timeout_seconds`` means no deadline.
    """
    strategy = resolve_failure_strategy(failure_strategy)
    if not port_names:
        return {}
    # The engine resolves one input for every branch: its artifact falls back to
    # the run's main, and its artifacts are built as a single invoke builds them.
    input_artifact = artifact or executor._artifacts.get("main")
    input_artifacts = dict(artifacts or {})
    if input_artifact and "main" not in input_artifacts:
        input_artifacts["main"] = input_artifact
    if not input_artifacts:
        input_artifacts = dict(executor._artifacts)

    # dict.fromkeys de-duplicates in order: one task per unique name, and each
    # duplicate reads that task's outcome.
    tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
    for port_name in dict.fromkeys(port_names):
        tasks[port_name] = asyncio.ensure_future(
            invoke_target(
                executor,
                port_name=port_name,
                request_data=request_data,
                input_artifact=input_artifact,
                input_artifacts=input_artifacts,
            )
        )

    pending = await _await_branches(set(tasks.values()), timeout_seconds)
    if pending:
        # Cancel and await the cancellations so no branch outlives the call.
        await _cancel_and_drain(pending)
        # One task per unique name, so a duplicate is counted and named once.
        timed_out = list(dict.fromkeys(name for name in port_names if tasks[name] in pending))
        logger.warning(
            "Parallel invocation timed out after %ss; cancelled %d branch(es): %s",
            timeout_seconds,
            len(timed_out),
            ", ".join(timed_out),
        )

    return _collect_branch_results(
        port_names,
        tasks,
        pending,
        strategy=strategy,
        timeout_seconds=timeout_seconds,
    )
