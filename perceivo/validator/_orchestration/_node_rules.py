"""Node-config rules the platform enforces on import.

* REVIEW survey questions.
* ``VAL-SECRET-001``: every declared, non-optional secret must be bound.
* ``VAL-PARAM-001``: every required, visible input parameter must have a value.
* Phantom mainflow continuation.

**Each rule is a no-op when its config key is absent**, as on the platform. A
project that declares no secrets, no input parameters and no ``survey_spec`` is
unaffected.

The platform's exemptions apply too: a ``depends_on.visible_when`` parameter
that is hidden, an explicitly optional secret, a hidden combo-internal node,
and a Database Connector in platform mode are all skipped. Dropping any one of
them would reject projects the platform accepts.
"""

from __future__ import annotations

from typing import Any, Callable

from .._report import ValidationCheck
from ._ports import _positional_node_id

#: Component types whose in-port is bidirectional on the mainflow and therefore
#: produce a phantom out-port when converted.
PHANTOM_CAPABLE = ("AGENT", "FSM", "ADAPTER", "ORCHESTRATION")

#: Config keys carrying a phantom mainflow continuation; an export writes the
#: portable ``_node_ref`` in place of ``_node_id``.
MAINFLOW_TARGET_KEYS = ("mainflow_target_node_id", "mainflow_target_node_ref")


_REVIEW_SURVEY_SPEC = "REVIEW survey spec"


def _err(check_id: str, category: str, title: str, message: str) -> ValidationCheck:
    return ValidationCheck(
        id=check_id, severity="error", category=category, title=title, message=message
    )


def _node_label(node: dict[str, Any], config: dict[str, Any]) -> Any:
    return node.get("name") or config.get("label") or node.get("id", "?")


def _node_type_of(node: dict[str, Any], key: str, node_type: Callable[[str], str]) -> str:
    """Return the component type of ``node``, resolved by its raw id before its key.

    ``node_type`` is keyed by the id exactly as it came out of the bundle. YAML
    leaves an unquoted ``id: 1`` as the int ``1`` while ``_node_key`` returns
    ``"1"``, so a lookup by the stringified key alone would miss the node and read
    as UNKNOWN. Falls back to ``key`` for a node carrying no id at all.
    """
    raw_id = node.get("id")
    if raw_id is not None:
        resolved = node_type(raw_id)
        if resolved:
            return resolved
    return node_type(key)


def _validate_review_survey_spec(
    nodes: list[dict[str, Any]], node_type: Callable[[str], str]
) -> list[ValidationCheck]:
    """Every REVIEW node needs at least one well-formed survey question."""
    checks: list[ValidationCheck] = []
    for index, node in enumerate(nodes):
        node_id = _node_key(node, index)
        if _node_type_of(node, node_id, node_type) != "REVIEW":
            continue

        config = node.get("config") or {}
        survey_spec = config.get("survey_spec")
        if not survey_spec or not isinstance(survey_spec, list):
            name = (
                config.get("label") or config.get("name") or node.get("name") or f"node {node_id}"
            )
            checks.append(
                _err(
                    "ORCH-REVIEW-001",
                    "graph",
                    _REVIEW_SURVEY_SPEC,
                    f"REVIEW node '{name}' has no survey questions configured. "
                    f"At least one question is required in survey_spec.",
                )
            )
            continue

        for question_index, question in enumerate(survey_spec):
            checks.extend(_survey_question_checks(question_index, question))
    return checks


def _survey_question_checks(question_index: int, question: object) -> list[ValidationCheck]:
    if not isinstance(question, dict):
        return [
            _err(
                "ORCH-REVIEW-002",
                "graph",
                _REVIEW_SURVEY_SPEC,
                f"REVIEW node survey_spec[{question_index}] is not a valid question object.",
            )
        ]
    return [
        _err(
            "ORCH-REVIEW-003",
            "graph",
            _REVIEW_SURVEY_SPEC,
            f"REVIEW node survey_spec[{question_index}] is missing required '{field}' field.",
        )
        for field in ("id", "type", "label")
        if not question.get(field)
    ]


def _skip_secret_validation(config: dict[str, Any]) -> bool:
    """Nodes the server exempts from secret validation."""
    if config.get("is_agent_tool_internal"):
        return True
    if config.get("is_database_connector"):
        values = (config.get("input_parameters") or {}).get("values") or {}
        if values.get("connection_mode") == "platform":
            return True
    return False


def _secret_declarations(config: dict[str, Any]) -> list[Any]:
    declarations = config.get("required_secrets") or []
    from_bindings = (config.get("secret_bindings") or {}).get("declarations") or []
    if from_bindings and not declarations:
        declarations = from_bindings
    return declarations


def _validate_required_secrets(
    nodes: list[dict[str, Any]], node_type: Callable[[str], str]
) -> list[ValidationCheck]:
    """VAL-SECRET-001: every declared, non-optional secret must be bound."""
    checks: list[ValidationCheck] = []
    for node in nodes:
        config = node.get("config") or {}
        if _skip_secret_validation(config):
            continue
        declarations = _secret_declarations(config)
        if not declarations:
            continue

        bindings = (config.get("secret_bindings") or {}).get("bindings") or {}
        type_label = node_type(str(node.get("id", ""))) or "Component"
        name = _node_label(node, config)
        for secret in declarations:
            checks.extend(_secret_binding_checks(secret, bindings, type_label, name))
    return checks


