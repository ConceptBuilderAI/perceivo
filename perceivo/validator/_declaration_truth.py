"""Manifest capability truth checks and ``response_data`` resolvability.

These run for every orchestration component whose manifest parsed, as the
platform runs them at import:

* ``VAL-CONTRACT-OPTIONAL``: ``optional(T)`` on an input whose script
  dereferences ``input.artifact`` unguarded; ``optional(T)`` on an output the
  script always (or never) emits; a bare ``T`` output the script emits only
  conditionally (e.g. a START forwarding ``input.artifact``).
* ``VAL-CONTRACT-PASSTHROUGH`` / ``VAL-CONTRACT-DYNAMIC``: a capability
  declaration the script does not bear out. These gate the resolution below: a
  false capability resolves nothing.
* ``VAL-UNVERIFIABLE-RESP``: a returned ``response_data`` whose key set
  cannot be enumerated and that no verified capability accounts for. This is a
  blocking "could not check" outcome, not a pass.
* ``VAL-CONTRACT-RESP``: returned fields the output ports do not declare, and
  declared fields the script never returns (see
  :mod:`perceivo.validator._response_contract`); fields read from a
  ``flow.invoke`` result that the invoked port does not declare (see
  :mod:`perceivo.validator._invoke_reads`).

Check ids, severities, titles and messages match the platform's so a local
report reads like the import refusal it predicts.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Callable, Optional

from ._invoke_reads import check_invoke_response_data_reads
from ._response_contract import ResponseDataResolution, check_response_contract
from ._script_facts import (
    analyze_response_data,
    artifact_emit_mode,
    artifact_read_mode,
    dropped_passthrough_keys,
    request_data_derived_vars,
    spread_is_request_data_passthrough,
)

AddCheck = Callable[..., None]

#: The orchestration component types these checks run for.
ORCHESTRATION_TYPES = frozenset(
    {"AGENT", "TOOL", "FSM", "POLICY", "START", "END", "REVIEW", "ADAPTER"}
)

PASSTHROUGH_KEY = "response_data_passthrough"
PASSTHROUGH_EXCEPT_KEY = "response_data_passthrough_except"
DYNAMIC_KEY = "response_data_contract"
DYNAMIC_REASON_KEY = "response_data_contract_reason"
PASSTHROUGH_SOURCE = "request_data"
DYNAMIC_VALUE = "dynamic"

_OPTIONAL_RE = re.compile(r"^optional\(\s*([a-z0-9_]+)\s*\)$", re.IGNORECASE)


def parse_artifact_type(raw: Any) -> tuple[str, bool]:
    """``optional(file)`` -> ``("file", True)``; ``file`` -> ``("file", False)``."""
    text = str(raw or "").strip()
    match = _OPTIONAL_RE.match(text)
    if match:
        return match.group(1).strip().lower(), True
    return text.lower(), False


# Manifest values are read from the raw YAML mapping, with exact spelling.


def _input_role_sections(component_type: str) -> tuple[str, ...]:
    return ("external_in_port", "input_ports") if component_type == "START" else ("input_ports",)


_OUTPUT_ROLE_SECTIONS: dict[str, tuple[str, ...]] = {
    "START": ("output_ports", "external_out_port"),
    "END": ("external_out_port", "output_ports"),
}
_DEFAULT_OUTPUT_ROLE_SECTIONS: tuple[str, ...] = (
    "input_ports",
    "output_ports",
    "external_out_port",
)


def _output_role_sections(component_type: str) -> tuple[str, ...]:
    return _OUTPUT_ROLE_SECTIONS.get(component_type, _DEFAULT_OUTPUT_ROLE_SECTIONS)


def _section_ports(parsed: dict[str, Any], sections: tuple[str, ...]) -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    for section in sections:
        entry = parsed.get(section) or []
        if isinstance(entry, dict):
            entry = [entry]
        if isinstance(entry, list):
            ports.extend(p for p in entry if isinstance(p, dict))
    return ports


def _field_names(data_def: Any) -> set[str]:
    """Field names of a ``request_data``/``response_data`` declaration.

    Dict form: every key whose value is a type string or a field mapping. List
    form: every item's non-empty ``name``.
    """
    if not data_def:
        return set()
    if isinstance(data_def, list):
        return {i["name"] for i in data_def if isinstance(i, dict) and i.get("name")}
    if not isinstance(data_def, dict):
        return set()
    return {k for k, v in data_def.items() if isinstance(v, (str, dict))}


def _declares_passthrough(port: dict[str, Any]) -> bool:
    return str(port.get(PASSTHROUGH_KEY) or "").strip().lower() == PASSTHROUGH_SOURCE


def _declares_dynamic(port: dict[str, Any]) -> bool:
    return str(port.get(DYNAMIC_KEY) or "").strip().lower() == DYNAMIC_VALUE


def _passthrough_exceptions(ports: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for port in ports:
        if not _declares_passthrough(port):
            continue
        raw = port.get(PASSTHROUGH_EXCEPT_KEY) or []
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(",")]
        if isinstance(raw, list):
            out |= {str(item).strip() for item in raw if str(item).strip()}
    return out


def _check_optional_artifact(
    tree: ast.AST, inputs: list[dict], outputs: list[dict], add: AddCheck  # type: ignore[type-arg]
) -> None:  # fmt: skip
    def optional(port: dict, key: str) -> bool:  # type: ignore[type-arg]
        return parse_artifact_type(port.get(key))[1]

    if any(optional(p, "artifact_type_input") for p in inputs):
        if artifact_read_mode(tree) == "required":
            add(
                "VAL-CONTRACT-OPTIONAL",
                "error",
                "contract",
                "optional(...) artifact input declared, but the script requires one",
                "An input port declares artifact_type_input: optional(...), which "
                "means the script works whether or not an artifact arrives. The "
                "script dereferences input.artifact without guarding against its "
                "absence, so it would fail when none is supplied — that "
                "declaration is false.",
                suggestion="Guard every artifact read (e.g. `if input.artifact:`), or declare "
                "the concrete artifact_type_input the script actually requires.",
            )

    emit_mode = artifact_emit_mode(tree)
    if any(optional(p, "artifact_type_output") for p in outputs):
        if emit_mode == "unconditional":
            add(
                "VAL-CONTRACT-OPTIONAL",
                "error",
                "contract",
                "optional(...) artifact output declared, but the script always emits",
                "An output-role port declares artifact_type_output: optional(...), "
                "which tells downstream components an artifact MAY be absent. Every "
                "return path emits one, so the weaker promise is false and would "
                "wrongly block consumers that legitimately require the artifact.",
                suggestion="Declare the concrete artifact_type_output the script always emits.",
            )
        elif emit_mode == "none":
            add(
                "VAL-CONTRACT-OPTIONAL",
                "error",
                "contract",
                "optional(...) artifact output declared, but the script emits none",
                "An output-role port declares artifact_type_output: optional(...) "
                "but no returned Output carries an artifact — that declaration is "
                "false.",
                suggestion="Declare artifact_type_output: none.",
            )

    bare = [
        p
        for p in outputs
        if p.get("artifact_type_output")
        and parse_artifact_type(p.get("artifact_type_output"))[0] != "none"
        and not optional(p, "artifact_type_output")
    ]
    if bare and emit_mode == "conditional":
        add(
            "VAL-CONTRACT-OPTIONAL",
            "error",
            "contract",
            "Artifact output declared unconditionally but emitted conditionally",
            "An output-role port declares a concrete artifact_type_output, which "
            "promises downstream components that an artifact always arrives. The "
            "script emits one only on some paths (or forwards an inbound artifact "
            "that may itself be absent) — that declaration is false.",
            suggestion="Declare artifact_type_output: optional(<type>) to state that the "
            "artifact may be absent, or emit one on every return path.",
        )


def _check_passthrough(tree: ast.AST, ports: list[dict], add: AddCheck) -> None:  # type: ignore[type-arg]
    if not any(_declares_passthrough(p) for p in ports):
        return
    shape = analyze_response_data(tree)
    if not any(spread_is_request_data_passthrough(tree, s) for s in shape.spread_exprs):
        add(
            "VAL-CONTRACT-PASSTHROUGH",
            "error",
            "contract",
            "response_data_passthrough declared, but nothing is passed through",
            "A port declares response_data_passthrough: request_data, which states "
            "that the script forwards every field of its inbound request_data. The "
            "script's returned response_data contains no spread of request_data, so "
            "that declaration is false — downstream ports would be promised fields "
            "that never arrive.",
            suggestion="Spread the inbound request_data into the returned response_data "
            "(`{**request_data, ...}`), or remove the passthrough declaration.",
        )
        return

    actually = dropped_passthrough_keys(tree, request_data_derived_vars(tree))
    declared = _passthrough_exceptions(ports)
    undeclared = sorted(actually - declared)
    if undeclared:
        add(
            "VAL-CONTRACT-PASSTHROUGH",
            "error",
            "contract",
            "Passthrough declaration does not name the fields it strips",
            f"The port declares it forwards the whole inbound request_data, but the "
            f"script removes {undeclared} before returning it. A passthrough "
            f"that silently drops fields is a false declaration.",
            suggestion=f"Add {undeclared} to {PASSTHROUGH_EXCEPT_KEY} on the port, or "
            f"stop removing them in the script.",
        )
    phantom = sorted(declared - actually)
    if phantom:
        add(
            "VAL-CONTRACT-PASSTHROUGH",
            "error",
            "contract",
            "Passthrough exception list names fields the script keeps",
            f"The port declares {PASSTHROUGH_EXCEPT_KEY}: {phantom}, but the "
            f"script does not remove those fields — they are forwarded. Declaring a "
            f"field as stripped when it is not hides it from downstream contracts.",
            suggestion=f"Remove {phantom} from {PASSTHROUGH_EXCEPT_KEY}, or strip them "
            f"in the script.",
        )


def _check_dynamic(tree: ast.AST, ports: list[dict], add: AddCheck) -> None:  # type: ignore[type-arg]
    declaring = [p for p in ports if _declares_dynamic(p)]
    if not declaring:
        return
    shape = analyze_response_data(tree)
    if not shape.qualifies_as_dynamic:
        if shape.spread_exprs:
            detail = (
                "the only thing hiding keys is a request_data spread, which is exactly "
                f"describable — declare {PASSTHROUGH_KEY}: {PASSTHROUGH_SOURCE} instead"
            )
        else:
            detail = (
                f"the script returns a fixed dict literal with keys "
                f"{sorted(shape.literal_keys)}, so a fixed contract does exist"
            )
        add(
            "VAL-CONTRACT-DYNAMIC",
            "error",
            "contract",
            "Dynamic response_data contract declared, but the contract is fixed",
            f"A port declares {DYNAMIC_KEY}: {DYNAMIC_VALUE}, which states that the "
            f"returned key set is determined by node configuration and cannot be "
            f"enumerated statically. That is not true of this script: {detail}.",
            suggestion="Declare the fields the script actually returns, or use the capability "
            "that precisely describes why they are not enumerable.",
        )
        return
    for port in declaring:
        if str(port.get(DYNAMIC_REASON_KEY) or "").strip():
            continue
        add(
            "VAL-CONTRACT-DYNAMIC",
            "error",
            "contract",
            "Dynamic response_data contract declared without a reason",
            f"Port '{port.get('port_name') or '<unnamed>'}' declares "
            f"{DYNAMIC_KEY}: {DYNAMIC_VALUE} but gives no "
            f"{DYNAMIC_REASON_KEY}. A contract that cannot be checked "
            f"statically must at least say what determines it, or the "
            f"declaration is unreviewable.",
            suggestion=f"Add {DYNAMIC_REASON_KEY} naming the configuration that "
            f"determines the key set (e.g. the parameter or port list it is "
            f"derived from).",
        )


def _resolve_response_data(
    tree: ast.AST, inputs: list[dict], outputs: list[dict], *, honour_capabilities: bool  # type: ignore[type-arg]
) -> ResponseDataResolution:  # fmt: skip
    """Resolve the returned ``response_data``, honouring verified capabilities.

    A spread is cleared only by a verified passthrough of the inbound
    request_data, which forwards the input ports' declared ``request_data``
    minus the declared exceptions; a computed / non-literal key set only by a
    verified dynamic contract. A capability that failed its own truth check
    clears nothing. Keys are read through a ``perceivo.primitives`` module alias
    as well.
    """
    shape = analyze_response_data(tree, through_primitives_aliases=True)
    visible = set(shape.literal_keys)
    satisfiable = set(visible)
    passthrough = honour_capabilities and any(_declares_passthrough(p) for p in outputs)
    dynamic = honour_capabilities and any(_declares_dynamic(p) for p in outputs)
    unresolved_spread = False
    if shape.spread_exprs:
        if passthrough and all(
            spread_is_request_data_passthrough(tree, s) for s in shape.spread_exprs
        ):
            forwarded: set[str] = set()
            for port in inputs:
                forwarded |= _field_names(port.get("request_data"))
            satisfiable |= forwarded - _passthrough_exceptions(outputs)
        else:
            unresolved_spread = True
    unverifiable = unresolved_spread or (shape.qualifies_as_dynamic and not dynamic)
    return ResponseDataResolution(
        visible_keys=visible,
        satisfiable_keys=satisfiable,
        unverifiable=unverifiable,
        check_declared_are_returned=not unverifiable and not shape.qualifies_as_dynamic,
    )


def _report_unverifiable(script_keys: set[str], declared: set[str], add: AddCheck) -> None:
    if script_keys:
        reason = (
            f"it is built with a spread or computed key, so only {sorted(script_keys)} "
            f"are visible. Fields declared but never returned cannot be detected"
            + (f" (declared: {sorted(declared)})" if declared else "")
        )
        add(
            "VAL-UNVERIFIABLE-RESP",
            "error",
            "unverifiable",
            "response_data only partially resolvable",
            "The script's Output response_data could not be fully enumerated. "
            f"Could not verify: {reason}",
            suggestion="Return response_data as a dict literal with constant string keys so the "
            "declaration can be checked in both directions.",
        )
        return
    reason = (
        "it is built dynamically (e.g. `response_data = dict(request_data)` followed "
        "by `.update(...)`, or assigned from a call), so NO field is statically "
        "visible and neither direction of the response_data contract can be checked"
        + (f". The manifest declares: {sorted(declared)}" if declared else "")
    )
    add(
        "VAL-UNVERIFIABLE-RESP",
        "error",
        "unverifiable",
        "response_data cannot be statically resolved",
        f"The script's Output response_data could not be enumerated at all. Could not verify: {reason}",
        suggestion="Construct response_data as a dict literal with constant string keys in the "
        'Output constructor, e.g. `return AgentOutput(response_data={"answer": ...})`.',
    )


def validate_declaration_truth(
    tree: ast.AST,
    parsed_manifest: Optional[dict[str, Any]],
    component_type: str,
    add: AddCheck,
    checks_before: Callable[[], int],
) -> None:
    """Run the capability truth checks, then judge the returned ``response_data``.

    ``checks_before`` returns the current check count, so a capability that
    failed its truth check is not honoured by the resolution.
    """
    if component_type not in ORCHESTRATION_TYPES or not isinstance(parsed_manifest, dict):
        return
    inputs = _section_ports(parsed_manifest, _input_role_sections(component_type))
    outputs = _section_ports(parsed_manifest, _output_role_sections(component_type))

    before = checks_before()
    _check_optional_artifact(tree, inputs, outputs, add)
    _check_passthrough(tree, outputs, add)
    _check_dynamic(tree, outputs, add)
    sound = checks_before() == before

    resolution = _resolve_response_data(tree, inputs, outputs, honour_capabilities=sound)
    declared: set[str] = set()
    for port in outputs:
        declared |= _field_names(port.get("response_data"))
    if resolution.unverifiable:
        _report_unverifiable(resolution.visible_keys, declared, add)
    check_response_contract(resolution, declared, add)
    check_invoke_response_data_reads(tree, parsed_manifest, _field_names, add)
