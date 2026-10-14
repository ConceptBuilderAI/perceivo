"""Edit an :class:`Orchestration` graph: remove nodes and edges, embed a child.

* :func:`remove_node` drops a node together with its edges, its port
  registrations and any ``mainflow_target_node_ref`` that pointed at it, so no
  dangling reference survives into ``orchestration.yaml``;
* :func:`remove_edge` drops edges by identity or by endpoint;
* :func:`add_orchestration_node` adds an ORCHESTRATION node whose child is an
  :class:`Orchestration` object or an existing project directory. The child is
  written to ``nested/<dir>`` beside the parent on ``save()``;
* :func:`add_library_endpoints` gives a new child the platform library's
  generic START / END (the ``generic-start`` / ``generic-end`` templates), so
  the child exports with its template links intact.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..._bundle.schema import NestedOrchestrationError
from ..._internal.local_state import ignore_local_state
from ...types.errors import ConfigurationError, NotFoundError
from ._models import Edge, Node

if TYPE_CHECKING:
    from ._orchestration import Orchestration


class NodeNotFoundError(NotFoundError, KeyError):
    """The orchestration has no node with that id."""


class GraphEditError(ConfigurationError, ValueError):
    """A graph edit names nothing the orchestration holds."""


# The runner's two execution modes.
EXECUTION_MODES = frozenset({"subprocess", "inline"})
NESTED_DIR = "nested"
# Nested projects add a level per depth; a short directory keeps a deep tree
# under the Windows MAX_PATH limit.
_DIR_MAX = 24
# ASCII: \W is Unicode-aware on str patterns; directories stay [A-Za-z0-9_-].
_DIR_UNSAFE = re.compile(r"\W+", re.ASCII)

_LIBRARY_PORTS: dict[str, list[dict[str, Any]]] = {
    "generic-start": [
        {"name": "external_input", "port_type": "external_in"},
        {"name": "output", "port_type": "out"},
    ],
    "generic-end": [
        {"name": "input", "port_type": "in"},
        {"name": "external_output", "port_type": "external_out"},
    ],
}


def _node(orch: Orchestration, node: Node | str) -> Node:
    nid = node.id if isinstance(node, Node) else str(node)
    if nid not in orch.nodes:
        raise NodeNotFoundError(f"No node '{nid}' in orchestration '{orch.name}'.")
    return orch.nodes[nid]


def remove_node(orch: Orchestration, node: Node | str) -> Node:
    """Remove *node*, its edges and port registrations; return the removed node."""
    target = _node(orch, node)
    del orch.nodes[target.id]
    owned = {pid for pid, owner in orch._port_registry.items() if owner == target.id}
    for pid in owned:
        del orch._port_registry[pid]
    orch.edges = [
        e for e in orch.edges if e.source_port_id not in owned and e.target_port_id not in owned
    ]
    for other in orch.nodes.values():
        if other.config.get("mainflow_target_node_ref") == target.id:
            del other.config["mainflow_target_node_ref"]
    return target


def remove_edge(
    orch: Orchestration,
    edge: Optional[Edge] = None,
    *,
    source: Node | str | None = None,
    target: Node | str | None = None,
    source_port: Optional[str] = None,
    target_port: Optional[str] = None,
    kind: Optional[str] = None,
) -> list[Edge]:
    """Remove *edge*, or every edge matching the given endpoints; return them.

    Raises:
        TypeError: If neither an edge nor a filter is given, or both are.
        ValueError: If nothing matches.
    """
    filtered = any(v is not None for v in (source, target, source_port, target_port, kind))
    if (edge is None) == (not filtered):
        raise TypeError("remove_edge: pass either an Edge or endpoint filters, not both/neither.")

    def matches(e: Edge) -> bool:
        if edge is not None:
            return e is edge
        src = orch._port_registry.get(e.source_port_id)
        tgt = orch._port_registry.get(e.target_port_id)
        checks = (
            source is None or src == _node(orch, source).id,
            target is None or tgt == _node(orch, target).id,
            source_port is None or e.source_port_id == f"port_{src}_{source_port}",
            target_port is None or e.target_port_id == f"port_{tgt}_{target_port}",
            kind is None or e.kind == kind.upper().strip(),
        )
        return all(checks)

    removed = [e for e in orch.edges if matches(e)]
    if not removed:
        raise GraphEditError(f"remove_edge: no matching edge in orchestration '{orch.name}'.")
    orch.edges = [e for e in orch.edges if not any(e is r for r in removed)]
    return removed


def _nested_dir(orch: Orchestration, name: str, dirname: Optional[str]) -> str:
    base = dirname or (_DIR_UNSAFE.sub("_", name).strip("_") or "child")[:_DIR_MAX]
    if "/" in base or "\\" in base or base in {".", ".."}:
        raise NestedOrchestrationError(
            f"add_orchestration_node: dirname {base!r} must be one path segment."
        )
    taken = {n.project for n in orch.nodes.values() if n.project}
    candidate, n = f"{NESTED_DIR}/{base}", 2
    while candidate in taken:
        candidate, n = f"{NESTED_DIR}/{base}_{n}", n + 1
    return candidate


def add_orchestration_node(
    orch: Orchestration,
    name: str,
    child: Orchestration | str | Path,
    *,
    node_id: Optional[str] = None,
    execution_mode: str = "subprocess",
    mainflow_target: Node | str | None = None,
    config: Optional[dict[str, Any]] = None,
    dirname: Optional[str] = None,
) -> Node:
    """Add an ORCHESTRATION node embedding *child*; see :meth:`Orchestration.add_orchestration_node`."""
    from ._orchestration import Orchestration as _Orchestration

    if execution_mode not in EXECUTION_MODES:
        raise NestedOrchestrationError(
            f"execution_mode must be one of {sorted(EXECUTION_MODES)}, got {execution_mode!r}."
        )
    if not isinstance(child, _Orchestration):
        child = Path(child).resolve()
        if not (child / "orchestration.yaml").is_file():
            raise FileNotFoundError(f"No orchestration.yaml in child project {child}")
    node_config = dict(config or {})
    node_config["execution_mode"] = execution_mode
    if mainflow_target is not None:
        target = mainflow_target.id if isinstance(mainflow_target, Node) else str(mainflow_target)
        node_config["mainflow_target_node_ref"] = target
    node = orch.add_node(
        name,
        "ORCHESTRATION",
        node_id=node_id,
        config=node_config,
        project=_nested_dir(orch, name, dirname),
    )
    node.embedded = child
    return node


def write_embedded_children(orch: Orchestration, dest: Path) -> None:
    """Write every embedded child to its node's ``project`` directory under *dest*."""
    from ._orchestration import Orchestration as _Orchestration

    for node in orch.nodes.values():
        if node.embedded is None or not node.project:
            continue
        target = (dest / node.project).resolve()
        if not target.is_relative_to(dest):
            raise NestedOrchestrationError(
                f"Node '{node.id}' project {node.project!r} escapes {dest}"
            )
        if isinstance(node.embedded, _Orchestration):
            node.embedded.save(str(target))
        elif Path(node.embedded).resolve() != target:
            shutil.copytree(node.embedded, target, dirs_exist_ok=True, ignore=ignore_local_state)


