"""Convert a local component (``<name>.py`` + ``<name>.yaml``) ⇄ a ``ComponentExport`` bundle.

Format bridges:

- local manifest key ``extension_type`` ⇄ bundle ``category``;
- local port ``port_type`` ⇄ bundle ``key`` + IN/OUT ``direction``: an explicit
  ``direction`` field wins, then ``_DIR_BY_PORT_TYPE`` (``{external_in,in}``→IN;
  ``{out,tool,policy,state,external_out,review}``→OUT), then IN;
- a component manifest declares ports in sections (``input_ports`` /
  ``output_ports`` / ``tool_ports`` / ``policy_ports`` / ``state_ports`` /
  ``review_ports`` / ``external_in_port`` / ``external_out_port``), which
  ``parse_manifest_ports`` reads;
- ``manifest_yaml`` is plaintext in the bundle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from perceivo._bundle._component_sidecar import (
    SIDECAR_SUFFIX,
    apply_sidecar,
    check_import_target,
    write_sidecar,
)
from perceivo._bundle._file_names import resolve_within, safe_stem, stem_from_display_name
from perceivo._bundle.schema import (
    ComponentExport,
    ComponentExportData,
    FSMStateExportData,
    PortExportData,
)
from perceivo.types.errors import ConfigurationError


class BundleConfigError(ConfigurationError, ValueError):
    """A local bundle/project carries invalid component configuration.

    Subclasses ``ConfigurationError`` and ``ValueError``.
    """


_COMPONENT_PROJECT_EXCLUDED = {"script", "manifest_yaml", "config", "ports"}


def restore_component_export_data(
    base: ComponentExportData, node: dict[str, Any]
) -> ComponentExportData:
    """Restore declared bundle fields retained separately from source/config.

    Imported projects keep compact declared metadata and ports on each node.
    Script, manifest, and component config retain their existing dedicated
    channels so this does not duplicate an entire component bundle in YAML.
    """
    if "component_metadata" not in node and "component_ports" not in node:
        return base
    metadata = node.get("component_metadata")
    if metadata is None:
        metadata = {}
    elif not isinstance(metadata, dict):
        raise BundleConfigError("Node component_metadata must be a mapping or null")
    allowed = set(ComponentExportData.model_fields) - _COMPONENT_PROJECT_EXCLUDED
    unknown = sorted(set(metadata) - allowed)
    if unknown:
        raise BundleConfigError(f"Node component_metadata has undeclared fields: {unknown}")

    payload = base.model_dump(mode="python")
    payload.update(metadata)
    if "component_ports" in node:
        ports = node["component_ports"]
        if not isinstance(ports, list):
            raise BundleConfigError("Node component_ports must be a list")
        payload["ports"] = ports
    return ComponentExportData.model_validate(payload)


# port_type -> bundle IN/OUT direction, used only when a port states no
# ``direction`` of its own (see ``_port_direction``). Undirected control ports
# (tool/policy/state/review) and external_out default to OUT.
_DIR_BY_PORT_TYPE: dict[str, str] = {
    "external_in": "IN",
    "in": "IN",
    "out": "OUT",
    "tool": "OUT",
    "policy": "OUT",
    "state": "OUT",
    "external_out": "OUT",
    "review": "OUT",
}

# Sectioned-manifest port declarations ("FORMAT A"): section key ->
# (port_type, direction, shape). ``external_*_port`` sections carry a single
# port dict; the ``*_ports`` sections carry a list of port dicts.
_FIXED_SECTIONS: dict[str, tuple[str, str, str]] = {
    "external_in_port": ("external_in", "IN", "dict"),
    "external_out_port": ("external_out", "OUT", "dict"),
    "tool_ports": ("tool", "OUT", "list"),
    "policy_ports": ("policy", "OUT", "list"),
    "state_ports": ("state", "OUT", "list"),
    "review_ports": ("review", "OUT", "list"),
}

# Sectioned lists whose items may carry their own ``port_type`` (default shown).
_TYPED_SECTIONS: dict[str, tuple[str, str]] = {
    "input_ports": ("in", "IN"),
    "output_ports": ("out", "OUT"),
}


def _port_direction(port: dict[str, Any]) -> str:
    """Return the bundle IN/OUT direction for a local manifest or orchestration port.

    An explicit ``direction`` wins: direction is data on the platform, and a
    ``tool`` port may be IN or OUT. Otherwise the ``port_type`` map decides, so an
    undirected ``tool``/``policy``/``state``/``review`` control port (and
    ``external_out``) resolves to OUT; anything else is IN. The validator applies
    the same order (``validator/_orchestration/_edges.py::port_direction``).
    """
    raw = str(port.get("direction") or "").lower()
    if raw in ("in", "out"):
        return raw.upper()
    return _DIR_BY_PORT_TYPE.get(str(port.get("port_type") or ""), "IN")


def _as_dict(value: Any) -> dict[str, Any] | None:
    """Return a structured schema field as a dict, or ``None``.

    A local manifest may carry a named base-schema string (e.g. ``StartOut``)
    instead; that form survives verbatim in ``manifest_yaml``.
    """
    return value if isinstance(value, dict) else None


def _port_from_item(item: dict[str, Any], port_type: str, direction: str) -> PortExportData:
    """Build one ``PortExportData`` from a sectioned manifest port item."""
    return PortExportData(
        direction=direction,
        key=str(item.get("port_name") or item.get("key") or item.get("name") or ""),
        label=item.get("description") or item.get("label"),
        port_type=port_type,
        base_schema=_as_dict(item.get("base_schema")),
        contract=_as_dict(item.get("contract")),
        request_data_schema=_as_dict(item.get("request_data") or item.get("request_data_schema")),
        response_data_schema=_as_dict(
            item.get("response_data") or item.get("response_data_schema")
        ),
        artifact_type_input=item.get("artifact_type_input"),
        artifact_type_output=item.get("artifact_type_output"),
        response_data_passthrough=item.get("response_data_passthrough"),
        response_data_passthrough_except=(item.get("response_data_passthrough_except") or None),
        response_data_contract=item.get("response_data_contract"),
        tool_call=port_type == "tool",
    )


def parse_manifest_ports(manifest: dict[str, Any]) -> list[PortExportData]:
    """Explicit ``PortExportData`` list from a sectioned component manifest.

    Sections: ``input_ports`` / ``output_ports`` / ``tool_ports`` /
    ``policy_ports`` / ``state_ports`` / ``review_ports`` (lists) plus
    ``external_in_port`` / ``external_out_port`` (single dicts). Section items
    use ``port_name`` / ``port_type`` / ``request_data`` / ``response_data`` /
    ``artifact_type_input`` / ``artifact_type_output`` / ``description``.
    """
    return [
        *_fixed_section_ports(manifest),
        *_typed_section_ports(manifest),
    ]


def _fixed_section_ports(manifest: dict[str, Any]) -> list[PortExportData]:
    """Sectioned form (canonical): fixed-type sections."""
    ports: list[PortExportData] = []
    for section, (port_type, direction, shape) in _FIXED_SECTIONS.items():
        block = manifest.get(section)
        if block is None:
            continue
        items = [block] if shape == "dict" else (block or [])
        ports.extend(
            _port_from_item(item, port_type, direction) for item in items if isinstance(item, dict)
        )
    return ports


def _typed_section_ports(manifest: dict[str, Any]) -> list[PortExportData]:
    """Sectioned form: typed lists (the item's own ``port_type`` wins if present)."""
    ports: list[PortExportData] = []
    for section, (default_type, default_dir) in _TYPED_SECTIONS.items():
        for item in manifest.get(section) or []:
            if not isinstance(item, dict):
                continue
            port_type = str(item.get("port_type") or default_type)
            ports.append(
                _port_from_item(item, port_type, _DIR_BY_PORT_TYPE.get(port_type, default_dir))
            )
    return ports


def node_declared_ports(
    manifest_ports: list[PortExportData],
    node_ports: list[dict[str, Any]],
) -> list[PortExportData]:
    """Merge orchestration-node port declarations into the component manifest.

    A local project may declare ports on the node; that is what
    ``Orchestration.add_node(extra_ports=...)`` writes, and it is how an AGENT gets
    the ``tool``/``policy``/``review`` port an INVOKE edge leaves through. The
    bundle has no node-level port declaration, and the platform resolves each
    orchestration port row against the component's ports, so node ports are merged
    onto the component.

    Fields carried only by an imported component port are copied onto a matching
    node declaration so a files-to-bundle round trip keeps them. The result is
    sorted by ``(key, direction)`` so declaration order does not affect component
    identity.
    """
    merged = {(port.key, port.direction): port for port in manifest_ports}
    for item in node_ports:
        if not isinstance(item, dict):
            continue
        direction = _port_direction(item)
        key = str(item.get("port_name") or item.get("key") or item.get("name") or "")
        if not key:
            continue
        port_type = str(item.get("port_type") or "") or ("in" if direction == "IN" else "out")
        declared = _port_from_item(item, port_type, direction)
        identity = (key, direction)
        existing = merged.get(identity)
        if existing is None:
            merged[identity] = declared
            continue
        updates = {
            name: value
            for name in (
                "request_data_schema",
                "response_data_schema",
                "artifact_type_input",
                "artifact_type_output",
                "artifact_type_constraint",
                "response_data_passthrough",
                "response_data_passthrough_except",
                "response_data_contract",
            )
            if (value := getattr(declared, name)) is not None
        }
        if updates:
            merged[identity] = existing.model_copy(update=updates)
    return sorted(merged.values(), key=lambda p: (p.key, p.direction))


def bundled_component_ports(
    base: ComponentExportData,
    node: dict[str, Any],
    node_ports: list[dict[str, Any]],
) -> list[PortExportData]:
    """The ports ``publish`` bundles onto *node*'s component.

    A node that pins ``component_ports`` (as every project materialized from a
    platform bundle does) keeps that list verbatim; any other node has its own
    port declarations merged onto *base*'s (:func:`node_declared_ports`). *base*
    is the component after :func:`restore_component_export_data`.
    """
    if "component_ports" in node:
        return base.ports
    return node_declared_ports(base.ports, node_ports)


def _manifest_fsm_states(manifest: dict[str, Any]) -> list[FSMStateExportData]:
    """Map local ``fsm_states`` (bare names or structured dicts) to export form."""
    states: list[FSMStateExportData] = []
    for entry in manifest.get("fsm_states") or []:
        if isinstance(entry, str):
            states.append(
                FSMStateExportData(
                    state_key=entry, label=entry, in_port_key=entry, out_port_key=entry
                )
            )
        elif isinstance(entry, dict):
            key = str(entry.get("state_key") or entry.get("key") or entry.get("name") or "")
            states.append(
                FSMStateExportData(
                    state_key=key,
                    label=str(entry.get("label") or key),
                    in_port_key=str(entry.get("in_port_key") or key),
                    out_port_key=str(entry.get("out_port_key") or key),
                    is_start=bool(entry.get("is_start", False)),
                    is_end=bool(entry.get("is_end", False)),
                )
            )
    return states


def _manifest_component_config(manifest: dict[str, Any]) -> dict[str, Any] | None:
    """Build platform component config from supported local manifest fields.

    Platform component creation enriches config from the manifest's parameter
    and secret declarations. Local source uses the same conventions. A literal
    ``config`` mapping remains authoritative for every key it names, so an
    author can preserve values beside declarations without a top-level field
    silently replacing them. An explicit ``config: null`` is authoritative too.
    """
    derived: dict[str, Any] = {}
    for key in ("input_parameters", "output_parameters"):
        declarations = manifest.get(key)
        if declarations:
            derived[key] = {"declarations": declarations, "values": {}}

    required_secrets = manifest.get("required_secrets")
    if required_secrets:
        derived["required_secrets"] = required_secrets

    if "config" not in manifest:
        return derived or None
    explicit = manifest["config"]
    if explicit is None:
        return None
    if not isinstance(explicit, dict):
        raise BundleConfigError("Component manifest 'config' must be a mapping or null")
    derived.update(explicit)
    return derived


def read_text(path: Path) -> str:
    """*path* as UTF-8 text with its newlines as written; a file it is not refuses the bundle."""
    from perceivo.validator._paths import read_text_or_reason

    text, reason = read_text_or_reason(path, str(path))
    if text is None:
        raise BundleConfigError(reason)
    return text


def manifest_mapping(manifest_raw: str, path: Path) -> dict[str, Any]:
    """*manifest_raw* parsed; export refuses one that is not a mapping, empty included."""
    manifest = yaml.safe_load(manifest_raw)
    if not isinstance(manifest, dict):
        kind = "nothing" if manifest is None else type(manifest).__name__
        raise BundleConfigError(f"{path}: a manifest must be a YAML mapping; it parses to {kind}")
    return manifest


#: The manifest keys export iterates, in the order it reads them.
ITERATED_KEYS: tuple[str, ...] = (
    *(section for section, (_, _, shape) in _FIXED_SECTIONS.items() if shape == "list"),
    *_TYPED_SECTIONS,
    "fsm_states",
)


def manifest_component(
    manifest_raw: str, manifest: dict[str, Any], *, name: str, script: str
) -> ComponentExportData:
    """The component *manifest* describes, before any record is applied.

    *name* stands in when the manifest names none. Validation builds the same
    component, so a manifest field export refuses is refused there too.
    """
    return ComponentExportData(
        name=str(manifest.get("name") or name),
        category=str(manifest.get("extension_type") or manifest.get("category") or "AGENT").upper(),
        description=manifest.get("description"),
        short_description=manifest.get("short_description"),
        tags=manifest.get("tags"),
        config=_manifest_component_config(manifest),
        additional_packages=manifest.get("additional_packages"),
        requires_gpu=bool(manifest.get("requires_gpu", False)),
        script=script,
        manifest_yaml=manifest_raw or None,
        ports=parse_manifest_ports(manifest),
        fsm_states=_manifest_fsm_states(manifest),
        template_source_id=_template_source_id(manifest),
        is_template_clone=_template_source_id(manifest) is not None,
    )


def component_to_bundle(
    script_path: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a platform ``ComponentExport`` bundle from a local component.

    Args:
        script_path: the component ``.py`` file.
        manifest_path: the manifest ``.yaml`` (defaults to ``<script>.yaml``).

    Returns:
        The bundle as a JSON-ready dict (``export_type: "component"``).
    """
    script = Path(script_path).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Script not found: {script}")
    # Bytes, not text mode: universal-newline reads rewrite a CRLF script to LF.
    code = read_text(script)

    mp = Path(manifest_path).resolve() if manifest_path else script.with_suffix(".yaml")
    manifest_raw = ""
    manifest: dict[str, Any] = {}
    if mp.exists():
        manifest_raw = read_text(mp)
        manifest = manifest_mapping(manifest_raw, mp)

    component = manifest_component(manifest_raw, manifest, name=script.stem, script=code)
    component = apply_sidecar(script, component, manifest_raw, manifest)
    bundle = ComponentExport(exported_at=datetime.now(UTC), component=component)
    return bundle.model_dump(mode="json")


def bundle_to_component(
    bundle: dict[str, Any],
    directory: str | Path = ".",
    *,
    name_override: str | None = None,
) -> Path:
    """Write a platform ``ComponentExport`` bundle out as local component files.

    Writes ``<name>.py``, ``<name>.yaml`` and ``<name>.component.json`` (the
    bundle fields neither file can hold; see ``_component_sidecar``). ``<name>``
    is ``name_override`` exactly, or else the bundle's display name lowercased
    with every run of other characters turned into ``_``.
    Returns the output directory. Raises ``ValueError``, writing nothing, on a
    non-component bundle, an unsafe or too-long name, or a name whose files
    already belong to a different component.
    """
    if bundle.get("export_type") != "component":
        raise ValueError("Input is not a portable component bundle (export_type != 'component')")
    parsed = ComponentExport.model_validate(bundle)
    comp = parsed.component

    # An override is the caller's own choice of file name, so it is used as given or refused.
    if name_override:
        name = safe_stem(name_override)
    else:
        name = safe_stem(stem_from_display_name(comp.name or ""))
    if not comp.script:
        raise ValueError("Component bundle contains no script source.")

    path = Path(directory).resolve()
    # Every check runs before the first write, so a refused import leaves no partial files.
    script, manifest_file, _ = (
        resolve_within(path, f"{name}{suffix}") for suffix in (".py", ".yaml", SIDECAR_SUFFIX)
    )
    check_import_target(comp, script, manifest_file)

    path.mkdir(parents=True, exist_ok=True)
    # newline="": a text-mode write on Windows turns every LF into CRLF.
    script.write_text(comp.script, encoding="utf-8", newline="")
    manifest_yaml = comp.manifest_yaml or _render_manifest(comp)
    manifest_file.write_text(manifest_yaml, encoding="utf-8", newline="")
    write_sidecar(script, comp, manifest_yaml)
    return path


def _template_source_id(manifest: dict[str, Any]) -> str | None:
    """The library template a manifest says it was cloned from, if any."""
    value = manifest.get("template_source_id")
    return str(value) if value else None


#: ``port_type -> manifest section`` for :func:`_render_manifest`.
_RENDER_SECTIONS: dict[str, str] = {
    "in": "input_ports",
    "external_in": "input_ports",
    "out": "output_ports",
    "external_out": "output_ports",
    "tool": "tool_ports",
    "policy": "policy_ports",
    "state": "state_ports",
    "review": "review_ports",
}


def _render_manifest(comp: ComponentExportData) -> str:
    """Reconstruct a local manifest from a bundle that carries no ``manifest_yaml``."""
    manifest: dict[str, Any] = {
        "extension_type": comp.category,
        "name": comp.name,
        "description": comp.description or "",
        "tags": comp.tags or [],
    }
    # Sectioned, the one manifest form the platform reads: a port's section is
    # fixed by its type, and the two typed sections carry it explicitly.
    for p in comp.ports:
        port_type = p.port_type or ("in" if p.direction.upper() == "IN" else "out")
        section = _RENDER_SECTIONS.get(port_type)
        if section is None:
            section = "input_ports" if p.direction.upper() == "IN" else "output_ports"
        item = {"port_name": p.key}
        if section in ("input_ports", "output_ports"):
            item["port_type"] = port_type
        manifest.setdefault(section, []).append(item)
    if comp.fsm_states:
        manifest["fsm_states"] = [s.state_key for s in comp.fsm_states]
    return yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
