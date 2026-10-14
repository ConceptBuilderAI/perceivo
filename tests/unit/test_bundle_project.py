import json
import re

import pytest
import yaml
from click.testing import CliRunner

from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._cli import main as perceivo_cli
from perceivo.orchestration import load_bundle
from perceivo.runner import run_orchestration
from perceivo.types.errors import PerceivoError


def _component(name, component_type, script):
    return {
        "name": name,
        "script": script,
        "manifest_yaml": f"extension_type: {component_type.lower()}\nname: {name}\n",
        "ports": [],
    }


def _bundle(*, nested=False):
    components = {
        "c_start": _component(
            "Start",
            "START",
            "import perceivo\n@perceivo.start\nasync def start(i): return perceivo.primitives.StartOutput(artifact=i.artifact, response_data=i.request_data)\n",
        ),
        "c_end": _component(
            "End",
            "END",
            "import perceivo\n@perceivo.end\nasync def end(i): return perceivo.primitives.EndOutput(artifact=i.artifact, response_data=i.response_data)\n",
        ),
    }
    nodes = [
        {
            "local_id": "start",
            "name": "Start",
            "component_ref": "c_start",
            "config": {"mainflow_target_node_ref": "end"},
        },
        {"local_id": "end", "name": "End", "component_ref": "c_end", "config": {}},
    ]
    orchestrations = {}
    if nested:
        child = _bundle()
        orchestrations["child"] = child
        nodes.insert(
            1,
            {
                "local_id": "nested",
                "name": "Nested",
                "component_ref": "child",
                "orchestration_ref": "child",
                "config": {"execution_mode": "subprocess", "mainflow_target_node_ref": "end"},
            },
        )
        nodes[0]["config"]["mainflow_target_node_ref"] = "nested"
    return {
        "export_version": "1.0",
        "export_type": "orchestration",
        "orchestration": {"name": "Parent", "description": "fixture"},
        "components": components,
        "orchestrations": orchestrations,
        "nodes": nodes,
        "ports": [],
        "edges": [],
    }


def test_import_current_platform_bundle_materializes_project(tmp_path):
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_bundle()), encoding="utf-8")

    project = load_bundle(str(source), str(tmp_path / "out"))

    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    assert data["format"] == "perceivo_platform_bundle_v1"
    assert [node["component_type"] for node in data["nodes"]] == ["START", "END"]
    assert (project / "c_start.py").exists()


def test_imported_passthrough_fields_survive_a_private_round_trip(tmp_path):
    bundle = _bundle()
    bundle["components"]["c_start"]["ports"] = [
        {"direction": "IN", "key": "external_input", "port_type": "external_in"},
        {
            "direction": "OUT",
            "key": "output",
            "port_type": "out",
            "response_data_passthrough": "response_data",
            "response_data_passthrough_except": ["private"],
            "response_data_contract": "StartResult",
        },
    ]
    bundle["components"]["c_end"]["ports"] = [
        {"direction": "IN", "key": "input", "port_type": "in"},
        {"direction": "OUT", "key": "external_output", "port_type": "external_out"},
    ]
    bundle["ports"] = [
        {
            "node_ref": "start",
            "component_port_key": "output",
            "component_port_direction": "OUT",
        }
    ]
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    project = load_bundle(str(source), str(tmp_path / "out"))
    again = orchestration_to_bundle(project)
    (output,) = [p for p in again["components"]["comp_0"]["ports"] if p["key"] == "output"]

    assert output["response_data_passthrough"] == "response_data"
    assert output["response_data_passthrough_except"] == ["private"]
    assert output["response_data_contract"] == "StartResult"


def test_import_current_platform_bundle_materializes_nested_project(tmp_path):
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_bundle(nested=True)), encoding="utf-8")

    project = load_bundle(str(source), str(tmp_path / "out"))

    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    nested = data["nodes"][1]
    assert nested["component_type"] == "ORCHESTRATION"
    assert (project / nested["project"] / "orchestration.yaml").exists()


def test_import_rejects_both_private_and_embedded_child_references(tmp_path):
    bundle = _bundle(nested=True)
    bundle["nodes"][1]["orchestration_asset_id"] = "9f9f9f9f"
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(
        ValueError, match=r"both orchestration_ref.*orchestration_asset_id"
    ) as caught:
        load_bundle(str(source), str(tmp_path / "out"))
    assert isinstance(caught.value, PerceivoError)


