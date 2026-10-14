"""Scaffolded scripts must call the SDK's own API correctly.

The manifests are checked next door in ``test_template_artifact_types.py``.
This file checks the *scripts*, against the live SDK objects rather than a
transcribed list, because that is how three shipped scaffolds were wrong:

* ``policy`` returned ``PolicyOutput(response_data={"next_state": ...})`` and
  never passed ``next_state`` itself. It is a required constructor field --
  the engine routes on ``result.next_state`` -- so every scaffolded policy
  raised ``TypeError`` on its first return.
* ``end`` read ``input.request_data``. ``EndInput`` has no such attribute; it
  carries ``response_data``. ``AttributeError`` on the first line that used it.
* Nothing in the suite compared a scaffold against the types it names, so both
  shipped.

Everything here is resolved from ``perceivo`` at runtime -- ``getattr`` for the
symbols, ``dataclasses.fields`` for the contracts. A test that replayed the
field lists would go stale the first time one changed, which is the failure mode
it exists to prevent. Each check ends with a non-vacuity assertion: a scaffold
the walker finds nothing in must fail, not pass quietly.
"""

from __future__ import annotations

import ast
import dataclasses

import pytest

import perceivo
from perceivo.component._manager._templates import _TYPE_SKELETONS

TEMPLATES = sorted(_TYPE_SKELETONS)


def _script(template: str) -> ast.Module:
    source = (
        _TYPE_SKELETONS[template]["script"]
        .replace("{{symbol}}", "my_component")
        .replace("{{name}}", "my-component")
    )
    return ast.parse(source)


def _dotted(node: ast.AST) -> str | None:
    """``perceivo.log.info`` for an Attribute chain rooted at a Name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _resolve(dotted: str):
    """The live object a ``perceivo.x.y`` reference names, or raise."""
    obj = perceivo
    for part in dotted.split(".")[1:]:
        obj = getattr(obj, part)
    return obj


def _entry(tree: ast.Module) -> ast.AsyncFunctionDef:
    fns = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)]
    assert len(fns) == 1, f"expected one entry function, found {[f.name for f in fns]}"
    return fns[0]


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_perceivo_symbol_a_scaffold_names_exists(template):
    """``@perceivo.component`` is the shape this catches: it resolves, but to a
    module, so decorating with it raises ``TypeError: 'module' object is not
    callable``. ``perceivo.review`` does not resolve at all."""
    seen = 0
    for node in ast.walk(_script(template)):
        if not isinstance(node, ast.Attribute):
            continue
        dotted = _dotted(node)
        if not dotted or not dotted.startswith("perceivo."):
            continue
        seen += 1
        _resolve(dotted)
    assert seen, f"{template} names no perceivo symbols -- the walker found nothing"


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_entry_decorator_is_callable(template):
    fn = _entry(_script(template))
    assert len(fn.decorator_list) == 1, ast.dump(fn)
    dotted = _dotted(fn.decorator_list[0])
    assert dotted, f"{template} uses a decorator this test cannot read"
    decorator = _resolve(dotted)
    assert callable(decorator), (
        f"{template} decorates with {dotted}, which is a "
        f"{type(decorator).__name__} -- decorating with it raises TypeError"
    )


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_entry_takes_exactly_one_parameter(template):
    """The runner calls ``await entry(input)``. A second parameter -- the
    ``(input, perceivo)`` shape -- is a TypeError at dispatch, not import."""
    args = _entry(_script(template)).args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    assert names == ["input"], f"{template} entry takes {names}"
    assert not args.vararg, f"{template} entry takes *args/**kwargs"
    assert not args.kwarg, f"{template} entry takes *args/**kwargs"


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_scaffold_reads_only_attributes_its_input_type_has(template):
    """``EndInput`` carries ``response_data``, not ``request_data``. Checking
    against the union of every Input type -- which is what a hand-written gate
    tends to do -- passes this defect."""
    tree = _script(template)
    fn = _entry(tree)
    annotation = _dotted(fn.args.args[0].annotation)
    assert annotation, f"{template} entry parameter carries no readable annotation"
    input_type = _resolve(annotation)
    fields = {f.name for f in dataclasses.fields(input_type)}

    seen = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
            continue
        if node.value.id != "input":
            continue
        seen += 1
        assert node.attr in fields, (
            f"{template} reads input.{node.attr}; {annotation.split('.')[-1]} has "
            f"{sorted(fields)} -- AttributeError at run time"
        )
    assert seen, f"{template} reads nothing off its input -- the walker found nothing"


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_returned_output_supplies_every_required_field(template):
    """``PolicyOutput`` requires ``next_state``. The scaffold passed it only
    inside ``response_data``, so the constructor raised before the engine ever
    saw a value to route on."""
    returns = [
        n.value
        for n in ast.walk(_script(template))
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Call)
    ]
    assert returns, f"{template} returns no constructor call"

    for call in returns:
        dotted = _dotted(call.func)
        assert dotted, f"{template} returns a call this test cannot read"
        output_type = _resolve(dotted)
        assert dataclasses.is_dataclass(output_type), f"{dotted} is not a dataclass"

        supplied = {kw.arg for kw in call.keywords}
        assert None not in supplied, f"{template} splats **kwargs into {dotted}"
        assert not call.args, f"{template} passes {dotted} positionally"

        declared = {f.name for f in dataclasses.fields(output_type)}
        required = {
            f.name
            for f in dataclasses.fields(output_type)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        }
        assert not required - supplied, (
            f"{template} constructs {dotted} without {sorted(required - supplied)}; "
            f"TypeError on the first return"
        )
        assert not supplied - declared, (
            f"{template} passes {sorted(supplied - declared)} to {dotted}, which "
            f"declares {sorted(declared)}"
        )


def test_at_least_one_scaffold_has_a_required_output_field():
    """Non-vacuity for the check above: if no shipped output type has a required
    field, that test asserts nothing and would pass on an empty scaffold."""
    with_required = [
        t
        for t in TEMPLATES
        for call in [
            n.value
            for n in ast.walk(_script(t))
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Call)
        ]
        if any(
            f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
            for f in dataclasses.fields(_resolve(_dotted(call.func)))
        )
    ]
    assert with_required, "no scaffold returns a type with a required field"
