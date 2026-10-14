"""Server rules the SDK validator runs too.

A project could validate clean locally and be rejected on import. Each rule
below is pinned as a PAIR — a graph the server rejects, which the SDK must
reject too, and a near-identical graph the server accepts, which the SDK must
still accept. The second half is what stops a tightening from becoming an
over-rejection: a rule that fired on everything would pass the first assertion
of every pair and fail the second.
"""

from __future__ import annotations

from perceivo.validator._orchestration._main import validate_graph


def _report(nodes, ports, edges):
    return validate_graph(nodes=nodes, edges=edges, ports=ports)


def _ids(report, prefix):
    return [c.message for c in report.errors if c.id.startswith(prefix)]


# ---------------------------------------------------------------------------
# R1 — a TOOL may not sit on the mainflow (server ALLOWED_MAINFLOW_TYPES)
# ---------------------------------------------------------------------------


def _mainflow_chain(middle_type):
    """START -> <middle> -> END, the middle node carrying mainflow on its in port."""
    nodes = [
        {"id": "s", "component_type": "START"},
        {"id": "m", "component_type": middle_type},
        {"id": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "s_ext", "node_id": "s", "port_type": "external_in"},
        {"id": "s_out", "node_id": "s", "port_type": "out"},
        {"id": "m_in", "node_id": "m", "port_type": "in"},
        {"id": "e_in", "node_id": "e", "port_type": "in"},
        {"id": "e_ext", "node_id": "e", "port_type": "external_out"},
    ]
    edges = [
        {"source_port_id": "s_out", "target_port_id": "m_in", "kind": "MAINFLOW"},
        {"source_port_id": "m_in", "target_port_id": "e_in", "kind": "MAINFLOW"},
    ]
    return nodes, ports, edges


def test_tool_on_the_mainflow_is_rejected():
    """The server: "Mainflow component must be AGENT, FSM, LAYER, ADAPTER,
    ORCHESTRATION (found TOOL)". TOOL was removed because it has no
    phantom out-port to carry the continuation; the SDK kept it."""
    report = _report(*_mainflow_chain("TOOL"))
    assert _ids(report, "ORCH-FLOW"), [c.message for c in report.errors]


def test_agent_on_the_mainflow_is_still_accepted():
    report = _report(*_mainflow_chain("AGENT"))
    assert _ids(report, "ORCH-FLOW") == [], [c.message for c in report.errors]


# ---------------------------------------------------------------------------
# R3 / R4 — edge direction
# ---------------------------------------------------------------------------


def _invoke_pair(source_direction, target_direction, source_port_type="tool"):
    """AGENT --INVOKE--> TOOL, with both port directions under test."""
    nodes = [
        {"id": "a", "component_type": "AGENT"},
        {"id": "t", "component_type": "TOOL"},
    ]
    ports = [
        {"id": "a_p", "node_id": "a", "port_type": source_port_type, "direction": source_direction},
        {"id": "t_in", "node_id": "t", "port_type": "in", "direction": target_direction},
    ]
    edges = [{"source_port_id": "a_p", "target_port_id": "t_in", "kind": "INVOKE"}]
    return nodes, ports, edges


def test_target_port_with_out_direction_is_rejected():
    """Server: "Target port must be IN or bidirectional"."""
    report = _report(*_invoke_pair("out", "out"))
    # The validator names the offending port and its direction, so the
    # assertion matches the invariant half of the sentence rather than a prefix
    # that a port id sits inside.
    assert any("must be IN or bidirectional" in m for m in _ids(report, "ORCH-DIR")), [
        c.message for c in report.errors
    ]


def test_target_port_with_in_direction_is_accepted():
    report = _report(*_invoke_pair("out", "in"))
    assert _ids(report, "ORCH-DIR") == [], [c.message for c in report.errors]


def test_plain_in_port_may_not_source_an_invoke_edge():
    """Server: "Source port must be OUT, bidirectional, or invoke port"."""
    report = _report(*_invoke_pair("in", "in", source_port_type="in"))
    assert any(
        "must be OUT, bidirectional, or an invoke port" in m for m in _ids(report, "ORCH-DIR")
    ), [c.message for c in report.errors]


def test_invoke_port_types_may_source_with_in_direction():
    """state/policy/tool ports are legal sources even at direction IN — an FSM
    state port invoking a handler is the ordinary case, not an error."""
    for port_type in ("state", "policy", "tool"):
        report = _report(*_invoke_pair("in", "in", source_port_type=port_type))
        assert _ids(report, "ORCH-DIR") == [], (port_type, [c.message for c in report.errors])


def test_mainflow_may_leave_the_in_port_of_a_bidirectional_type():
    """The mainflow pass-through: being on the mainflow splits an AGENT's
    bidirectional in port into an in port and a phantom out port, so a MAINFLOW
    edge leaving it is legal. Mirrors the server's bidirectional_types clause."""
    nodes, ports, edges = _mainflow_chain("AGENT")
    for port in ports:
        port["direction"] = "in" if port["port_type"] in ("in", "external_in") else "out"
    report = _report(nodes, ports, edges)
    assert _ids(report, "ORCH-DIR") == [], [c.message for c in report.errors]


