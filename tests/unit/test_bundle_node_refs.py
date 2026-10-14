"""One mainflow key on import and export; ``@member:`` nodes resolve locally.

The fixture's Plain member carries node configs with both
``_mainflow_target_node_ref`` (platform import scaffolding, discarded by the
platform) and ``mainflow_target_node_ref`` (the key it reads), naming different
nodes. A router nests capability members by ``@member:<name>``; imported alone
such a node had no ``project`` and a local run failed with "has no valid
project path".
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
from perceivo._bundle._node_refs import (
    SCAFFOLD_MAINFLOW_KEY,
    canonical_node_config,
    capability_members,
    embed_member_refs,
)
from perceivo._bundle.capability import CAPABILITY_FILE, capability_to_project, member_name
from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo._cli import main
from perceivo.orchestration._builder._bundle_project import materialize_platform_bundle

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "capability_company_policy_chatbot.json"
)
PLAIN = "Company Policy Chatbot - Plain"
RAG = "Company Policy Chatbot - RAG"


@pytest.fixture
def capability() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


def _member(capability: dict[str, Any], name: str) -> dict[str, Any]:
    return copy.deepcopy(next(m for m in capability["members"] if member_name(m) == name))


def _nodes(project: Path) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load((project / "orchestration.yaml").read_text(encoding="utf-8"))
    return {n["id"]: n for n in doc["nodes"]}


def _router(capability: dict[str, Any], *refs: str) -> dict[str, Any]:
    """The Plain member plus one ``@member:`` node per ref, as a router nests them."""
    bundle = _member(capability, PLAIN)
    bundle["orchestration"]["name"] = bundle["new_name"] = "Router"
    for index, ref in enumerate(refs):
        bundle["nodes"].append(
            {
                "local_id": f"node_m{index}",
                "name": f"Nested {index}",
                "component_ref": f"orch_{index}",
                "orchestration_asset_id": f"@member:{ref}",
                "config": {"port_sides": {"": "bottom"}},
            }
        )
    return bundle


def _stable(bundle: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in bundle.items() if k != "exported_at"}, sort_keys=True)


# --------------------------------------------------------------------------- #
# (c) one mainflow key                                                         #
# --------------------------------------------------------------------------- #


def test_canonical_node_config_drops_only_the_scaffold_key():
    both = {SCAFFOLD_MAINFLOW_KEY: "node_8", "mainflow_target_node_ref": "node_9", "x": 1}
    assert canonical_node_config(both) == {"mainflow_target_node_ref": "node_9", "x": 1}
    assert both[SCAFFOLD_MAINFLOW_KEY] == "node_8"  # the caller's dict is not mutated
    plain = {"mainflow_target_node_ref": "node_9"}
    assert canonical_node_config(plain) is plain
    assert canonical_node_config(None) is None


def test_import_and_export_keep_the_key_the_platform_reads(capability, tmp_path):
    member = _member(capability, PLAIN)
    node_4 = next(n for n in member["nodes"] if n["local_id"] == "node_4")
    assert node_4["config"][SCAFFOLD_MAINFLOW_KEY] == "node_8"

    project = materialize_platform_bundle(member, tmp_path / "a")
    config = _nodes(project)["node_4"]["config"]
    assert config["mainflow_target_node_ref"] == "node_9"
    assert SCAFFOLD_MAINFLOW_KEY not in config

    first = orchestration_to_bundle(project)
    assert all(SCAFFOLD_MAINFLOW_KEY not in (n.get("config") or {}) for n in first["nodes"])
    second = orchestration_to_bundle(materialize_platform_bundle(first, tmp_path / "b"))
    assert _stable(second) == _stable(first)


def test_export_of_a_hand_written_scaffold_key_is_canonical(capability, tmp_path):
    project = materialize_platform_bundle(_member(capability, PLAIN), tmp_path)
    path = project / "orchestration.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for node in doc["nodes"]:
        if node["id"] == "node_4":
            node["config"][SCAFFOLD_MAINFLOW_KEY] = "node_8"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    exported = orchestration_to_bundle(project)
    assert all(SCAFFOLD_MAINFLOW_KEY not in (n.get("config") or {}) for n in exported["nodes"])


# --------------------------------------------------------------------------- #
# (b) @member: nodes                                                           #
# --------------------------------------------------------------------------- #


def test_member_nodes_resolve_to_an_embedded_child_and_export_unchanged(capability, tmp_path):
    root = capability_to_project(capability, tmp_path / "cap")
    router = _router(capability, PLAIN, RAG, "Not A Member")

    bare = orch_api.load_bundle(copy.deepcopy(router), str(tmp_path / "bare"))
    assert all("project" not in _nodes(bare)[f"node_m{i}"] for i in range(3))

    project = orch_api.load_bundle(router, str(tmp_path / "resolved"), members=str(root))
    nodes = _nodes(project)
    assert nodes["node_m0"]["project"] == "nested/m-Company-Policy-Chatbot-P"
    assert nodes["node_m1"]["project"] == "nested/m-Company-Policy-Chatbot-R"
    assert nodes["node_m0"]["orchestration_asset_id"] == f"@member:{PLAIN}"
    assert "project" not in nodes["node_m2"]
    for i in range(2):
        child = project / nodes[f"node_m{i}"]["project"]
        assert (child / "orchestration.yaml").is_file()

    # The reference is what is exported; the local copy is not.
    assert _stable(orchestration_to_bundle(project)) == _stable(orchestration_to_bundle(bare))


def test_members_resolve_siblings_and_children_inside_a_capability(capability, tmp_path):
    plain = next(m for m in capability["members"] if member_name(m) == PLAIN)
    plain["nodes"].append(
        {
            "local_id": "node_rag",
            "name": "RAG fallback",
            "component_ref": "orch_rag",
            "orchestration_asset_id": f"@member:{RAG}",
            "config": None,
        }
    )
    root = capability_to_project(capability, tmp_path)
    doc = yaml.safe_load((root / CAPABILITY_FILE).read_text(encoding="utf-8"))
    entry = next(e for e in doc["members"] if e["name"] == PLAIN)
    node = _nodes(root / entry["project"])["node_rag"]
    assert (root / entry["project"] / node["project"] / "orchestration.yaml").is_file()

    # A router whose embedded child nests a member is resolved at that depth.
    outer = tmp_path / "outer"
    (outer / "nested" / "inner").mkdir(parents=True)
    child_doc = {
        "name": "inner",
        "nodes": [{"id": "n", "orchestration_asset_id": f"@member:{RAG}"}],
    }
    (outer / "nested" / "inner" / "orchestration.yaml").write_text(
        yaml.safe_dump(child_doc), encoding="utf-8"
    )
    (outer / "orchestration.yaml").write_text(
        yaml.safe_dump({"name": "outer", "nodes": [{"id": "c", "project": "nested/inner"}]}),
        encoding="utf-8",
    )
    assert embed_member_refs(outer, capability_members(root)) == [RAG]
    assert _nodes(outer / "nested" / "inner")["n"]["project"] == "nested/m-Company-Policy-Chatbot-R"


def test_a_member_nesting_itself_is_refused(tmp_path):
    (tmp_path / "orchestration.yaml").write_text(
        yaml.safe_dump({"nodes": [{"id": "n", "orchestration_asset_id": "@member:self"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="nests its own"):
        embed_member_refs(tmp_path, {"self": tmp_path})


def test_capability_members_needs_a_capability_project(tmp_path):
    with pytest.raises(FileNotFoundError, match=CAPABILITY_FILE):
        capability_members(tmp_path)
    (tmp_path / CAPABILITY_FILE).write_text("format: x\n", encoding="utf-8")
    with pytest.raises(BundleConfigError, match="declares no members"):
        capability_members(tmp_path)
    (tmp_path / CAPABILITY_FILE).write_text(
        yaml.safe_dump({"members": [{"name": "up", "project": "../up"}, {"name": "x"}]}),
        encoding="utf-8",
    )
    assert capability_members(tmp_path) == {}


def test_cli_import_bundle_with_members(capability, tmp_path):
    root = capability_to_project(capability, tmp_path / "cap")
    source = tmp_path / "router.bundle.json"
    source.write_text(json.dumps(_router(capability, RAG)), encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(
        main,
        ["import-bundle", str(source), "--output", str(tmp_path / "out"), "--members", str(root)],
    )
    assert res.exit_code == 0, res.output
    project = Path(res.output.strip().splitlines()[-1])
    assert _nodes(project)["node_m0"]["project"] == "nested/m-Company-Policy-Chatbot-R"
