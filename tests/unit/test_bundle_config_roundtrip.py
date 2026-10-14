"""Component config and secret-hint fidelity across SDK project materialization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import perceivo.orchestration as orchestration
from perceivo._bundle.component import BundleConfigError, component_to_bundle
from perceivo.orchestration._builder._bundle_integrity import assert_bundle_fidelity
from perceivo.types.errors import ConfigurationError

_SCRIPT = (
    "class SyntheticAgent:\n"
    "    async def run(self, input_data):\n"
    "        return input_data\n"
    "\n"
    "ENTRY_CLASS = SyntheticAgent\n"
)
_MANIFEST = """\
name: Synthetic Agent
extension_type: agent
input_parameters:
  - name: prompt
    type: text
    default: hello
required_secrets:
  - name: synthetic_api_key
    required: false
ports: []
"""


def _component(config: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "name": "Synthetic Agent",
        "category": "AGENT",
        "config": config,
        "script": _SCRIPT,
        "manifest_yaml": _MANIFEST,
        "ports": [],
        "fsm_states": [],
    }


def _flat_bundle(
    *,
    name: str = "Config Fidelity",
    config: dict[str, Any] | None,
    secrets: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "export_version": "1.0",
        "export_type": "orchestration",
        "exported_at": "2026-09-16T12:00:00Z",
        "orchestration": {"name": name, "description": "synthetic fidelity fixture"},
        "components": {"comp_0": _component(config)},
        "nodes": [
            {
                "local_id": "node_0",
                "component_ref": "comp_0",
                "name": "Synthetic Agent",
                "config": {
                    "input_parameters": {"values": {"prompt": "hello"}},
                    "secret_bindings": {"bindings": {"synthetic_api_key": None}},
                },
            }
        ],
        "ports": [],
        "edges": [],
        "secrets_required": secrets,
    }


def test_public_import_export_preserves_config_secrets_and_source(tmp_path: Path) -> None:
    component_config = {
        "input_parameters": {
            "declarations": [{"name": "prompt", "type": "text", "default": "hello"}],
            "values": {},
        },
        "required_secrets": [{"name": "synthetic_api_key", "required": False}],
        "freeform": {"optional": None, "items": [1, None]},
    }
    secret_hints = [{"alias": "synthetic_api_key", "hint": None, "node_ref": "node_0"}]
    original = _flat_bundle(config=component_config, secrets=secret_hints)

    project = orchestration.load_bundle(original, str(tmp_path / "materialized"))
    project_yaml = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    candidate = orchestration.save_bundle(str(project))

    assert project_yaml["nodes"][0]["component_config"] == component_config
    assert project_yaml["nodes"][0]["config"] == original["nodes"][0]["config"]
    assert project_yaml["secrets_required"] == secret_hints
    assert candidate["components"]["comp_0"]["config"] == component_config
    assert candidate["secrets_required"] == secret_hints
    assert candidate["components"]["comp_0"]["script"] == _SCRIPT
    assert candidate["components"]["comp_0"]["manifest_yaml"] == _MANIFEST
    assert_bundle_fidelity(original, candidate)


def test_nested_secret_hints_and_explicit_null_component_config_survive(tmp_path: Path) -> None:
    child_secrets = [
        {"alias": "child_optional", "hint": None, "node_ref": None},
    ]
    child = _flat_bundle(name="Child", config=None, secrets=child_secrets)
    child["exported_at"] = "2026-09-16T12:01:00Z"
    parent_secrets = [
        {"alias": "parent_optional", "hint": "optional", "node_ref": "node_parent"},
    ]
    original = {
        "export_version": "1.0",
        "export_type": "orchestration",
        "exported_at": "2026-09-16T12:00:00Z",
        "orchestration": {"name": "Parent", "description": "nested fidelity fixture"},
        "components": {},
        "nodes": [
            {
                "local_id": "node_parent",
                "component_ref": "orch_0",
                "orchestration_ref": "orch_0",
                "name": "Child",
                "config": {"execution_mode": "subprocess"},
            }
        ],
        "ports": [],
        "edges": [],
        "secrets_required": parent_secrets,
        "orchestrations": {"orch_0": child},
    }

    project = orchestration.load_bundle(original, str(tmp_path / "materialized"))
    candidate = orchestration.save_bundle(str(project))

    exported_child = candidate["orchestrations"]["orch_0"]
    assert candidate["secrets_required"] == parent_secrets
    assert exported_child["secrets_required"] == child_secrets
    assert exported_child["components"]["comp_0"]["config"] is None
    assert exported_child["components"]["comp_0"]["manifest_yaml"] == _MANIFEST
    assert_bundle_fidelity(
        original,
        candidate,
        allowed_differences=("exported_at", "orchestrations.orch_0.exported_at"),
    )


def test_source_manifest_maps_declarations_with_explicit_config_precedence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "source"
    project.mkdir()
    (project / "agent.py").write_text(_SCRIPT, encoding="utf-8")
    manifest = """\
