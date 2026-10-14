"""Give each port row the forwarding facts of the component port ``publish`` bundles.

The platform reads what a node forwards downstream from the component port a row
resolves to (``response_data_passthrough``, its exceptions, the response
contract), never from the row. ``publish`` builds that component port from the
node's pinned ``component_ports`` verbatim, or else from the manifest with the
node's own port declarations merged over it
(``perceivo._bundle.component.bundled_component_ports``). A mainflow ``out``
port the writer translated into a config hop is then retired and its response
contract folded into the in port (``perceivo._bundle._mainflow_ports``).

This module builds each component with those same functions and copies the
result onto the rows, so a project is judged on the ports ``publish`` sends. It
also returns those ports, so the port-count rules judge the component the
platform creates rather than the rows the graph happens to list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._manifest_overlay import _overlay, _parsed_ports

#: What a component port forwards downstream. The platform stores these from the
#: bundled component port, so a row's own value is replaced, not merged.
_FORWARDING_FIELDS = (
    "response_data_passthrough",
    "response_data_passthrough_except",
    "response_data_contract",
)

#: Folded response fields that are not forwarding facts, by the manifest key the
#: overlay reads them under.
_FOLDED_OVERLAY_KEYS = {
    "response_data_schema": "response_data",
    "artifact_type_output": "artifact_type_output",
    "artifact_output_schema": "artifact_output_schema",
}


def _row_key(port: dict[str, Any]) -> str:
    return str(port.get("port_name") or port.get("key") or port.get("name") or "")


def _bundled_component(
    project: Path, node: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[Any, list[dict[str, Any]]] | None:
    """*node*'s component as ``publish`` bundles it, with the rows it was built from.

    None when the component cannot be built; the bundled-ports check reports
    that.
    """
    from perceivo._bundle._component_references import component_base
    from perceivo._bundle.component import (
        bundled_component_ports,
        restore_component_export_data,
    )

    nid = str(node.get("id") or "")
    script, manifest = node.get("script"), node.get("manifest")
    files_key = (str(script), str(manifest or ""))
    try:
        base = component_base(project, nid, script, manifest, files_key, {})
        base = restore_component_export_data(base, node)
    except Exception:  # an unbuildable component is the bundled-ports check's finding
        return None
    ports = bundled_component_ports(base, node, rows)
    return base.model_copy(update={"ports": ports}), rows


def _retire_translated(component: Any, keys: list[str], kept: set[tuple[str, str]]) -> Any:
    """*component* with each translated out port retired as the writer retires it.

    A port some kept row still resolves to stays. A conflicting fold makes the
    writer refuse the bundle (``ORCH-BUNDLE-CONTRACT-001``); the component is
    then left unfolded.
    """
    from perceivo._bundle._mainflow_ports import _retire
    from perceivo._bundle.component import BundleConfigError

    for key in keys:
        if (key, "OUT") in kept:
            continue
        try:
            retired = _retire(component, key)
        except BundleConfigError:
            continue
        if retired is not None:
            component = retired
    return component


def _apply(row: dict[str, Any], port: Any, unfolded: Any, stated: dict[str, Any]) -> None:
    """Copy *port*'s forwarding facts onto *row*, and what the fold added to it.

    A folded value replaces the row's: ``publish`` sends the bundled port, and a
    pinned node's bundled port ignores its rows. A field the component manifest
    states on the port (*stated*) replaces the folded value in turn: the platform
    stores the bundled port with the bundled manifest overlaid on it.
    """
    for field in _FORWARDING_FIELDS:
        value = getattr(port, field)
        if value is None:
            row.pop(field, None)
        else:
            row[field] = value
    if unfolded is None:
        return
    entry = {
        key: getattr(port, field)
        for field, key in _FOLDED_OVERLAY_KEYS.items()
        if getattr(unfolded, field) is None and getattr(port, field) is not None
    }
    _overlay(row, entry)
    _overlay(row, {key: stated[key] for key in entry if key in stated})


def _fold_rows(rows: list[dict[str, Any]], translated: list[dict[str, Any]]) -> None:
    """Fold each translated out row's response fields into the node's single in row.

    For a node with no script there is no component: its rows, which no manifest
    overlays, are its only declaration, so the fold reads them as written. A field
    both rows state differently is not folded, as the writer folds nothing there.
    """
    from perceivo._bundle._mainflow_ports import _RESPONSE_FIELDS
    from perceivo._bundle.component import _port_direction

    in_rows = [
        r
        for r in rows
        if _port_direction(r) == "IN" and str(r.get("port_type") or "").lower() == "in"
    ]
    kept = {(_row_key(r), _port_direction(r)) for r in rows}
    if len(in_rows) != 1:
        return
    in_row = in_rows[0]
    for out_row in translated:
        if (_row_key(out_row), "OUT") in kept:
            continue
        if any(
            in_row.get(name) is not None
            and out_row.get(name) is not None
            and in_row.get(name) != out_row.get(name)
            for name in _RESPONSE_FIELDS
        ):
            continue
        for name in _RESPONSE_FIELDS:
            if in_row.get(name) is None and out_row.get(name) is not None:
                in_row[name] = out_row[name]


def apply_component_ports(
    project: Path,
    nodes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    source_ports: list[Any],
    translated: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Give each of *ports* (in place) its bundled component port's forwarding facts.

    *source_ports* holds each row of *ports*, at the same position, as
    ``orchestration.yaml`` states it: the writer builds the component from those,
    not from rows the manifest overlay has already rewritten. *translated* holds
    the rows of *ports* the mainflow translation dropped; their component ports
    are retired and folded into the in port before the facts are copied.
    A node with no script has no component; its translated rows are folded as
    written (:func:`_fold_rows`).

    Returns each built component's bundled ports by node id, as ``key``,
    ``port_type`` and ``direction``. A node whose component cannot be built is
    absent.
    """
    from perceivo._bundle.component import _port_direction

    bundled: dict[str, list[dict[str, Any]]] = {}
    dropped = {id(row) for row in translated}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("component_type") or "").upper() == "ORCHESTRATION":
            continue
        nid = str(node.get("id") or "")
        node_translated = [p for p in translated if str(p.get("node_id", "")) == nid]
        pairs = [
            (row, source)
            for row, source in zip(ports, source_ports, strict=True)
            if isinstance(row, dict)
            and str(row.get("node_id", "")) == nid
            and not row.get("virtual")
            and id(row) not in dropped
        ]
        rows = [row for row, _ in pairs]
        if not node.get("script"):
            _fold_rows(rows, node_translated)
            continue
        built = _bundled_component(project, node, [source for _, source in pairs])
        if built is None:
            continue
        component, resolved = built
        kept = {(_row_key(r), _port_direction(r)) for r in resolved}
        keys = [_row_key(p) for p in node_translated]
        unfolded = {(p.key, p.direction): p for p in component.ports}
        manifest_ports = _parsed_ports(component.manifest_yaml or "")
        component = _retire_translated(component, keys, kept)
        bundled[nid] = [
            {"key": p.key, "port_type": p.port_type, "direction": p.direction}
            for p in component.ports
        ]
        for row, identity_row in zip(rows, resolved, strict=True):
            identity = (_row_key(identity_row), _port_direction(identity_row))
            port = next((p for p in component.ports if (p.key, p.direction) == identity), None)
            if port is None:
                continue
            before = unfolded.get(identity)
            stated = manifest_ports.get(identity[0], {})
            _apply(row, port, before if before is not port else None, stated)
    return bundled
