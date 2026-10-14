"""Nested ORCHESTRATION node execution for the local runner.

Supports the platform's two execution modes:

``subprocess`` (default)
    The child runs START, its mainflow and END with the upstream node's
    ``response_data`` as its request, and the parent receives END's output with
    the artifact merged into the response.
``inline``
    The child's START and END are stripped and its body spliced into the
    parent, so the upstream artifact and request go straight to the child's
    single ENTRY node and the child's single EXIT node's output flows on to the
    ORCHESTRATION node's own mainflow target.

Both modes run with the child's own invoke wiring swapped in and swapped back,
and never activate or deactivate the extension manager: the parent run owns it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..types.artifact import Artifact
from ..types.errors import ConfigurationError
from ..validator._orchestration._loader import orchestration_unreadable
from ..validator._orchestration._nested import nested_prefix
from ..validator._orchestration._unreadable import load_failure_check
from ._component_execution import apply_result_artifacts
from .compiler import _CONFIG_TARGET_KEYS, _load_orchestration_file, compile_orchestration
from .types import NodeShell, NodeTrace

if TYPE_CHECKING:
    from .executor import _OrchestrationExecutor

logger = logging.getLogger("perceivo.runner")

DEFAULT_EXECUTION_MODE = "subprocess"
INLINE_EXECUTION_MODE = "inline"

# Maximum inline-within-inline nesting depth.
MAX_INLINE_DEPTH = 5

# Separator for namespaced child node ids: "{orchNodeId}:{childId}".
NS_SEP = ":"


class UnreadableChildError(ConfigurationError):
    """A child this run reached has an ``orchestration.yaml`` export cannot read.

    Its message is the error validation gives for the child, so the run stops
    there and the caller reports it as a refusal, not a failed node.
    """


def _load_child(project: Path) -> dict[str, Any]:
    if orchestration_unreadable(str(project)) is not None:
        raise UnreadableChildError(load_failure_check(str(project.resolve())).message)
    return _load_orchestration_file(project)


def merge_artifact_into_response(response_data: dict[str, Any], artifact: Any) -> None:
    """Surface the artifact's content when response_data holds only token metadata."""
    data = getattr(artifact, "data", None)
    if not data:
        return
    if any(k not in ("tokens_in", "tokens_out") for k in response_data):
        return
    if isinstance(data, str):
        response_data["response"] = data
    elif isinstance(data, dict):
        for key, value in data.items():
            response_data.setdefault(key, value)


def _config_target(node: Mapping[str, Any], node_ids: set[str]) -> str | None:
    config = node.get("config")
    if not isinstance(config, dict):
        return None
    for key in _CONFIG_TARGET_KEYS:
        raw = config.get(key)
        if raw is not None and raw != "":
            text = str(raw)
            return text if text in node_ids else f"node_{text}"
    return None


def _single_endpoint(candidates: set[str], removed: set[str], orch_node_id: str) -> str:
    """Return the single real (non-START/END) mainflow endpoint, or raise."""
    if not candidates:
        raise ConfigurationError(
            f"Inline orchestration node {orch_node_id}: child START/END are not "
            f"connected to real nodes via MAINFLOW edges or config"
        )
    if len(candidates) > 1:
        raise ConfigurationError(
            f"Inline orchestration node {orch_node_id}: inline child has multiple "
            f"entry/exit mainflow nodes; not supported"
        )
    node = next(iter(candidates))
    if node in removed:
        raise ConfigurationError(
            f"Inline orchestration node {orch_node_id}: inline child has no real "
            f"node between START and END"
        )
    return node


def inline_endpoints(graph: Mapping[str, Any], orch_node_id: str) -> tuple[str, str]:
    """Return the child's ``(entry, exit)`` node ids.

    Computed from the declared graph, not the compiled plan: the compiler wires
    every successor-less node to END, which would make an invoke-only tool look
    like a second exit. ENTRY is the union of START's MAINFLOW edge targets and
    START's config target; EXIT is the union of MAINFLOW edge sources into END
    and nodes whose config target is END.
    """
    nodes = [n for n in graph.get("nodes") or [] if isinstance(n, Mapping)]
    node_ids = {str(n.get("id")) for n in nodes}
    kinds = {str(n.get("id")): str(n.get("component_type", "")).upper() for n in nodes}
    start_ids = {nid for nid, kind in kinds.items() if kind == "START"}
    end_ids = {nid for nid, kind in kinds.items() if kind == "END"}
    if not start_ids or not end_ids:
        raise ConfigurationError(
            f"Inline orchestration node {orch_node_id}: child bundle is missing a "
            f"START and/or END node to flatten around"
        )
    port_node = {str(p.get("id")): str(p.get("node_id")) for p in graph.get("ports") or []}
    hops = [
        (port_node.get(str(e.get("source_port_id"))), port_node.get(str(e.get("target_port_id"))))
        for e in graph.get("edges") or []
        if str(e.get("kind", e.get("edge_kind", "MAINFLOW"))).upper() == "MAINFLOW"
    ]
    for node in nodes:
        target = _config_target(node, node_ids)
        if target:
            hops.append((str(node.get("id")), target))

    removed = start_ids | end_ids
    entries = {tgt for src, tgt in hops if src in start_ids and tgt}
    exits = {src for src, tgt in hops if tgt in end_ids and src}
    return (
        _single_endpoint(entries, removed, orch_node_id),
        _single_endpoint(exits, removed, orch_node_id),
    )


