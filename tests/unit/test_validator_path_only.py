"""The public validators are path-only; the in-memory forms are private.

``validator.validate_component(path)`` and ``validator.validate_orchestration(
path, *, validate_scripts=True)`` are the public forms. The in-memory forms --
``script=``/``component_type=``/``manifest_yaml=``/``ports=``/``fsm_states=``
and ``nodes=``/``edges=``/``ports=``/``component_types=`` -- are private
(``validator._component.validate_source``, ``validator._orchestration._main.
validate_graph``).

The call-site population is defined by an instrument, not a hand list:
:func:`in_memory_callers` walks every module in ``perceivo/`` and ``tests/``
with ``ast`` and reports each call to a public validator -- by its own name or
by any alias an import binds it to -- that passes a keyword the public
signature does not have. It must report none, and planted callers prove it can.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import perceivo.validator as validator
from perceivo.types import ConfigurationError
from perceivo.validator._component import validate_source
from perceivo.validator._orchestration._main import validate_graph

SDK_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_NAMES = frozenset({"validate_component", "validate_orchestration"})
#: The keywords each public validator accepts; any other keyword is a caller
#: still on an in-memory (or pre-freeze) form.
PUBLIC_KEYWORDS = {
    "validate_component": frozenset({"path"}),
    "validate_orchestration": frozenset({"path", "validate_scripts"}),
}


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    """``local name -> public validator name`` for every import that binds one."""
    bound = {name: name for name in PUBLIC_NAMES}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("perceivo"):
            for alias in node.names:
                if alias.name in PUBLIC_NAMES:
                    bound[alias.asname or alias.name] = alias.name
    return bound


def in_memory_callers(source: str, filename: str = "<source>") -> list[str]:
    """``file:line`` of every public-validator call with a non-public keyword."""
    tree = ast.parse(source, filename=filename)
    bound = _aliases(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        public = bound.get(_callee_name(node.func) or "")
        if public is None:
            continue
        keywords = {k.arg for k in node.keywords if k.arg}
        # A ``**mapping`` splat cannot be checked statically, so it counts.
        splatted = any(k.arg is None for k in node.keywords)
        if splatted or keywords - PUBLIC_KEYWORDS[public]:
            found.append(f"{filename}:{node.lineno}")
    return found


def _population() -> list[Path]:
    return sorted(
        p
        for root in ("perceivo", "tests")
        for p in (SDK_ROOT / root).rglob("*.py")
        if "__pycache__" not in p.parts
    )


def test_no_caller_passes_an_in_memory_keyword_to_a_public_validator():
    population = _population()
    assert len(population) > 300, "the scan read too few files to mean anything"
    callers = [
        hit
        for path in population
        for hit in in_memory_callers(
            path.read_text(encoding="utf-8"), str(path.relative_to(SDK_ROOT))
        )
    ]
    assert callers == [], callers


def test_the_instrument_finds_a_planted_caller():
    planted = (
        "from perceivo.validator import validate_component, validate_orchestration\n"
        "validate_component(script='x', component_type='AGENT')\n"
        "validate_orchestration(nodes=[], edges=[], ports=[])\n"
        "validate_orchestration('proj', validate_scripts=False)\n"
    )
    assert in_memory_callers(planted, "planted.py") == ["planted.py:2", "planted.py:3"]


@pytest.mark.parametrize(
    ("public", "parameters"),
    [
        (validator.validate_component, ["path"]),
        (validator.validate_orchestration, ["path", "validate_scripts"]),
    ],
)
def test_the_public_validators_are_path_only(public, parameters):
    assert list(inspect.signature(public).parameters) == parameters


def test_the_in_memory_forms_are_private_and_still_work():
    report = validate_source(
        script="import perceivo\n\n@perceivo.agent\nasync def a(i):\n    return i\n",
        component_type="AGENT",
    )
    assert report.valid, [c.message for c in report.errors]
    empty = validate_graph(nodes=[], edges=[], ports=[])
    assert [c.id for c in empty.errors] == ["ORCH-EMPTY-001"]


def test_validate_component_accepts_a_script_file_and_a_component_directory(tmp_path):
    folder = tmp_path / "agent"
    folder.mkdir()
    (folder / "agent.py").write_text(
        "import perceivo\n\n@perceivo.agent\nasync def agent(i):\n    return i\n", encoding="utf-8"
    )
    (folder / "agent.yaml").write_text("extension_type: AGENT\nname: agent\n", encoding="utf-8")
    by_file = validator.validate_component(folder / "agent.py")
    by_dir = validator.validate_component(str(folder))
    assert [c.id for c in by_file.checks] == [c.id for c in by_dir.checks]


def test_validate_component_refuses_an_ambiguous_directory(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="exactly one component script"):
        validator.validate_component(tmp_path)
    with pytest.raises(FileNotFoundError):
        validator.validate_component(tmp_path / "missing.py")