def test_a_port_that_declares_no_direction_is_judged_on_its_port_type():
    """A missing ``direction`` is derived, not skipped.

    Leaving a port that omits ``direction`` alone, on the reasoning that
    inventing a direction would reject a graph on a value the author never
    wrote, looks safe. The premise does not hold, and ``perceivo/_bundle/component.py:83-96`` is
    where it fails. ``_port_direction`` treats ``port_type`` as *authoritative*:
    for any of the eight types in ``_DIR_BY_PORT_TYPE`` it returns the mapped
    direction and never reads the ``direction`` field at all, which is the
    fallback only for a port type the table does not know. So the author's
    omission never reaches the platform -- the bundle the SDK itself writes
    carries ``direction: IN`` for this port, the server's NOT NULL column
    receives IN, and the server rejects the edge.

    Skipping the check therefore would not decline to invent a value; it would
    decline to read the one the SDK is about to stamp, and pass locally exactly
    the graph the import rejects. That is the false negative a local validator
    exists to prevent, so the validator derives from the same table the writer
    uses -- pinned equal to it by
    ``tests/unit/test_manifest_port_sections.py``.

    The guard half of the pair is
    ``test_invoke_port_types_may_source_with_in_direction`` above: derivation
    must not turn into over-rejection for the state/policy/tool ports that are
    legal sources at direction IN.
    """
    nodes, ports, edges = _invoke_pair("in", "in", source_port_type="in")
    for port in ports:
        port.pop("direction")
    report = _report(nodes, ports, edges)
    assert any(
        "must be OUT, bidirectional, or an invoke port" in m for m in _ids(report, "ORCH-DIR")
    ), [c.message for c in report.errors]


# ---------------------------------------------------------------------------
# R5 — END must not be fed by a bidirectional state port
# ---------------------------------------------------------------------------


def _end_fed_by(port_type):
    nodes = [
        {"id": "a", "component_type": "AGENT"},
        {"id": "e", "component_type": "END"},
    ]
    ports = [
        {"id": "a_p", "node_id": "a", "port_type": port_type, "key": "bad_state"},
        {"id": "e_in", "node_id": "e", "port_type": "in"},
        {"id": "e_ext", "node_id": "e", "port_type": "external_out"},
    ]
    edges = [{"source_port_id": "a_p", "target_port_id": "e_in", "kind": "MAINFLOW"}]
    return nodes, ports, edges


def test_end_fed_by_a_state_port_is_rejected():
    """Server: "END component input must be connected to a unidirectional mainflow
    OUT port, not a bidirectional state port." A state port expects a response
    END will never send."""
    report = _report(*_end_fed_by("state"))
    assert any("state port" in m for m in _ids(report, "ORCH-END")), [
        c.message for c in report.errors
    ]


def test_end_fed_by_an_in_port_is_accepted():
    report = _report(*_end_fed_by("in"))
    assert not any("state port" in m for m in _ids(report, "ORCH-END")), [
        c.message for c in report.errors
    ]


# ---------------------------------------------------------------------------
# R6 — port_type must be legal for the node's component category
# ---------------------------------------------------------------------------


def _single_port(component_type, port_type):
    nodes = [{"id": "n", "component_type": component_type}]
    ports = [{"id": "n_p", "node_id": "n", "port_type": port_type, "key": "p"}]
    edges = [{"source_port_id": "n_p", "target_port_id": "n_p", "kind": "INVOKE"}]
    return nodes, ports, edges


def test_illegal_port_type_for_the_category_is_rejected():
    """``VALID_PORT_TYPES_BY_CATEGORY`` was present and exported since the module
    was written, but no check ever read it."""
    for component_type, port_type in (("FSM", "tool"), ("AGENT", "state")):
        report = _report(*_single_port(component_type, port_type))
        assert _ids(report, "ORCH-PORTTYPE"), (
            component_type,
            port_type,
            [c.message for c in report.errors],
        )


def test_legal_port_type_for_the_category_is_accepted():
    for component_type, port_type in (
        ("FSM", "state"),
        ("FSM", "policy"),
        ("AGENT", "tool"),
        ("AGENT", "review"),
        ("START", "external_in"),
        ("END", "external_out"),
    ):
        report = _report(*_single_port(component_type, port_type))
        assert _ids(report, "ORCH-PORTTYPE") == [], (
            component_type,
            port_type,
            [c.message for c in report.errors],
        )


def test_unknown_category_is_skipped_not_guessed():
    """Mirrors the server's ``if not category: return None``. A component type the
    table does not cover must exempt its ports rather than reject them all."""
    report = _report(*_single_port("FEATURE", "anything"))
    assert _ids(report, "ORCH-PORTTYPE") == [], [c.message for c in report.errors]
