"""Result types for the ``capabilities`` plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Capability", "ExecutionGraph", "ExecutionGraphSummary"]


@dataclass(frozen=True)
class Capability:
    """One active capability in the organization.

    ``organization_id`` is echoed back by the platform; the caller never
    supplies it.
    """

    #: The capability's UUID, as a string.
    id: str
    #: Display name. The route orders by it.
    name: str
    #: Owning organization's UUID, as a string.
    organization_id: str


@dataclass(frozen=True)
class ExecutionGraphSummary:
    """A pointer to a stored execution graph, not the graph itself.

    Listing refs does not open the stored graphs, so a ref is cheap and a graph
    is not. Iterate refs, then call ``capabilities.graph()`` on the ones you
    want. ``size_bytes`` and ``created_at`` can be ``None`` when storage does not
    report them.
    """

    #: Graph id, parsed from the blob filename.
    graph_id: str
    #: The capability the graph belongs to, as a string UUID.
    capability_id: str
    #: Full blob path. Pass ``graph_id`` to ``graph()`` rather than using this.
    blob_path: str
    #: Blob size. ``None`` when storage did not report one.
    size_bytes: int | None
    #: Blob creation time, falling back to last-modified, then ``None``.
    created_at: str | None


@dataclass(frozen=True)
class ExecutionGraph:
    """A stored execution graph, read back whole.

    ``entries`` is a list of dicts because its per-entry shape depends on
    ``graph_type``: full-run and fsm-transition entries share some keys and not
    others.
    """

    #: The graph's own id.
    graph_id: str
    #: Capability this graph was built for.
    capability_id: str
    #: ``"full_run"`` or ``"fsm_transition"``; selects the per-entry shape of
    #: ``entries``.
    graph_type: str
    #: The build configuration the builder was given.
    config: dict[str, Any]
    #: When the graph was built.
    created_at: str
    #: ``{"total_entries", "scored_entries", "orchestration_summary"}``.
    summary: dict[str, Any]
    #: Orchestration id to ``{"name", "orchestration_id"}``.
    orchestrations: dict[str, Any]
    #: The rows. Their shape depends on ``graph_type``.
    entries: list[dict[str, Any]]
