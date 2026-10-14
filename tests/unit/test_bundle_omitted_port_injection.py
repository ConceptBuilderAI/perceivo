"""Importing a bundle gives each node every port its component declares.

The platform materializes an orchestration bundle by adding a node port for
every component port the bundle's ``ports`` list omitted, except state and
policy slots. A bundle that omits a START's external input or an END's external output,
as the platform's assistant seed bundles do, therefore has those ports locally
and passes the PORT-RULE checks the platform passes it on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from perceivo.orchestration import load_bundle
from perceivo.validator._orchestration import validate_orchestration

_START = (
    "import perceivo\n@perceivo.start\nasync def start(i): "
    "return perceivo.primitives.StartOutput(artifact=i.artifact, response_data=i.request_data)\n"
)
_END = (
    "import perceivo\n@perceivo.end\nasync def end(i): "
    "return perceivo.primitives.EndOutput(artifact=i.artifact, response_data=i.response_data)\n"
)


def _port(key: str, direction: str, port_type: str) -> dict[str, Any]:
    return {"key": key, "direction": direction, "port_type": port_type}


def _component(name: str, kind: str, script: str, ports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "script": script,
        "manifest_yaml": f"extension_type: {kind.lower()}\nname: {name}\n",
        "ports": ports,
    }


def _bundle(end_ports: list[dict[str, Any]]) -> dict[str, Any]:
    """START -> END, with only the mainflow ports listed, as the assistant seeds list them."""
    return {
        "export_type": "orchestration",
        "orchestration": {"name": "Omitted Ports"},
        "components": {
            "c_start": _component(
                "Start",
                "START",
                _START,
                [_port("trigger", "IN", "external_in"), _port("output", "OUT", "out")],
            ),
            "c_end": _component("End", "END", _END, end_ports),
        },
        "nodes": [
            {"local_id": "start", "name": "Start", "component_ref": "c_start", "config": {}},
            {"local_id": "end", "name": "End", "component_ref": "c_end", "config": {}},
        ],
        "ports": [
            {
                "node_ref": "start",
                "component_port_key": "output",
                "component_port_direction": "OUT",
                "alias": "output",
            },
            {
                "node_ref": "end",
                "component_port_key": "input",
                "component_port_direction": "IN",
                "alias": "input",
            },
        ],
        "edges": [{"source_port_index": 0, "target_port_index": 1, "edge_kind": "MAINFLOW"}],
    }


_END_PORTS = [
    _port("input", "IN", "in"),
    _port("response", "OUT", "external_out"),
    _port("slot", "OUT", "state"),
    _port("rules", "IN", "policy"),
]


def _ports(project: Path) -> list[tuple[str, str, str, str]]:
    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    return [
        (p["node_id"], p["key"], p["direction"], p["port_type"])
        for p in data["ports"]
        if not p.get("virtual")
    ]


def test_omitted_component_ports_are_added_except_state_and_policy(tmp_path):
    project = load_bundle(_bundle(_END_PORTS), str(tmp_path))

    assert _ports(project) == [
        ("start", "output", "out", "out"),
        ("end", "input", "in", "in"),
        ("start", "trigger", "in", "external_in"),
        ("end", "response", "out", "external_out"),
    ]


def test_a_bundle_omitting_its_external_ports_has_them_when_validated(tmp_path):
    """The END here declares only its two ports: the platform refuses to create an
    END with state or policy ports, whatever the bundle's rows list."""
    report = validate_orchestration(str(load_bundle(_bundle(_END_PORTS[:2]), str(tmp_path))))

    assert not [c.message for c in report.checks if "PORT-RULE" in c.id]


def test_an_omitted_port_nothing_connects_is_reported_unconnected(tmp_path):
    """The added port is a real port: the platform refuses it unconnected, and so does the SDK."""
    end_ports = [*_END_PORTS[:2], _port("audit", "IN", "in")]

    report = validate_orchestration(str(load_bundle(_bundle(end_ports), str(tmp_path))))

    unwired = [c.message for c in report.checks if c.id.startswith("ORCH-UNWIRED")]
    assert not report.valid
    assert unwired == [
        "Port 'audit' on END node is not connected. Wire it, or remove it from the "
        "component if it has nothing to connect to."
    ]
