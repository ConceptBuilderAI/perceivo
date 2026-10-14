"""Node rules the server runs, mirrored by the SDK.

Each test below is a graph that the platform data service rejects. A validator
returning ``valid=True`` for any of them lets the author validate clean
locally and be rejected on import.

The direction is fixed: **tighten the SDK, never loosen the
server.** So each case asserts the SDK *rejects*, and names the check id, so
a rule that is deleted or renamed fails here rather than going quiet.

The second half is the other half of the contract. Tightening is only correct if
it does not reject what the server accepts, so the server's exemptions --
``depends_on.visible_when``, an explicitly optional secret, a hidden combo
internal, a Database Connector in platform mode -- are pinned as *valid*. Without
them this file would pass just as well if the new rules fired unconditionally.
"""

from __future__ import annotations

from perceivo.validator._orchestration._main import validate_graph

_START_END_NODES = [
    {"id": "s", "component_type": "START"},
    {"id": "a", "component_type": "AGENT"},
    {"id": "e", "component_type": "END"},
]
_START_END_PORTS = [
    {"id": "s_ext", "node_id": "s", "port_type": "external_in", "key": "external_in"},
    {"id": "s_out", "node_id": "s", "port_type": "out", "key": "output"},
    {"id": "a_in", "node_id": "a", "port_type": "in", "key": "input"},
    {"id": "e_in", "node_id": "e", "port_type": "in", "key": "input"},
    {"id": "e_ext", "node_id": "e", "port_type": "external_out", "key": "external_out"},
]
_START_END_EDGES = [{"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"}]


def _simple_valid(agent_config: dict[str, object] | None = None) -> dict:
    """The canonical START -> AGENT -> END graph, mainflow carried by config.

    Mirrors the platform's own minimal valid orchestration.
    """
    config: dict[str, object] = {"mainflow_target_node_id": "e"}
    config.update(agent_config or {})
    nodes: list[dict[str, object]] = [dict(n) for n in _START_END_NODES]
    for node in nodes:
        if node["id"] == "a":
            node["config"] = config
    return {
        "nodes": nodes,
        "ports": [dict(p) for p in _START_END_PORTS],
        "edges": [dict(e) for e in _START_END_EDGES],
    }


def _report(graph: dict):
    return validate_graph(
        nodes=graph["nodes"],
        edges=graph["edges"],
        ports=graph["ports"],
    )


def _error_ids(report) -> set[str]:
    return {c.id for c in report.checks if c.severity == "error"}


# ---------------------------------------------------------------------------
# Control: the shape both validators accept
# ---------------------------------------------------------------------------


def test_the_canonical_valid_graph_is_still_valid():
    """The floor for every test below.

    If this graph ever became invalid, each rejection asserted further down
    would be satisfied by the wrong error and the file would still pass.
    """
    report = _report(_simple_valid())
    assert report.valid, sorted(_error_ids(report))


# ---------------------------------------------------------------------------
# 1. END is terminal -- no edge may leave it
# ---------------------------------------------------------------------------


def test_invoke_edge_out_of_end_is_rejected():
    """``ALLOWED_CONNECTIONS`` is keyed on the SOURCE.

    The SDK's ``END`` entry listed the mainflow types that may connect *to* END,
    which made the matrix answer "TOOL is reachable from END" and let an edge out
    of the terminal node validate clean. The server allows no edge out of END.
    """
    graph = _simple_valid()
    graph["nodes"].append({"id": "t", "component_type": "TOOL"})
    graph["ports"].append({"id": "t_in", "node_id": "t", "port_type": "in", "key": "input"})
    graph["edges"].append({"source_port_id": "e_ext", "target_port_id": "t_in", "kind": "INVOKE"})

    report = _report(graph)
    assert not report.valid
    assert any(i.startswith("ORCH-CONN-") for i in _error_ids(report)), _error_ids(report)


