"""The template catalog behind :mod:`perceivo.templates`.

Two sources, one name set:

* **The platform template library**, vendored under ``_library/`` by
  ``scripts/vendor_template_library.py``: eighteen components and five
  orchestrations, each the library asset's own content rewritten to the public
  SDK surface. ``_library/SOURCE.json`` records the rewrite rules applied,
  each master's asset id and the sha256 of the source and vendored bytes.
* **Local scaffolds**: the SDK's own orchestration starters. They carry no
  library id, and none shares a name with a library entry.
"""

from __future__ import annotations

import builtins
import copy
import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Optional

from ..types.errors import NotFoundError

__all__ = ["Template", "get", "list", "use"]

TemplateKind = Literal["component", "orchestration"]

_KINDS: tuple[str, ...] = ("component", "orchestration")

#: The label the platform puts on a System-owned master's name. A copy is the
#: user's own, so ``use`` strips it from every string it writes.
_SYSTEM_LABEL = " (SYSTEM, READ-ONLY)"


@dataclass(frozen=True)
class Template:
    """One catalog entry.

    Attributes:
        name: The kebab-case name ``use`` and ``get`` take.
        kind: ``"component"`` or ``"orchestration"``.
        description: One line on what it builds.
        component_type: The component type a component template produces
            (``START``, ``AGENT``, ...); ``None`` for an orchestration template.
        template_source_id: The platform library asset this entry is vendored
            from; ``None`` for a local scaffold.
    """

    name: str
    kind: TemplateKind
    description: str
    component_type: Optional[str] = None
    template_source_id: Optional[str] = None


def _library_root() -> Any:
    return resources.files("perceivo.templates") / "_library"


@cache
def _source_record() -> dict[str, Any]:
    return json.loads((_library_root() / "SOURCE.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _library_bundle(name: str) -> dict[str, Any]:
    """A fresh copy of the vendored bundle for library entry *name*."""
    entry = _source_record()["entries"][name]
    return copy.deepcopy(_parsed_bundle(entry["file"]))


@cache
def _parsed_bundle(file_name: str) -> dict[str, Any]:
    return json.loads((_library_root() / file_name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _first_sentence(text: str) -> str:
    text = re.sub(r"^What it does\s+", "", (text or "").strip())
    head = re.split(r"(?<=\.)\s", text, maxsplit=1)[0]
    return head.strip()


def _library_entries() -> tuple[Template, ...]:
    entries = []
    for name, entry in _source_record()["entries"].items():
        bundle = _parsed_bundle(entry["file"])
        if entry["kind"] == "component":
            comp = bundle["component"]
            entries.append(
                Template(
                    name,
                    "component",
                    comp.get("short_description") or _first_sentence(comp.get("description", "")),
                    str(comp["category"]).upper(),
                    entry["asset_id"],
                )
            )
        else:
            meta = bundle["orchestration"]
            entries.append(
                Template(
                    name,
                    "orchestration",
                    _first_sentence(meta.get("description", "")),
                    None,
                    entry["asset_id"],
                )
            )
    return tuple(entries)


def _scaffold_entries() -> tuple[Template, ...]:
    from ..orchestration._builder._templates import _ORCHESTRATION_TEMPLATES

    return tuple(
        Template(name, "orchestration", spec["description"])
        for name, spec in sorted(_ORCHESTRATION_TEMPLATES.items())
    )


def _catalog() -> tuple[Template, ...]:
    entries = _library_entries() + _scaffold_entries()
    return tuple(sorted(entries, key=lambda t: (t.kind, t.name)))


def list(kind: Optional[str] = None) -> builtins.list[Template]:
    """Every template, or every template of one kind.

    Args:
        kind: ``"component"``, ``"orchestration"``, or ``None`` for both.

    Raises:
        ValueError: ``kind`` is not one of the two kinds.

    Example:
        ```python
        for t in perceivo.templates.list(kind="component"):
            print(t.name, t.component_type, t.description)
        ```
    """
    if kind is not None and kind not in _KINDS:
        raise ValueError(f"Unknown template kind {kind!r}. Expected one of: {', '.join(_KINDS)}.")
    return [t for t in _catalog() if kind is None or t.kind == kind]


def get(name: str) -> Template:
    """The catalog entry named *name*.

    Raises:
        NotFoundError: No template has that name.
    """
    for template in _catalog():
        if template.name == name:
            return template
    available = ", ".join(t.name for t in _catalog())
    raise NotFoundError(f"Unknown template {name!r}. Available: {available}")


def _unlabelled(value: Any) -> Any:
    """*value* with the System label removed from every string in it."""
    if isinstance(value, str):
        return value.replace(_SYSTEM_LABEL, "")
    if isinstance(value, builtins.list):
        return [_unlabelled(item) for item in value]
    if isinstance(value, dict):
        return {key: _unlabelled(item) for key, item in value.items()}
    return value


def _stamped_component_bundle(name: str) -> dict[str, Any]:
    """Library component *name* as a clone: label stripped, source id stamped."""
    template = get(name)
    bundle = _unlabelled(_library_bundle(name))
    comp = bundle["component"]
    comp["template_source_id"] = template.template_source_id
    comp["is_template_clone"] = True
    manifest = comp.get("manifest_yaml") or ""
    if manifest and not manifest.endswith("\n"):
        manifest += "\n"
    comp["manifest_yaml"] = manifest + f"template_source_id: {template.template_source_id}\n"
    return bundle  # type: ignore[no-any-return]


def _component_sources(name: str) -> tuple[str, str]:
    """The script and manifest text ``use`` writes for library component *name*."""
    comp = _stamped_component_bundle(name)["component"]
    return comp["script"], comp["manifest_yaml"]


def use(name: str, into: str = ".", *, as_name: Optional[str] = None) -> Path:
    """Copy template *name* into a project.

    Args:
        name: A template name from :func:`list`.
        into: The directory to write into; created if absent.
        as_name: The name to give the copy. Defaults to the template name in
            snake case.

    Returns:
        The directory written. For a component template it holds
        ``<as_name>.py`` and ``<as_name>.yaml``, and the manifest carries the
        library master's ``template_source_id``; for an orchestration template
        it is a full orchestration project.

    Raises:
        NotFoundError: No template has that name.

    Example:
        ```python
        path = perceivo.templates.use("llm-request", into="./my_project")
        # ./my_project/llm_request/llm_request.py and llm_request.yaml
        ```
    """
    template = get(name)
    target_name = as_name or template.name.replace("-", "_")
    into_path = Path(into).resolve()

    if template.template_source_id is None:
        from ..orchestration._builder._templates import (
            _ORCHESTRATION_TEMPLATES,
            _scaffold_from_template,
        )

        target = into_path / target_name
        target.mkdir(parents=True, exist_ok=True)
        return _scaffold_from_template(
            target_name, target, _ORCHESTRATION_TEMPLATES[template.name], ""
        )

    if template.kind == "component":
        from ..component import load_bundle as load_component

        return load_component(
            _stamped_component_bundle(template.name),
            str(into_path / target_name),
            name_override=target_name,
        )

    from ..orchestration import load_bundle as load_orchestration

    bundle = _unlabelled(_library_bundle(template.name))
    return load_orchestration(bundle, str(into_path), name_override=target_name)
