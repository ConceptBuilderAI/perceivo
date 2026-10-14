"""Port contract matching and schema validation.

- **MAINFLOW** (unidirectional): the source's ``response_data_schema`` must cover
  the target's ``request_data_schema``. START is not exempt: its OUT port
  declares what START emits, like any other source.
- **INVOKE** (bidirectional): the forward request pair and the reverse response
  pair must each match. The source's ``request_data_schema`` covers the target's
  ``request_data_schema`` (forward), and the target's ``response_data_schema``
  covers the source's ``response_data_schema`` (reverse).

An empty declaration is **not** an escape hatch: a provider that
declares no fields while the consumer requires some is an error, because the
receiving script is promised fields nothing will supply. A consumer that requires
nothing is compatible with anything.

Contracts use the platform's **flat field-map** representation. Fields are
required by default and opt out only when their field dict explicitly sets
``required: False``. JSON Schema ``properties`` envelopes are rejected rather
than unwrapped so offline validation matches the platform import boundary.
"""

from __future__ import annotations

from typing import Any

from ._declaration_truth import parse_artifact_type as _parse_artifact_type
from ._type_names import canonical_type


def validate_port_contract_match(
    source_port: dict[str, Any],
    target_port: dict[str, Any],
    edge_kind: str = "",
) -> list[str]:
    """Validate that connected port schemas are contract-compatible.

    Args:
        source_port: Source port dict (request_data_schema / response_data_schema).
        target_port: Target port dict (request_data_schema / response_data_schema).
        edge_kind: Edge type ("MAINFLOW" or "INVOKE").

    Returns:
        List of error message strings (empty if valid).
    """
    errors: list[str] = []

    if edge_kind == "INVOKE":
        # Forward: source sends request_data to target.
        errors.extend(
            _schema_pair_errors(
                _schema_value(source_port, "request_data_schema"),
                _schema_value(target_port, "request_data_schema"),
                provider_label="source request_data_schema",
                consumer_label="target request_data_schema",
            )
        )
        # Reverse: target returns response_data to source.
        errors.extend(
            _schema_pair_errors(
                _schema_value(target_port, "response_data_schema"),
                _schema_value(source_port, "response_data_schema"),
                provider_label="target response_data_schema",
                consumer_label="source response_data_schema",
            )
        )
        return errors

    # MAINFLOW (and default): source response_data covers target request_data.
    # No START exemption: START's OUT port is a normal provider.
    errors.extend(
        _schema_pair_errors(
            _schema_value(source_port, "response_data_schema"),
            _schema_value(target_port, "request_data_schema"),
            provider_label="source response_data_schema",
            consumer_label="target request_data_schema",
        )
    )
    return errors


def _check_fields_covered(
    provider_fields: dict[str, dict[str, Any]],
    consumer_fields: dict[str, dict[str, Any]],
    provider_label: str,
    consumer_label: str,
) -> list[str]:
    """Return errors where *provider* fails to supply a field *consumer* requires.

    A consumer that requires nothing is compatible with anything. Every required
    consumer field must be present in the provider with a compatible type; an
    empty provider therefore fails any required consumer field, which is the
    "empty declaration is not an escape hatch" rule.
    """
    errors: list[str] = []
    for field_name, field_info in consumer_fields.items():
        error = _check_field_match(
            field_name, field_info, provider_fields, provider_label, consumer_label
        )
        if error:
            errors.append(error)
    return errors


def _check_field_match(
    field_name: str,
    field_info: dict[str, Any],
    provider_fields: dict[str, dict[str, Any]],
    provider_label: str,
    consumer_label: str,
) -> str | None:
    """Return an error string for a single consumer field, or None if it is valid."""
    if field_name not in provider_fields:
        if field_info.get("required", True):
            return (
                f"Required field '{field_name}' in {consumer_label} "
                f"is not provided by {provider_label}."
            )
        return None

    provider_type = canonical_type(provider_fields[field_name].get("type", ""))
    consumer_type = canonical_type(field_info.get("type", ""))
    if provider_type and consumer_type and provider_type != consumer_type:
        return (
            f"Field '{field_name}' type mismatch: "
            f"{provider_label} has '{provider_type}', {consumer_label} expects '{consumer_type}'."
        )
    return None


