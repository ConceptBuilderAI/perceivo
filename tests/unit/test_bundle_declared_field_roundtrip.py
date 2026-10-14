"""Lossless declared-field coverage for bundle project materialization."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

import perceivo.orchestration as orchestration
from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.schema import (
    ComponentExportData,
    FSMStateExportData,
    NodeExportData,
    OrchestrationExport,
    OrchestrationExportMetadata,
    PortExportData,
    PortRefExportData,
    SecretHintData,
)
from perceivo.orchestration._builder._bundle_integrity import assert_bundle_fidelity

_STAMP = datetime(2026, 9, 16, 18, 30, tzinfo=UTC)
_SCRIPT = "import perceivo\n\n@perceivo.agent\nasync def preserved(input):\n    return input\n"
_MANIFEST = """name: ManifestName
extension_type: agent
input_ports:
  - port_name: input
    port_type: in
"""


def _component() -> ComponentExportData:
    return ComponentExportData(
        asset_id="source-component-asset",
        name="Preserved Display Name",
        category="AGENT",
        description="long description",
        short_description="short description",
        tags=["one", "two"],
        config={"freeform": {"optional": None}},
        additional_packages=["example-package==1.0"],
        requires_gpu=True,
        artifact_type_input="json",
        artifact_type_output="text",
        script=_SCRIPT,
        manifest_yaml=_MANIFEST,
        ports=[
            PortExportData(
                direction="IN",
                key="input",
                label="Exact label",
                port_type="in",
                contract={"type": "object"},
                tool_call=False,
                post_tool_contract={"required": ["result"]},
                base_schema={"type": "object"},
                artifact_type_constraint="json",
                allowed_states=["ready"],
                request_data_schema={"prompt": {"type": "string"}},
                response_data_schema={"result": {"type": "string"}},
                artifact_type_input="json",
                artifact_type_output="text",
                artifact_input_schema={"type": "object"},
                artifact_output_schema={"type": "string"},
                multi_artifact=False,
                artifact_types=["json", "text"],
                response_data_passthrough="all",
                response_data_passthrough_except=["private"],
                response_data_contract="strict",
            )
        ],
        fsm_states=[
            FSMStateExportData(
                state_key="ready",
                label="Ready",
                in_port_key="input",
                out_port_key="input",
                is_start=True,
                is_end=False,
            )
        ],
        is_template_clone=True,
        template_source_id="template-source",
        is_prebuilt_reference=True,
        prebuilt_asset_id="prebuilt-source",
    )


def _flat_bundle(component_ref: str = "source_ref") -> dict:
    return OrchestrationExport(
        exported_at=_STAMP,
        orchestration=OrchestrationExportMetadata(
            name="Declared fields", description="Declared field fixture"
        ),
        components={component_ref: _component()},
        nodes=[
            NodeExportData(
                local_id="node_0",
                component_ref=component_ref,
                component_asset_id="component-asset",
                name="First",
                position={},
                config={},
            ),
            NodeExportData(
                local_id="node_1",
                component_ref=component_ref,
                name="Second",
                position=None,
                config=None,
            ),
        ],
        ports=[
            PortRefExportData(
                node_ref="node_0",
                component_port_key="input",
                component_port_direction="IN",
                alias=None,
                effective_contract={"type": "object"},
            ),
            PortRefExportData(
                node_ref="node_1",
                component_port_key="input",
                component_port_direction="IN",
                alias="named-input",
                effective_contract=None,
            ),
        ],
        edges=[],
        secrets_required=[SecretHintData(alias="optional", hint=None, node_ref="node_0")],
    ).model_dump(mode="json")


def _align_generated_component_ref(source: dict, candidate: dict) -> dict:
    aligned = deepcopy(candidate)
    source_ref = next(iter(source["components"]))
    candidate_ref = next(iter(aligned["components"]))
    aligned["components"] = {source_ref: aligned["components"][candidate_ref]}
    for node in aligned["nodes"]:
        if node["component_ref"] == candidate_ref:
            node["component_ref"] = source_ref
    return aligned


def test_flat_roundtrip_preserves_all_declared_component_node_and_port_fields(
    tmp_path: Path,
) -> None:
    original = _flat_bundle()
    project = orchestration.load_bundle(original, str(tmp_path / "materialized"))
    candidate = orchestration.save_bundle(str(project))

    aligned = _align_generated_component_ref(original, candidate)
    assert aligned["components"]["source_ref"]["script"] == _SCRIPT
    assert aligned["components"]["source_ref"]["manifest_yaml"] == _MANIFEST
    assert aligned["nodes"][0]["config"] == {}
    assert aligned["nodes"][1]["config"] is None
    assert aligned["ports"][0]["alias"] is None
    assert_bundle_fidelity(original, aligned)


def test_nested_roundtrip_restores_declared_fields_recursively(tmp_path: Path) -> None:
    child = _flat_bundle("comp_0")
    parent = OrchestrationExport(
        exported_at=_STAMP,
        orchestration=OrchestrationExportMetadata(name="Parent", description="Nested parent"),
        components={},
        nodes=[
            NodeExportData(
                local_id="nested",
                component_ref="orch_0",
                orchestration_ref="orch_0",
                name="Nested",
                position={"x": 1, "y": 2},
                config={},
            )
        ],
        ports=[],
        edges=[],
        orchestrations={"orch_0": OrchestrationExport.model_validate(child)},
    ).model_dump(mode="json")

    project = orchestration.load_bundle(parent, str(tmp_path / "materialized"))
    candidate = orchestration.save_bundle(str(project))

    assert_bundle_fidelity(
        parent,
        candidate,
        allowed_differences=("exported_at", "orchestrations.orch_0.exported_at"),
    )


@pytest.mark.parametrize("invalid", [[], "invalid", False, 0])
def test_export_rejects_malformed_node_config_instead_of_discarding_it(
    tmp_path: Path, invalid: object
) -> None:
    project = orchestration.load_bundle(_flat_bundle(), str(tmp_path / "materialized"))
    manifest = project / "orchestration.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["nodes"][0]["config"] = invalid
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(BundleConfigError, match=r"node_0.*config must be mapping or null"):
        orchestration.save_bundle(str(project))


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    [
        ("component_metadata", [], "component_metadata must be a mapping or null"),
        ("component_metadata", {"script": "replacement"}, "undeclared fields"),
        ("component_metadata", {"unsupported": True}, "undeclared fields"),
        ("component_ports", {}, "component_ports must be a list"),
    ],
)
def test_export_rejects_invalid_preserved_component_fields(
    tmp_path: Path, field: str, invalid: object, message: str
) -> None:
    """Edited project metadata cannot bypass source files or lose invalid ports."""
    project = orchestration.load_bundle(_flat_bundle(), str(tmp_path / "materialized"))
    manifest = project / "orchestration.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["nodes"][0][field] = invalid
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(BundleConfigError, match=message):
        orchestration.save_bundle(str(project))


def test_null_component_metadata_uses_source_manifest_fields(tmp_path: Path) -> None:
    """A project can clear metadata while retaining its source and port contract."""
    project = orchestration.load_bundle(_flat_bundle(), str(tmp_path / "materialized"))
    manifest = project / "orchestration.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    for node in data["nodes"]:
        node["component_metadata"] = None
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")

    candidate = orchestration.save_bundle(str(project))
    component = next(iter(candidate["components"].values()))
    assert component["name"] == "ManifestName"
    assert component["script"] == _SCRIPT
    assert component["ports"][0]["label"] == "Exact label"
