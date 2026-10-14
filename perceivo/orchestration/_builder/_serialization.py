"""Serialization helpers for :class:`Orchestration`.

These are kept as module-level functions so they can be unit-tested
without instantiating the full builder, and so the main class file
stays under the 500-line limit.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ._graph_edit import ignore_local_state, write_embedded_children  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from ._orchestration import Orchestration


_IN_PORT_TYPES = frozenset({"in", "external_in"})
_OUT_PORT_TYPES = frozenset({"out", "external_out"})
_ROOT_KEYS = frozenset({"name", "description", "nodes", "ports", "edges"})


def _with_extra(canonical: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Canonical keys first, then every preserved key they do not already set."""
    for key, value in extra.items():
        canonical.setdefault(key, value)
    return canonical


def render_default_manifest(name: str, component_type: str, ports: list[dict[str, Any]]) -> str:
    """Render a minimal manifest YAML for a function-defined node."""
    port_entries: list[dict[str, Any]] = []
    for p in ports:
        if p["port_type"] in _IN_PORT_TYPES:
            direction = "in"
        elif p["port_type"] in _OUT_PORT_TYPES:
            direction = "out"
        else:
            direction = p["port_type"]
        port_entries.append(
            {
                "key": p["name"],
                "direction": direction,
                "port_type": p["port_type"],
            }
        )
    manifest = {
        "name": name,
        "type": component_type,
        "description": f"Auto-generated manifest for {name}.",
        "ports": port_entries,
        "required_secrets": [],
        "input_parameters": [],
    }
    return yaml.dump(manifest, sort_keys=False, default_flow_style=False)


def orchestration_to_yaml(orch: Orchestration) -> str:
    """Serialize an Orchestration to YAML."""
    data: dict[str, Any] = {
        "name": orch.name,
        "description": orch.description,
        "nodes": [],
        "ports": [],
        "edges": [],
    }

    for node in orch.nodes.values():
        node_data: dict[str, Any] = {
            "id": node.id,
            "name": node.name,
            "component_type": node.component_type,
        }
        if node.script:
            node_data["script"] = node.script
        if node.manifest:
            node_data["manifest"] = node.manifest
        if node.config:
            node_data["config"] = dict(node.config)
        if node.project:
            node_data["project"] = node.project
        data["nodes"].append(_with_extra(node_data, node.extra))

        for port in node.ports:
            port_data: dict[str, Any] = {
                "id": port["id"],
                "node_id": node.id,
                "name": port["name"],
                "port_type": port["port_type"],
            }
            data["ports"].append(_with_extra(port_data, port))

    for edge in orch.edges:
        edge_data: dict[str, Any] = {
            "source_port_id": edge.source_port_id,
            "target_port_id": edge.target_port_id,
            "kind": edge.kind,
        }
        data["edges"].append(_with_extra(edge_data, edge.extra))

    extra = {k: v for k, v in getattr(orch, "extra", {}).items() if k not in _ROOT_KEYS}
    return yaml.dump(_with_extra(data, extra), default_flow_style=False, sort_keys=False)


def orchestration_to_dict(orch: Orchestration) -> dict[str, Any]:
    """Serialize to a plain dict, skipping runtime-only fields."""
    nodes: list[dict[str, Any]] = []
    for n in orch.nodes.values():
        data = asdict(n)
        for field_name in ("func", "script_source", "manifest_source", "embedded"):
            data.pop(field_name, None)
        nodes.append(data)
    return {
        "name": orch.name,
        "description": orch.description,
        "nodes": nodes,
        "edges": [asdict(e) for e in orch.edges],
    }


def save_orchestration(orch: Orchestration, directory: str) -> Path:
    """Persist an Orchestration's YAML, captured scripts, and manifests to disk.

    Returns the path to ``orchestration.yaml``. Updates ``orch.project_dir``
    to the resolved output directory as a side-effect (callers depend on
    this for subsequent run() materialisation).
    """
    path = Path(directory).resolve()
    path.mkdir(parents=True, exist_ok=True)
    source = Path(orch.project_dir).resolve() if orch.project_dir else None
    if source is not None and source != path:
        _copy_referenced_files(orch, source, path)
    write_embedded_children(orch, path)

    for node in orch.nodes.values():
        if node.script_source and node.script:
            # newline="": the captured text is exported byte-for-byte, and a
            # text-mode write on Windows would turn every LF into CRLF.
            (path / node.script).write_text(node.script_source, encoding="utf-8", newline="")
        if node.manifest_source and node.manifest:
            (path / node.manifest).write_text(node.manifest_source, encoding="utf-8", newline="")

    orch_file = path / "orchestration.yaml"
    orch_file.write_text(orchestration_to_yaml(orch), encoding="utf-8")

    orch.project_dir = str(path)
    return orch_file


def _inside(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    return candidate if candidate.is_relative_to(root) else None


def _copy_referenced_files(orch: Orchestration, source: Path, dest: Path) -> None:
    """Carry on-disk scripts, manifests and nested projects to a new directory.

    A loaded orchestration refers to its component files by path, relative to
    the directory it was loaded from; saving it elsewhere without them wrote a
    graph that could not run. Paths escaping either directory are skipped.
    """
    for node in orch.nodes.values():
        for rel, captured in (
            (node.script, node.script_source),
            (node.manifest, node.manifest_source),
        ):
            if rel and not captured:
                _copy_file(source, dest, rel)
        if node.project and node.embedded is None:
            _copy_project(source, dest, node.project)


def _copy_file(source: Path, dest: Path, rel: str) -> None:
    """Copy one on-disk component file unless it escapes or already exists."""
    src, dst = _inside(source, rel), _inside(dest, rel)
    if src is not None and dst is not None and src.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _copy_project(source: Path, dest: Path, rel: str) -> None:
    """Copy one nested project directory unless it escapes either root."""
    src, dst = _inside(source, rel), _inside(dest, rel)
    if src is not None and dst is not None and src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore_local_state)
