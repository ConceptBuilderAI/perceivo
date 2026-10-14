"""Bundle-integrity checks the import path runs before it writes anything.

Split out of ``_bundle_project`` for the source-size limit. These checks decide
whether a bundle may become a project, so they remain one unit instead of being
scattered through the writer.
"""

from __future__ import annotations

import types
from collections.abc import Mapping, Sequence
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from perceivo._bundle.schema import OrchestrationExport


def reject_empty_component_scripts(
    data: dict[str, Any],
    components: dict[str, Any],
    nested: dict[str, Any],
) -> None:
    """Raise unless every component definition that will be written has source.

    A ``components`` entry whose ``script`` is missing, empty or whitespace-only
    would materialize as a 0-byte ``.py``, so it fails the import instead.

    A ref that also appears in ``nested`` is exempt and never written: the node
    referencing it carries ``orchestration_ref``, so its real content is the
    child bundle under ``orchestrations`` and the scriptless component definition
    beside it is redundant, not missing.
    """
    referenced_components = {
        str(node.get("component_ref") or "")
        for node in data.get("nodes") or []
        if isinstance(node, dict)
        and node.get("orchestration_asset_id")
        and node.get("component_ref")
    }
    offenders = [
        (ref, component)
        for ref, component in components.items()
        if isinstance(component, dict)
        and ref not in nested
        and ref not in referenced_components
        and not str(component.get("script") or "").strip()
    ]
    if offenders:
        raise ValueError(_empty_script_message(data, offenders))


class BundleFidelityError(ValueError):
    """The candidate changes or cannot faithfully represent a platform bundle."""


def assert_bundle_fidelity(
    original: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    allowed_differences: Sequence[str] = ("exported_at",),
) -> None:
    """Fail if *candidate* loses or changes typed platform export data.

    Raw key validation runs before Pydantic can apply defaults or discard an
    unknown field. Dictionaries declared as free-form values are leaves: their
    user keys are compared verbatim and never interpreted as schema fields.
    """
    raw_errors = [
        *_undeclared_model_keys(original, OrchestrationExport, "$original"),
        *_undeclared_model_keys(candidate, OrchestrationExport, "$candidate"),
    ]
    if raw_errors:
        raise BundleFidelityError("Undeclared platform fields:\n" + "\n".join(raw_errors))

    try:
        original_data = OrchestrationExport.model_validate(original).model_dump(mode="json")
        candidate_data = OrchestrationExport.model_validate(candidate).model_dump(mode="json")
    except ValidationError as exc:
        raise BundleFidelityError(f"Bundle schema validation failed: {exc}") from exc

    differences = _value_differences(
        original_data,
        candidate_data,
        path="",
        allowed=frozenset(allowed_differences),
    )
    if differences:
        raise BundleFidelityError("Bundle fidelity differences:\n" + "\n".join(differences))


def _undeclared_model_keys(value: Any, model: type[BaseModel], path: str) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    errors = [f"{path}.{key}" for key in value if key not in model.model_fields]
    for name, field in model.model_fields.items():
        if name not in value:
            continue
        errors.extend(_undeclared_for_annotation(value[name], field.annotation, f"{path}.{name}"))
    return errors


def _undeclared_for_annotation(value: Any, annotation: Any, path: str) -> list[str]:
    model = _model_type(annotation)
    if model is not None:
        return _undeclared_model_keys(value, model, path)

    sequence_errors = _undeclared_sequence_keys(value, annotation, path)
    if sequence_errors is not None:
        return sequence_errors
    mapping_errors = _undeclared_mapping_keys(value, annotation, path)
    if mapping_errors is not None:
        return mapping_errors
    return _undeclared_union_keys(value, annotation, path)


def _undeclared_sequence_keys(value: Any, annotation: Any, path: str) -> list[str] | None:
    """Return nested errors for a model-valued sequence, if this is one."""
    args = get_args(annotation)
    if get_origin(annotation) not in (list, tuple) or not args or not isinstance(value, Sequence):
        return None
    item_model = _model_type(args[0])
    if item_model is None:
        return []
    errors: list[str] = []
    for index, item in enumerate(value):
        errors.extend(_undeclared_model_keys(item, item_model, f"{path}[{index}]"))
    return errors


def _undeclared_mapping_keys(value: Any, annotation: Any, path: str) -> list[str] | None:
    """Return nested errors for a model-valued mapping, if this is one."""
    args = get_args(annotation)
    if get_origin(annotation) is not dict or len(args) != 2 or not isinstance(value, Mapping):
        return None
    item_model = _model_type(args[1])
    if item_model is None:
        return []
    errors: list[str] = []
    for key, item in value.items():
        errors.extend(_undeclared_model_keys(item, item_model, f"{path}.{key}"))
    return errors


def _undeclared_union_keys(value: Any, annotation: Any, path: str) -> list[str]:
    """Check the first structured union member, matching the prior traversal."""
    if get_origin(annotation) not in (types.UnionType, Union):
        return []
    for member in get_args(annotation):
        if member is type(None):
            continue
        nested = _undeclared_for_annotation(value, member, path)
        if _model_type(member) is not None or get_origin(member) in (list, dict):
            return nested
    return []


