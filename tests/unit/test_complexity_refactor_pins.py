"""Behaviour pins for functions split up to meet Sonar's cognitive-complexity cap.

Each test drives a branch the rest of the suite left unexercised, so a refactor
that drops, reorders or merges one of those branches fails here rather than in
a service that consumes the SDK.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from perceivo import secrets
from perceivo._bundle.component import parse_manifest_ports
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo.config import load
from perceivo.local.context import LocalExtensionManager
from perceivo.primitives import Artifact, ArtifactType
from perceivo.runner.executor import _OrchestrationExecutor
from perceivo.runner.types import CompileResult, NodeShell
from perceivo.validator._orchestration._edges import mainflow_continuation_findings
from perceivo.validator._orchestration._ports import (
    _unwired_exempt_nodes,
    _validate_all_ports_connected,
)
from perceivo.validator._orchestration._structure import _compute_fsm_depths
from perceivo.validator._port_rules import _manifest_ports

_NONE_ART = Artifact(type=ArtifactType.NONE, data=None)


# ---------------------------------------------------------------------------
# perceivo._bundle.component.parse_manifest_ports
# ---------------------------------------------------------------------------


def test_parse_manifest_ports_skips_non_mapping_items_in_every_shape():
    manifest = {
        "ports": ["bare-string", {"key": "legacy_in", "port_type": "in"}],
        "tool_ports": [42, {"port_name": "search"}],
        "input_ports": [None, {"port_name": "question"}],
        "output_ports": ["x"],
    }
    ports = parse_manifest_ports(manifest)
    # The flat ``ports:`` list is not a manifest form: it contributes nothing.
    assert [(p.key, p.port_type, p.direction) for p in ports] == [
        ("search", "tool", "OUT"),
        ("question", "in", "IN"),
    ]


def test_parse_manifest_ports_orders_fixed_then_typed_sections():
    manifest = {
        "output_ports": [{"port_name": "answer"}],
        "external_out_port": {"port_name": "reply"},
        "external_in_port": None,
        "ports": [{"key": "old", "direction": "out"}],
    }
    ports = parse_manifest_ports(manifest)
    assert [(p.key, p.port_type, p.direction) for p in ports] == [
        ("reply", "external_out", "OUT"),
        ("answer", "out", "OUT"),
    ]


def test_parse_manifest_ports_typed_item_port_type_sets_direction():
    ports = parse_manifest_ports(
        {
            "input_ports": [
                {"port_name": "ctl", "port_type": "tool"},
                {"port_name": "odd", "port_type": "custom"},
            ]
        }
    )
    assert [(p.key, p.port_type, p.direction, p.tool_call) for p in ports] == [
        ("ctl", "tool", "OUT", True),
        ("odd", "custom", "IN", False),
    ]


# ---------------------------------------------------------------------------
# perceivo.validator._port_rules._manifest_ports
# ---------------------------------------------------------------------------


def test_manifest_ports_skips_non_mapping_items_and_non_list_blocks():
    parsed = {
        "tool_ports": ["not-a-port", {"port_name": "  search  "}],
        "policy_ports": "not-a-list",
        "state_ports": 5,
        "external_in_port": {"name": "inbound"},
    }
    ports = _manifest_ports(parsed)
    assert sorted((p["key"], p["port_type"], p["direction"]) for p in ports) == [
        ("inbound", "external_in", "IN"),
        ("search", "tool", "OUT"),
    ]


def test_manifest_ports_reads_alias_spelling_and_ignores_a_flat_list():
    parsed = {
        "ports": [{"key": "a", "port_type": "tool"}, "junk"],
        "input_port": [{"port_name": "q", "port_type": "tool"}],
    }
    ports = _manifest_ports(parsed)
    assert ports == [{"key": "q", "port_type": "tool", "direction": "OUT"}]


def test_manifest_ports_blank_name_yields_empty_key():
    ports = _manifest_ports(
        {"tool_ports": [{"port_name": "   ", "name": 7}, {"port_name": "  ", "key": "fallback"}]}
    )
    assert ports == [
        {"key": "", "port_type": "tool", "direction": "OUT"},
        {"key": "fallback", "port_type": "tool", "direction": "OUT"},
    ]


# ---------------------------------------------------------------------------
# perceivo._bundle.orchestration.orchestration_to_bundle
# ---------------------------------------------------------------------------


def _write_project(path, data):
    path.mkdir(parents=True, exist_ok=True)
    (path / "orchestration.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_orchestration_to_bundle_without_yaml_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"No orchestration\.yaml"):
        orchestration_to_bundle(tmp_path)


def test_orchestration_to_bundle_drops_edges_to_unknown_and_converted_ports(tmp_path):
    project = _write_project(
        tmp_path / "proj",
        {
            "name": "pins",
            "nodes": [
                {"id": "a", "component_type": "AGENT", "name": "A"},
                {"id": "b", "component_type": "AGENT", "name": "B"},
            ],
            "ports": [
                {"id": "a_out", "node_id": "a", "key": "out", "port_type": "out"},
                {"id": "b_in", "node_id": "b", "key": "in", "port_type": "in"},
                {"id": "b_tool", "node_id": "b", "key": "tool", "port_type": "tool"},
            ],
            "edges": [
                {"source_port_id": "a_out", "target_port_id": "ghost"},
                {"source_port_id": "a_out", "target_port_id": "b_in"},
                {"source_port_id": "b_tool", "target_port_id": "ghost", "kind": "INVOKE"},
                {"source_port_id": "b_tool", "target_port_id": "a_out", "kind": "INVOKE"},
            ],
        },
    )
    bundle = orchestration_to_bundle(project)
    assert [p["component_port_key"] for p in bundle["ports"]] == ["in", "tool"]
    assert bundle["edges"] == []
    configs = {n["local_id"]: n["config"] for n in bundle["nodes"]}
    assert configs == {"a": {"mainflow_target_node_ref": "b"}, "b": None}
    assert bundle["components"] == {}
    assert "orchestrations" not in bundle


def test_orchestration_to_bundle_keeps_edges_between_kept_ports(tmp_path):
    project = _write_project(
        tmp_path / "proj",
        {
            "nodes": [
                {"id": "s", "component_type": "START"},
                {"id": "t", "component_type": "TOOL"},
            ],
            "ports": [
                {"id": "s_out", "node_id": "s", "key": "out", "port_type": "out"},
                {"id": "t_in", "node_id": "t", "key": "in", "port_type": "in"},
            ],
            "edges": [{"source_port_id": "s_out", "target_port_id": "t_in", "kind": "INVOKE"}],
        },
    )
    bundle = orchestration_to_bundle(project)
    assert bundle["orchestration"]["name"] == "proj"
    assert bundle["edges"] == [
        {"source_port_index": 0, "target_port_index": 1, "edge_kind": "INVOKE"}
    ]


def test_orchestration_to_bundle_rejects_a_nesting_cycle(tmp_path):
    from perceivo._bundle.orchestration import NestedOrchestrationError

    project = _write_project(
        tmp_path / "loop",
        {"nodes": [{"id": "n", "component_type": "ORCHESTRATION", "project": "."}]},
    )
    with pytest.raises(NestedOrchestrationError, match="cycle"):
        orchestration_to_bundle(project)


# ---------------------------------------------------------------------------
# perceivo.secrets.get
# ---------------------------------------------------------------------------


def _bind(stub_ext, *, declarations=None, aliases=None, values=None):
    info = {"declarations": declarations or {}, "aliases": aliases or {}}
    stored = values or {}

    async def fetch(secret_id):
        return stored.get(secret_id)

    stub_ext.get_node_secret_info = lambda: info
    stub_ext.fetch_secret_value = fetch


async def test_secrets_get_required_unbound_returns_explicit_default(stub_ext):
    _bind(stub_ext, declarations={"api_key": {"required": True}})
    assert await secrets.get("api_key", "fallback") == "fallback"


async def test_secrets_get_bound_without_value_says_bound(stub_ext):
    _bind(stub_ext, aliases={"api_key": "sec-1", "other": None})
    with pytest.raises(KeyError) as info:
        await secrets.get("api_key")
    message = info.value.args[0]
    assert message.startswith("Secret 'api_key' is bound but the platform returned no value for it")
    assert "Bound: api_key." in message
    assert "Declared but unbound: other." in message


async def test_secrets_get_declared_unbound_names_the_slot(stub_ext):
    _bind(stub_ext, declarations={"api_key": {}}, aliases={"api_key": None})
    with pytest.raises(KeyError) as info:
        await secrets.get("api_key")
    message = info.value.args[0]
    assert message.startswith("Secret 'api_key' is declared on this node but not bound")
    assert "Bound: (none)." in message
    assert "Declared but unbound: api_key." in message


async def test_secrets_get_undeclared_name_says_neither(stub_ext):
    _bind(stub_ext, declarations={"known": {}})
    with pytest.raises(KeyError) as info:
        await secrets.get("mystery")
    assert info.value.args[0].startswith(
        "Secret 'mystery' is not bound, and it is not declared on this node either"
    )


async def test_secrets_get_notebook_mode_miss_uses_default(stub_ext):
    _bind(stub_ext)
    assert await secrets.get("uuid-missing", None) is None


# ---------------------------------------------------------------------------
# perceivo.runner.executor._OrchestrationExecutor._execute_node (nested)
# ---------------------------------------------------------------------------


def _nested_executor(project_path):
    shell = NodeShell(
        node_id="child",
        name="child",
        component_type="ORCHESTRATION",
        project_path=project_path,
    )
    plan = CompileResult(
        root_id="child",
        start_node_id="child",
        end_node_id="child",
        shells_by_id={"child": shell},
    )
    return _OrchestrationExecutor(plan, LocalExtensionManager(load(None)))


async def test_execute_node_nested_without_project_path_raises():
    executor = _nested_executor(None)
    with pytest.raises(RuntimeError, match="Nested orchestration child has no valid project path"):
        await executor._execute_node("child", _NONE_ART, {})


async def test_execute_node_nested_child_failure_raises():
    executor = _nested_executor("sub")
    failed = SimpleNamespace(success=False, error="boom", terminal_failed=False)
    with (
        patch("perceivo.runner._nested._load_orchestration_file", return_value={}) as loaded,
        patch("perceivo.runner._nested.compile_orchestration", return_value=executor._plan),
        patch.object(_OrchestrationExecutor, "_execute_active", AsyncMock(return_value=failed)),
        pytest.raises(RuntimeError, match="Nested orchestration child failed: boom"),
    ):
        await executor._execute_node("child", _NONE_ART, {"q": 1})
    assert str(loaded.call_args.args[0]) == "sub"


async def test_execute_node_nested_child_success_returns_child_output():
    executor = _nested_executor("sub")
    done = SimpleNamespace(
        success=True,
        terminal_failed=False,
        artifact="art",
        response_data={"r": 1},
        artifacts={"x": 2},
        conversation_log=["log"],
    )
    run = AsyncMock(return_value=done)
    with (
        patch("perceivo.runner._nested._load_orchestration_file", return_value={}),
        patch("perceivo.runner._nested.compile_orchestration", return_value=executor._plan),
        patch.object(_OrchestrationExecutor, "_execute_active", run),
    ):
        out = await executor._execute_node("child", _NONE_ART, {"q": 1})
    assert out == {
        "artifact": "art",
        "response_data": {"r": 1},
        "artifacts": {"x": 2},
        "conversation_log": ["log"],
    }
    # subprocess mode: the child's request is the upstream response_data, and it
    # runs as a nested child, which seeds its own artifacts
    run.assert_awaited_once_with({"q": 1}, _NONE_ART, subprocess_child=True)


# ---------------------------------------------------------------------------
# perceivo.validator._orchestration._ports
# ---------------------------------------------------------------------------


def test_unwired_exempt_nodes_classifies_every_spelling():
    nodes = [
        {"id": "node_0", "config": {"mainflow_target_node_index": 2}},
        {"id": "node_1", "config": {"mainflow_target_node_id": 0, "is_agent_tool": True}},
        {
            "id": "node_2",
            "config": {"mainflow_target_node_ref": "", "is_agent_tool_internal": True},
        },
        {"id": "node_3", "config": {"mainflow_target_node_ref": "node_9"}},
        {"id": "node_4"},
    ]
    targets, combo, hidden = _unwired_exempt_nodes(nodes)
    assert targets == {"2", "node_2", "0", "node_9"}
    assert combo == {"node_1"}
    assert hidden == {"node_2"}


def test_all_ports_connected_exempts_hidden_and_combo_tool_in_ports():
    nodes = [
        {"id": "combo", "config": {"is_agent_tool": True}},
        {"id": "inner", "config": {"is_agent_tool_internal": True}},
        {"id": "plain"},
    ]
    ports = [
        {"id": "c_tool_in", "node_id": "combo", "port_type": "tool", "direction": "IN"},
        {"id": "c_tool_out", "node_id": "combo", "port_type": "tool", "direction": "OUT"},
        {"id": "c_in", "node_id": "combo", "port_type": "in"},
        {"id": "i_out", "node_id": "inner", "port_type": "out"},
        {"id": "p_tool_in", "node_id": "plain", "port_type": "tool", "direction": "in"},
    ]
    checks = _validate_all_ports_connected(ports, [], lambda _n: "AGENT", nodes)
    assert [(c.id, c.message.split("'")[1]) for c in checks] == [
        ("ORCH-UNWIRED-001", "c_tool_out"),
        ("ORCH-UNWIRED-002", "c_in"),
        ("ORCH-UNWIRED-003", "p_tool_in"),
    ]


# ---------------------------------------------------------------------------
# perceivo.validator._orchestration._edges.mainflow_continuation_findings
# ---------------------------------------------------------------------------


def test_mainflow_continuation_findings_flags_only_incapable_types():
    types = {
        "tool_cfg": "TOOL",
        "tool_port": "TOOL",
        "tool_invoke": "TOOL",
        "agent": "AGENT",
        "anon": "",
        "odd": "WEIRD",
    }
    nodes = [
        {"id": "tool_cfg", "config": {"mainflow_target_node_ref": "agent"}},
        {"id": "agent", "config": {"mainflow_target_node_id": "x"}},
        {"id": "anon", "config": {"mainflow_target_node_ref": "agent"}},
        {"id": "tool_port"},
        {"id": "tool_invoke"},
    ]
    ports = [
        {"id": "tp_out", "node_id": "tool_port", "port_type": "out", "name": "next"},
        {
            "id": "tp_virtual",
            "node_id": "tool_port",
            "port_type": "out",
            "virtual": True,
            "name": "ghost",
        },
        {"id": "tp_in", "node_id": "tool_port", "port_type": "in", "name": "entry"},
        {"id": "tc_out", "node_id": "tool_cfg", "port_type": "out", "name": "dup"},
        {"id": "ti_out", "node_id": "tool_invoke", "port_type": "out", "name": "call"},
        {"id": "odd_out", "node_id": "odd", "port_type": "out", "name": "onward"},
    ]
    # Skipped edges come first: a skip that stopped skipping would claim the
    # node's continuation before the real out-port edge could.
    edges = [
        {"source_port_id": "tp_virtual", "target_port_id": "x"},
        {"source_port_id": "tp_in", "target_port_id": "x"},
        {"source_port_id": "missing", "target_port_id": "x"},
        {"source_port_id": "ti_out", "target_port_id": "x", "kind": "INVOKE"},
        {"source_port_id": "tp_out", "target_port_id": "x"},
        {"source_port_id": "tc_out", "target_port_id": "x"},
        # A non-AGENT/TOOL out port on the mainflow is not the converted spelling.
        {"source_port_id": "odd_out", "target_port_id": "x"},
    ]
    findings = mainflow_continuation_findings(nodes, ports, edges, lambda n: types.get(n, ""))
    assert [f[0] for f in findings] == ["ORCH-MFCONT", "ORCH-MFCONT"]
    assert (
        "Node 'tool_cfg' declares a mainflow continuation (via config.mainflow_target_node_ref)"
        in findings[0][2]
    )
    assert (
        "Node 'tool_port' declares a mainflow continuation (via its declared 'next' out port)"
        in findings[1][2]
    )


# ---------------------------------------------------------------------------
# perceivo.validator._orchestration._structure._compute_fsm_depths
# ---------------------------------------------------------------------------


def test_compute_fsm_depths_chain_diamond_and_cycle():
    depth, cycles = _compute_fsm_depths(
        {"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": set(), "x": {"y"}, "y": {"x"}}
    )
    assert {k: depth[k] for k in "abcd"} == {"a": 3, "b": 2, "c": 2, "d": 1}
    assert cycles == {"x"}
    assert depth["x"] == 2
    assert depth["y"] == 1
