"""Project I/O: load, validate, run, list, export, and import orchestrations."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from ._bundle_project import is_platform_bundle, materialize_platform_bundle
from ._models import Edge, Node
from ._orchestration import Orchestration

logger = logging.getLogger("perceivo.orchestration")

_ORCH_YAML_FILE = "orchestration.yaml"
_NODE_KEYS = frozenset({"id", "name", "component_type", "script", "manifest", "config", "project"})
_EDGE_KEYS = frozenset({"source_port_id", "target_port_id", "kind"})
_ROOT_KEYS = frozenset({"name", "description", "nodes", "ports", "edges"})


def load(project_dir: str) -> Orchestration:
    """Load an orchestration from a project directory.

    Reads orchestration.yaml and reconstructs the :class:`Orchestration`
    object with all nodes, ports and edges. Node ``config`` and ``project``,
    each port's full declaration, and every key the builder does not model
    are kept, so ``load(d).save(other)`` writes an equivalent project --
    including the scripts, manifests and nested projects it references.

    Args:
        project_dir: Path to the orchestration project directory.

    Returns:
        An :class:`Orchestration` instance.

    Raises:
        FileNotFoundError: If orchestration.yaml doesn't exist.
    """
    path = Path(project_dir).resolve()
    orch_file = path / _ORCH_YAML_FILE
    if not orch_file.exists():
        raise FileNotFoundError(f"orchestration.yaml not found in {path}")

    data = yaml.safe_load(orch_file.read_text(encoding="utf-8")) or {}

    orch = Orchestration(
        name=data.get("name", path.name),
        description=data.get("description", ""),
        project_dir=str(path),
    )

    orch.extra = {k: v for k, v in data.items() if k not in _ROOT_KEYS}

    declared_ports: dict[str, list[dict[str, Any]]] = {}
    for port_data in data.get("ports") or []:
        port = dict(port_data)
        port.setdefault("name", port.get("key") or port.get("id"))
        port.setdefault("port_type", str(port.get("direction") or "").lower())
        declared_ports.setdefault(str(port.get("node_id")), []).append(port)

    for node_data in data.get("nodes", []):
        config = node_data.get("config")
        node = orch.add_node(
            name=node_data.get("name", node_data["id"]),
            component_type=node_data.get("component_type", "AGENT"),
            script=node_data.get("script"),
            manifest=node_data.get("manifest"),
            node_id=node_data["id"],
            config=config if isinstance(config, dict) else None,
            project=node_data.get("project"),
        )
        node.extra = {k: v for k, v in node_data.items() if k not in _NODE_KEYS}
        _adopt_declared_ports(orch, node, declared_ports.get(node.id))

    for edge_data in data.get("edges", []):
        edge = Edge(
            source_port_id=edge_data["source_port_id"],
            target_port_id=edge_data["target_port_id"],
            kind=edge_data.get("kind", "MAINFLOW"),
            extra={k: v for k, v in edge_data.items() if k not in _EDGE_KEYS},
        )
        orch.edges.append(edge)

    return orch


def _adopt_declared_ports(
    orch: Orchestration, node: Node, ports: Optional[list[dict[str, Any]]]
) -> None:
    """Replace a node's default ports with the ones the file declares for it.

    The file's port ids are what its edges name, and its port dicts carry the
    contracts; the type defaults are kept only for a node the file gives none.
    """
    if not ports:
        return
    for port in node.ports:
        orch._port_registry.pop(port["id"], None)
    node.ports = ports
    for port in ports:
        orch._port_registry[str(port["id"])] = node.id


def validate(project_dir: str) -> Any:
    """Validate an orchestration project directory.

    Args:
        project_dir: Path to the project directory.

    Returns:
        A :class:`~perceivo.validator.ValidationReport`.
    """
    from ...validator import validate_orchestration

    return validate_orchestration(project_dir)


async def run(
    project_dir: str,
    *,
    input_data: Optional[dict[str, Any]] = None,
    config_path: Optional[str] = None,
) -> Any:
    """Run an orchestration from a project directory.

    Args:
        project_dir: Path to the project directory.
        input_data: Input data passed to the START component.
        config_path: Path to perceivo.yaml configuration.

    Returns:
        An :class:`~perceivo.runner.types.ExecutionResult`.
    """
    from ...runner import run_orchestration

    return await run_orchestration(project_dir, input_data=input_data, config_path=config_path)


def list_orchestrations(
    directory: str = ".",
    *,
    recursive: bool = False,
) -> list[dict[str, Any]]:
    """List orchestration projects found in a directory.

    A project is a directory holding an ``orchestration.yaml``.

    Args:
        directory: Root directory to search.
        recursive: If False, only the immediate subdirectories of *directory*
            are checked. If True, *directory* itself and every directory below
            it are checked.

    Returns:
        List of dicts with name, path, node_count, and edge_count.
    """
    path = Path(directory).resolve()
    pattern = "**/orchestration.yaml" if recursive else "*/orchestration.yaml"
    results: list[dict[str, Any]] = []

    for orch_file in sorted(path.glob(pattern)):
        try:
            data = yaml.safe_load(orch_file.read_text(encoding="utf-8")) or {}
            results.append(
                {
                    "name": data.get("name", orch_file.parent.name),
                    "description": data.get("description", ""),
                    "path": str(orch_file.parent),
                    "node_count": len(data.get("nodes", [])),
                    "edge_count": len(data.get("edges", [])),
                }
            )
        except Exception:
            continue

    return results


def save_bundle(
    project_dir: str,
    *,
    output: Optional[str] = None,
) -> dict[str, Any]:
    """Export an orchestration project as a platform bundle.

    Includes the orchestration graph, every component script and every manifest,
    in the same shape the platform's import endpoint accepts.

    Args:
        project_dir: Path to the project directory.
        output: If provided, write the bundle JSON to this file path.

    A capability project (a directory holding ``capability.yaml``, as
    :func:`load_bundle` writes for a capability bundle) exports as a
    ``capability_bundle`` instead.

    Returns:
        The bundle as a JSON-ready dict (``export_type: "orchestration"`` or
        ``"capability_bundle"``).
    """
    from ..._bundle.capability import is_capability_project, project_to_capability
    from ..._bundle.orchestration import orchestration_to_bundle

    if is_capability_project(project_dir):
        bundle = project_to_capability(project_dir)
    else:
        bundle = orchestration_to_bundle(project_dir)

    if output:
        # Reject traversal in the caller-supplied destination before writing.
        if ".." in Path(output).parts:
            raise ValueError(f"Output path must not contain '..': {output!r}")
        out_path = Path(output).resolve()
        out_path.write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Exported orchestration to %s", out_path)

    return bundle


def load_bundle(
    source: str | Path | dict[str, Any],
    directory: str = ".",
    *,
    name_override: Optional[str] = None,
    members: str | Path | None = None,
) -> Path:
    """Import an orchestration from a platform bundle.

    Args:
        source: A path to a bundle JSON file (``str`` / ``Path``), or a bundle
            already parsed into a ``dict``. The dispatch is on argument type
            because the argument itself states which of the two it is.
        directory: Parent directory for the imported project.
        name_override: Override the orchestration name.
        members: An imported capability project. Each node that nests one of
            its members by name (``orchestration_asset_id: "@member:<name>"``)
            gets a local copy of that member, so the orchestration can run.

    Returns:
        Path to the imported project directory.

    Raises:
        FileNotFoundError: If a source path does not exist.
        TypeError: If ``source`` is neither a path nor a dict.
        ValueError: If the payload is neither an orchestration bundle nor a
            ``capability_bundle`` (whose members become ``members/<n>-<name>``
            sub-projects beside a ``capability.yaml``).
    """
    if isinstance(source, dict):
        bundle = source
    elif isinstance(source, (str, Path)):
        source_path = Path(source).resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Bundle file not found: {source_path}")
        bundle = json.loads(source_path.read_text(encoding="utf-8"))
    else:
        raise TypeError(
            "source must be a path (str | Path) or a parsed bundle (dict), "
            f"not {type(source).__name__}"
        )

    from ..._bundle.capability import capability_to_project, is_capability_bundle

    if is_capability_bundle(bundle):
        path = capability_to_project(bundle, directory, name_override=name_override)
        logger.info("Imported capability to %s", path)
        return path
    if not is_platform_bundle(bundle):
        raise ValueError(
            "Input is not a portable orchestration bundle (export_type != 'orchestration') "
            "or capability bundle (export_type != 'capability_bundle')"
        )

    path = materialize_platform_bundle(bundle, directory, name_override=name_override)
    if members is not None:
        from ..._bundle._node_refs import capability_members, embed_member_refs

        embed_member_refs(path, capability_members(members))
    logger.info("Imported orchestration to %s", path)
    return path
