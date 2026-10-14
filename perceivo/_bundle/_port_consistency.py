"""Keep the exported ``ports[]`` list resolvable by the platform importer.

**A synthesized port never reaches a bundle.** When the bundle reader
(:mod:`perceivo.orchestration._builder._bundle_project`) meets a node that
continues the mainflow through ``config.mainflow_target_node_ref`` rather than
through a wired port, it synthesizes an out-port marked ``virtual: True`` and an
explicit MAINFLOW edge, so the local graph is walkable. That synthesis is local
representation, not platform data: the platform's export of the same
orchestration carries only ``config.mainflow_target_node_ref``. The writer drops
every synthesized port, whatever the node type, and keeps the hop in config.

**Anything else undeclared fails here rather than at the server.** The platform
importer resolves every port row strictly against the component's declared
ports. :func:`reject_undeclared_ports` checks that on the finished export, so a
row that cannot resolve raises naming the node, the component and what the
component declares.

Ports on nested-orchestration nodes are skipped: the platform resolves those
against the child orchestration's own port rows.

**A START or END carries exactly its two ports.** The platform refuses any
other shape when it creates the component; :func:`reject_terminal_port_shape`
raises the platform's message before upload, followed by every port the
component carries as bundled.
"""

from __future__ import annotations

from typing import Any

from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.schema import ComponentExportData, PortExportData, PortRefExportData
from perceivo.validator._create_ports import create_refusal

#: The categories whose create-time rule this module checks before upload, and
#: the two port types each must carry.
_TERMINAL_TYPES = {"START": ("external_in", "out"), "END": ("in", "external_out")}


class TerminalPortShapeError(BundleConfigError):
    """A START or END bundled with other than exactly its two ports.

    ``finding`` is the platform's message alone, so a caller that already reports
    the same finding from the files can tell it is one defect.
    """

    def __init__(self, name: str, finding: str, ports: str) -> None:
        self.finding = finding
        super().__init__(f"Component {name!r}: {finding}. Its ports: {ports}.")


def _source(port: PortExportData, pinned: bool, manifest_ports: list[PortExportData] | None) -> str:
    """Where *port* is declared: a pin or a node merge brings ports from orchestration.yaml."""
    if manifest_ports is not None:
        declared = {(p.key, p.direction) for p in manifest_ports}
        return "the manifest" if (port.key, port.direction) in declared else "orchestration.yaml"
    return "orchestration.yaml" if pinned else "the manifest"


def _port_list(
    ports: list[PortExportData], pinned: bool, manifest_ports: list[PortExportData] | None
) -> str:
    """Every port as bundled: key, type as written, direction and where it is declared.

    The list states what the ports are and prescribes nothing: the platform's
    message says what it counted, and a port read beside it shows the cause.
    """
    items = [
        f"{port.key!r} (type {port.port_type!r}, direction {port.direction}, "
        f"from {_source(port, pinned, manifest_ports)})"
        for port in ports
    ]
    return ", ".join(items) or "none"


def set_mainflow_target(
    node_config: dict[str, dict[str, Any] | None], node: str, target: str
) -> None:
    """Record *target* as *node*'s mainflow next hop, creating the config if absent."""
    config = node_config.get(node)
    if config is None:
        config = {}
        node_config[node] = config
    config["mainflow_target_node_ref"] = target


def absorb_synthesized_target(
    node_config: dict[str, dict[str, Any] | None],
    source_node: str,
    target_id: str,
    target_node: str,
    kind: str,
    synthesized: set[str],
) -> bool:
    """Consume an edge that points *into* a synthesized port; report whether it was.

    A synthesized port is never exported, so the hop is written as the source
    node's ``config.mainflow_target_node_ref`` instead of being dropped. Where that
    config is already present the write is a no-op.
    """
    if target_id not in synthesized:
        return False
    if kind == "MAINFLOW":
        set_mainflow_target(node_config, source_node, target_node)
    return True


def reject_undeclared_ports(
    ports: list[PortRefExportData],
    ref_by_node: dict[str, str],
    components: dict[str, ComponentExportData],
    nested_node_ids: set[str],
) -> None:
    """Raise unless every exported port row resolves on its node's component.

    Applies the platform importer's rule: ``(component_port_key,
    component_port_direction)`` must appear among ``components[ref].ports`` as
    ``(key, direction)``, compared case-insensitively on direction.

    A node that pins ``component_ports`` (as every project materialized from a
    platform bundle does) takes that list verbatim as its component's port set, so
    a port declared on the node is never merged onto the component. The error
    message names both remedies.
    """
    for port in ports:
        if port.node_ref in nested_node_ids:
            continue
        ref = ref_by_node.get(port.node_ref, "")
        component = components.get(ref)
        if component is None:
            continue
        declared = {(p.key, p.direction.upper()) for p in component.ports}
        wanted = (port.component_port_key, port.component_port_direction.upper())
        if wanted not in declared:
            raise BundleConfigError(
                f"Node {port.node_ref!r} declares port key={port.component_port_key!r} "
                f"direction={port.component_port_direction.upper()!r}, but its component "
                f"{ref!r} ({component.name!r}) does not declare it. The component declares "
                f"{sorted(declared)}. The platform importer resolves every port row against "
                f"the component's own ports, so this bundle would be rejected on import. "
                f"A node that pins 'component_ports' takes that list as its component's "
                f"ports verbatim, so fix it at the source: either add "
                f"{{'key': {port.component_port_key!r}, 'direction': "
                f"{port.component_port_direction.upper()!r}, ...}} to the "
                f"'component_ports' of node {port.node_ref!r} (and to component {ref!r} in "
                f"the source bundle), or stop the node declaring the port and remove its "
                f"row from the orchestration's 'ports' list."
            )


def reject_terminal_port_shape(
    components: dict[str, ComponentExportData],
    pinned_refs: set[str],
    manifest_ports: dict[str, list[PortExportData]],
) -> None:
    """Raise unless every START and END component has exactly its two ports.

    *pinned_refs* are the components some node pins ``component_ports`` for, and
    *manifest_ports* holds, for each component some node merges its own ports
    onto, the manifest's ports before that merge; the message lists each port
    with where it is declared.
    """
    for ref, component in components.items():
        category = component.category.upper()
        if category not in _TERMINAL_TYPES:
            continue
        refusal = create_refusal(category, [{"port_type": p.port_type} for p in component.ports])
        if refusal is not None:
            listed = _port_list(component.ports, ref in pinned_refs, manifest_ports.get(ref))
            raise TerminalPortShapeError(component.name, refusal.message, listed)