def test_import_current_platform_bundle_rejects_bad_edge_index(tmp_path):
    bundle = _bundle()
    bundle["edges"] = [{"source_port_index": 3, "target_port_index": 0, "edge_kind": "MAINFLOW"}]
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    try:
        load_bundle(str(source), str(tmp_path / "out"))
    except ValueError as exc:
        assert "outside the port list" in str(exc)
    else:
        raise AssertionError("invalid edge index was accepted")


@pytest.mark.parametrize("index", [-1, "0", 1.5, True, None])
def test_import_current_platform_bundle_rejects_invalid_edge_index(tmp_path, index):
    bundle = _bundle()
    bundle["ports"] = [{}, {}]
    bundle["edges"] = [
        {"source_port_index": index, "target_port_index": 0, "edge_kind": "MAINFLOW"}
    ]
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(ValueError, match="non-negative integer"):
        load_bundle(str(source), str(tmp_path / "out"))


def test_import_current_platform_bundle_rejects_component_filename_collision(tmp_path):
    bundle = _bundle()
    # Real scripts are required because an empty one is rejected first, which
    # would make this test pass on the wrong error.
    bundle["components"]["same/name"] = _component("One", "AGENT", "ENTRY_CLASS = 'One'\n")
    bundle["components"]["same?name"] = _component("Two", "AGENT", "ENTRY_CLASS = 'Two'\n")
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(ValueError, match="component refs"):
        load_bundle(str(source), str(tmp_path / "out"))


def test_import_current_platform_bundle_rejects_nested_directory_collision(tmp_path):
    bundle = _bundle()
    bundle["orchestrations"] = {"same/name": _bundle(), "same?name": _bundle()}
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(ValueError, match="nested orchestration refs"):
        load_bundle(str(source), str(tmp_path / "out"))


def test_import_bundle_cli_materializes_project(tmp_path):
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_bundle()), encoding="utf-8")

    result = CliRunner().invoke(
        perceivo_cli,
        ["import-bundle", str(source), "--output", str(tmp_path), "--name", "CLI"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "CLI" / "orchestration.yaml").exists()


@pytest.mark.asyncio
async def test_materialized_nested_bundle_executes_end_to_end(tmp_path):
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_bundle(nested=True)), encoding="utf-8")
    project = load_bundle(str(source), str(tmp_path / "out"))

    result = await run_orchestration(str(project), input_data={"prompt": "hello"})

    assert result.success, result.error
    assert result.response_data == {"prompt": "hello"}


# ---------------------------------------------------------------------------
# An empty component script is a corrupt bundle, not a zero-byte file.
# ---------------------------------------------------------------------------


def _empty_script_bundle():
    bundle = _bundle()
    bundle["components"]["comp_3"] = {
        "name": "Nested Orchestration Node",
        "script": "",
        "asset_id": "a1b2c3d4",
        "category": "ORCHESTRATION",
        "manifest_yaml": "extension_type: agent\nname: Nested Orchestration Node\n",
        "ports": [],
    }
    bundle["nodes"].insert(
        1,
        {
            "local_id": "n_child",
            "name": "Child",
            "component_ref": "comp_3",
            "config": {"mainflow_target_node_ref": "end"},
        },
    )
    bundle["nodes"][0]["config"]["mainflow_target_node_ref"] = "n_child"
    return bundle


# The shape of a private service repository name; a public message names none.
_PRIVATE_SERVICE_REPO = re.compile(
    r"perceivo-[a-z]+(?:-[a-z]+)*-(?:microservice|service|manager|gateway|broker)\b"
)


def test_import_of_empty_script_component_fails_loud_and_names_it(tmp_path):
    """`perceivo import-bundle` must not exit 0 on a component with an empty script."""
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_empty_script_bundle()), encoding="utf-8")

    result = CliRunner().invoke(
        perceivo_cli,
        ["import-bundle", str(source), "--output", str(tmp_path), "--name", "Broken"],
    )

    assert result.exit_code != 0, result.output
    assert "comp_3" in result.output, result.output
    assert "Nested Orchestration Node" in result.output, result.output
    assert "a1b2c3d4" in result.output, result.output
    # Names the producer category without exposing a private service source path.
    assert "platform export operation" in result.output, result.output
    assert not _PRIVATE_SERVICE_REPO.search(result.output), result.output
    # And it wrote nothing: a half-project reads as an import that worked.
    assert not (tmp_path / "Broken" / "orchestration.yaml").exists()