def validate_artifact_constraints(
    source_port: dict[str, Any],
    target_port: dict[str, Any],
    edge_kind: str = "",
) -> list[str]:
    """Validate artifact type compatibility between connected ports.

    Runs the platform's three checks, in its order and with its messages:

    1. The ``artifact_type_constraint`` equality check.
    2. The per-port ``artifact_type_output`` -> ``artifact_type_input`` check,
       including "target requires an artifact but the source declares none"
       and the JSON artifact schema check.
    3. For ``INVOKE`` edges only, the return leg: the target's
       ``artifact_type_output`` must satisfy the source's ``artifact_type_input``.

    Every message is an error; the platform refuses to save the orchestration.

    Returns:
        List of error message strings (empty if valid).
    """
    errors: list[str] = []
    errors.extend(_artifact_constraint_errors(source_port, target_port))
    errors.extend(_per_port_artifact_errors(source_port, target_port))
    if str(edge_kind or "").upper() == "INVOKE":
        errors.extend(_invoke_reverse_artifact_errors(source_port, target_port))
    return errors


def _artifact_constraint_errors(
    source_port: dict[str, Any], target_port: dict[str, Any]
) -> list[str]:
    """Check ``artifact_type_constraint`` equality."""
    source_constraint = source_port.get("artifact_type_constraint")
    target_constraint = target_port.get("artifact_type_constraint")
    if target_constraint and source_constraint and source_constraint != target_constraint:
        return [
            f"Artifact type mismatch: source provides {source_constraint}, "
            f"target expects {target_constraint}"
        ]
    return []


def _per_port_artifact_errors(
    source_port: dict[str, Any], target_port: dict[str, Any]
) -> list[str]:
    """Per-port ``artifact_type_output`` -> ``artifact_type_input`` compatibility."""
    source_output_type = source_port.get("artifact_type_output")
    target_input_type = target_port.get("artifact_type_input")

    if source_output_type and target_input_type:
        compatible, reason = _artifact_types_compatible(source_output_type, target_input_type)
        if not compatible:
            return [reason]
        if (
            _parse_artifact_type(source_output_type)[0] == "json"
            and _parse_artifact_type(target_input_type)[0] == "json"
        ):
            return _json_artifact_schema_errors(
                source_port.get("artifact_output_schema") or {},
                target_port.get("artifact_input_schema") or {},
            )
        return []

    # A target that requires an artifact, fed by a source that declares no
    # output type. An ``optional(...)`` target works without one, so it is exempt.
    target_base, target_optional = _parse_artifact_type(target_input_type)
    if (
        target_input_type
        and target_base != "none"
        and not target_optional
        and not source_output_type
    ):
        return [
            f"Target port requires artifact type '{target_input_type}' "
            f"but source port does not specify an output artifact type"
        ]

    if (
        source_output_type
        and _parse_artifact_type(source_output_type)[0] != "none"
        and target_base == "none"
        and target_input_type
    ):
        return [
            f"Source port produces artifact type '{source_output_type}' "
            f"but target port expects no artifact (none)"
        ]
    return []


def _json_artifact_schema_errors(source_schema: Any, target_schema: Any) -> list[str]:
    """Every field the target's JSON artifact schema names must exist, type-matched, in the source."""
    if not isinstance(source_schema, dict) or not isinstance(target_schema, dict):
        return []
    errors: list[str] = []
    for field_name, field_info in target_schema.items():
        if field_name not in source_schema:
            errors.append(
                f"Missing field in artifact schema: target expects '{field_name}' "
                f"but source does not provide it"
            )
            continue
        source_field = source_schema[field_name]
        source_field_type = source_field.get("type") if isinstance(source_field, dict) else None
        target_field_type = field_info.get("type") if isinstance(field_info, dict) else None
        if source_field_type and target_field_type and source_field_type != target_field_type:
            errors.append(
                f"Artifact schema type mismatch for field '{field_name}': "
                f"source has '{source_field_type}', target expects '{target_field_type}'"
            )
    return errors


def _invoke_reverse_artifact_errors(
    source_port: dict[str, Any], target_port: dict[str, Any]
) -> list[str]:
    """INVOKE return leg: the target's output must satisfy the source's input.

    The invoked component is the producer on the return leg and the caller is
    the consumer, so the forward lattice applies with the roles swapped.
    """
    target_output_type = target_port.get("artifact_type_output")
    source_input_type = source_port.get("artifact_type_input")

    if target_output_type and source_input_type:
        compatible, _reason = _artifact_types_compatible(target_output_type, source_input_type)
        if not compatible:
            return [
                f"INVOKE reverse artifact mismatch: target returns "
                f"'{target_output_type}', source expects '{source_input_type}'"
            ]
    elif source_input_type and source_input_type != "none" and not target_output_type:
        return [
            f"INVOKE reverse: source expects artifact type '{source_input_type}' "
            f"back from target, but target has no artifact_type_output"
        ]
    return []


