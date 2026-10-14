"""T4: Orchestration.add_edge — port resolution, kind inference, error paths."""

from __future__ import annotations

import pytest

from perceivo.orchestration._builder import Orchestration


def _make_pair():
    orch = Orchestration("t")
    src = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    tgt = orch.add_node("b", "AGENT", script="b.py", manifest="b.yaml")
    return orch, src, tgt


def test_add_edge_defaults_to_mainflow_between_agents():
    orch, src, tgt = _make_pair()
    edge = orch.add_edge(src, tgt)
    assert edge.kind == "MAINFLOW"
    assert edge.source_port_id == f"port_{src.id}_output"
    assert edge.target_port_id == f"port_{tgt.id}_input"


def test_add_edge_infers_invoke_for_policy_source_port():
    orch = Orchestration("t")
    fsm = orch.add_node("f", "FSM", script="f.py", manifest="f.yaml")
    policy = orch.add_node("p", "POLICY", script="p.py", manifest="p.yaml")
    edge = orch.add_edge(fsm, policy, source_port="policy")
    assert edge.kind == "INVOKE"


def test_add_edge_explicit_kind_overrides_inference():
    orch, src, tgt = _make_pair()
    edge = orch.add_edge(src, tgt, kind="invoke")
    assert edge.kind == "INVOKE"


def test_add_edge_uppercases_and_strips_kind():
    orch, src, tgt = _make_pair()
    edge = orch.add_edge(src, tgt, kind=" mainflow ")
    assert edge.kind == "MAINFLOW"


def test_add_edge_unknown_source_port_raises():
    orch, src, tgt = _make_pair()
    with pytest.raises(ValueError, match="no port named 'missing'"):
        orch.add_edge(src, tgt, source_port="missing")


def test_add_edge_unknown_target_port_raises():
    orch, src, tgt = _make_pair()
    with pytest.raises(ValueError, match="no port named 'missing'"):
        orch.add_edge(src, tgt, target_port="missing")


def test_add_edge_source_with_no_out_ports_raises():
    orch = Orchestration("t")
    # END has only IN / external_out — external_out IS an OUT-type, so use a
    # bare node with no defaults to test the "no OUT" path.
    src = orch.add_node("nada", "WHATEVER", script="n.py", manifest="n.yaml")
    tgt = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    with pytest.raises(ValueError, match="no OUT ports"):
        orch.add_edge(src, tgt)


def test_add_edge_target_with_no_in_ports_raises():
    orch = Orchestration("t")
    src = orch.add_node("a", "AGENT", script="a.py", manifest="a.yaml")
    tgt = orch.add_node("nada", "WHATEVER", script="n.py", manifest="n.yaml")
    with pytest.raises(ValueError, match="no IN ports"):
        orch.add_edge(src, tgt)


def test_add_edge_appends_to_edges_list():
    orch, src, tgt = _make_pair()
    assert orch.edges == []
    edge = orch.add_edge(src, tgt)
    assert orch.edges == [edge]


def test_connect_is_removed_and_add_edge_covers_it():
    """The builder has no ``connect``, and ``add_edge`` produces the edge.

    Asserting the edge as well as the absence keeps the behaviour covered, not
    only the name.
    """
    orch, src, tgt = _make_pair()
    assert not hasattr(orch, "connect")
    assert orch.add_edge(src, tgt).kind == "MAINFLOW"


def test_add_invoke_is_removed_and_add_edge_still_infers_invoke():
    """Same removal, and the inference that made the shim redundant.

    ``add_invoke`` existed to spell INVOKE explicitly. ``add_edge`` infers it
    from a named source port, which is why the shim could be dropped rather
    than replaced -- so the kind assertion here is load-bearing, not decoration:
    without it, removing the verb would look identical to losing INVOKE edges.
    """
    orch = Orchestration("t")
    fsm = orch.add_node("f", "FSM", script="f.py", manifest="f.yaml")
    policy = orch.add_node("p", "POLICY", script="p.py", manifest="p.yaml")
    assert not hasattr(orch, "add_invoke")
    edge = orch.add_edge(fsm, policy, source_port="policy")
    assert edge.kind == "INVOKE"


def test_add_edge_picks_first_out_port_when_multiple():
    orch = Orchestration("t")
    src = orch.add_node(
        "multi",
        "AGENT",
        script="a.py",
        manifest="a.yaml",
        extra_ports=[{"name": "secondary_out", "port_type": "out"}],
    )
    tgt = orch.add_node("b", "AGENT", script="b.py", manifest="b.yaml")
    edge = orch.add_edge(src, tgt)
    # The first OUT port is the AGENT default "output", not the extra.
    assert edge.source_port_id == f"port_{src.id}_output"