def _secret_binding_checks(
    secret: object, bindings: dict[str, Any], type_label: str, node_name: Any
) -> list[ValidationCheck]:
    if not isinstance(secret, dict):
        return []
    key = secret.get("name") or secret.get("alias") or secret.get("key")
    if not key or secret.get("required") is False or bindings.get(key):
        return []
    label = secret.get("label") or key
    display = f'"{label}" ({key})' if label != key else f'"{key}"'
    return [
        _err(
            "ORCH-SECRET-001",
            "config",
            "Required secret not bound",
            f'[VAL-SECRET-001] {type_label} "{node_name}" requires secret {display} '
            f"but none is assigned. Set the secret's id (GUID) at "
            f"config.secret_bindings.bindings.{key} on this node.",
        )
    ]


def _param_hidden_by_depends_on(declaration: dict[str, Any], values: dict[str, Any]) -> bool:
    """True when a ``depends_on.visible_when`` rule hides this parameter."""
    depends_on = declaration.get("depends_on")
    if not isinstance(depends_on, dict):
        return False
    visible_when = depends_on.get("visible_when")
    parent = depends_on.get("parameter")
    if not (visible_when and parent):
        return False
    parent_value = values.get(parent)
    return parent_value is None or str(parent_value) not in [str(v) for v in visible_when]


def _validate_required_input_parameters(
    nodes: list[dict[str, Any]], node_type: Callable[[str], str]
) -> list[ValidationCheck]:
    """VAL-PARAM-001: every required, visible input parameter must have a value."""
    checks: list[ValidationCheck] = []
    for node in nodes:
        config = node.get("config") or {}
        parameters = config.get("input_parameters") or {}
        declarations = parameters.get("declarations") or []
        if not declarations:
            continue

        values = parameters.get("values") or {}
        type_label = node_type(str(node.get("id", ""))) or "Component"
        name = _node_label(node, config)
        for declaration in declarations:
            checks.extend(_required_param_checks(declaration, values, type_label, name))
    return checks


def _required_param_checks(
    declaration: object, values: dict[str, Any], type_label: str, node_name: Any
) -> list[ValidationCheck]:
    if not isinstance(declaration, dict):
        return []
    key = declaration.get("name")
    if not key or not declaration.get("required"):
        return []
    if _param_hidden_by_depends_on(declaration, values):
        return []
    value = values.get(key)
    if value is not None and value != "":
        return []
    label = declaration.get("label") or key
    display = f'"{label}" ({key})' if label != key else f'"{key}"'
    return [
        _err(
            "ORCH-PARAM-001",
            "config",
            "Required input parameter missing",
            f'[VAL-PARAM-001] {type_label} "{node_name}" requires input parameter '
            f"{display} but no value is set. Set it at "
            f"config.input_parameters.values.{key} on this node.",
        )
    ]


def _node_key(node: dict[str, Any], index: int) -> str:
    """Return a stable key for a node: its id when present, else its bundle index."""
    node_id = node.get("id")
    return str(node_id) if node_id is not None else str(index)


def _phantom_target_ref(config: dict[str, Any]) -> Any:
    """Return the phantom continuation target declared on this config, or ``None``.

    The first spelling whose value is neither ``None`` nor the empty string wins.
    ``""`` is absent: an export that blanks ``mainflow_target_node_id`` while
    writing the portable ``_node_ref`` beside it must resolve through
    ``_node_ref``.
    """
    for key in MAINFLOW_TARGET_KEYS:
        value = config.get(key)
        if value not in (None, ""):
            return value
    return None


def _resolve_phantom_target(target: Any, key_by_index: dict[int, str]) -> str | None:
    """Return the node key *target* names, or ``None`` when it names no node.

    Three forms resolve:

    * the exact node key, a UUID on a live orchestration;
    * the export local id ``node_N``;
    * a bare index ``N``, the bootstrap-bundle spelling.

    On this path an unresolvable target is an error, so reading fewer forms than
    the platform does would invent a false rejection. The exact key is tried first:
    a bundle whose node ids are literally ``node_5``/``node_9`` must match by
    identity, not be read as positions 5 and 9.

    ``_ports._positional_node_id`` is reused. Its positional-only caution applies to
    the exemption sets it feeds, where a broad reading silences a check; here the
    risk runs the other way.
    """
    ref = str(target)
    if ref in key_by_index.values():
        return ref
    return _positional_node_id(ref, key_by_index)


def _validate_phantom_mainflow_targets(
    nodes: list[dict[str, Any]], node_type: Callable[[str], str]
) -> list[ValidationCheck]:
    """Check that each ``config.mainflow_target_node_*`` hop resolves.

    The hop's source must also be a type that has a phantom out-port to carry the
    continuation.
    """
    checks: list[ValidationCheck] = []
    key_by_index = {i: _node_key(n, i) for i, n in enumerate(nodes)}
    for index, node in enumerate(nodes):
        config = node.get("config") or {}
        target = _phantom_target_ref(config)
        if target is None:
            continue

        source_id = key_by_index[index]
        if _resolve_phantom_target(target, key_by_index) is None:
            checks.append(
                _err(
                    "ORCH-PHANTOM-001",
                    "graph",
                    "Unresolvable mainflow continuation",
                    f"Phantom mainflow continuation on node '{source_id}' targets "
                    f"'{target}', which is not a node in this orchestration.",
                )
            )
            continue

        source_type = _node_type_of(node, source_id, node_type)
        if source_type not in PHANTOM_CAPABLE:
            checks.append(
                _err(
                    "ORCH-PHANTOM-002",
                    "graph",
                    "Invalid mainflow continuation source",
                    f"Node '{source_id}' declares a mainflow continuation but its type "
                    f"'{source_type or 'UNKNOWN'}' has no phantom out-port. Only "
                    f"{', '.join(PHANTOM_CAPABLE)} components continue the mainflow "
                    f"through a converted in-port.",
                )
            )
    return checks