def _artifact_types_compatible(source_output: Any, target_input: Any) -> tuple[bool, str]:
    """Can a port producing ``source_output`` feed one expecting ``target_input``?

    Returns ``(compatible, reason_if_not)``. ``optional(T)`` means "a T, or
    nothing". A producer of ``optional(T)`` cannot satisfy a consumer that
    requires ``T``; widening is allowed only in the safe direction.
    """
    src_base, src_optional = _parse_artifact_type(source_output)
    tgt_base, tgt_optional = _parse_artifact_type(target_input)

    if src_base == "none":
        if tgt_base == "none" or tgt_optional:
            return True, ""
        return False, (
            f"Port artifact type mismatch: source produces no artifact ('none') "
            f"but target requires '{target_input}'. Declare the target as "
            f"optional({tgt_base}) if it genuinely works without one."
        )
    if tgt_base == "none":
        return False, (
            f"Source port produces artifact type '{source_output}' "
            f"but target port expects no artifact (none)"
        )
    if src_base != tgt_base:
        return False, (
            f"Port artifact type mismatch: source produces '{source_output}', "
            f"target expects '{target_input}'"
        )
    if src_optional and not tgt_optional:
        return False, (
            f"Port artifact type mismatch: source declares '{source_output}', so "
            f"it may emit no artifact at all, but the target requires "
            f"'{target_input}'. Declare the target as optional({tgt_base}), or "
            f"make the source emit one on every path."
        )
    return True, ""


def _schema_value(port: dict[str, Any], key: str) -> Any:
    """Return a declared schema while preserving malformed falsey values."""
    value = port.get(key)
    return {} if value is None else value


def _is_json_schema_envelope(schema: dict[str, Any]) -> bool:
    """Whether ``schema`` structurally matches a JSON Schema object envelope.

    ``properties`` alone is not enough: it is also a valid platform field name
    in a flat contract such as ``{"properties": {"type": "string"}}``.
    """
    properties = schema.get("properties")
    return isinstance(properties, dict) and (
        schema.get("type") == "object" or isinstance(schema.get("required"), list)
    )


def _schema_pair_errors(
    provider: Any,
    consumer: Any,
    provider_label: str,
    consumer_label: str,
) -> list[str]:
    """Validate schema shapes, then compare their flat field declarations."""
    errors: list[str] = []
    for schema, label in ((provider, provider_label), (consumer, consumer_label)):
        if not isinstance(schema, dict):
            errors.append(
                f"Invalid {label}: schema must be a field mapping, found {type(schema).__name__}."
            )
        elif _is_json_schema_envelope(schema):
            errors.append(
                f"Invalid {label}: JSON Schema properties envelopes are not supported; "
                "declare fields directly."
            )
    if errors:
        return errors
    return _check_fields_covered(
        _extract_fields(provider),
        _extract_fields(consumer),
        provider_label=provider_label,
        consumer_label=consumer_label,
    )


def _extract_fields(schema: Any) -> dict[str, dict[str, Any]]:
    """Extract fields from the platform's flat schema-map representation.

    A malformed non-mapping returns no fields; public validation reports the
    shape error via ``_schema_pair_errors`` rather than raising an exception.
    Fields are required by default and opt out only with ``required: False``.
    JSON Schema ``properties`` envelopes are intentionally not unwrapped because
    the platform import validator does not accept that authoring form.

    Any other value (``None`` from a YAML ``name:`` with no value, a number, a
    list) declares a field that is present and required but has no type, as
    the platform reads it; it is never dropped.
    """
    fields: dict[str, dict[str, Any]] = {}
    if not isinstance(schema, dict):
        return fields

    for name, value in schema.items():
        if isinstance(value, str):
            fields[name] = {"type": value, "required": True}
        elif isinstance(value, dict):
            fields[name] = {
                "type": value.get("type", ""),
                "required": value.get("required", True),
            }
        else:
            fields[name] = {"type": "", "required": True}

    return fields
