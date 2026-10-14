"""Node config and nested-orchestration references, canonical on import and export.

* **``_mainflow_target_node_ref``.** The platform importer uses this key as
  resolution scaffolding beside ``mainflow_target_node_ref`` and discards it;
  only ``mainflow_target_node_ref`` is read. A bundle may carry both keys, naming
  different nodes. Only the key the platform reads is kept, so an import ->
  export round trip cannot change which hop a reader sees.
* **``orchestration_asset_id: "@member:<name>"``.** A router nests capability
  members by name, because their asset ids are minted by the import that creates
  them. Locally there is no asset store, so the named member's project is copied
  under ``nested/m-<slug>`` and the node points at it; the reference itself is
  unchanged, so export writes the same ``@member:`` node back.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from perceivo._bundle.schema import NestedOrchestrationError
from perceivo._internal.local_state import ignore_local_state

SCAFFOLD_MAINFLOW_KEY = "_mainflow_target_node_ref"
MEMBER_REF_PREFIX = "@member:"
# ASCII: \W is Unicode-aware on str patterns; slugs stay [A-Za-z0-9_-].
_SLUG = re.compile(r"\W+", re.ASCII)
_SLUG_MAX = 24


def canonical_node_config(config: Any) -> Any:
    """*config* without the platform's discarded mainflow scaffold key."""
    if not isinstance(config, dict) or SCAFFOLD_MAINFLOW_KEY not in config:
        return config
    return {k: v for k, v in config.items() if k != SCAFFOLD_MAINFLOW_KEY}


def member_ref(node: dict[str, Any]) -> str | None:
    """The capability member a node nests by name, if it does."""
    ref = node.get("orchestration_asset_id")
    if isinstance(ref, str) and ref.startswith(MEMBER_REF_PREFIX):
        return ref[len(MEMBER_REF_PREFIX) :]
    return None


def _member_dir(project: Path, name: str, taken: dict[str, str]) -> str:
    """``nested/m-<slug>``: short (MAX_PATH), one per member, never another's."""
    if name in taken:
        return taken[name]
    slug = (_SLUG.sub("-", name).strip("-_") or "member")[:_SLUG_MAX].rstrip("-_")
    relative, n = f"nested/m-{slug}", 1
    while relative in taken.values() or (project / relative).exists():
        n += 1
        relative = f"nested/m-{slug}-{n}"
    taken[name] = relative
    return relative


def embed_member_refs(project: Path, members: Mapping[str, Path]) -> list[str]:
    """Give each ``@member:`` node in *project* a local copy of that member.

    Returns the member names resolved, embedded children's included. A name
    *members* does not hold is left as it was; a node that already has a
    ``project`` is not touched, but an embedded child's own nodes are resolved.
    """
    path = project / "orchestration.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    resolved: list[str] = []
    taken: dict[str, str] = {}
    for node in document.get("nodes") or []:
        if isinstance(node, dict):
            resolved.extend(_embed_node(project, node, members, taken))
    if taken:
        path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    return resolved


def _embed_nested_child(
    project: Path, node: dict[str, Any], members: Mapping[str, Path]
) -> list[str]:
    """Resolve ``@member:`` nodes inside a node's own nested project."""
    child = (project / str(node["project"])).resolve()
    if child.is_relative_to(project.resolve()) and (child / "orchestration.yaml").is_file():
        return embed_member_refs(child, members)
    return []


def _embed_node(
    project: Path, node: dict[str, Any], members: Mapping[str, Path], taken: dict[str, str]
) -> list[str]:
    """The member names one node of *project* resolves; copies its member once."""
    name = member_ref(node)
    if name is None:
        return _embed_nested_child(project, node, members) if node.get("project") else []
    source = members.get(name)
    if source is None or node.get("project"):
        return []
    if Path(source).resolve() == project.resolve():
        raise NestedOrchestrationError(
            f"Node {node.get('id')!r} nests its own orchestration ({name!r})"
        )
    fresh = name not in taken
    relative = _member_dir(project, name, taken)
    if fresh:
        shutil.copytree(source, project / relative, ignore=ignore_local_state)
    node["project"] = relative
    return [name]


def capability_members(capability_dir: str | Path) -> dict[str, Path]:
    """Member name -> project directory, read from a capability project."""
    from perceivo._bundle.capability import CAPABILITY_FILE
    from perceivo._bundle.component import BundleConfigError

    root = Path(capability_dir).resolve()
    source = root / CAPABILITY_FILE
    if not source.is_file():
        raise FileNotFoundError(f"No {CAPABILITY_FILE} in {root}")
    document = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    entries = document.get("members")
    if not isinstance(entries, list):
        raise BundleConfigError(f"{source} declares no members")
    members: dict[str, Path] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") and entry.get("project"):
            candidate = (root / str(entry["project"])).resolve()
            if candidate.is_relative_to(root):
                members[str(entry["name"])] = candidate
    return members
