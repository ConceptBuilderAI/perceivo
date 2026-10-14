"""Local import/export of components & orchestrations in the platform bundle format."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import perceivo.component as component_api
import perceivo.orchestration as orch_api
from perceivo._bundle.component import bundle_to_component, component_to_bundle
from perceivo._bundle.orchestration import bundle_to_orchestration, orchestration_to_bundle
from perceivo._bundle.schema import ComponentExport, OrchestrationExport
from perceivo._cli import main


def _write_component(directory: Path, name: str = "greet") -> Path:
    (directory / f"{name}.py").write_text(
        "import perceivo\n\n\n@perceivo.tool\nasync def greet(i):\n    return i\n",
        encoding="utf-8",
    )
    (directory / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {
                "extension_type": "TOOL",
                "name": name,
                "description": "greets",
                "tags": ["demo"],
                "input_ports": [{"port_name": "input", "port_type": "in"}],
                "output_ports": [{"port_name": "output", "port_type": "out"}],
            }
        ),
        encoding="utf-8",
    )
    return directory / f"{name}.py"


# -- component ---------------------------------------------------------------


def test_component_export_matches_platform_schema(tmp_path):
    script = _write_component(tmp_path)
    bundle = component_to_bundle(script)
    ComponentExport.model_validate(bundle)  # exact platform shape
    assert bundle["export_type"] == "component"
    comp = bundle["component"]
    assert comp["name"] == "greet"
    assert comp["category"] == "TOOL"  # extension_type -> category
    dirs = {p["key"]: p["direction"] for p in comp["ports"]}
    assert dirs == {"input": "IN", "output": "OUT"}  # in/out -> IN/OUT
    assert "def greet" in comp["script"]
    assert "extension_type: TOOL" in comp["manifest_yaml"]  # manifest preserved verbatim


def test_component_export_preserves_response_passthrough_declarations(tmp_path):
    script = _write_component(tmp_path)
    manifest_path = script.with_suffix(".yaml")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["output_ports"][0].update(
        {
            "response_data_passthrough": "response_data",
            "response_data_passthrough_except": ["private"],
            "response_data_contract": "GreetingResult",
        }
    )
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    output = component_to_bundle(script)["component"]["ports"][1]

    assert output["response_data_passthrough"] == "response_data"
    assert output["response_data_passthrough_except"] == ["private"]
    assert output["response_data_contract"] == "GreetingResult"


def test_component_round_trip_restores_files(tmp_path):
    script = _write_component(tmp_path)
    bundle = component_to_bundle(script)
    out = tmp_path / "restored"
    bundle_to_component(bundle, out)
    assert "def greet" in (out / "greet.py").read_text(encoding="utf-8")
    manifest = yaml.safe_load((out / "greet.yaml").read_text(encoding="utf-8"))
    assert manifest["extension_type"] == "TOOL"


def test_component_import_rejects_wrong_bundle(tmp_path):
    with pytest.raises(ValueError, match="not a portable component bundle"):
        bundle_to_component({"export_type": "orchestration"}, tmp_path)


def test_component_import_rejects_traversal_name(tmp_path):
    script = _write_component(tmp_path)
    bundle = component_to_bundle(script)
    with pytest.raises(ValueError, match="Unsafe component name"):
        bundle_to_component(bundle, tmp_path, name_override="../evil")


def _named_bundle(tmp_path: Path, display_name: str) -> dict:
    bundle = component_to_bundle(_write_component(tmp_path))
    bundle["component"]["name"] = display_name
    return bundle


@pytest.mark.parametrize(
    ("display_name", "stem"),
    [
        ("DELAY / WAIT (SYSTEM, READ-ONLY)", "delay_wait_system_read_only"),
        ("GENERIC END (SYSTEM, READ-ONLY)", "generic_end_system_read_only"),
        ("../x", "x"),
        ("/ ()", "imported"),
    ],
    ids=["separator", "spaces", "traversal", "empty-slug"],
)
def test_component_import_derives_the_stem_from_the_display_name(tmp_path, display_name, stem):
    out = tmp_path / "out"
    bundle_to_component(_named_bundle(tmp_path, display_name), out)
    assert sorted(p.name for p in out.iterdir()) == [
        f"{stem}.component.json",
        f"{stem}.py",
        f"{stem}.yaml",
    ]


@pytest.mark.parametrize("override", ["a/b", "..", "../evil"])
def test_component_import_refuses_an_unsafe_override_as_given(tmp_path, override):
    with pytest.raises(ValueError, match="Unsafe component name"):
        bundle_to_component(_named_bundle(tmp_path, "Fine"), tmp_path / "o", name_override=override)


def test_component_import_uses_an_override_exactly(tmp_path):
    out = tmp_path / "out"
    bundle_to_component(_named_bundle(tmp_path, "DELAY / WAIT"), out, name_override="My Wait")
    assert (out / "My Wait.py").is_file()


@pytest.mark.parametrize("second", ["Foo / Bar", "foo-bar"])
def test_component_import_refuses_a_stem_another_component_holds(tmp_path, second):
    out = tmp_path / "out"
    bundle_to_component(_named_bundle(tmp_path, "Foo (Bar)"), out)
    first = (out / "foo_bar.py").read_bytes()
    other = _named_bundle(tmp_path, second)
    other["component"]["script"] = "# another component\n"
    with pytest.raises(ValueError, match=r"'foo_bar'.*'Foo \(Bar\)'.*--name") as caught:
        bundle_to_component(other, out)
    assert repr(second) in str(caught.value)
    assert (out / "foo_bar.py").read_bytes() == first


def test_component_import_keeps_files_no_record_ties_to_it(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "foo_bar.py").write_text("# hand-written\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no import record"):
        bundle_to_component(_named_bundle(tmp_path, "Foo (Bar)"), out)
    assert (out / "foo_bar.py").read_text(encoding="utf-8") == "# hand-written\n"


def test_component_reimport_of_the_same_component_updates_it(tmp_path):
    out = tmp_path / "out"
    bundle_to_component(_named_bundle(tmp_path, "Foo (Bar)"), out)
    updated = _named_bundle(tmp_path, "Foo (Bar)")
    updated["component"]["script"] = "# version two\n"
    bundle_to_component(updated, out)
    assert (out / "foo_bar.py").read_text(encoding="utf-8") == "# version two\n"


def test_component_reimport_matches_on_asset_id_over_a_renamed_display_name(tmp_path):
    out = tmp_path / "out"
    first = _named_bundle(tmp_path, "Foo")
    first["component"]["asset_id"] = "a-1"
    bundle_to_component(first, out, name_override="foo")
    renamed = _named_bundle(tmp_path, "Foo renamed")
    renamed["component"]["asset_id"] = "a-1"
    renamed["component"]["script"] = "# renamed\n"
    bundle_to_component(renamed, out, name_override="foo")
    assert (out / "foo.py").read_text(encoding="utf-8") == "# renamed\n"
    stranger = _named_bundle(tmp_path, "Foo renamed")
    stranger["component"]["asset_id"] = "a-2"
    with pytest.raises(ValueError, match="Refusing to import"):
        bundle_to_component(stranger, out, name_override="foo")


def test_component_import_refuses_a_too_long_name_before_writing(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="too long"):
        bundle_to_component(_named_bundle(tmp_path, "B" * 245), out)
    assert not out.exists()


def test_cli_imports_the_delay_wait_library_bundle(tmp_path):
    from perceivo.templates._catalog import _library_root

    bundle = _library_root() / "delay-wait.cbcomponent.json"
    res = CliRunner().invoke(main, ["import-bundle", str(bundle), "--output", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "delay_wait_system_read_only.py").is_file()


# -- orchestration -----------------------------------------------------------


def test_orchestration_export_matches_platform_schema(tmp_path):
    project = orch_api.create("demo", directory=str(tmp_path))
    bundle = orchestration_to_bundle(project)
    OrchestrationExport.model_validate(bundle)
    assert bundle["export_type"] == "orchestration"
    assert bundle["components"]
    assert bundle["nodes"]
    assert bundle["ports"]
    # every node references a component ref that exists in the components dict
    for node in bundle["nodes"]:
        assert node["component_ref"] in bundle["components"]
    # edges reference valid port indices
    for edge in bundle["edges"]:
        assert 0 <= edge["source_port_index"] < len(bundle["ports"])
        assert 0 <= edge["target_port_index"] < len(bundle["ports"])


def test_orchestration_round_trip_is_stable(tmp_path):
    project = orch_api.create("demo", directory=str(tmp_path))
    bundle = orchestration_to_bundle(project)
    restored = bundle_to_orchestration(bundle, tmp_path / "out")
    again = orchestration_to_bundle(restored)
    assert len(again["nodes"]) == len(bundle["nodes"])
    assert len(again["components"]) == len(bundle["components"])
    assert len(again["edges"]) == len(bundle["edges"])


# -- public API + CLI --------------------------------------------------------


def test_the_bundle_aliases_are_gone():
    """Each plane has one ``save_bundle`` and one ``load_bundle``, not two names.

    The ``*_bundle`` aliases and the trailing-underscore ``import_`` (with its
    ``export`` pair) are all gone; the pair is named for what it does.
    """
    for plane, gone in (
        (
            component_api,
            ("export_component_bundle", "import_component_bundle", "import_", "export"),
        ),
        (
            orch_api,
            ("export_orchestration_bundle", "import_orchestration_bundle", "import_", "export"),
        ),
    ):
        for name in gone:
            assert not hasattr(plane, name), f"{plane.__name__}.{name} survived"
        # Positive control: the verbs that replaced them really are there, so a
        # plane that failed to import could not pass this by being empty.
        assert callable(plane.save_bundle), f"{plane.__name__}.save_bundle is missing"
        assert callable(plane.load_bundle), f"{plane.__name__}.load_bundle is missing"


def test_cli_export_and_import_component(tmp_path):
    script = _write_component(tmp_path)
    out_bundle = tmp_path / "greet.bundle.json"
    runner = CliRunner()
    res = runner.invoke(main, ["export", str(script), "-o", str(out_bundle)])
    assert res.exit_code == 0, res.output
    data = json.loads(out_bundle.read_text(encoding="utf-8"))
    assert data["export_type"] == "component"

    dest = tmp_path / "imported"
    res2 = runner.invoke(main, ["import-bundle", str(out_bundle), "--output", str(dest)])
    assert res2.exit_code == 0, res2.output
    assert (dest / "greet.py").exists()


def test_cli_export_orchestration(tmp_path):
    project = orch_api.create("demo", directory=str(tmp_path))
    out_bundle = tmp_path / "orch.bundle.json"
    runner = CliRunner()
    res = runner.invoke(main, ["export", str(project), "-o", str(out_bundle)])
    assert res.exit_code == 0, res.output
    data = json.loads(out_bundle.read_text(encoding="utf-8"))
    assert data["export_type"] == "orchestration"


def test_publish_dry_run_ships_platform_bundle(tmp_path):
    import asyncio

    project = orch_api.create("demo", directory=str(tmp_path))
    result = asyncio.run(orch_api.publish(str(project), dry_run=True))
    assert result.dry_run is True
    assert result.bundle["export_type"] == "orchestration"  # platform shape, not private


def test_orchestration_import_types_a_node_from_a_category_only_manifest(tmp_path):
    bundle = orchestration_to_bundle(orch_api.create("demo", directory=str(tmp_path)))
    (ref,) = [
        r
        for r, c in bundle["components"].items()
        if "extension_type: AGENT" in (c.get("manifest_yaml") or "")
    ]
    component = bundle["components"][ref]
    component["manifest_yaml"] = component["manifest_yaml"].replace(
        "extension_type: AGENT", "category: TOOL"
    )
    component.pop("component_type", None)
    restored = bundle_to_orchestration(bundle, tmp_path / "out")
    project = Path(restored) / "orchestration.yaml"
    nodes = yaml.safe_load(project.read_text(encoding="utf-8"))["nodes"]
    assert "TOOL" in {n["component_type"] for n in nodes}
    assert "AGENT" not in {n["component_type"] for n in nodes}


def test_orchestration_import_types_a_node_from_the_bundle_category(tmp_path):
    bundle = orchestration_to_bundle(orch_api.create("demo", directory=str(tmp_path)))
    (ref,) = [
        r
        for r, c in bundle["components"].items()
        if "extension_type: AGENT" in (c.get("manifest_yaml") or "")
    ]
    component = bundle["components"][ref]
    # The manifest names no type, so only the bundle's own category can make it a TOOL.
    manifest = yaml.safe_load(component["manifest_yaml"])
    del manifest["extension_type"]
    component["manifest_yaml"] = yaml.safe_dump(manifest)
    component["category"] = "TOOL"
    component.pop("component_type", None)
    restored = bundle_to_orchestration(bundle, tmp_path / "out")
    project = Path(restored) / "orchestration.yaml"
    nodes = yaml.safe_load(project.read_text(encoding="utf-8"))["nodes"]
    assert "TOOL" in {n["component_type"] for n in nodes}
    assert "AGENT" not in {n["component_type"] for n in nodes}
