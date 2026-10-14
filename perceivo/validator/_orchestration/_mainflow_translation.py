"""Translate an AGENT/TOOL ``out`` mainflow hop the way ``publish`` sends it.

The platform carries no OUT port on a mainflow AGENT or TOOL. The bundle writer
turns a MAINFLOW edge leaving such a node's ``out`` port into
``config.mainflow_target_node_ref``, drops the edge and the port row, and folds
the port's response contract into the component's single ``in`` port
(``perceivo._bundle.orchestration._translate_mainflow_edges`` and
``perceivo._bundle._mainflow_ports``). The platform then reads what the node sends
downstream from that in port and checks the hop from it.

A project loaded from disk still spells the hop as an edge from the ``out`` port,
so it would be judged on a port the platform never sees: the response half of a
mainflow node's contract (passthrough, ``artifact_type_output``) lives on the
manifest's input port, and the ``out`` port reads as forwarding nothing. This
module applies the writer's translation to the loaded graph, so the config hop
is then expanded like any other; the fold is applied with the rest of the
bundled component ports (``_component_ports``).
"""

from __future__ import annotations

from typing import Any


def translate_mainflow_out_ports(
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return *ports* and *edges* with each translated ``out`` hop removed, and its rows.

    The hop's target is written to the source node's config, as the writer
    writes it. Ports synthesized for a config hop (``virtual``) are left to the
    config expansion that follows. The removed rows are returned so their
    component ports can be retired as the writer retires them
    (:func:`._component_ports.apply_component_ports`).
    """
    from perceivo._bundle.orchestration import _port_info_by_id, _translate_mainflow_edges

    real_ports = [p for p in ports if isinstance(p, dict) and not p.get("virtual")]
    real_ids = {str(p.get("id")) for p in real_ports}
    real_edges = [
        e
        for e in edges
        if isinstance(e, dict)
        and str(e.get("source_port_id")) in real_ids
        and str(e.get("target_port_id")) in real_ids
    ]
    node_type = {
        str(n.get("id") or ""): str(n.get("component_type") or "").upper()
        for n in nodes
        if isinstance(n, dict)
    }
    # The writer records each hop in this map, creating a node's config where it
    # has none; a config that is not a mapping is left for the config rules.
    configs: dict[str, dict[str, Any] | None] = {
        str(n.get("id") or ""): n.get("config")
        for n in nodes
        if isinstance(n, dict) and isinstance(n.get("config"), (dict, type(None)))
    }
    writable = set(configs)
    _, translated = _translate_mainflow_edges(
        real_edges, _port_info_by_id(real_ports), node_type, configs, real_ports
    )
    if not translated:
        return ports, edges, []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if node_id in writable:
            node["config"] = configs[node_id]

    kept_ports = [p for p in ports if not (isinstance(p, dict) and str(p.get("id")) in translated)]
    kept_edges = [
        e
        for e in edges
        if not (
            isinstance(e, dict)
            and (
                str(e.get("source_port_id")) in translated
                or str(e.get("target_port_id")) in translated
            )
        )
    ]
    removed = [p for p in real_ports if str(p.get("id")) in translated]
    return kept_ports, kept_edges, removed
