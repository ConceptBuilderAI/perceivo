"""Convert a platform capability bundle ⇄ a local capability project.

A capability bundle (``export_type: "capability_bundle"``) wraps one or more
orchestration exports ("members") with capability-level data: ``capability``
(name, execution mode, idle timeout, ...), ``contract`` (the START / END
components every member must share) and ``traffic_plan`` (which member takes
what share of traffic, addressed by member *name*). Each member is an ordinary
orchestration export plus ``new_name`` and without its own ``exported_at``
(nested child exports keep theirs), component ``asset_id`` or node
``component_asset_id``.

On disk a capability project is::

    <capability>/capability.yaml     capability-level keys, verbatim, + members
    <capability>/members/<n>-<slug>/ one ordinary orchestration project each

Every top-level key other than ``members``, ``export_type`` and ``exported_at``
is kept in ``capability.yaml`` verbatim and emitted back verbatim, so an unknown
key survives the round trip. Member directory names are kept short because
nested children add further levels and Windows refuses a subprocess working
directory longer than MAX_PATH.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from perceivo._bundle.component import BundleConfigError, read_text
from perceivo._internal.paths import resolve_within_project

CAPABILITY_FILE = "capability.yaml"
_FORMAT = "perceivo_capability_bundle_v1"
_EXPORT_TYPE = "capability_bundle"
# ASCII: \W is Unicode-aware on str patterns; slugs stay [A-Za-z0-9_-].
_SLUG = re.compile(r"\W+", re.ASCII)
_SLUG_MAX = 32
_NOT_CARRIED = frozenset({"members", "export_type", "exported_at"})
# Keys the platform normalizer strips from every member (see module docstring).
_MEMBER_DROPPED = ("exported_at",)
_COMPONENT_DROPPED = ("asset_id",)
_NODE_DROPPED = ("component_asset_id",)


def is_capability_bundle(data: dict[str, Any]) -> bool:
    """Return whether *data* is a platform capability bundle."""
    return data.get("export_type") == _EXPORT_TYPE and isinstance(data.get("members"), list)


def is_capability_project(path: str | Path) -> bool:
    """Return whether *path* is a local capability project directory."""
    return (Path(path) / CAPABILITY_FILE).is_file()


def member_name(member: dict[str, Any]) -> str:
    """A member's pinned name: ``new_name``, else its orchestration's name."""
    name = member.get("new_name") or (member.get("orchestration") or {}).get("name")
    if not name:
        raise BundleConfigError("Capability member has neither new_name nor orchestration.name")
    return str(name)


def _slug(value: str) -> str:
    return (_SLUG.sub("-", value.strip()).strip("-_") or "capability")[:_SLUG_MAX].rstrip("-_")


def capability_to_project(
    data: dict[str, Any],
    directory: str | Path = ".",
    *,
    name_override: str | None = None,
) -> Path:
    """Write a capability bundle as a capability project; return its directory."""
    from perceivo._bundle._node_refs import embed_member_refs
    from perceivo.orchestration._builder._bundle_project import (
        is_platform_bundle,
        write_platform_project,
    )

    if not is_capability_bundle(data):
        raise BundleConfigError(
            "Input is not a capability bundle (export_type != 'capability_bundle')"
        )
    capability = data.get("capability") or {}
    name = name_override or capability.get("name") or "capability"
    members = data["members"]
    if not members:
        raise BundleConfigError("Capability bundle has no members")

    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, member in enumerate(members):
        if not isinstance(member, dict) or not is_platform_bundle(member):
            raise BundleConfigError(f"Capability member {index} is not an orchestration bundle")
        pinned = member_name(member)
        if pinned in seen:
            raise BundleConfigError(f"Two capability members are both named {pinned!r}")
        seen.add(pinned)
        entries.append({"name": pinned, "project": f"members/{index}-{_slug(pinned)}"})

    root = Path(directory).resolve() / _slug(str(name))
    root.mkdir(parents=True, exist_ok=True)
    for member, entry in zip(members, entries, strict=True):
        project = root / entry["project"]
        project.mkdir(parents=True, exist_ok=True)
        display = (member.get("orchestration") or {}).get("name") or entry["name"]
        write_platform_project(member, project, display_name=str(display))
    # Members nest one another by name (``@member:<name>``); each such node gets
    # a local copy of the sibling so the member runs without an asset store.
    by_name = {entry["name"]: root / entry["project"] for entry in entries}
    for entry in entries:
        siblings = {k: v for k, v in by_name.items() if k != entry["name"]}
        embed_member_refs(root / entry["project"], siblings)

    document: dict[str, Any] = {"format": _FORMAT}
    document.update({k: v for k, v in data.items() if k not in _NOT_CARRIED})
    if name_override:
        document["capability"] = {**capability, "name": name_override}
    document["members"] = entries
    (root / CAPABILITY_FILE).write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return root


def project_to_capability(project_dir: str | Path) -> dict[str, Any]:
    """Build a capability bundle from a local capability project."""
    from perceivo._bundle.orchestration import orchestration_to_bundle

    root = Path(project_dir).resolve()
    source = root / CAPABILITY_FILE
    if not source.is_file():
        raise FileNotFoundError(f"No {CAPABILITY_FILE} in {root}")
    document = yaml.safe_load(read_text(source)) or {}
    if document.get("format") != _FORMAT:
        raise BundleConfigError(f"{source} is not a {_FORMAT} document")
    entries = document.get("members")
    if not isinstance(entries, list) or not entries:
        raise BundleConfigError(f"{source} declares no members")

    members: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("project"):
            raise BundleConfigError(f"{source}: member {index} needs a name and a project")
        project = resolve_within_project(root, str(entry["project"]), field="members.project")
        member = orchestration_to_bundle(project)
        members.append(_portable_member(member, str(entry["name"])))

    bundle: dict[str, Any] = {
        k: v for k, v in document.items() if k not in {"format", "members", "exported_at"}
    }
    bundle["export_type"] = _EXPORT_TYPE
    bundle.setdefault("export_version", "1")
    bundle["exported_at"] = datetime.now(UTC).isoformat()
    bundle["members"] = members
    return bundle


def _portable_member(member: dict[str, Any], name: str) -> dict[str, Any]:
    """Strip a member export to the shape the platform normalizer writes."""
    for key in _MEMBER_DROPPED:
        member.pop(key, None)
    for component in (member.get("components") or {}).values():
        for key in _COMPONENT_DROPPED:
            component.pop(key, None)
    for node in member.get("nodes") or []:
        for key in _NODE_DROPPED:
            node.pop(key, None)
        # The platform writes this key on every member node, null or not.
        node.setdefault("orchestration_asset_id", None)
    # Only the member's own stamp is dropped: the platform requires
    # ``exported_at`` on each nested child export.
    member["new_name"] = name
    return member
