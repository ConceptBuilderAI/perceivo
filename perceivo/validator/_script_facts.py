"""Structural facts about a component script, for declaration truth-checking.

The capability checks in :mod:`perceivo.validator._declaration_truth` judge a
manifest declaration against these facts, as the platform does at import.

The extractors are conservative in the direction that keeps the gate honest:
an unrecognised construct yields the *stricter* classification (``required``
rather than ``tolerant``), so a declaration is rejected when the property it
claims cannot be positively confirmed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ._primitives_alias import primitives_aliases

#: Attribute names carrying the inbound artifact off the component's input.
_ARTIFACT_ATTRS = ("artifact", "artifacts")

#: Every Output class the runtime accepts: bare, ``perceivo.primitives.``-qualified
#: and ``perceivo.``-qualified.
_SDK_OUTPUT_NAMES: tuple[str, ...] = (
    "AdapterOutput",
    "AgentOutput",
    "EndOutput",
    "FsmOutput",
    "PolicyOutput",
    "ReviewOutput",
    "StartOutput",
    "ToolOutput",
)
_OUTPUT_CONSTRUCTORS: frozenset[str] = frozenset(
    {
        *_SDK_OUTPUT_NAMES,
        *(f"perceivo.primitives.{name}" for name in _SDK_OUTPUT_NAMES),
        *(f"perceivo.{name}" for name in _SDK_OUTPUT_NAMES),
    }
)

#: The SDK invoke family; the artifact rides in ``artifact=`` or positional slot 2.
_INVOKE_METHODS = ("invoke", "invoke_parallel")
_INVOKE_ARTIFACT_POS = 2

Parents = dict[ast.AST, tuple[ast.AST, str]]


def _call_chain(node: ast.AST) -> str:
    """Dotted name for a call target: ``perceivo.flow.invoke`` etc."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_chain(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_output_constructor(
    call: ast.AST | None, prim_aliases: frozenset[str] = frozenset()
) -> bool:
    if not isinstance(call, ast.Call):
        return False
    chain = _call_chain(call.func)
    root, _, rest = chain.partition(".")
    if rest and root in prim_aliases:
        chain = f"perceivo.primitives.{rest}"
    return chain in _OUTPUT_CONSTRUCTORS


def _returned_names(tree: ast.AST) -> set[str]:
    """Names appearing as ``return <name>`` (assign-then-return Outputs)."""
    return {
        node.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name)
    }


def _returned_output_call(
    node: ast.AST, returned_names: set[str], prim_aliases: frozenset[str] = frozenset()
) -> ast.Call | None:
    """The Output constructor call RETURNED at ``node``, directly or via a name."""
    if isinstance(node, ast.Return) and _is_output_constructor(node.value, prim_aliases):
        return node.value  # type: ignore[return-value]
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in returned_names
        and _is_output_constructor(node.value, prim_aliases)
    ):
        return node.value  # type: ignore[return-value]
    return None


def _is_invoke_family_call(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _INVOKE_METHODS
    )


def _is_forwarded_artifact_arg(node: ast.AST, parents: Parents) -> bool:
    """True when ``node`` is a bare artifact handed straight into an invoke call.

    Only the IMMEDIATE parent is inspected, so ``artifact=input.artifact.data``
    is a dereference, not a forward.
    """
    entry = parents.get(node)
    parent, fname = entry if entry else (None, "")
    if isinstance(parent, ast.keyword) and parent.arg in _ARTIFACT_ATTRS:
        grand = parents.get(parent)
        parent = grand[0] if grand else None
    elif not (
        _is_invoke_family_call(parent)
        and fname == "args"
        and parent.args[_INVOKE_ARTIFACT_POS : _INVOKE_ARTIFACT_POS + 1] == [node]  # type: ignore[union-attr]
    ):
        return False
    return _is_invoke_family_call(parent)


def build_parent_map(tree: ast.AST) -> Parents:
    """Map each node to ``(parent, field_name_on_parent)``."""
    parents: Parents = {}
    for parent in ast.walk(tree):
        for fname, value in ast.iter_fields(parent):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if isinstance(child, ast.AST):
                    parents[child] = (parent, fname)
    return parents


