"""Component dispatch for the standalone orchestration runner."""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..types.artifact import Artifact
from ._component_kinds import COMPONENT_KIND_TYPES, execute_component_kind
from .types import ExecutionResult

logger = logging.getLogger("perceivo.runner")

# Reason used when an END declares failure but supplies none; matches the
# platform's ``error_message`` for the same case.
END_FAILURE_DEFAULT_REASON = "END component reported failure"


def _end_result(out: Any) -> ExecutionResult:
    """Translate an ``EndOutput`` into a run outcome the way the engine does.

    The platform reads ``failed`` / ``failure_reason`` off the END node and
    records the run as FAILED while keeping the output END returned.
    """
    failed = bool(getattr(out, "failed", False))
    reason = getattr(out, "failure_reason", None) or END_FAILURE_DEFAULT_REASON
    return ExecutionResult(
        # ``success`` means the node dispatched without raising. An END's failure
        # outcome is carried in ``terminal_failed``.
        success=True,
        artifact=getattr(out, "artifact", None),
        response_data=getattr(out, "response_data", {}),
        artifacts=getattr(out, "artifacts", None) or {},
        error=reason if failed else None,
        terminal_failed=failed,
        # Separate from ``error`` so a declared failure is distinguishable from a raise.
        terminal_failure_reason=reason if failed else None,
    )


async def execute_component(
    instance: Any,
    component_type: str,
    artifact: Optional[Artifact],
    request_data: dict[str, Any],
    artifacts: Optional[dict[str, Any]] = None,
) -> ExecutionResult:
    """Call the appropriate method on a component instance.

    ``artifacts`` is the run's named artifacts. Each input except a REVIEW
    node's gets its own copy, as the engine passes ``dict(self._artifacts)``, so
    a component that changes ``input.artifacts`` does not change the run's. A
    REVIEW node's input gets an empty ``artifacts``.
    """
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

    try:
        ctype = component_type.upper().strip()

        if ctype in COMPONENT_KIND_TYPES:
            # The engine refuses a node of these types, so no engine result says what
            # one leaves the run holding. Locally the node passes the run's artifact
            # and artifacts through unchanged, so a graph holding one still runs.
            result = await execute_component_kind(instance, ctype, request_data)
            result.artifact = artifact
            result.artifacts = dict(artifacts or {})
            return result

        if ctype == "REVIEW":
            return await _execute_review(
                instance, artifact, request_data, ReviewInput, dict(artifacts or {})
            )

        inp: Any
        if ctype == "START":
            inp = StartInput(
                artifact=artifact,
                request_data=request_data,
                artifacts=dict(artifacts or {}),
            )
            out = (
                await instance.start(inp)
                if hasattr(instance, "start")
                else await instance._start(inp)
            )
            return ExecutionResult(
                success=True,
                artifact=getattr(out, "artifact", None),
                response_data=getattr(out, "response_data", {}),
                artifacts=getattr(out, "artifacts", None) or {},
            )

        elif ctype == "END":
            inp = EndInput(
                artifact=artifact,
                response_data=request_data,
                artifacts=dict(artifacts or {}),
            )
            out = await instance.end(inp) if hasattr(instance, "end") else await instance._end(inp)
            return _end_result(out)

        elif ctype == "AGENT":
            inp = AgentInput(
                artifact=artifact,
                request_data=request_data,
                artifacts=dict(artifacts or {}),
            )
            out = await instance.run(inp)
            return ExecutionResult(
                success=True,
                artifact=getattr(out, "artifact", None),
                response_data=getattr(out, "response_data", {}),
                artifacts=getattr(out, "artifacts", {}),
                conversation_log=getattr(out, "conversation_log", None),
            )

        elif ctype == "TOOL":
            inp = ToolInput(
                artifact=artifact,
                request_data=request_data,
                artifacts=dict(artifacts or {}),
            )
            out = await instance.run(inp)
            return ExecutionResult(
                success=True,
                artifact=getattr(out, "artifact", None),
                response_data=getattr(out, "response_data", {}),
                artifacts=getattr(out, "artifacts", None) or {},
            )

        elif ctype in ("FSM", "POLICY", "ADAPTER"):
            input_class = {
                "FSM": FsmInput,
                "POLICY": PolicyInput,
                "ADAPTER": AdapterInput,
            }[ctype]

            inp = input_class(
                artifact=artifact,
                request_data=request_data,
                artifacts=dict(artifacts or {}),
            )
            out = await instance.run(inp)
            return ExecutionResult(
                success=True,
                artifact=getattr(out, "artifact", None),
                response_data=getattr(out, "response_data", {}),
                artifacts=getattr(out, "artifacts", None) or {},
            )

        else:
            return ExecutionResult(
                success=False,
                error=f"Unsupported component type: {component_type}",
            )

    except Exception as exc:
        # Imported here: _nested imports this module.
        from ._nested import UnreadableChildError

        if isinstance(exc, UnreadableChildError):
            # A child reached through a port is refused like one on the mainflow.
            raise
        logger.exception("Component execution error")
        return ExecutionResult(success=False, error=str(exc))


# No human is present locally, so a REVIEW node auto-approves with the decision
# keys review consumers read.
_AUTO_REVIEW_DECISION: dict[str, Any] = {
    "approved": True,
    "decision": "approved",
    "rating": 5,
    "review_completed": True,
    "review_status": "completed",
    "review_required": True,
}


async def _execute_review(
    instance: Any,
    artifact: Optional[Artifact],
    request_data: dict[str, Any],
    review_input_cls: Any,
    artifacts: dict[str, Any],
) -> ExecutionResult:
    """Execute a REVIEW node by auto-approving (no human is present locally).

    Runs a :class:`perceivo.primitives.Review` component's ``run`` (or a
    decorated ``@review`` function) and merges its response data over the fixed
    approval above, so a key the component returns wins. The input ``artifact``
    and the run's ``artifacts`` are returned unchanged, as the engine's REVIEW
    handler returns them.
    """
    out = None
    inp = review_input_cls(artifact=artifact, request_data=request_data, artifacts={})
    entry = getattr(instance, "review", None) or getattr(instance, "run", None)
    if entry is not None:
        try:
            out = await entry(inp)
        except Exception as exc:
            # Imported here: _nested imports this module.
            from ._nested import UnreadableChildError

            if isinstance(exc, UnreadableChildError):
                raise
            logger.debug("REVIEW component entry raised (ignored locally): %s", exc)
    component_data = getattr(out, "response_data", None) or {}
    response_data = {**_AUTO_REVIEW_DECISION, **component_data}
    return ExecutionResult(
        success=True,
        artifact=artifact,
        response_data=response_data,
        artifacts=artifacts,
    )


def apply_result_artifacts(store: dict[str, Any], result: dict[str, Any]) -> None:
    """Replace ``store`` with what a node's ``result`` leaves the run holding.

    As the engine applies a result: the node's named artifacts, else its artifact
    as ``"main"``, else nothing when it returned no artifact. A result without an
    ``artifact`` key leaves the store as it is. ``store`` is changed in place
    because the run shares it by reference.
    """
    if result.get("artifacts"):
        replacement = dict(result["artifacts"])
    elif result.get("artifact"):
        replacement = {"main": result["artifact"]}
    elif "artifact" in result:
        replacement = {}
    else:
        return
    store.clear()
    store.update(replacement)
