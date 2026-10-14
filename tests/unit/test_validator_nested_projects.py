"""``validate_orchestration`` validates the projects nested ORCHESTRATION nodes run.

Checking only the parent graph would report PASS for a parent over a broken
child, and the run would fail inside the child.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml
from click.testing import CliRunner

from perceivo._cli import main as cli_main
from perceivo.validator._orchestration import validate_orchestration


def _ports_for(nid: str, ctype: str) -> list[dict[str, Any]]:
    ports = []
    if ctype != "START":
        ports.append({"id": f"{nid}_in", "node_id": nid, "name": "input", "port_type": "in"})
    if ctype != "END":
        ports.append({"id": f"{nid}_out", "node_id": nid, "name": "output", "port_type": "out"})
    return ports


def _edge(src: str, dst: str) -> dict[str, Any]:
    return {"source_port_id": f"{src}_out", "target_port_id": f"{dst}_in", "kind": "MAINFLOW"}


def _project(
    path: Path, middle: list[tuple[str, str, dict[str, Any]]], *, broken: bool = False
) -> Path:
    """START -> middle... -> END as a chain of MAINFLOW edges.

    ``broken`` drops the last edge, leaving END unreachable.
    """
    chain = [("s", "START", {}), *middle, ("e", "END", {})]
    nodes, ports = [], []
    for nid, ctype, extra in chain:
        node = {"id": nid, "name": nid, "component_type": ctype, **extra}
        nodes.append(node)
        ports += _ports_for(nid, ctype)
    edges = [_edge(a[0], b[0]) for a, b in pairwise(chain)]
    if broken:
        edges.pop()
    path.mkdir(parents=True, exist_ok=True)
    graph = {"nodes": nodes, "ports": ports, "edges": edges}
    (path / "orchestration.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    return path


def _leaf(path: Path, *, broken: bool = False) -> Path:
    return _project(path, [("a", "AGENT", {})], broken=broken)


def _parent(root: Path, *, broken_child: bool = False) -> Path:
    _leaf(root / "kids" / "good")
    _leaf(root / "kids" / "maybe", broken=broken_child)
    return _project(
        root,
        [
            ("n1", "ORCHESTRATION", {"project": "kids/good"}),
            ("n2", "ORCHESTRATION", {"project": "kids/maybe"}),
        ],
    )


def _errors(report) -> list[str]:
    return [c.id for c in report.errors]


def test_the_fixture_leaf_is_valid_and_its_broken_twin_is_not(tmp_path: Path) -> None:
    """Control: the leaf the tests nest must itself validate, and break."""
    assert validate_orchestration(str(_leaf(tmp_path / "ok")), validate_scripts=False).valid
    broken = validate_orchestration(
        str(_leaf(tmp_path / "bad", broken=True)), validate_scripts=False
    )
    assert not broken.valid


def test_parent_over_valid_children_passes(tmp_path: Path) -> None:
    report = validate_orchestration(str(_parent(tmp_path)), validate_scripts=False)
    assert report.valid, _errors(report)


def test_a_failing_child_fails_the_parent_under_its_nested_path(tmp_path: Path) -> None:
    root = _parent(tmp_path, broken_child=True)
    child_alone = validate_orchestration(str(root / "kids" / "maybe"), validate_scripts=False)

    report = validate_orchestration(str(root), validate_scripts=False)

    assert not report.valid
    assert _errors(report) == [f"n2/{cid}" for cid in _errors(child_alone)]
    assert all(c.message.startswith("In nested orchestration 'n2': ") for c in report.errors)


def test_nesting_is_validated_at_every_depth(tmp_path: Path) -> None:
    _leaf(tmp_path / "inner" / "leaf", broken=True)
    _project(tmp_path / "inner", [("deep", "ORCHESTRATION", {"project": "leaf"})])
    root = _project(tmp_path, [("outer", "ORCHESTRATION", {"project": "inner"})])

    report = validate_orchestration(str(root), validate_scripts=False)

    assert not report.valid
    assert report.errors
    assert all(c.id.startswith("outer/deep/") for c in report.errors)


def test_a_missing_nested_project_is_an_error(tmp_path: Path) -> None:
    root = _project(tmp_path, [("n", "ORCHESTRATION", {"project": "nowhere"})])
    report = validate_orchestration(str(root), validate_scripts=False)
    assert _errors(report) == ["n/ORCH-NESTED-001"]


def test_a_nesting_cycle_is_reported_not_recursed(tmp_path: Path) -> None:
    root = _project(tmp_path, [("self", "ORCHESTRATION", {"project": "."})])
    report = validate_orchestration(str(root), validate_scripts=False)
    assert _errors(report) == ["self/ORCH-NESTED-002"]


def test_cli_reports_fail_for_a_failing_child(tmp_path: Path) -> None:
    root = _parent(tmp_path, broken_child=True)
    result = CliRunner().invoke(cli_main, ["validate", "orchestration", str(root), "--no-scripts"])
    assert result.exit_code == 1, result.output
    assert "RESULT: FAIL" in result.output
    assert "n2/" in result.output

    good = _parent(tmp_path / "fine")
    result = CliRunner().invoke(cli_main, ["validate", "orchestration", str(good), "--no-scripts"])
    assert result.exit_code == 0, result.output
    assert "RESULT: PASS" in result.output