async def execute_nested(
    parent: _OrchestrationExecutor,
    shell: NodeShell,
    artifact: Optional[Artifact],
    request_data: dict[str, Any],
) -> dict[str, Any]:
    """Run an ORCHESTRATION node's child and record it in the parent trace."""
    from .executor import _OrchestrationExecutor

    mode = str((shell.config or {}).get("execution_mode") or DEFAULT_EXECUTION_MODE)
    child: _OrchestrationExecutor | None = None
    output: dict[str, Any] = {}
    error: str | None = None
    try:
        depth = parent._inline_depth + 1 if mode == INLINE_EXECUTION_MODE else 0
        if depth > MAX_INLINE_DEPTH:
            raise ConfigurationError(
                f"Inline orchestration nesting exceeds maximum depth {MAX_INLINE_DEPTH}"
            )
        if not shell.project_path:
            raise RuntimeError(f"Nested orchestration {shell.name} has no valid project path.")
        project = Path(shell.project_path)
        graph = _load_child(project)
        child = _OrchestrationExecutor(compile_orchestration(graph, base_dir=project), parent._ext)
        child._inline_depth = depth
        tokens = child._activate_callbacks()
        try:
            if mode == INLINE_EXECUTION_MODE:
                output = await _run_inline(parent, child, graph, shell, artifact, request_data)
            else:
                output = await _run_subprocess(parent, child, shell, artifact, request_data)
        finally:
            parent._ext.reset_invoke_port_parallel_callback(tokens[1])
            parent._ext.reset_invoke_port_callback(tokens[0])
        return output
    except UnreadableChildError as exc:
        # Each nested level adds its node, outermost first, as validation does.
        refusal = UnreadableChildError(nested_prefix(shell.name or shell.node_id) + str(exc))
        error = str(refusal)
        raise refusal from None
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        _record_trace(parent, shell, child, request_data, output, error)


async def _run_subprocess(
    parent: _OrchestrationExecutor,
    child: _OrchestrationExecutor,
    shell: NodeShell,
    artifact: Optional[Artifact],
    request_data: dict[str, Any],
) -> dict[str, Any]:
    """Run the whole child as a subprocess orchestration.

    The child's artifacts are seeded with ``"main"`` when the node has an
    artifact. A child END that reports
    failure does not fail the node: parent and child share one terminal outcome,
    so the child's END outcome is recorded and the parent's own END later
    overwrites it. The last END wins.

    A child END that returns no artifact does not drop the child's artifact: the
    node hands up the artifact the child held once its START had run, not the one
    its mainflow passed to END. Only a top-level END's output is authoritative.
    """
    result = await child._execute_active(request_data, artifact, subprocess_child=True)
    if not result.success and not result.terminal_failed:
        raise RuntimeError(f"Nested orchestration {shell.name} failed: {result.error}")
    if child._end_verdict_recorded:
        parent._record_terminal_failure(result)
    response_data = dict(result.response_data or {})
    out_artifact = result.artifact or child._start_artifact
    merge_artifact_into_response(response_data, out_artifact)
    return {
        "artifact": out_artifact,
        "response_data": response_data,
        "artifacts": result.artifacts,
        "conversation_log": result.conversation_log,
    }


async def _run_inline(
    parent: _OrchestrationExecutor,
    child: _OrchestrationExecutor,
    graph: Mapping[str, Any],
    shell: NodeShell,
    artifact: Optional[Artifact],
    request_data: dict[str, Any],
) -> dict[str, Any]:
    entry_id, exit_id = inline_endpoints(graph, shell.node_id)
    load_error = child._load_all_instances()
    if load_error is not None:
        raise ConfigurationError(f"Nested orchestration {shell.name} failed: {load_error.error}")
    plan = child._plan
    # The engine splices an inline body into the parent's mainflow, so its nodes
    # read and replace the parent's artifacts.
    artifacts = child._artifacts = dict(parent._artifacts)
    last: dict[str, Any] = {"artifact": artifact, "response_data": request_data}
    current: str | None = entry_id
    while current and current not in (plan.end_node_id, plan.start_node_id):
        last = await child._execute_node(current, artifact, request_data, artifacts)
        child._node_outputs[current] = last
        if "artifact" in last:
            artifact = last["artifact"]
        apply_result_artifacts(artifacts, last)
        # The engine hands on the previous response data when a node returns none.
        if last.get("response_data"):
            request_data = last["response_data"]
        if current == exit_id:
            break
        successors = plan.shells_by_id[current].mainflow_successors
        current = successors[0] if successors else None
    return {
        "artifact": artifact,
        "response_data": request_data,
        "artifacts": artifacts,
        "conversation_log": last.get("conversation_log"),
    }


def _record_trace(
    parent: _OrchestrationExecutor,
    shell: NodeShell,
    child: _OrchestrationExecutor | None,
    request_data: dict[str, Any],
    output: dict[str, Any],
    error: str | None,
) -> None:
    """Append the child's node traces, namespaced, then the nested node's own."""
    for entry in child._trace if child is not None else []:
        entry.node = f"{shell.name}{NS_SEP}{entry.node}"
        parent._trace.append(entry)
    parent._trace.append(
        NodeTrace(
            node=shell.name,
            component_type=shell.component_type,
            status="failed" if error is not None else "ok",
            input_keys=sorted(request_data.keys()),
            output_keys=sorted((output.get("response_data") or {}).keys()),
            error=error,
        )
    )