def test_mainflow_edge_out_of_end_is_rejected():
    """The SPEC-6 matrix skip for MAINFLOW edges is right for the chain and wrong
    for its terminus: the server runs the matrix over *every* edge kind."""
    graph = _simple_valid()
    graph["nodes"].append({"id": "a2", "component_type": "AGENT"})
    graph["ports"].append({"id": "a2_in", "node_id": "a2", "port_type": "in", "key": "input"})
    graph["edges"].append(
        {"source_port_id": "e_ext", "target_port_id": "a2_in", "kind": "MAINFLOW"}
    )

    report = _report(graph)
    assert not report.valid
    assert "ORCH-FLOW-END-001" in _error_ids(report), _error_ids(report)


# ---------------------------------------------------------------------------
# 2. REVIEW survey questions
# ---------------------------------------------------------------------------


def test_review_node_without_survey_questions_is_rejected():
    graph = _simple_valid()
    graph["nodes"].append({"id": "r", "component_type": "REVIEW", "config": {}})
    graph["ports"] += [
        {"id": "r_in", "node_id": "r", "port_type": "in", "key": "input"},
        {"id": "a_rev", "node_id": "a", "port_type": "review", "key": "review_port"},
    ]
    graph["edges"].append({"source_port_id": "a_rev", "target_port_id": "r_in", "kind": "INVOKE"})

    report = _report(graph)
    assert not report.valid
    assert "ORCH-REVIEW-001" in _error_ids(report), _error_ids(report)


def test_review_question_missing_a_required_field_is_rejected():
    graph = _simple_valid()
    graph["nodes"].append(
        {
            "id": "r",
            "component_type": "REVIEW",
            "config": {"survey_spec": [{"id": "q1", "type": "text"}]},  # no label
        }
    )
    graph["ports"] += [
        {"id": "r_in", "node_id": "r", "port_type": "in", "key": "input"},
        {"id": "a_rev", "node_id": "a", "port_type": "review", "key": "review_port"},
    ]
    graph["edges"].append({"source_port_id": "a_rev", "target_port_id": "r_in", "kind": "INVOKE"})

    report = _report(graph)
    assert not report.valid
    assert "ORCH-REVIEW-003" in _error_ids(report), _error_ids(report)


# ---------------------------------------------------------------------------
# 3. VAL-SECRET-001
# ---------------------------------------------------------------------------


def test_declared_secret_with_no_binding_is_rejected():
    graph = _simple_valid(
        {
            "secret_bindings": {
                "declarations": [{"name": "OPENAI_API_KEY", "required": True}],
                "bindings": {},
            }
        }
    )
    report = _report(graph)
    assert not report.valid
    assert "ORCH-SECRET-001" in _error_ids(report), _error_ids(report)


def test_declared_secret_that_is_bound_is_accepted():
    graph = _simple_valid(
        {
            "secret_bindings": {
                "declarations": [{"name": "OPENAI_API_KEY", "required": True}],
                "bindings": {"OPENAI_API_KEY": "0f1c9c1e-0000-4000-8000-000000000000"},
            }
        }
    )
    assert _report(graph).valid


def test_optional_secret_left_unbound_is_accepted():
    """``required: False`` is skipped on the server; a rule that fired here would
    reject graphs the platform imports."""
    graph = _simple_valid(
        {
            "secret_bindings": {
                "declarations": [{"name": "OPTIONAL_KEY", "required": False}],
                "bindings": {},
            }
        }
    )
    assert _report(graph).valid


def test_hidden_combo_internal_node_is_exempt_from_secret_validation():
    graph = _simple_valid(
        {
            "is_agent_tool_internal": True,
            "secret_bindings": {
                "declarations": [{"name": "OPENAI_API_KEY", "required": True}],
                "bindings": {},
            },
        }
    )
    assert _report(graph).valid


def test_database_connector_in_platform_mode_is_exempt_from_secret_validation():
    graph = _simple_valid(
        {
            "is_database_connector": True,
            "input_parameters": {"values": {"connection_mode": "platform"}},
            "secret_bindings": {
                "declarations": [{"name": "PG_PASSWORD", "required": True}],
                "bindings": {},
            },
        }
    )
    assert _report(graph).valid


# ---------------------------------------------------------------------------
# 4. VAL-PARAM-001
# ---------------------------------------------------------------------------


