"""T4: Orchestration.validate and _auto_inject_endpoints."""

from __future__ import annotations

from unittest.mock import patch

from perceivo.orchestration._builder import Orchestration


def test_validate_uses_existing_project_dir_when_set(tmp_workspace):
    orch = Orchestration("t")
    orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    orch.save(str(tmp_workspace))

    with patch("perceivo.validator.validate_orchestration") as m:
        m.return_value = "sentinel-report"
        result = orch.validate()
    m.assert_called_once_with(str(tmp_workspace.resolve()))
    assert result == "sentinel-report"


def test_validate_materialises_tmpdir_when_no_project_dir():
    orch = Orchestration("t")
    orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    assert orch.project_dir is None

    captured: dict = {}

    def _spy(project_dir):
        captured["dir"] = project_dir
        return "spy-report"

    with patch("perceivo.validator.validate_orchestration", side_effect=_spy):
        result = orch.validate()
    assert result == "spy-report"
    # Tempdir is cleaned up on exit, but we captured the path it used.
    assert captured["dir"]


def test_auto_inject_endpoints_adds_start_and_end_when_missing():
    orch = Orchestration("t")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    b = orch.add_node("b", "AGENT", script="b.py", manifest="b.yaml")
    orch.add_edge(a, b)

    orch._auto_inject_endpoints()
    types = {n.component_type for n in orch.nodes.values()}
    assert "START" in types
    assert "END" in types


def test_auto_inject_endpoints_idempotent_when_start_and_end_present():
    orch = Orchestration("t")
    s = orch.add_node("s", "START", script="s.py", manifest="s.yaml")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    e = orch.add_node("e", "END", script="e.py", manifest="e.yaml")
    orch.add_edge(s, a)
    orch.add_edge(a, e)

    before = list(orch.nodes.keys())
    orch._auto_inject_endpoints()
    assert list(orch.nodes.keys()) == before


def test_auto_inject_endpoints_wires_start_to_roots():
    orch = Orchestration("t")
    a = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    orch._auto_inject_endpoints()

    start_id = next(n.id for n in orch.nodes.values() if n.component_type == "START")
    edges_from_start = [e for e in orch.edges if e.source_port_id.startswith(f"port_{start_id}_")]
    assert edges_from_start, "expected START -> root edge"
    assert any(e.target_port_id == f"port_{a.id}_input" for e in edges_from_start)
