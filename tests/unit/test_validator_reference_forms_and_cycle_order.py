"""Every spelling of a node reference the platform resolves.

A parity suite whose acceptance graphs pin **one** spelling of a node reference
(a real node id) passes green while a phantom target written the way a platform
export actually writes it -- ``node_N`` against UUID node ids, or a bare
integer -- is rejected by the SDK with ORCH-PHANTOM-001 and accepted by the
server with 201. This file covers that dimension: **one case per reference
form**.

Every rejection case here is paired with an acceptance case, and every
acceptance case with a rejection, so a resolver that became unconditionally
permissive -- the obvious wrong fix for a false rejection -- fails this file
rather than passing it. The server's resolution of a mainflow target is the
authority each case is measured against.
"""

from __future__ import annotations

import pytest

from perceivo.validator._orchestration._main import validate_graph

# A platform export carries UUID node ids and positional hop references. That
# combination is the whole point: with positional ids the two resolutions agree
# by accident, and the divergence is invisible.
S_ID = "11111111-1111-4111-8111-111111111111"
A_ID = "22222222-2222-4222-8222-222222222222"
E_ID = "33333333-3333-4333-8333-333333333333"
#: Index of the END node in the graphs below -- the position ``node_2`` and the
#: bare integer ``2`` both name.
END_INDEX = 2


def _graph(
    node_ids: tuple[str, str, str],
    agent_config: dict,
    start_config: dict | None = None,
    extra_nodes: list[dict] | None = None,
) -> dict:
    """START -> AGENT -> END, mainflow into END carried by AGENT node config."""
    start, agent, end = node_ids
    nodes: list[dict] = [
        {"id": start, "component_type": "START"},
        {"id": agent, "component_type": "AGENT", "config": dict(agent_config)},
        {"id": end, "component_type": "END"},
    ]
    if start_config is not None:
        nodes[0]["config"] = dict(start_config)
    nodes += extra_nodes or []
    return {
        "nodes": nodes,
        "ports": [
            {"id": "s_ext", "node_id": start, "port_type": "external_in", "key": "external_in"},
            {"id": "s_out", "node_id": start, "port_type": "out", "key": "output"},
            {"id": "a_in", "node_id": agent, "port_type": "in", "key": "input"},
            {"id": "e_in", "node_id": end, "port_type": "in", "key": "input"},
            {"id": "e_ext", "node_id": end, "port_type": "external_out", "key": "external_out"},
        ],
        "edges": [{"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"}],
    }


def _export_graph(agent_config: dict, **kwargs) -> dict:
    return _graph((S_ID, A_ID, E_ID), agent_config, **kwargs)


def _report(graph: dict):
    return validate_graph(
        nodes=graph["nodes"],
        edges=graph["edges"],
        ports=graph["ports"],
    )


def _error_ids(report) -> set[str]:
    return {c.id for c in report.checks if c.severity == "error"}


# ---------------------------------------------------------------------------
# Phantom mainflow target: one case per reference form
# ---------------------------------------------------------------------------


def test_export_shaped_graph_with_a_canonical_uuid_target_is_valid():
    """The floor. Without it every "is accepted" assertion below could be
    satisfied by a graph that was never valid for unrelated reasons."""
    report = _report(_export_graph({"mainflow_target_node_ref": E_ID}))
    assert report.valid, sorted(_error_ids(report))


def test_positional_node_n_target_is_resolved_not_rejected():
    """SPEC-1 export form. ``_orchestration_export._resolve_mainflow_refs``
    writes ``f"node_{idx}"``, so this is what an exported graph carries; the
    server resolves it through ``_resolve_target_key`` and imports with 201.

    Unresolved, the validator reports ``ORCH-PHANTOM-001 ... targets 'node_2',
    which is not a node in this orchestration``.
    """
    report = _report(_export_graph({"mainflow_target_node_ref": f"node_{END_INDEX}"}))
    assert report.valid, sorted(_error_ids(report))


