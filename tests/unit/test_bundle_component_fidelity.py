"""Component bundles: template links, and import -> export without loss.

``bundle_to_component`` writes every field, not only the script and the
manifest. An export that rebuilt the rest from the manifest would change or lose
the component's own name and description, ``short_description``, the artifact
types, ``config``, port labels and contracts, and the template link. The script
keeps its LF line endings on Windows.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

import perceivo
from perceivo._bundle._component_sidecar import sidecar_path
from perceivo._bundle.component import (
    BundleConfigError,
    bundle_to_component,
    component_to_bundle,
)
from perceivo._cli import main

LIBRARY_IDS = {
    "llm-request": "a0000000-0000-000a-00c0-000000000016",
    "generic-start": "a0000000-0000-000a-00c0-000000000011",
    "generic-end": "a0000000-0000-000a-00c0-000000000010",
}


def _rich_bundle() -> dict[str, Any]:
    return {
        "export_version": "1.0",
        "export_type": "component",
        "exported_at": "2026-09-01T00:00:00+00:00",
        "component": {
            "asset_id": None,
            "name": "LLM REQUEST (COPY)",
            "category": "AGENT",
            "description": "Platform description, not the manifest's.",
            "short_description": "Short.",
            "tags": ["template", "llm"],
            "config": {
                "port_order": ["input", "output"],
                "input_parameters": {"declarations": [{"name": "p"}], "values": {"p": 1}},
            },
            "additional_packages": ["httpx"],
            "requires_gpu": False,
            "artifact_type_input": "json",
            "artifact_type_output": "json",
            "script": "import perceivo\n\n\nasync def run(x):\n    return x\n",
            "manifest_yaml": "extension_type: agent\nname: LLM Request\ninput_ports:\n"
            "  - port_name: input\n    port_type: in\n",
            "ports": [
                {
                    "key": "input",
                    "label": "Input",
                    "direction": "IN",
                    "port_type": "in",
                    "contract": {"type": "object"},
                    "artifact_types": ["json"],
                }
            ],
            "fsm_states": [],
            "is_template_clone": True,
            "template_source_id": LIBRARY_IDS["llm-request"],
            "is_prebuilt_reference": False,
            "prebuilt_asset_id": None,
        },
    }


def _component(bundle: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in bundle["component"].items()}


def _import(bundle: dict[str, Any], tmp_path: Path) -> Path:
    return bundle_to_component(bundle, tmp_path, name_override="c") / "c.py"


def _use(name, dest):
    folder = perceivo.templates.use(name, into=str(dest))
    script = next(folder.glob("*.py"))
    return SimpleNamespace(script=script, manifest=script.with_suffix(".yaml"))


# --------------------------------------------------------------------------- #
# template link                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(LIBRARY_IDS))
def test_library_template_exports_its_template_link(name, tmp_path):
    t = _use(name, tmp_path)
    component = component_to_bundle(t.script, t.manifest)["component"]
    assert component["template_source_id"] == LIBRARY_IDS[name]
    assert component["is_template_clone"] is True


def test_llm_request_template_carries_the_platform_declarations(tmp_path):
    t = _use("llm-request", tmp_path)
    component = component_to_bundle(t.script, t.manifest)["component"]
    assert component["category"] == "AGENT"
    declared = {d["name"] for d in component["config"]["input_parameters"]["declarations"]}
    assert {"provider", "model", "user_prompt", "system_prompt"} <= declared
    assert [(p["key"], p["direction"]) for p in component["ports"]] == [("input", "IN")]


def test_a_manifest_without_a_link_exports_none(tmp_path):
    t = _use("json-handler", tmp_path)
    t.manifest.write_text(
        "".join(
            line
            for line in t.manifest.read_text(encoding="utf-8").splitlines(keepends=True)
            if not line.startswith("template_source_id:")
        ),
        encoding="utf-8",
        newline="",
    )
    # The import sidecar also records the link; a hand-made component has none.
    t.script.with_suffix(".component.json").unlink()
    component = component_to_bundle(t.script, t.manifest)["component"]
    assert component["template_source_id"] is None
    assert component["is_template_clone"] is False


# --------------------------------------------------------------------------- #
# import -> export fidelity                                                    #
# --------------------------------------------------------------------------- #


def test_component_round_trip_is_exact(tmp_path):
    bundle = _rich_bundle()
    script = _import(bundle, tmp_path)
    assert sidecar_path(script).is_file()

    out = component_to_bundle(script, script.with_suffix(".yaml"))
    got = _component(out)
    want = _component(bundle)
    got["ports"] = [{k: v for k, v in p.items() if k in want["ports"][0]} for p in got["ports"]]
    assert got == want


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_script_and_manifest_bytes_survive(tmp_path, newline):
    bundle = _rich_bundle()
    component = bundle["component"]
    component["script"] = component["script"].replace("\n", newline)
    component["manifest_yaml"] = component["manifest_yaml"].replace("\n", newline)
    script = _import(bundle, tmp_path)

    assert script.read_bytes() == component["script"].encode("utf-8")
    out = component_to_bundle(script, script.with_suffix(".yaml"))["component"]
    assert out["script"] == component["script"]
    assert out["manifest_yaml"] == component["manifest_yaml"]


def test_an_edited_manifest_wins_for_what_it_declares(tmp_path):
    script = _import(_rich_bundle(), tmp_path)
    manifest = script.with_suffix(".yaml")
    manifest.write_text(
        "extension_type: tool\nname: Edited\ndescription: New.\ninput_ports:\n"
        "  - port_name: invoke\n    port_type: in\n",
        encoding="utf-8",
        newline="",
    )
    out = component_to_bundle(script, manifest)["component"]
    assert (out["name"], out["category"], out["description"]) == ("Edited", "TOOL", "New.")
    assert [p["key"] for p in out["ports"]] == ["invoke"]
    # What the manifest does not declare still comes from the record,
    # including the template link.
    assert out["template_source_id"] == LIBRARY_IDS["llm-request"]
    assert out["short_description"] == "Short."
    assert out["artifact_type_input"] == "json"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "not valid JSON"),
        (json.dumps({"format": "other"}), "is not a"),
        (json.dumps({"format": "perceivo_component_fields_v1", "component": []}), "mapping"),
        (
            json.dumps({"format": "perceivo_component_fields_v1", "component": {"script": "x"}}),
            "undeclared fields",
        ),
    ],
)
def test_a_bad_record_is_refused(tmp_path, content, message):
    script = _import(_rich_bundle(), tmp_path)
    sidecar_path(script).write_text(content, encoding="utf-8")
    manifest = script.with_suffix(".yaml")
    with pytest.raises(BundleConfigError, match=message):
        component_to_bundle(script, manifest)


def test_cli_import_then_export_component(tmp_path, monkeypatch):
    source = tmp_path / "c.bundle.json"
    source.write_text(json.dumps(_rich_bundle()), encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(main, ["import-bundle", str(source), "--output", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert list((tmp_path / "out").rglob("*.component.json"))

    script = next((tmp_path / "out").rglob("*.py"))
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(main, ["export", str(script)])
    assert res.exit_code == 0, res.output
    written = Path(res.output.strip().splitlines()[-1])
    component = json.loads(written.read_text(encoding="utf-8"))["component"]
    assert component["template_source_id"] == LIBRARY_IDS["llm-request"]
    assert yaml.safe_load(component["manifest_yaml"])["name"] == "LLM Request"