def test_required_input_parameter_with_no_value_is_rejected():
    graph = _simple_valid(
        {
            "input_parameters": {
                "declarations": [{"name": "temperature", "required": True}],
                "values": {},
            }
        }
    )
    report = _report(graph)
    assert not report.valid
    assert "ORCH-PARAM-001" in _error_ids(report), _error_ids(report)


def test_required_input_parameter_hidden_by_depends_on_is_accepted():
    """The parent value does not make the child visible, so the server skips it."""
    graph = _simple_valid(
        {
            "input_parameters": {
                "declarations": [
                    {"name": "mode", "required": False},
                    {
                        "name": "api_key",
                        "required": True,
                        "depends_on": {"parameter": "mode", "visible_when": ["remote"]},
                    },
                ],
                "values": {"mode": "local"},
            }
        }
    )
    assert _report(graph).valid


def test_required_input_parameter_made_visible_by_depends_on_is_rejected():
    """The discriminating half of the exemption: flip the parent value and the
    same declaration must be enforced."""
    graph = _simple_valid(
        {
            "input_parameters": {
                "declarations": [
                    {"name": "mode", "required": False},
                    {
                        "name": "api_key",
                        "required": True,
                        "depends_on": {"parameter": "mode", "visible_when": ["remote"]},
                    },
                ],
                "values": {"mode": "remote"},
            }
        }
    )
    report = _report(graph)
    assert not report.valid
    assert "ORCH-PARAM-001" in _error_ids(report), _error_ids(report)


# ---------------------------------------------------------------------------
# 5. Phantom mainflow continuations
# ---------------------------------------------------------------------------


def test_mainflow_continuation_on_a_type_without_a_phantom_out_port_is_rejected():
    """A TOOL has no bidirectional in-port to split, so it cannot carry the
    mainflow onward, and the server rejects it."""
    graph = _simple_valid()
    graph["nodes"].append(
        {"id": "t", "component_type": "TOOL", "config": {"mainflow_target_node_id": "e"}}
    )
    graph["ports"].append({"id": "t_in", "node_id": "t", "port_type": "in", "key": "input"})
    graph["ports"].append({"id": "a_tool", "node_id": "a", "port_type": "tool", "key": "tool_port"})
    graph["edges"].append({"source_port_id": "a_tool", "target_port_id": "t_in", "kind": "INVOKE"})

    report = _report(graph)
    assert not report.valid
    assert "ORCH-PHANTOM-002" in _error_ids(report), _error_ids(report)


def test_mainflow_continuation_to_an_unknown_node_is_rejected():
    graph = _simple_valid({"mainflow_target_node_id": "does-not-exist"})
    report = _report(graph)
    assert not report.valid
    assert "ORCH-PHANTOM-001" in _error_ids(report), _error_ids(report)


def test_the_exported_ref_spelling_is_validated_too():
    """Production writes ``_node_id``; export rewrites it to the
    portable ``_node_ref``. Reading only one spelling is how the server's own
    copy of this check went silent over every exported graph."""
    graph = _simple_valid()
    for node in graph["nodes"]:
        if node["id"] == "a":
            node["config"] = {"mainflow_target_node_ref": "does-not-exist"}

    report = _report(graph)
    assert not report.valid
    assert "ORCH-PHANTOM-001" in _error_ids(report), _error_ids(report)


# ---------------------------------------------------------------------------
# 5b. Integer node ids -- the tightening must not point the wrong way
# ---------------------------------------------------------------------------


def _simple_valid_int_ids() -> dict:
    """The canonical graph with node ids written as YAML integers.

    ``orchestration.yaml`` may spell an id unquoted, and YAML then yields the
    **int** ``1`` rather than ``"1"``. ``_main`` keys ``node_map``/``types_map``
    by that raw value, so a rule that looks a type up by the stringified key
    misses the node entirely and reads its type as UNKNOWN.
    """
    return {
        "nodes": [
            {"id": 0, "component_type": "START"},
            {"id": 1, "component_type": "AGENT", "config": {"mainflow_target_node_id": 2}},
            {"id": 2, "component_type": "END"},
        ],
        "ports": [
            {"id": "p_s_ext", "node_id": 0, "port_type": "external_in", "key": "external_in"},
            {"id": "p_s_out", "node_id": 0, "port_type": "out", "key": "output"},
            {"id": "p_a_in", "node_id": 1, "port_type": "in", "key": "input"},
            {"id": "p_e_in", "node_id": 2, "port_type": "in", "key": "input"},
            {"id": "p_e_ext", "node_id": 2, "port_type": "external_out", "key": "external_out"},
        ],
        "edges": [{"source_port_id": "p_s_out", "target_port_id": "p_a_in", "kind": "MAINFLOW"}],
    }


