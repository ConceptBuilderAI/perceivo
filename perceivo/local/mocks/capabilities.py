"""Mock capability reads for ``perceivo.capabilities``, offline.

Hosted runs answer ``perceivo.capabilities.list``, ``graphs`` and ``graph``
through the extension manager's ``list_capabilities``,
``list_execution_graphs`` and ``get_execution_graph``. Locally they are served
here in the same shapes. There is no local capability store: the service is
``mock`` or ``disabled``.

Nothing is invented. With no seed the organization has no capabilities, so a
component that discovers capabilities sees an empty organization. Seed
``mocks.seed.capabilities`` to give it some:

``items`` (list of mappings)
    The active capabilities, each ``{id, name}`` and optionally
    ``organization_id``. Returned name-ordered, as the platform orders them.
``graphs`` (mapping of capability id to a list of mappings)
    The execution graphs stored for each capability. Each entry is a whole
    graph: ``graph_id`` is required; ``graph_type`` (default ``full_run``),
    ``entries``, ``config``, ``summary``, ``orchestrations`` and ``created_at``
    are optional. ``graphs()`` lists them as refs and ``graph()`` reads one back.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

#: Organization id reported on a seeded capability that names none.
_MOCK_ORGANIZATION_ID = "mock-organization"


class MockCapabilities:
    """Seedable stand-in for the platform's capability and execution-graph reads."""

    def __init__(self, seed: dict[str, Any]):
        self._seed = seed or {}

    def _items(self) -> list[dict[str, Any]]:
        raw = self._seed.get("items")
        return [item for item in raw or [] if isinstance(item, dict)]

    def _graphs_for(self, capability_id: str) -> list[dict[str, Any]]:
        table = self._seed.get("graphs")
        if not isinstance(table, dict):
            return []
        raw = table.get(str(capability_id))
        return [graph for graph in raw or [] if isinstance(graph, dict)]

    async def list_capabilities(self) -> list[dict[str, Any]]:
        """Return the seeded capabilities, name-ordered, in the platform's row shape."""
        rows = [
            {
                "id": str(item.get("id", "")),
                "name": str(item.get("name", "")),
                "organization_id": str(item.get("organization_id") or _MOCK_ORGANIZATION_ID),
            }
            for item in self._items()
        ]
        return sorted(rows, key=lambda row: row["name"])

    async def list_execution_graphs(self, capability_id: str) -> list[dict[str, Any]]:
        """Return one ref per graph seeded for *capability_id*."""
        refs: list[dict[str, Any]] = []
        for graph in self._graphs_for(capability_id):
            graph_id = str(graph.get("graph_id", ""))
            refs.append(
                {
                    "graph_id": graph_id,
                    "capability_id": str(capability_id),
                    "blob_path": f"mock/{capability_id}/{graph_id}.json",
                    "size_bytes": None,
                    "created_at": graph.get("created_at"),
                }
            )
        return refs

    async def get_execution_graph(
        self, *, graph_id: str, capability_id: str
    ) -> Optional[dict[str, Any]]:
        """Return the seeded graph *graph_id* under *capability_id*, or ``None``."""
        for graph in self._graphs_for(capability_id):
            if str(graph.get("graph_id", "")) == str(graph_id):
                return {
                    "graph_id": str(graph_id),
                    "capability_id": str(capability_id),
                    "graph_type": str(graph.get("graph_type") or "full_run"),
                    "config": copy.deepcopy(graph.get("config") or {}),
                    "created_at": str(graph.get("created_at") or ""),
                    "summary": copy.deepcopy(graph.get("summary") or {}),
                    "orchestrations": copy.deepcopy(graph.get("orchestrations") or {}),
                    "entries": copy.deepcopy(graph.get("entries") or []),
                }
        return None