name: Source Agent
extension_type: agent
input_parameters:
  - name: top_level_prompt
    type: text
output_parameters:
  - name: answer
    type: text
required_secrets:
  - name: source_api_key
    required: false
config:
  input_parameters:
    declarations:
      - name: explicit_prompt
        type: text
    values:
      explicit_prompt: null
  freeform:
    optional: null
ports: []
"""
    (project / "agent.yaml").write_text(manifest, encoding="utf-8", newline="")
    (project / "orchestration.yaml").write_text(
        yaml.safe_dump(
            {
                "format": "perceivo_platform_bundle_v1",
                "name": "Source manifest mapping",
                "nodes": [
                    {
                        "id": "agent",
                        "name": "Source Agent",
                        "component_type": "AGENT",
                        "script": "agent.py",
                        "manifest": "agent.yaml",
                        "config": {"runtime": {"optional": None}},
                    }
                ],
                "ports": [],
                "edges": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    candidate = orchestration.save_bundle(str(project))
    component = candidate["components"]["comp_0"]

    assert component["config"] == {
        "input_parameters": {
            "declarations": [{"name": "explicit_prompt", "type": "text"}],
            "values": {"explicit_prompt": None},
        },
        "output_parameters": {
            "declarations": [{"name": "answer", "type": "text"}],
            "values": {},
        },
        "required_secrets": [{"name": "source_api_key", "required": False}],
        "freeform": {"optional": None},
    }
    assert component["manifest_yaml"] == manifest


def test_component_config_participates_in_same_file_dedup_identity(tmp_path: Path) -> None:
    project = tmp_path / "identity"
    project.mkdir()
    (project / "shared.py").write_text(_SCRIPT, encoding="utf-8")
    (project / "shared.yaml").write_text(
        "name: Shared Agent\nextension_type: agent\nports: []\n",
        encoding="utf-8",
    )
    first = {"variant": "first", "optional": None}
    second = {"variant": "second", "optional": None}
    nodes = [
        {
            "id": node_id,
            "name": node_id,
            "component_type": "AGENT",
            "script": "shared.py",
            "manifest": "shared.yaml",
            "component_config": config,
            "config": {"runtime_node": node_id},
        }
        for node_id, config in (("one", first), ("two", second), ("three", first))
    ]
    (project / "orchestration.yaml").write_text(
        yaml.safe_dump(
            {
                "format": "perceivo_platform_bundle_v1",
                "name": "Config identity",
                "nodes": nodes,
                "ports": [],
                "edges": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    candidate = orchestration.save_bundle(str(project))
    refs = {node["local_id"]: node["component_ref"] for node in candidate["nodes"]}

    assert len(candidate["components"]) == 2
    assert refs["one"] == refs["three"]
    assert refs["one"] != refs["two"]
    assert candidate["components"][refs["one"]]["config"] == first
    assert candidate["components"][refs["two"]]["config"] == second


def test_invalid_manifest_config_uses_typed_legacy_compatible_error(tmp_path: Path) -> None:
    script = tmp_path / "invalid.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    script.with_suffix(".yaml").write_text(
        "name: Invalid\nextension_type: agent\nconfig: not-a-mapping\n",
        encoding="utf-8",
    )

    with pytest.raises(BundleConfigError) as caught:
        component_to_bundle(script)

    assert isinstance(caught.value, ConfigurationError)
    assert isinstance(caught.value, ValueError)


def test_invalid_node_component_config_uses_typed_legacy_compatible_error(
    tmp_path: Path,
) -> None:
    project = tmp_path / "invalid-node"
    project.mkdir()
    (project / "agent.py").write_text(_SCRIPT, encoding="utf-8")
    (project / "agent.yaml").write_text(
        "name: Invalid Node\nextension_type: agent\nports: []\n",
        encoding="utf-8",
    )
    (project / "orchestration.yaml").write_text(
        yaml.safe_dump(
            {
                "format": "perceivo_platform_bundle_v1",
                "name": "Invalid node config",
                "nodes": [
                    {
                        "id": "agent",
                        "name": "Invalid Node",
                        "component_type": "AGENT",
                        "script": "agent.py",
                        "manifest": "agent.yaml",
                        "component_config": "not-a-mapping",
                    }
                ],
                "ports": [],
                "edges": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(BundleConfigError) as caught:
        orchestration.save_bundle(str(project))

    assert isinstance(caught.value, ConfigurationError)
    assert isinstance(caught.value, ValueError)