def test_integer_node_ids_are_accepted():
    """The graph is valid and the server accepts it, so the SDK must too.

    This is the tightening pointed the wrong way: the phantom rule read the
    AGENT's type through the stringified key, got ``''``, and rejected a project
    the platform imports -- the exact inversion this parity check removes.
    """
    report = _report(_simple_valid_int_ids())
    assert report.valid, sorted(_error_ids(report))


def test_integer_node_ids_still_reject_a_phantom_incapable_source():
    """The other half of the contract, and the reason the resolution is not vacuous.

    Resolving the type by raw id could equally have been "resolve to nothing and
    never fire". A TOOL source with integer ids must still be rejected, or this
    file would pass just as well with the rule deleted.
    """
    graph = _simple_valid_int_ids()
    graph["nodes"].append(
        {"id": 3, "component_type": "TOOL", "config": {"mainflow_target_node_id": 2}}
    )
    graph["ports"] += [
        {"id": "p_t_in", "node_id": 3, "port_type": "in", "key": "input"},
        {"id": "p_a_tool", "node_id": 1, "port_type": "tool", "key": "tool_port"},
    ]
    graph["edges"].append(
        {"source_port_id": "p_a_tool", "target_port_id": "p_t_in", "kind": "INVOKE"}
    )

    report = _report(graph)
    assert not report.valid
    assert "ORCH-PHANTOM-002" in _error_ids(report), _error_ids(report)


def test_review_node_whose_id_is_zero_is_still_checked():
    """``0`` is falsy, so ``node.get("id") or str(index)`` silently discarded it.

    Under-enforcement rather than false rejection: the survey rule simply did not
    fire for the node, and a REVIEW with no questions validated clean locally
    before failing on import.
    """
    graph = _simple_valid_int_ids()
    graph["nodes"].append({"id": 0, "component_type": "REVIEW", "config": {}})
    graph["nodes"][0]["id"] = "s"
    for port in graph["ports"]:
        if port["node_id"] == 0:
            port["node_id"] = "s"
    graph["ports"] += [
        {"id": "p_r_in", "node_id": 0, "port_type": "in", "key": "input"},
        {"id": "p_a_rev", "node_id": 1, "port_type": "review", "key": "review_port"},
    ]
    graph["edges"].append(
        {"source_port_id": "p_a_rev", "target_port_id": "p_r_in", "kind": "INVOKE"}
    )

    report = _report(graph)
    assert not report.valid
    assert "ORCH-REVIEW-001" in _error_ids(report), _error_ids(report)


# ---------------------------------------------------------------------------
# 6. No VAL-COMP rule (composite components)
# ---------------------------------------------------------------------------


def test_a_composite_graph_is_ignored_like_the_server_ignores_it():
    """The platform has no composites: the server has no VAL-COMP rule and
    drops a ``composite_graph`` key on every save path, so a project carrying
    one -- even a self-referencing one -- imports cleanly. The SDK must not
    reject what the server accepts. A composite-graph rule fails this test."""
    graph = _simple_valid()
    for node in graph["nodes"]:
        if node["id"] == "a":
            node["component_id"] = "comp-a"
            node["config"]["composite_graph"] = {
                "primary_ref": "p",
                "internal_nodes": [
                    {"ref": "p", "component_id": "comp-a"},
                    {"ref": "c", "component_id": "comp-a"},
                ],
            }

    report = _report(graph)
    assert report.valid, _error_ids(report)
    assert not any("COMP" in check.id for check in report.checks), [c.id for c in report.checks]
