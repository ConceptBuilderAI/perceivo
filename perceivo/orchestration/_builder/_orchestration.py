"""The Orchestration class: programmatic orchestration construction and management."""

from __future__ import annotations

import contextlib
import inspect
import logging
import textwrap
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from ...runner.events import ExecutionEvent

from ..._internal.home import session_tmp as _session_tmp
from ...types.errors import ConfigurationError
from . import _graph_edit
from ._models import _COMPONENT_PORT_DEFAULTS, Edge, Node
from ._serialization import (
    orchestration_to_dict,
    orchestration_to_yaml,
    render_default_manifest,
    save_orchestration,
)

logger = logging.getLogger("perceivo.orchestration")

_INVOKE_PORT_TYPES = frozenset({"tool", "policy", "review"})
_OUT_PORT_TYPES = frozenset({"out", "external_out"})
_IN_PORT_TYPES = frozenset({"in", "external_in"})


class Orchestration:
    """Programmatic orchestration builder.

    Build orchestration graphs in Python, then save to YAML, validate, or run.

    Example::

        orch = Orchestration("my_pipeline")
        start = orch.add_node("start", "START", script="start.py")
        agent = orch.add_node("agent", "AGENT", script="agent.py")
        end = orch.add_node("end", "END", script="end.py")
        orch.add_edge(start, agent)
        orch.add_edge(agent, end)
        orch.save("./my-pipeline/")
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        project_dir: Optional[str] = None,
    ):
        self.name = name
        self.description = description
        self.project_dir = project_dir
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._port_registry: dict[str, str] = {}  # port_id -> node_id
        # Top-level orchestration.yaml keys the builder does not model
        # (``format``, ``runtime_config``, ...), preserved across load/save.
        self.extra: dict[str, Any] = {}

    def add_node(
        self,
        name: str,
        component_type: str,
        *,
        script: Optional[str | Callable[..., Any]] = None,
        manifest: Optional[str] = None,
        node_id: Optional[str] = None,
        extra_ports: Optional[list[dict[str, Any]]] = None,
        ports: Optional[list[dict[str, Any]]] = None,
        config: Optional[dict[str, Any]] = None,
        project: Optional[str] = None,
    ) -> Node:
        """Add a component node to the orchestration.

        ``script`` accepts either a path string or a callable. When a
        callable is passed, its source is captured via
        :func:`inspect.getsource` and materialised to disk on
        :meth:`save`. If ``manifest`` is also omitted a minimal manifest
        is auto-generated from the component type's default ports.

        Args:
            name: Node name (used for display and file references).
            component_type: Component type (START, AGENT, TOOL, END, FSM,
                POLICY, ADAPTER).
            script: Path to the component script *or* a callable whose
                source should be used as the script body.
            manifest: Path to the manifest YAML (relative to project dir).
            node_id: Optional explicit node ID (auto-generated if omitted).
            extra_ports: Additional ports beyond the defaults for this type.
            ports: The node's complete port list, *replacing* the defaults for
                this type. Use it when a default port cannot legally be wired:
                an INVOKE target returns through the same ``in`` port it was
                called on, so a TOOL used only as a tool has no ``out`` port to
                connect and ``validate()`` reports it unconnected. Mutually
                exclusive with ``extra_ports``.
            config: The platform node config, e.g. ``{"execution_mode":
                "inline", "mainflow_target_node_ref": "node_end"}``. An
                ORCHESTRATION node has no out-port; its mainflow successor is
                the config's ``mainflow_target_node_ref``.
            project: For an ORCHESTRATION node, the nested project directory,
                relative to this orchestration's project directory.

        Returns:
            The created :class:`Node`.

        Raises:
            TypeError: If both ``ports`` and ``extra_ports`` are given.
            ValueError: If a node with the same ID already exists, or if
                ``script`` is a callable but its source cannot be captured.
        """
        ctype = component_type.upper().strip()
        nid = node_id or f"node_{name}"

        if nid in self.nodes:
            raise ValueError(f"Node '{nid}' already exists.")

        ports = _node_ports(ctype, ports, extra_ports)

        for port in ports:
            port_id = f"port_{nid}_{port['name']}"
            port["id"] = port_id
            port["node_id"] = nid
            self._port_registry[port_id] = nid

        func: Optional[Callable[..., Any]] = None
        script_source: Optional[str] = None
        script_path: Optional[str] = None
        manifest_source: Optional[str] = None
        manifest_path: Optional[str] = manifest

        if callable(script):
            func = script
            script_source = _callable_source(name, func)
            script_path = f"{nid}.py"
            if manifest_path is None:
                manifest_source = render_default_manifest(name, ctype, ports)
                manifest_path = f"{nid}.yaml"
        else:
            script_path = script

        node = Node(
            id=nid,
            name=name,
            component_type=ctype,
            script=script_path,
            manifest=manifest_path,
            ports=ports,
            func=func,
            script_source=script_source,
            manifest_source=manifest_source,
            config=dict(config or {}),
            project=project,
        )
        self.nodes[nid] = node
        logger.debug("Added node: %s (%s)", name, ctype)
        return node

    def add_edge(
        self,
        source: Node,
        target: Node,
        *,
        source_port: Optional[str] = None,
        target_port: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> Edge:
        """Add an edge between two nodes, inferring its kind from the port types.

        When ``source_port`` names a tool/policy/review port the edge is
        treated as INVOKE; otherwise it defaults to MAINFLOW. Pass ``kind``
        explicitly to override.

        Default ports:
            - source: first ``out`` / ``external_out`` port, so an inferred
              edge is MAINFLOW; with ``kind="INVOKE"`` the edge is INVOKE from
              that port
            - target: first ``in`` / ``external_in`` port

        Args:
            source: Source node.
            target: Target node.
            source_port: Source port name. If omitted, the first ``out`` /
                ``external_out`` port is used, whatever ``kind`` is.
            target_port: Target port name (defaults to first IN port).
            kind: Override edge kind, ``"MAINFLOW"`` or ``"INVOKE"``. When
                omitted, kind is inferred from the source port's ``port_type``.

        Returns:
            The created :class:`Edge`.
        """
        src_port = self._resolve_source_port(source, source_port)
        inferred_kind = "INVOKE" if src_port["port_type"] in _INVOKE_PORT_TYPES else "MAINFLOW"
        edge_kind = (kind or inferred_kind).upper().strip()

        tgt_port = self._resolve_target_port(target, target_port)

        edge = Edge(
            source_port_id=src_port["id"],
            target_port_id=tgt_port["id"],
            kind=edge_kind,
        )
        self.edges.append(edge)
        logger.debug(
            "add_edge: %s.%s -> %s.%s (%s)",
            source.name,
            src_port["name"],
            target.name,
            tgt_port["name"],
            edge_kind,
        )
        return edge

    def remove_node(self, node: Node | str) -> Node:
        """Remove a node with its edges, port registrations and inbound config hops."""
        return _graph_edit.remove_node(self, node)

    def remove_edge(self, edge: Optional[Edge] = None, **filters: Any) -> list[Edge]:
        """Remove ``edge``, or every edge matching ``source`` / ``target`` /
        ``source_port`` / ``target_port`` / ``kind``; return the removed edges."""
        return _graph_edit.remove_edge(self, edge, **filters)

    def add_orchestration_node(
        self, name: str, child: Orchestration | str | Path, **options: Any
    ) -> Node:
        """Embed ``child`` (an Orchestration or a project directory) as an ORCHESTRATION node.

        The child is written to ``nested/<dir>`` under this project on
        :meth:`save`. Options: ``node_id``, ``execution_mode`` (``"subprocess"``
        default, or ``"inline"``), ``mainflow_target`` (the node the child hands
        on to), ``config`` and ``dirname`` (default: the node name, short).
        """
        return _graph_edit.add_orchestration_node(self, name, child, **options)

    def add_library_endpoints(self, **options: Any) -> tuple[Node, Node]:
        """Add the platform library's generic START / END where missing.

        With ``connect=True`` (default) START feeds every node without an
        inbound mainflow and every node without an outbound one feeds END.
        """
        return _graph_edit.add_library_endpoints(self, **options)

    @staticmethod
    def _resolve_source_port(source: Node, source_port: Optional[str]) -> dict[str, Any]:
        if source_port:
            for p in source.ports:
                if p["name"] == source_port:
                    return p
            raise ValueError(f"Node '{source.name}' has no port named '{source_port}'.")
        out_ports = [p for p in source.ports if p["port_type"] in _OUT_PORT_TYPES]
        if not out_ports and source.component_type == "ORCHESTRATION":
            raise ConfigurationError(
                f"Node '{source.name}' is an ORCHESTRATION node and has no OUT port: "
                "declare its mainflow successor in its config instead, "
                "config={'mainflow_target_node_ref': '<target node id>'}."
            )
        if not out_ports:
            raise ValueError(f"Node '{source.name}' has no OUT ports.")
        return out_ports[0]

    @staticmethod
    def _resolve_target_port(target: Node, target_port: Optional[str]) -> dict[str, Any]:
        if target_port:
            for p in target.ports:
                if p["name"] == target_port:
                    return p
            raise ValueError(f"Node '{target.name}' has no port named '{target_port}'.")
        in_ports = [p for p in target.ports if p["port_type"] in _IN_PORT_TYPES]
        if not in_ports:
            raise ValueError(f"Node '{target.name}' has no IN ports.")
        return in_ports[0]

    def to_yaml(self) -> str:
        """Serialize the orchestration to YAML format."""
        return orchestration_to_yaml(self)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (runtime-only fields skipped)."""
        return orchestration_to_dict(self)

    def save(self, directory: str) -> Path:
        """Save orchestration.yaml plus any captured scripts/manifests.

        Args:
            directory: Output directory (created if needed).

        Returns:
            Path to the orchestration.yaml file.
        """
        orch_file = save_orchestration(self, directory)
        logger.info("Saved orchestration to %s", orch_file)
        return orch_file

    def validate(self) -> Any:
        """Validate this orchestration.

        Saves to a temporary directory if no project_dir is set, then
        runs the orchestration validator.
        """
        from ...validator import validate_orchestration

        if self.project_dir:
            return validate_orchestration(self.project_dir)

        with _session_tmp("perceivo_validate_") as tmpdir:
            self.save(str(tmpdir))
            return validate_orchestration(str(tmpdir))

    def _auto_inject_endpoints(self) -> None:
        """Ensure the graph has a START and END node, wiring them into the chain.

        Mutates the orchestration in place. Only called from
        :meth:`run` / :meth:`run_stream` when the user assembled a graph
        without explicit START/END.
        """
        has_start = any(n.component_type == "START" for n in self.nodes.values())
        has_end = any(n.component_type == "END" for n in self.nodes.values())
        if has_start and has_end:
            return

        nodes_with_edges_in, nodes_with_edges_out = self._mainflow_endpoints()
        ordered_ids = list(self.nodes.keys())

        if not has_start:
            self._inject_start(ordered_ids, nodes_with_edges_in)
        if not has_end:
            self._inject_end(ordered_ids, nodes_with_edges_out)

    def _mainflow_endpoints(self) -> tuple[set[str], set[str]]:
        """Return (nodes with an inbound edge, nodes with an outbound edge).

        MAINFLOW edges and config-declared ``mainflow_target_node_ref`` hops
        are considered.
        """
        nodes_with_edges_in: set[str] = set()
        nodes_with_edges_out: set[str] = set()
        for edge in self.edges:
            if edge.kind != "MAINFLOW":
                continue
            src_node = self._port_registry.get(edge.source_port_id)
            tgt_node = self._port_registry.get(edge.target_port_id)
            if src_node:
                nodes_with_edges_out.add(src_node)
            if tgt_node:
                nodes_with_edges_in.add(tgt_node)
        for node in self.nodes.values():
            target = node.config.get("mainflow_target_node_ref")
            if target:
                nodes_with_edges_out.add(node.id)
                nodes_with_edges_in.add(str(target))
        return nodes_with_edges_in, nodes_with_edges_out

    def _inject_start(self, ordered_ids: list[str], nodes_with_edges_in: set[str]) -> None:
        """Add an auto START node wired to every root (no inbound MAINFLOW)."""
        roots = [
            nid
            for nid in ordered_ids
            if nid not in nodes_with_edges_in
            and self.nodes[nid].component_type not in ("START", "END")
        ]
        start = self.add_node("auto_start", "START", node_id="auto_start")
        for root_id in roots:
            self.add_edge(start, self.nodes[root_id])

    def _inject_end(self, ordered_ids: list[str], nodes_with_edges_out: set[str]) -> None:
        """Add an auto END node wired from every leaf (no outbound MAINFLOW)."""
        leaves = [
            nid
            for nid in ordered_ids
            if nid not in nodes_with_edges_out
            and self.nodes[nid].component_type not in ("START", "END")
        ]
        end = self.add_node("auto_end", "END", node_id="auto_end")
        for leaf_id in leaves:
            self.add_edge(self.nodes[leaf_id], end)

    @contextlib.contextmanager
    def _runnable_project_dir(self) -> Iterator[str]:
        """Yield a project_dir path, materialising the graph to session scratch if needed.

        An unsaved graph is written under ``<home>/tmp/<session>/`` for the run
        and removed when the run returns, including when it raises.
        """
        if self.project_dir:
            yield self.project_dir
            return
        self._auto_inject_endpoints()
        with _session_tmp("perceivo_inmem_") as tmp:
            self.save(str(tmp))
            yield str(tmp)

    async def run(
        self,
        *,
        input_data: Optional[dict[str, Any]] = None,
        config_path: Optional[str] = None,
    ) -> Any:
        """Run this orchestration locally.

        When the orchestration has not been ``.save()``d to disk yet, the
        graph is materialised to a temporary directory and any missing
        START / END nodes are auto-injected.
        """
        from ...runner import run_orchestration

        with self._runnable_project_dir() as project_dir:
            return await run_orchestration(
                project_dir,
                input_data=input_data,
                config_path=config_path,
            )

    async def run_stream(
        self,
        *,
        input_data: Optional[dict[str, Any]] = None,
        config_path: Optional[str] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        """Run this orchestration and stream execution events."""
        from ...runner import run_stream as _run_stream

        with self._runnable_project_dir() as project_dir:
            async for event in _run_stream(
                project_dir,
                input_data=input_data,
                config_path=config_path,
            ):
                yield event

    def __repr__(self) -> str:
        return (
            f"Orchestration(name={self.name!r}, nodes={len(self.nodes)}, edges={len(self.edges)})"
        )


def _node_ports(
    ctype: str,
    ports: Optional[list[dict[str, Any]]],
    extra_ports: Optional[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """A new node's port list: *ports* verbatim, else the type defaults + *extra_ports*."""
    if ports is not None and extra_ports:
        raise TypeError(
            "add_node: pass either 'ports' (replaces the type defaults) or "
            "'extra_ports' (adds to them), not both."
        )
    if ports is not None:
        return [dict(p) for p in ports]
    node_ports = [dict(p) for p in _COMPONENT_PORT_DEFAULTS.get(ctype, [])]
    if extra_ports:
        node_ports.extend(dict(p) for p in extra_ports)
    return node_ports


def _callable_source(name: str, func: Callable[..., Any]) -> str:
    """The dedented source of a function-ref node's callable."""
    try:
        return textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError) as exc:
        raise ValueError(
            f"add_node: cannot capture source of callable '{name}'. "
            "Function-ref nodes must be defined at module top-level "
            "or in a notebook."
        ) from exc