def _library_node(orch: Orchestration, template: str, node_id: str, ctype: str) -> Node:
    from ...templates._catalog import _component_sources

    script_source, manifest_source = _component_sources(template)
    node = orch.add_node(
        ctype.lower(),
        ctype,
        script=f"{node_id}.py",
        manifest=f"{node_id}.yaml",
        node_id=node_id,
        ports=[dict(p) for p in _LIBRARY_PORTS[template]],
    )
    node.script_source = script_source
    node.manifest_source = manifest_source
    return node


def add_library_endpoints(
    orch: Orchestration,
    *,
    connect: bool = True,
    start_id: str = "node_start",
    end_id: str = "node_end",
) -> tuple[Node, Node]:
    """Give *orch* the library START / END it lacks; return ``(start, end)``."""
    starts = [n for n in orch.nodes.values() if n.component_type == "START"]
    ends = [n for n in orch.nodes.values() if n.component_type == "END"]
    has_in, has_out = orch._mainflow_endpoints()
    body = [n for n in orch.nodes.values() if n.component_type not in ("START", "END")]
    start = starts[0] if starts else _library_node(orch, "generic-start", start_id, "START")
    end = ends[0] if ends else _library_node(orch, "generic-end", end_id, "END")
    if connect and not starts:
        _wire_start(orch, start, body, has_in)
    if connect and not ends:
        _wire_end(orch, end, body, has_out)
    return start, end


def _wire_start(orch: Orchestration, start: Node, body: list[Node], has_in: set[str]) -> None:
    """Edge *start* into every body node with no mainflow predecessor."""
    for node in body:
        if node.id not in has_in:
            orch.add_edge(start, node)


def _wire_end(orch: Orchestration, end: Node, body: list[Node], has_out: set[str]) -> None:
    """Route every body node with no mainflow successor to *end*.

    An ORCHESTRATION node has no out-port, so its hop is the config ref.
    """
    for node in body:
        if node.id in has_out:
            continue
        if node.component_type == "ORCHESTRATION":
            node.config["mainflow_target_node_ref"] = end.id
        else:
            orch.add_edge(node, end)