def _is_input_artifact(node: ast.AST) -> bool:
    """True for an ``input.artifact`` / ``input.artifacts`` LOAD."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr in _ARTIFACT_ATTRS
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
        and node.value.id == "input"
    )


def _mentions_artifact(node: ast.AST | None) -> bool:
    return node is not None and any(_is_input_artifact(n) for n in ast.walk(node))


def _is_empty_artifact_value(node: ast.expr) -> bool:
    """True for an explicit "no artifact" value: ``None``, ``[]``, ``{}``."""
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not node.elts:
        return True
    return isinstance(node, ast.Dict) and not node.keys


def _guarded_by_branch(parent: ast.AST, fname: str) -> bool:
    # (a) The read IS the guard: `if input.artifact:`.
    if isinstance(parent, (ast.If, ast.IfExp, ast.While)) and fname == "test":
        return True
    # (b) The read sits in a branch whose test checks the artifact.
    return (
        isinstance(parent, (ast.If, ast.IfExp))
        and fname in ("body", "orelse")
        and _mentions_artifact(parent.test)
    )


def _guarded_by_and(parent: ast.AST, child: ast.AST) -> bool:
    # (c) `input.artifact and input.artifact.data`.
    if not (isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.And)):
        return False
    for operand in parent.values:
        if operand is child:
            return False
        if _mentions_artifact(operand):
            return True
    return False


def _is_forward_keyword(parent: ast.AST, parents: Parents) -> bool:
    # (d) Pure forward: XOutput(artifact=input.artifact).
    if not (isinstance(parent, ast.keyword) and parent.arg in _ARTIFACT_ATTRS):
        return False
    grand = parents.get(parent)
    return grand is not None and _is_output_constructor(grand[0])


def _ancestor_tolerates(parent: ast.AST, fname: str, child: ast.AST, parents: Parents) -> bool:
    """True when this single ancestor frame tolerates a missing artifact."""
    if (
        _guarded_by_branch(parent, fname)
        or _guarded_by_and(parent, child)
        or _is_forward_keyword(parent, parents)
    ):
        return True
    # (e) getattr(input.artifact, "x", default): the default absorbs absence.
    return (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id == "getattr"
        and len(parent.args) >= 3
        and fname == "args"
    )


def _tolerant_via_ancestor(node: ast.AST, parents: Parents) -> bool:
    if _is_forwarded_artifact_arg(node, parents):
        return True
    child: ast.AST = node
    entry = parents.get(child)
    while entry is not None:
        parent, fname = entry
        if _ancestor_tolerates(parent, fname, child, parents):
            return True
        child = parent
        entry = parents.get(child)
    return False


def artifact_read_mode(tree: ast.AST) -> str:
    """``"none"``, ``"tolerant"`` (every read survives absence) or ``"required"``."""
    reads = [n for n in ast.walk(tree) if _is_input_artifact(n)]
    if not reads:
        return "none"
    parents = build_parent_map(tree)
    if all(_tolerant_via_ancestor(r, parents) for r in reads):
        return "tolerant"
    return "required"


def _emitted_artifact_values(tree: ast.AST) -> list[ast.expr | None]:
    """The ``artifact=``/``artifacts=`` value of each RETURNED Output (None = absent)."""
    returned_names = _returned_names(tree)
    values: list[ast.expr | None] = []
    for node in ast.walk(tree):
        call = _returned_output_call(node, returned_names)
        if call is None:
            continue
        found: ast.expr | None = None
        for kw in call.keywords:
            if kw.arg in _ARTIFACT_ATTRS and not _is_empty_artifact_value(kw.value):
                found = kw.value
                break
        values.append(found)
    return values


def artifact_emit_mode(tree: ast.AST) -> str:
    """``"none"``, ``"conditional"`` or ``"unconditional"`` artifact emission.

    Conditional when any return path emits nothing, or when an emitted value is
    the (possibly absent) inbound artifact.
    """
    values = _emitted_artifact_values(tree)
    if all(v is None for v in values):
        return "none"
    if any(v is None for v in values):
        return "conditional"
    if any(_mentions_artifact(v) for v in values):
        return "conditional"
    return "unconditional"


@dataclass
class ResponseDataShape:
    """What the returned ``response_data`` is made of; each opacity kept apart."""

    literal_keys: set[str] = field(default_factory=set)
    spread_exprs: list[ast.expr] = field(default_factory=list)
    has_computed_key: bool = False
    has_nonliteral: bool = False
    mutated_alias: bool = False

    @property
    def qualifies_as_dynamic(self) -> bool:
        """A bare ``**spread`` does not qualify: passthrough describes it exactly."""
        return self.has_computed_key or self.has_nonliteral or self.mutated_alias


def _simple_name_assign(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id
    return None


def _mutated_names(tree: ast.AST) -> set[str]:
    """Vars mutated in place (``.update``/``.pop``/... or ``x[k] = ...``)."""
    mutated: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("update", "pop", "setdefault", "clear", "popitem")
            and isinstance(node.func.value, ast.Name)
        ):
            mutated.add(node.func.value.id)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                    mutated.add(tgt.value.id)
    return mutated


def _dict_assignments(tree: ast.AST) -> dict[str, tuple[list[ast.Dict], bool]]:
    """Vars assigned dicts -> ``(literals, every_assignment_was_a_literal)``."""
    literals: dict[str, list[ast.Dict]] = {}
    all_literal: dict[str, bool] = {}
    for node in ast.walk(tree):
        name = _simple_name_assign(node)
        if name is None:
            continue
        if isinstance(node.value, ast.Dict):  # type: ignore[attr-defined]
            literals.setdefault(name, []).append(node.value)  # type: ignore[attr-defined]
            all_literal.setdefault(name, True)
        else:
            all_literal[name] = False
    return {n: (literals.get(n, []), all_literal.get(n, False)) for n in all_literal}


def _absorb_dict(shape: ResponseDataShape, node: ast.Dict) -> None:
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            shape.spread_exprs.append(value)
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            shape.literal_keys.add(key.value)
        else:
            shape.has_computed_key = True


def _absorb_response_expr(
    shape: ResponseDataShape, rd: ast.expr, dict_assigns: dict, mutated: set[str]  # type: ignore[type-arg]
) -> None:  # fmt: skip
    if isinstance(rd, ast.Dict):
        _absorb_dict(shape, rd)
        return
    if not (isinstance(rd, ast.Name) and rd.id in dict_assigns):
        shape.has_nonliteral = True
        return
    lits, every_literal = dict_assigns[rd.id]
    for lit in lits:
        _absorb_dict(shape, lit)
    if rd.id in mutated:
        shape.mutated_alias = True
    if not every_literal:
        shape.has_nonliteral = True


def analyze_response_data(
    tree: ast.AST, *, through_primitives_aliases: bool = False
) -> ResponseDataShape:
    """Structural shape of every RETURNED Output's ``response_data``.

    ``through_primitives_aliases`` also reads ``p.XOutput(...)`` where ``p`` is
    bound to the ``perceivo.primitives`` module. Only the key resolution behind
    ``VAL-CONTRACT-RESP`` passes it, as the platform does; the passthrough and
    dynamic truth checks read the spellings the runtime names directly.
    """
    returned_names = _returned_names(tree)
    prim_aliases = primitives_aliases(tree) if through_primitives_aliases else frozenset()
    dict_assigns = _dict_assignments(tree)
    mutated = _mutated_names(tree)
    shape = ResponseDataShape()
    for node in ast.walk(tree):
        call = _returned_output_call(node, returned_names, prim_aliases)
        if call is None:
            continue
        for rd in (kw.value for kw in call.keywords if kw.arg == "response_data"):
            _absorb_response_expr(shape, rd, dict_assigns, mutated)
    return shape


def _is_input_request_data(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "request_data"
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
        and node.value.id == "input"
    )


def _expr_is_derived(node: ast.expr, derived: set[str]) -> bool:
    """True when ``node`` evaluates to (a copy of) the inbound request_data."""
    if _is_input_request_data(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in derived
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(_expr_is_derived(v, derived) for v in node.values)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "dict" and node.args:
            return _expr_is_derived(node.args[0], derived)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "copy"
            and isinstance(node.func.value, (ast.Name, ast.Attribute))
        ):
            return _expr_is_derived(node.func.value, derived)
    if isinstance(node, ast.Dict):
        return any(
            k is None and _expr_is_derived(v, derived)
            for k, v in zip(node.keys, node.values, strict=True)
        )
    return False


def request_data_derived_vars(tree: ast.AST) -> set[str]:
    """Local vars carrying the inbound ``request_data`` forward (bounded fixed point)."""
    derived: set[str] = set()
    for _ in range(6):
        changed = False
        for node in ast.walk(tree):
            name = _simple_name_assign(node)
            if name is None or name in derived:
                continue
            if _expr_is_derived(node.value, derived):  # type: ignore[attr-defined]
                derived.add(name)
                changed = True
        if not changed:
            break
    return derived


def _literal_loop_vars(tree: ast.AST) -> dict[str, set[str]]:
    """``for k in ("a", "b"):`` loop variables mapped to their string constants."""
    loop_vars: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set))
        ):
            continue
        consts = {
            e.value
            for e in node.iter.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        }
        if consts:
            loop_vars[node.target.id] = consts
    return loop_vars


def _is_pop_on(node: ast.AST, var_names: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "pop"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in var_names
        and bool(node.args)
    )


def dropped_passthrough_keys(tree: ast.AST, var_names: set[str]) -> set[str]:
    """Keys removed from a request_data-derived var via ``v.pop("k")`` (or a literal loop)."""
    loop_vars = _literal_loop_vars(tree)
    dropped: set[str] = set()
    for node in ast.walk(tree):
        if not _is_pop_on(node, var_names):
            continue
        arg = node.args[0]  # type: ignore[attr-defined]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            dropped.add(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in loop_vars:
            dropped.update(loop_vars[arg.id])
    return dropped


def spread_is_request_data_passthrough(tree: ast.AST, spread: ast.expr) -> bool:
    return _expr_is_derived(spread, request_data_derived_vars(tree))
