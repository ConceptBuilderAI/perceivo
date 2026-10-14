"""Capability discovery and execution-graph reads.

Three verbs that answer three questions in order: what capabilities exist, what
execution graphs has this one produced, and what is in that graph.

None of them takes an organization argument: the runtime fills the
organization from the execution context.
"""

from __future__ import annotations

import builtins

from .._internal.context import _ExecutionContext
from ..types.errors import NotFoundError
from ..types.records import _from_wire
from ._types import Capability, ExecutionGraph, ExecutionGraphSummary


async def list() -> builtins.list[Capability]:
    """List the organization's active capabilities.

    Returns:
        One :class:`~perceivo.capabilities._types.Capability` per active
        capability, name-ordered. Inactive and deleted capabilities are not
        returned, so an id from this list is safe to pass to
        :func:`graphs`.

    Raises:
        RuntimeError: If called outside component execution context.

    Example::

        for cap in await perceivo.capabilities.list():
            for ref in await perceivo.capabilities.graphs(cap.id):
                graph = await perceivo.capabilities.graph(
                    ref.graph_id, capability_id=cap.id
                )
                ...
    """
    ext = _ExecutionContext.get_ext()
    return [_from_wire(Capability, c) for c in await ext.list_capabilities()]


async def graphs(capability_id: str) -> builtins.list[ExecutionGraphSummary]:
    """List the execution graphs stored for one capability.

    Returns refs only, without opening the graphs. Call :func:`graph` on the
    ones you want.

    Args:
        capability_id: UUID of the capability, as a string.

    Returns:
        One :class:`~perceivo.capabilities._types.ExecutionGraphSummary` per
        stored graph. Empty when the capability has produced none or the
        organization has no storage container yet.

    Raises:
        RuntimeError: If called outside component execution context.
    """
    ext = _ExecutionContext.get_ext()
    return [
        _from_wire(ExecutionGraphSummary, g) for g in await ext.list_execution_graphs(capability_id)
    ]


async def graph(graph_id: str, *, capability_id: str) -> ExecutionGraph:
    """Read one stored execution graph.

    ``capability_id`` is keyword-only and required: a graph is addressed by
    capability and id, and both are UUID strings that would be easy to swap.

    Args:
        graph_id: The graph's id, from an
            :class:`~perceivo.capabilities._types.ExecutionGraphSummary`.
        capability_id: The capability that produced it.

    Returns:
        An :class:`~perceivo.capabilities._types.ExecutionGraph`. Read
        ``graph_type`` before interpreting ``entries``: the per-entry shape
        differs between the two graph types.

    Raises:
        RuntimeError: If called outside component execution context.
        NotFoundError: If no graph with that id exists under that capability.
    """
    ext = _ExecutionContext.get_ext()
    raw = await ext.get_execution_graph(graph_id=graph_id, capability_id=capability_id)
    if raw is None:
        raise NotFoundError(f"No execution graph {graph_id!r} under capability {capability_id!r}")
    return _from_wire(ExecutionGraph, raw)
