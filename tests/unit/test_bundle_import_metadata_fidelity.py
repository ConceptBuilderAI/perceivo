"""Regressions from the first-five public SDK-writer candidates."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from perceivo import orchestration
from perceivo._bundle.schema import (
    ComponentExportData,
    EdgeExportData,
    NodeExportData,
    OrchestrationExport,
    OrchestrationExportMetadata,
    PortExportData,
    PortRefExportData,
)
from perceivo.orchestration._builder._bundle_integrity import assert_bundle_fidelity
from perceivo.runner.compiler import compile_project

_STAMP = datetime(2026, 9, 17, tzinfo=UTC)


def _component(name: str, category: str, ports: list[PortExportData]) -> ComponentExportData:
    return ComponentExportData(
        name=name,
        category=category,
        script=f"# {name}\n",
        manifest_yaml=f"name: {name}\nextension_type: {category.lower()}\n",
        ports=ports,
    )


def _flat_bundle(*, tags: list[str] | None = None) -> dict[str, Any]:
    # A START needs its external port and exactly one OUT port, as the platform requires.
    component = _component(
        "Tagged Start",
        "START",
        [
            PortExportData(direction="IN", key="trigger", port_type="external_in"),
            PortExportData(direction="OUT", key="output", port_type="out"),
        ],
    )
    return cast(
        dict[str, Any],
        OrchestrationExport(
            exported_at=_STAMP,
            orchestration=OrchestrationExportMetadata(
                name="Tagged",
                description="Tagged orchestration",
                tags=tags,
            ),
            components={"comp_0": component},
            nodes=[NodeExportData(local_id="start", component_ref="comp_0", config={})],
            ports=[
                PortRefExportData(
                    node_ref="start",
                    component_port_key="trigger",
                    component_port_direction="IN",
                ),
                PortRefExportData(
                    node_ref="start",
                    component_port_key="output",
                    component_port_direction="OUT",
                ),
            ],
            edges=[],
        ).model_dump(mode="json"),
    )


def _nested_config_mainflow_bundle() -> dict[str, Any]:
    start = _component(
        "Start",
        "START",
        [
            PortExportData(direction="IN", key="trigger", port_type="external_in"),
            PortExportData(direction="OUT", key="output", port_type="out"),
        ],
    )
    end = _component(
        "End",
        "END",
        [
            PortExportData(direction="IN", key="input", port_type="in"),
            PortExportData(direction="OUT", key="result", port_type="external_out"),
        ],
    )
    child = OrchestrationExport(
        exported_at=_STAMP,
        orchestration=OrchestrationExportMetadata(name="Child", description="Child"),
        components={},
        nodes=[],
        ports=[],
        edges=[],
    )
    return cast(
        dict[str, Any],
        OrchestrationExport(
            exported_at=_STAMP,
            orchestration=OrchestrationExportMetadata(name="Parent", description="Parent"),
            components={"comp_0": start, "comp_1": end},
            nodes=[
                NodeExportData(local_id="start", component_ref="comp_0", config={}),
                NodeExportData(
                    local_id="nested",
                    component_ref="orch_0",
                    orchestration_ref="orch_0",
                    config={
                        "execution_mode": "subprocess",
                        "mainflow_target_node_ref": "end",
                    },
                ),
                NodeExportData(local_id="end", component_ref="comp_1", config={}),
            ],
            ports=[
                PortRefExportData(
                    node_ref="start",
                    component_port_key="output",
                    component_port_direction="OUT",
                ),
                PortRefExportData(
                    node_ref="nested",
                    component_port_key="orchestration",
                    component_port_direction="IN",
                ),
                PortRefExportData(
                    node_ref="start",
                    component_port_key="trigger",
                    component_port_direction="IN",
                ),
                PortRefExportData(
                    node_ref="end",
                    component_port_key="input",
                    component_port_direction="IN",
                ),
                PortRefExportData(
                    node_ref="end",
                    component_port_key="result",
                    component_port_direction="OUT",
                ),
            ],
            edges=[
                EdgeExportData(
                    source_port_index=0,
                    target_port_index=1,
                    edge_kind="MAINFLOW",
                )
            ],
            orchestrations={"orch_0": child},
        ).model_dump(mode="json"),
    )


def test_import_roundtrip_preserves_orchestration_tags(tmp_path: Path) -> None:
    original = _flat_bundle(tags=["sample", "tool-chaining", "invoke"])
    project = orchestration.load_bundle(original, str(tmp_path / "materialized"))
    candidate = orchestration.save_bundle(str(project))

    assert candidate["orchestration"]["tags"] == original["orchestration"]["tags"]
    assert_bundle_fidelity(original, candidate)


def test_import_roundtrip_does_not_expand_nested_config_mainflow(tmp_path: Path) -> None:
    original = _nested_config_mainflow_bundle()
    project = orchestration.load_bundle(original, str(tmp_path / "materialized"))
    compiled = compile_project(str(project))
    candidate = orchestration.save_bundle(str(project))

    assert compiled.shells_by_id["nested"].mainflow_successors == ["end"]
    assert len(candidate["ports"]) == 5
    assert len(candidate["edges"]) == 1
    assert_bundle_fidelity(
        original,
        candidate,
        allowed_differences=("exported_at", "orchestrations.orch_0.exported_at"),
    )