def _model_type(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (types.UnionType, Union):
        for member in get_args(annotation):
            if isinstance(member, type) and issubclass(member, BaseModel):
                return member
    return None


def _value_differences(
    original: Any,
    candidate: Any,
    *,
    path: str,
    allowed: frozenset[str],
) -> list[str]:
    if path in allowed:
        return []
    if isinstance(original, dict) and isinstance(candidate, dict):
        return _mapping_differences(original, candidate, path=path, allowed=allowed)
    if isinstance(original, list) and isinstance(candidate, list):
        return _list_differences(original, candidate, path=path, allowed=allowed)
    if original != candidate:
        return [f"{path}: {original!r} != {candidate!r}"]
    return []


def _mapping_differences(
    original: dict[Any, Any], candidate: dict[Any, Any], *, path: str, allowed: frozenset[str]
) -> list[str]:
    """Compare mapping membership and recursively compare shared entries."""
    differences: list[str] = []
    for key in sorted(set(original) | set(candidate)):
        child = f"{path}.{key}" if path else key
        if child in allowed:
            continue
        if key not in original:
            differences.append(f"{child}: added in candidate")
        elif key not in candidate:
            differences.append(f"{child}: missing from candidate")
        else:
            differences.extend(
                _value_differences(original[key], candidate[key], path=child, allowed=allowed)
            )
    return differences


def _list_differences(
    original: list[Any], candidate: list[Any], *, path: str, allowed: frozenset[str]
) -> list[str]:
    """Compare list lengths and paired entries in their original order."""
    differences: list[str] = []
    if len(original) != len(candidate):
        differences.append(f"{path}: length {len(original)} != {len(candidate)}")
    for index, (left, right) in enumerate(zip(original, candidate, strict=False)):
        differences.extend(
            _value_differences(left, right, path=f"{path}[{index}]", allowed=allowed)
        )
    return differences


def _empty_script_message(data: dict[str, Any], offenders: list[tuple[str, dict[str, Any]]]) -> str:
    """Name every offending component, the nodes using it, and the export."""
    nodes_by_ref: dict[str, list[dict[str, Any]]] = {}
    for node in data.get("nodes") or []:
        if isinstance(node, dict):
            nodes_by_ref.setdefault(str(node.get("component_ref") or ""), []).append(node)

    meta = data.get("orchestration") or {}
    lines = [
        f"Bundle contains {len(offenders)} component definition(s) with an empty "
        f"script; refusing to write 0-byte component files.",
    ]
    for ref, component in offenders:
        lines.append(
            f"  - components[{ref!r}]: name={str(component.get('name') or '<unnamed>')!r} "
            f"asset_id={component.get('asset_id') or '<none>'} "
            f"category={component.get('category') or '<none>'}; "
            f"used by {_describe_nodes(nodes_by_ref.get(ref, []))}"
        )
    lines.append(
        f"Source: orchestration {str(meta.get('name') or '<unnamed>')!r}, "
        f"export_type={data.get('export_type')!r}, "
        f"export_version={data.get('export_version')!r}, "
        f"exported_at={data.get('exported_at')!r}."
    )
    lines.append(
        "The export writes the component's source into components[ref]['script']; "
        "an empty one means the exporter had none to write. The producer is the "
        "orchestration exporter that built this bundle -- either the platform "
        "export operation or local `perceivo export` via "
        "perceivo/_bundle/orchestration.py. Re-export after ensuring the component "
        "source is available. A nested orchestration should be embedded as a child "
        "bundle rather than represented as a blank component."
    )
    return "\n".join(lines)


def _describe_nodes(nodes: list[dict[str, Any]]) -> str:
    if not nodes:
        return "no node in this bundle (orphaned component definition)"
    parts = []
    for node in nodes:
        node_id = node.get("local_id") or node.get("id") or "<no id>"
        marker = ""
        if node.get("orchestration_ref") or node.get("orchestration_asset_id"):
            marker = " [nested-orchestration node]"
        parts.append(f"node {node_id!r} ({str(node.get('name') or '<unnamed>')!r}){marker}")
    return ", ".join(parts)


def collect_additional_packages(data: dict[str, Any]) -> list[str]:
    """Return every distinct ``additional_packages`` entry declared anywhere in *data*.

    The bundle carries the list on the orchestration metadata and on each
    component definition, and the platform installs the union into the workload
    image. The local runner cannot install anything, so the union is written to
    ``orchestration.yaml`` and re-checked at run time.

    Nested bundles are folded in, because a nested orchestration executes in the
    parent's interpreter under the local runner. Each nested project also records
    its own subset, so running the child directly reports the same way.

    A component may also declare the list inside its ``manifest_yaml`` text
    rather than on the component definition; those entries are folded in too,
    since the platform builds the workload image from the component manifest.

    The result is sorted so a re-import of the same bundle produces a
    byte-identical project.
    """
    found: set[str] = set()
    meta = data.get("orchestration")
    if isinstance(meta, dict):
        found.update(_package_names(meta.get("additional_packages")))
    for component in (data.get("components") or {}).values():
        if isinstance(component, dict):
            found.update(_package_names(component.get("additional_packages")))
            found.update(_manifest_yaml_packages(component.get("manifest_yaml")))
    for child in (data.get("orchestrations") or {}).values():
        if isinstance(child, dict):
            found.update(collect_additional_packages(child))
    return sorted(found)


def _manifest_yaml_packages(raw: Any) -> list[str]:
    """Return the ``additional_packages`` declared inside a component's manifest text.

    An unparseable or non-mapping manifest contributes nothing here; manifest
    validity is reported by the validator, not by the package union.
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    import yaml

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return []
    if not isinstance(parsed, Mapping):
        return []
    return _package_names(parsed.get("additional_packages"))


def _package_names(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]