def test_bare_integer_target_is_resolved_not_rejected():
    """Bootstrap-bundle form: a bare index, which the server also resolves.

    Unresolved, the validator reports ``ORCH-PHANTOM-001 ... targets '2'``.

    ``valid`` is deliberately **not** asserted, and the ORCH-UNWIRED-001 assert
    is the control that keeps that from being a free pass. The unwired-port
    exemption resolves ``_ref`` and ``_index`` positionally but matches ``_id``
    raw, so END's in-port stays unexempt here. That asymmetry is deliberate
    (``_ports.py``).

    The server at ``_port_validation._build_mainflow_sets`` agrees on ``_id``
    only -- it likewise adds ``str(target_id)`` raw, and positionally resolves
    only ``export_ref``. It does **not** agree on ``_index``: the server adds
    that raw where the SDK resolves it positionally. This graph carries
    ``mainflow_target_node_id``, so only the half the two validators agree on is
    exercised, and both still reject it. The ``_index`` divergence is real, lives
    in ``_ports.py``, and is out of scope here; the divergence pinned here is
    ORCH-PHANTOM-001 alone.
    """
    ids = _error_ids(_report(_export_graph({"mainflow_target_node_id": END_INDEX})))
    assert "ORCH-PHANTOM-001" not in ids, sorted(ids)
    assert "ORCH-UNWIRED-001" in ids, sorted(ids)


def test_empty_string_target_falls_through_to_the_next_spelling():
    """``mainflow_target_node_id: ""`` beside a real ``_node_ref``.

    The server's ``mainflow_target_ref`` returns the first spelling whose value
    is ``not in (None, "")``, so it skips the blank and reads the ref. Skipping
    only ``None`` would stop at ``""`` and never see the ref that carries the
    hop, reporting ``ORCH-PHANTOM-001 ... targets ''``.
    """
    graph = _export_graph({"mainflow_target_node_id": "", "mainflow_target_node_ref": E_ID})
    report = _report(graph)
    assert report.valid, sorted(_error_ids(report))


def test_empty_string_target_alone_declares_no_hop():
    """``""`` on a node that declares nothing else is *absent*, per the
    platform's own ``reference_presence.is_set`` doctrine -- so no phantom rule
    runs for it at all. Note the START node is not phantom-capable: were ``""``
    read as present and resolvable this would surface as ORCH-PHANTOM-002
    instead, so this case pins absence rather than merely resolvability.

    Read as present, ``""`` reports ``ORCH-PHANTOM-001 ... targets ''``.
    """
    graph = _export_graph(
        {"mainflow_target_node_ref": E_ID},
        start_config={"mainflow_target_node_id": ""},
    )
    report = _report(graph)
    assert report.valid, sorted(_error_ids(report))


@pytest.mark.parametrize("dangling", ["no-such-node", "node_99", 99])
def test_a_target_that_names_no_node_is_still_rejected(dangling):
    """The discriminating half. Widening the resolver to three forms is only
    correct if an unresolvable target in *any* of them still fails -- otherwise
    ORCH-PHANTOM-001 would be dead code and every case above would pass on a
    resolver that returned success unconditionally."""
    report = _report(_export_graph({"mainflow_target_node_ref": dangling}))
    assert not report.valid
    assert "ORCH-PHANTOM-001" in _error_ids(report), sorted(_error_ids(report))


def test_a_node_id_shaped_like_node_n_matches_by_identity_not_position():
    """Exact key before position, as on the server.

    A bundle whose node ids are literally ``node_5``/``node_9``/``node_7`` is
    naming nodes, not positions. Resolving positionally first would read
    ``node_7`` as index 7, find nothing, and reject a graph the server accepts.
    """
    graph = _graph(("node_5", "node_9", "node_7"), {"mainflow_target_node_ref": "node_7"})
    report = _report(graph)
    assert report.valid, sorted(_error_ids(report))
