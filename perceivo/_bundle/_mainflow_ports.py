"""Retire the OUT port whose MAINFLOW edge the writer turned into node config.

An AGENT or TOOL node may not continue the mainflow through an ``out`` port on
the platform (``VAL-MAINFLOW-OUT-001``). The writer translates such an edge into
``config.mainflow_target_node_ref``, drops the edge, and drops the out-port's
orchestration row (``orchestration._translate_mainflow_edges``). On the platform
a mainflow node has no OUT port: it declares what it passes on
(``artifact_type_output``, ``response_data``) on its single IN port.

A translated port is therefore removed from the component, and the response half
of its contract moves onto the component's single ``in`` port wherever that port
does not already state it. When both ports state the same response field with
different values, the bundle is refused with a ``BundleConfigError`` carrying the
:data:`CONTRACT_CONFLICT` note. The port is kept when any exported port row still
references it, and when the component has no single ``in`` port to carry the
contract; the local bundle check then reports the unconnected port.
"""

from __future__ import annotations

from typing import Any

from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.schema import ComponentExportData, PortExportData, PortRefExportData

#: The response half of a port contract, as a mainflow node states it on IN.
_RESPONSE_FIELDS = (
    "response_data_schema",
    "response_data_passthrough",
    "response_data_passthrough_except",
    "response_data_contract",
    "artifact_type_output",
    "artifact_output_schema",
)


#: Note added to the ``BundleConfigError`` raised when a retired OUT port and its
#: component's IN port disagree on a response field.
CONTRACT_CONFLICT = "perceivo: mainflow response contract conflict"


def _fold_response(
    component: ComponentExportData, in_port: PortExportData, out_port: PortExportData
) -> PortExportData:
    """*in_port* with each response field it leaves unset taken from *out_port*.

    Raises ``BundleConfigError`` (noted :data:`CONTRACT_CONFLICT`) when both
    ports set a field to different values.
    """
    conflicts = [
        name
        for name in _RESPONSE_FIELDS
        if getattr(in_port, name) is not None
        and getattr(out_port, name) is not None
        and getattr(in_port, name) != getattr(out_port, name)
    ]
    if conflicts:
        error = BundleConfigError(
            f"Component '{component.name}' declares a different {', '.join(conflicts)} on "
            f"its IN port '{in_port.key}' than on its OUT port '{out_port.key}'. The platform "
            f"carries no OUT port on a mainflow {component.category}, so '{out_port.key}' is "
            f"folded into '{in_port.key}' and only one contract can survive. Declare the "
            "response contract on one of the two ports, or make both declarations agree."
        )
        error.add_note(CONTRACT_CONFLICT)
        raise error
    updates = {
        name: value
        for name in _RESPONSE_FIELDS
        if getattr(in_port, name) is None and (value := getattr(out_port, name)) is not None
    }
    return in_port.model_copy(update=updates) if updates else in_port


def _retire(component: ComponentExportData, key: str) -> ComponentExportData | None:
    """*component* without its ``out`` port *key*, contract folded; None if not safe."""
    out_ports = [p for p in component.ports if p.key == key and p.direction == "OUT"]
    in_ports = [p for p in component.ports if p.direction == "IN" and p.port_type == "in"]
    if len(out_ports) != 1 or out_ports[0].port_type != "out" or len(in_ports) != 1:
        return None
    folded = _fold_response(component, in_ports[0], out_ports[0])
    ports = [folded if p is in_ports[0] else p for p in component.ports if p is not out_ports[0]]
    return component.model_copy(update={"ports": ports})


def retire_translated_out_ports(
    components: dict[str, ComponentExportData],
    ref_by_node: dict[str, str],
    raw_ports: list[dict[str, Any]],
    translated: set[str],
    exported: list[PortRefExportData],
) -> None:
    """Remove from *components* (in place) each out port a translation orphaned.

    *translated* holds the ids of the ``orchestration.yaml`` port rows the
    writer dropped; synthesized (``virtual``) ports never reached a component
    and are ignored. *exported* is the finished orchestration-level port list.
    """
    referenced = {
        (ref_by_node.get(p.node_ref, ""), p.component_port_key, p.component_port_direction)
        for p in exported
    }
    for port in raw_ports:
        if str(port.get("id")) not in translated or port.get("virtual"):
            continue
        ref = ref_by_node.get(str(port.get("node_id") or ""), "")
        key = str(port.get("key") or port.get("name") or "")
        component = components.get(ref)
        if component is None or (ref, key, "OUT") in referenced:
            continue
        retired = _retire(component, key)
        if retired is not None:
            components[ref] = retired
