"""Data models for orchestration graphs: Node, Edge, and component port defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Must match the manifest's external port names: ports merge by (key, direction)
# and the platform refuses a START or END with more than one external port.
_COMPONENT_PORT_DEFAULTS = {
    "START": [
        {"name": "external_input", "port_type": "external_in"},
        {"name": "output", "port_type": "out", "base_schema": "StartOut"},
    ],
    "END": [
        {"name": "input", "port_type": "in", "base_schema": "EndIn"},
        {"name": "external_output", "port_type": "external_out"},
    ],
    "AGENT": [
        {"name": "input", "port_type": "in", "base_schema": "AgentIn"},
        {"name": "output", "port_type": "out", "base_schema": "AgentOut"},
    ],
    "TOOL": [
        {"name": "input", "port_type": "in", "base_schema": "ToolIn"},
        {"name": "output", "port_type": "out", "base_schema": "ToolOut"},
    ],
    "FSM": [
        {"name": "input", "port_type": "in", "base_schema": "FSMIn"},
        {"name": "output", "port_type": "out", "base_schema": "FSMOut"},
        {"name": "policy", "port_type": "policy"},
    ],
    "POLICY": [
        {"name": "input", "port_type": "in", "base_schema": "PolicyIn"},
        {"name": "output", "port_type": "out", "base_schema": "PolicyOut"},
    ],
    "ADAPTER": [
        {"name": "input", "port_type": "in"},
        {"name": "output", "port_type": "out"},
    ],
    # A nested orchestration has an in-port only; its mainflow successor is
    # ``mainflow_target_node_ref`` in its config.
    "ORCHESTRATION": [
        {"name": "orchestration", "port_type": "in"},
    ],
}


@dataclass
class Node:
    """A node in an orchestration graph.

    A node points to a script and manifest on disk or, for an ORCHESTRATION
    node, to a nested ``project`` directory. ``config`` is the platform node
    config (``execution_mode``, ``mainflow_target_node_ref``, ...); ``extra``
    carries every other key found in ``orchestration.yaml`` so a load/save
    round trip does not drop it. The in-memory builder also accepts a function
    reference (``func``) and optional raw source or manifest text; these are
    materialised to disk on :meth:`Orchestration.save` so a function-only graph
    still becomes a valid project directory.
    """

    id: str
    name: str
    component_type: str
    script: Optional[str] = None
    manifest: Optional[str] = None
    ports: list[dict[str, Any]] = field(default_factory=list)
    func: Optional[Callable[..., Any]] = field(default=None, repr=False)
    script_source: Optional[str] = field(default=None, repr=False)
    manifest_source: Optional[str] = field(default=None, repr=False)
    config: dict[str, Any] = field(default_factory=dict)
    project: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Runtime-only: the child an ORCHESTRATION node embeds (an Orchestration or
    # a project directory), written to ``project`` on save. Never serialized.
    embedded: Any = field(default=None, repr=False)

    def port_id(self, port_name: str) -> str:
        """Get the full port ID for a named port on this node."""
        return f"port_{self.id}_{port_name}"


@dataclass
class Edge:
    """An edge connecting two ports in an orchestration graph."""

    source_port_id: str
    target_port_id: str
    kind: str = "MAINFLOW"  # MAINFLOW or INVOKE
    extra: dict[str, Any] = field(default_factory=dict)
