"""Capability bundles, IN tool ports, CRLF scripts and orchestration metadata.

The fixture is derived from the platform's seeded ``company-policy-chatbot``
capability bundle, the only seeded capability bundle. Its component scripts have
been edited since, by the primitives spelling migration and for the public-tree
content policy, so it is not byte-identical to the seed. Its RAG member carries
the three shapes these tests pin:

* an ``Embedding`` agent whose ``embedding_tool`` port is a ``tool`` port with
  direction **IN** (an exporter that forces every ``tool`` port to OUT fails to
  export the imported member with an undeclared-port error);
* a component script with CRLF line endings (text-mode writes on Windows turn
  it into doubled lines on re-export);
* ``orchestration.additional_packages: []`` (came back ``null``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

import perceivo.orchestration as orch_api
from perceivo._bundle._node_refs import canonical_node_config
from perceivo._bundle.capability import (
    CAPABILITY_FILE,
    capability_to_project,
    is_capability_project,
    member_name,
    project_to_capability,
)
from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._bundle.schema import OrchestrationExport
from perceivo._cli import main
from perceivo.orchestration._builder._bundle_project import (
    materialize_platform_bundle,
    write_platform_project,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "capability_company_policy_chatbot.json"
)
RAG = "Company Policy Chatbot - RAG"


@pytest.fixture
def capability() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


def _member(capability: dict[str, Any], name: str) -> dict[str, Any]:
    return copy.deepcopy(next(m for m in capability["members"] if member_name(m) == name))


def _semantic(bundle: dict[str, Any]) -> dict[str, Any]:
    """A bundle with ``comp_N`` refs resolved and null-vs-absent keys folded.

    The writer renumbers ``comp_N`` by node order and prunes null asset keys; a
    round-trip is correct when everything else is equal.
    """
    comps = bundle.get("components") or {}

    def clean(value: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in value.items() if v is not None}

    nodes = {}
    for node in bundle["nodes"]:
        body = clean({k: v for k, v in node.items() if k != "component_ref"})
        # The platform reads one mainflow key; its import scaffold is dropped.
        if "config" in body:
            body["config"] = canonical_node_config(body["config"])
        body["component"] = clean(comps.get(node["component_ref"]) or {})
        nodes[node["local_id"]] = body
    ports = [
        (p["node_ref"], p["component_port_key"], p["component_port_direction"], p.get("alias"))
        for p in bundle["ports"]
    ]
    edges = sorted(
        (ports[e["source_port_index"]], ports[e["target_port_index"]], e["edge_kind"])
        for e in bundle["edges"]
    )
    top = {
        k: v
        for k, v in bundle.items()
        if k not in {"components", "nodes", "ports", "edges", "exported_at", "new_name"}
    }
    return {"top": top, "nodes": nodes, "ports": sorted(ports), "edges": edges}


# --------------------------------------------------------------------------- #
# orchestration members                                                        #
# --------------------------------------------------------------------------- #


def test_in_tool_port_member_round_trips(capability, tmp_path):
    source = _member(capability, RAG)
    embedding = [
        p for c in source["components"].values() for p in c["ports"] if p["key"] == "embedding_tool"
    ]
    assert embedding
    assert {p["direction"] for p in embedding} == {"IN"}
    assert {p["port_type"] for p in embedding} == {"tool"}

    exported = orchestration_to_bundle(materialize_platform_bundle(source, tmp_path))

    assert _semantic(exported) == _semantic(source)
    rows = [p for p in exported["ports"] if p["component_port_key"] == "embedding_tool"]
    assert rows
    assert {p["component_port_direction"] for p in rows} == {"IN"}


def test_crlf_component_script_survives_byte_for_byte(capability, tmp_path):
    source = _member(capability, RAG)
    crlf = sorted(c["script"] for c in source["components"].values() if "\r\n" in c["script"])
    assert crlf, "fixture no longer carries a CRLF script"

    exported = orchestration_to_bundle(materialize_platform_bundle(source, tmp_path))

    scripts = sorted(c["script"] for c in exported["components"].values() if "\r\n" in c["script"])
    assert scripts == crlf


def test_orchestration_metadata_is_carried(capability, tmp_path):
    source = _member(capability, RAG)
    source["orchestration"].update(requires_gpu=True, disable_intermediate_snapshots=True)
    project = materialize_platform_bundle(source, tmp_path)
    written = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    assert written["orchestration_metadata"] == {
        "additional_packages": [],
        "requires_gpu": True,
        "disable_intermediate_snapshots": True,
    }
    meta = orchestration_to_bundle(project)["orchestration"]
    assert meta["additional_packages"] == []
    assert meta["requires_gpu"] is True
    assert meta["disable_intermediate_snapshots"] is True


@pytest.mark.parametrize(
    ("carried", "message"),
    [(["x"], "must be a mapping"), ({"name": "x"}, "undeclared fields")],
)
def test_orchestration_metadata_rejects_malformed_block(capability, tmp_path, carried, message):
    project = materialize_platform_bundle(_member(capability, RAG), tmp_path)
    path = project / "orchestration.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["orchestration_metadata"] = carried
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(BundleConfigError, match=message):
        orchestration_to_bundle(project)


def test_write_platform_project_rejects_non_bundle(tmp_path):
    with pytest.raises(ValueError, match="not a portable platform orchestration bundle"):
        write_platform_project({"export_type": "component"}, tmp_path, display_name="x")


# --------------------------------------------------------------------------- #
# capability bundles                                                           #
# --------------------------------------------------------------------------- #


def test_capability_import_writes_members_and_metadata(capability, tmp_path):
    root = capability_to_project(capability, tmp_path)

    assert is_capability_project(root)
    doc = yaml.safe_load((root / CAPABILITY_FILE).read_text(encoding="utf-8"))
    for key in ("capability", "contract", "traffic_plan", "export_version"):
        assert doc[key] == capability[key]
    assert [m["name"] for m in doc["members"]] == [member_name(m) for m in capability["members"]]
    for entry in doc["members"]:
        project = root / entry["project"]
        assert (project / "orchestration.yaml").is_file()
        # Short member dirs: nested children add levels below them (MAX_PATH).
        assert len(Path(entry["project"]).name) <= 34


def test_capability_round_trip_is_stable_and_faithful(capability, tmp_path):
    first = project_to_capability(capability_to_project(capability, tmp_path / "a"))
    second = project_to_capability(capability_to_project(first, tmp_path / "b"))

    assert first.pop("exported_at")
    assert second.pop("exported_at")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    assert first["export_type"] == "capability_bundle"
    for key in ("capability", "contract", "traffic_plan", "export_version"):
        assert first[key] == capability[key]
    assert len(first["members"]) == len(capability["members"])
    for got, want in zip(first["members"], capability["members"], strict=True):
        assert got["new_name"] == want["new_name"]
        assert "exported_at" not in got
        assert all("asset_id" not in c for c in got["components"].values())
        assert all("component_asset_id" not in n for n in got["nodes"])
        assert _semantic(got) == _semantic(want)


NESTED_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "nested_parity"
    / "fixtures"
    / "simple-embedded-orchestration.json"
)


def test_nested_member_children_keep_exported_at(capability, tmp_path):
    """Only the member's own stamp is dropped; a nested child is a full export.

    The platform import validates each ``orchestrations`` child as an
    ``OrchestrationExport`` with a required ``exported_at``, and its seed
    normalizer pops the field from the member alone.
    """
    member = json.loads(NESTED_FIXTURE.read_text(encoding="utf-8"))
    member.pop("exported_at", None)
    member["new_name"] = "Nested member"
    capability["members"].append(member)

    got = project_to_capability(capability_to_project(capability, tmp_path / "a"))
    exported = next(m for m in got["members"] if m["new_name"] == "Nested member")

    assert "exported_at" not in exported
    children = exported["orchestrations"]
    assert children
    for child in children.values():
        assert child["exported_at"]
        OrchestrationExport.model_validate(child)


def test_capability_name_override_renames_capability_only(capability, tmp_path):
    root = capability_to_project(capability, tmp_path, name_override="Renamed")
    assert root.name == "Renamed"
    bundle = project_to_capability(root)
    assert bundle["capability"]["name"] == "Renamed"
    assert [m["new_name"] for m in bundle["members"]] == [
        m["new_name"] for m in capability["members"]
    ]


def test_capability_unknown_top_level_keys_survive(capability, tmp_path):
    capability["future_key"] = {"kept": True}
    bundle = project_to_capability(capability_to_project(capability, tmp_path))
    assert bundle["future_key"] == {"kept": True}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.update(export_type="orchestration"), "not a capability bundle"),
        (lambda c: c.update(members=[]), "has no members"),
        (lambda c: c["members"].append({"export_type": "component"}), "is not an orchestration"),
        (lambda c: c["members"].append(copy.deepcopy(c["members"][0])), "both named"),
    ],
)
def test_capability_import_rejects_bad_bundles(capability, tmp_path, mutate, message):
    mutate(capability)
    with pytest.raises(ValueError, match=message):
        capability_to_project(capability, tmp_path)


def test_member_name_requires_a_name():
    assert member_name({"orchestration": {"name": "x"}}) == "x"
    with pytest.raises(BundleConfigError, match="neither new_name"):
        member_name({"orchestration": {}})


@pytest.mark.parametrize(
    ("document", "error", "message"),
    [
        ({"format": "other"}, BundleConfigError, "is not a"),
        (
            {"format": "perceivo_capability_bundle_v1", "members": []},
            BundleConfigError,
            "no members",
        ),
        (
            {"format": "perceivo_capability_bundle_v1", "members": [{"name": "x"}]},
            BundleConfigError,
            "needs a name and a project",
        ),
        (
            {
                "format": "perceivo_capability_bundle_v1",
                "members": [{"name": "x", "project": ".."}],
            },
            ValueError,
            "outside",
        ),
    ],
)
def test_capability_export_rejects_bad_projects(tmp_path, document, error, message):
    (tmp_path / CAPABILITY_FILE).write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(error, match=message):
        project_to_capability(tmp_path)


def test_capability_export_needs_capability_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        project_to_capability(tmp_path)


def test_orchestration_plane_import_and_export_capability(capability, tmp_path):
    root = orch_api.load_bundle(capability, str(tmp_path))
    out = tmp_path / "cap.json"
    bundle = orch_api.save_bundle(str(root), output=str(out))
    assert bundle["export_type"] == "capability_bundle"
    assert json.loads(out.read_text(encoding="utf-8"))["members"]
    with pytest.raises(ValueError, match="capability_bundle"):
        orch_api.load_bundle({"export_type": "nope"}, str(tmp_path))


def test_cli_import_and_export_capability(capability, tmp_path, monkeypatch):
    capability["members"][0]["orchestration"]["additional_packages"] = ["not-a-real-pkg-xyz"]
    source = tmp_path / "cap.bundle.json"
    source.write_text(json.dumps(capability), encoding="utf-8")
    runner = CliRunner()

    res = runner.invoke(main, ["import-bundle", str(source), "--output", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert "not-a-real-pkg-xyz" in res.output
    root = Path(res.output.strip().splitlines()[-1])
    assert is_capability_project(root)

    monkeypatch.chdir(tmp_path)
    res = runner.invoke(main, ["export", str(root)])
    assert res.exit_code == 0, res.output
    written = Path(res.output.strip().splitlines()[-1])
    assert written.name == "Company Policy Chatbot.bundle.json"
    assert json.loads(written.read_text(encoding="utf-8"))["export_type"] == "capability_bundle"
