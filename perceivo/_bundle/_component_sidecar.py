"""The bundle fields a local component's script and manifest cannot hold.

A platform component bundle carries more than ``script`` and ``manifest_yaml``:
the component's own ``name`` and ``description``, ``short_description``, the
artifact types, ``config`` (parameter values, port order and sides, detected
endpoints), every port's label, contract and schemas, FSM state detail, and the
template / prebuilt links.

The importer writes ``<name>.component.json`` holding every bundle field except
the script and manifest, plus a hash of the manifest it wrote. The exporter
reads it back:

* manifest unchanged -> the recorded fields are the bundle, exactly;
* manifest edited    -> the manifest wins for ports, config, FSM states and every
  field whose key it declares; the rest still comes from the record.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from perceivo._bundle.schema import ComponentExportData

SIDECAR_SUFFIX = ".component.json"
_FORMAT = "perceivo_component_fields_v1"
_NOT_RECORDED = frozenset({"script", "manifest_yaml"})
# Once the manifest is edited, it owns the fields derived from its whole body...
_MANIFEST_DERIVED = frozenset({"config", "ports", "fsm_states"})
# ... and each of these whenever it declares the key(s) the field is read from.
_MANIFEST_KEYED: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "category": ("extension_type", "category"),
    "description": ("description",),
    "short_description": ("short_description",),
    "tags": ("tags",),
    "additional_packages": ("additional_packages",),
    "requires_gpu": ("requires_gpu",),
    "template_source_id": ("template_source_id",),
    "is_template_clone": ("template_source_id",),
}


def sidecar_path(script: Path) -> Path:
    """The record that sits beside *script* (``<stem>.component.json``)."""
    return script.with_name(script.stem + SIDECAR_SUFFIX)


def read_manifest(path: Path) -> str:
    """The manifest as the record's digest covers it.

    Bytes, not text mode: a universal-newline read rewrites CRLF to LF, so a
    reader in text mode would match a digest the exporter does not.
    """
    return path.read_bytes().decode("utf-8")


def _digest(manifest_raw: str) -> str:
    return hashlib.sha256(manifest_raw.encode("utf-8")).hexdigest()


def write_sidecar(script: Path, component: ComponentExportData, manifest_raw: str) -> Path:
    """Record *component*'s bundle fields beside *script*."""
    record = {
        "format": _FORMAT,
        "manifest_sha256": _digest(manifest_raw),
        "component": component.model_dump(mode="json", exclude=set(_NOT_RECORDED)),
    }
    path = sidecar_path(script)
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline=""
    )
    return path


def recorded_fields(
    script: Path, manifest_raw: str, manifest: dict[str, Any]
) -> dict[str, Any] | None:
    """The recorded fields that overlay the manifest-derived component.

    None when *script* has no record. A record the exporter cannot read raises
    ``BundleConfigError``; an edited manifest drops the fields it owns.
    """
    path = sidecar_path(script)
    if not path.is_file():
        return None
    from perceivo._bundle.component import BundleConfigError

    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleConfigError(f"{path} is not valid JSON: {exc}") from exc
    except (UnicodeDecodeError, OSError) as exc:
        raise BundleConfigError(f"{path} cannot be read: {exc}") from exc
    if not isinstance(record, dict) or record.get("format") != _FORMAT:
        raise BundleConfigError(f"{path} is not a {_FORMAT} record")
    fields = record.get("component")
    if not isinstance(fields, dict):
        raise BundleConfigError(f"{path}: 'component' must be a mapping")
    unknown = sorted(set(fields) - (set(ComponentExportData.model_fields) - _NOT_RECORDED))
    if unknown:
        raise BundleConfigError(f"{path} has undeclared fields: {unknown}")
    if record.get("manifest_sha256") != _digest(manifest_raw):
        owned = set(_MANIFEST_DERIVED) | {
            field for field, keys in _MANIFEST_KEYED.items() if any(k in manifest for k in keys)
        }
        fields = {k: v for k, v in fields.items() if k not in owned}
    return fields


def apply_sidecar(
    script: Path, derived: ComponentExportData, manifest_raw: str, manifest: dict[str, Any]
) -> ComponentExportData:
    """Overlay the recorded fields beside *script* onto the manifest-derived component."""
    fields = recorded_fields(script, manifest_raw, manifest)
    if fields is None:
        return derived
    payload: dict[str, Any] = derived.model_dump(mode="python")
    payload.update(fields)
    return ComponentExportData.model_validate(payload)


# The longest file name most filesystems accept for one path segment.
_MAX_FILE_NAME = 255


def check_import_target(component: ComponentExportData, script: Path, manifest: Path) -> None:
    """Refuse, before anything is written, an import that cannot land whole at *script*.

    The sidecar's name is the longest of the three, so it bounds the name length.
    Different display names can share a stem, so existing files are only replaced
    when their record is this same component: the same platform ``asset_id`` when
    both carry one, otherwise the same display name. Files with no readable record
    cannot be shown to be this component and are kept.
    """
    from perceivo._bundle.component import BundleConfigError

    record = sidecar_path(script)
    if len(record.name.encode("utf-8")) > _MAX_FILE_NAME:
        raise BundleConfigError(
            f"Component name {script.stem!r} is too long: {record.name!r} exceeds "
            f"{_MAX_FILE_NAME} bytes. Pass --name (name_override) to choose a shorter one."
        )
    if not any(path.exists() for path in (script, manifest, record)):
        return
    recorded = _recorded_fields(record)
    if recorded is not None and _same_component(recorded, component):
        return
    holder = repr(recorded.get("name")) if recorded else "files with no import record"
    raise BundleConfigError(
        f"Refusing to import {component.name!r} as {script.stem!r}: {script.parent} already "
        f"holds {holder} under that name. Pass --name (name_override) to choose another file name."
    )


def _recorded_fields(record: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    # A record nested past the parser's depth is as unreadable as a malformed one.
    except (OSError, ValueError, RecursionError):
        return None
    fields = data.get("component") if isinstance(data, dict) else None
    return fields if isinstance(fields, dict) else None


def _same_component(recorded: dict[str, Any], component: ComponentExportData) -> bool:
    if recorded.get("asset_id") and component.asset_id:
        return bool(recorded["asset_id"] == component.asset_id)
    return recorded.get("name") == component.name