def test_import_of_the_same_bundle_with_a_script_succeeds(tmp_path):
    """Positive control for the test above: only the blank script fails it."""
    bundle = _empty_script_bundle()
    bundle["components"]["comp_3"]["script"] = (
        "import perceivo\n"
        "@perceivo.agent\n"
        "async def run(i): return perceivo.primitives.AgentOutput(artifact=i.artifact)\n"
    )
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    result = CliRunner().invoke(
        perceivo_cli,
        ["import-bundle", str(source), "--output", str(tmp_path), "--name", "Fixed"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "Fixed" / "orchestration.yaml").exists()


def test_private_orchestration_reference_is_preserved_without_blank_source(tmp_path):
    bundle = _empty_script_bundle()
    bundle["nodes"][1]["orchestration_asset_id"] = "9f9f9f9f"
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    project = load_bundle(str(source), str(tmp_path / "out"))
    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    referenced = next(node for node in data["nodes"] if node["id"] == "n_child")

    assert referenced["component_type"] == "ORCHESTRATION"
    assert referenced["component_ref"] == "comp_3"
    assert referenced["orchestration_asset_id"] == "9f9f9f9f"
    assert not (project / "comp_3.py").exists()


def test_scriptless_component_that_is_a_nested_orchestration_is_exempt(tmp_path):
    """A ref keying both `components` and `orchestrations` resolves to the child.

    Without the exemption the rejection would fire on every well-formed nested
    export, so this is what keeps the empty-script check from being a blanket ban.
    """
    bundle = _bundle(nested=True)
    bundle["components"]["child"] = _component("Nested", "ORCHESTRATION", "")

    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")
    project = load_bundle(str(source), str(tmp_path / "out"))

    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    assert data["nodes"][1]["component_type"] == "ORCHESTRATION"
    assert not (project / "child.py").exists()


# ---------------------------------------------------------------------------
# Additional packages are recorded, warned about, and surfaced.
# ---------------------------------------------------------------------------

_ABSENT = "unavailable-public-test-distribution"


def _packages_bundle():
    bundle = _bundle(nested=True)
    bundle["orchestration"]["additional_packages"] = ["pyyaml"]
    bundle["components"]["c_start"]["additional_packages"] = [f"{_ABSENT}>=1.0"]
    bundle["orchestrations"]["child"]["components"]["c_end"]["additional_packages"] = [
        "unavailable-nested-only"
    ]
    return bundle


def test_additional_packages_are_recorded_in_the_generated_project(tmp_path):
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_packages_bundle()), encoding="utf-8")

    project = load_bundle(str(source), str(tmp_path / "out"))

    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    assert data["additional_packages"] == [
        "pyyaml",
        "unavailable-nested-only",
        f"{_ABSENT}>=1.0",
    ], data["additional_packages"]


def test_a_bundle_declaring_no_packages_records_no_key(tmp_path):
    """Positive control: the key appears because the bundle declared something."""
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_bundle(nested=True)), encoding="utf-8")

    project = load_bundle(str(source), str(tmp_path / "out"))

    data = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    assert "additional_packages" not in data, data


def test_import_bundle_warns_about_declared_packages_that_are_absent(tmp_path):
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_packages_bundle()), encoding="utf-8")

    result = CliRunner().invoke(
        perceivo_cli,
        ["import-bundle", str(source), "--output", str(tmp_path), "--name", "Pkgs"],
    )

    assert result.exit_code == 0, result.output
    assert _ABSENT in result.output, result.output
    assert "unavailable-nested-only" in result.output, result.output
    # pyyaml IS installed (this test file imports it), so naming it would mean
    # the check reports every declaration rather than the missing ones.
    assert "pyyaml" not in result.output, result.output


@pytest.mark.asyncio
async def test_run_surfaces_missing_packages_on_the_result(tmp_path):
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(_packages_bundle()), encoding="utf-8")
    project = load_bundle(str(source), str(tmp_path / "out"))

    result = await run_orchestration(str(project), input_data={"prompt": "hello"})

    assert result.success, result.error
    assert result.missing_packages == [
        "unavailable-nested-only",
        f"{_ABSENT}>=1.0",
    ], result.missing_packages
