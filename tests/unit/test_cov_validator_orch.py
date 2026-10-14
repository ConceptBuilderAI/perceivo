"""Coverage-focused unit tests for ``perceivo.validator._orchestration._loader``.

Exercises the loader's file-handling branches: missing and malformed files,
empty collections, nodes without a script, and manifests whose ports are
absent, mistyped or unparseable.
"""

from __future__ import annotations

from perceivo.validator._orchestration._loader import _load_from_directory

from .fixtures_orch_validator import _write

# ---------------------------------------------------------------------------
# _loader.py
# ---------------------------------------------------------------------------


def test_loader_missing_file_returns_none(tmp_path):
    assert _load_from_directory(str(tmp_path)) is None


def test_loader_malformed_yaml_returns_none(tmp_path):
    # Unbalanced flow sequence -> yaml.YAMLError.
    _write(tmp_path, "orchestration.yaml", "nodes: [unclosed\n")
    assert _load_from_directory(str(tmp_path)) is None


def test_loader_empty_yaml_yields_empty_collections(tmp_path):
    # safe_load returns None -> `or {}` path.
    _write(tmp_path, "orchestration.yaml", "")
    loaded = _load_from_directory(str(tmp_path))
    assert loaded == {
        "nodes": [],
        "edges": [],
        "ports": [],
        "component_types": {},
        "components": [],
        "bundled_ports": {},
    }


def test_loader_node_without_script_is_skipped(tmp_path):
    _write(
        tmp_path,
        "orchestration.yaml",
        "nodes:\n  - id: s\n    component_type: START\n",
    )
    loaded = _load_from_directory(str(tmp_path))
    assert loaded is not None
    assert loaded["component_types"] == {"s": "START"}
    assert loaded["components"] == []


def test_loader_script_and_manifest_with_ports(tmp_path):
    _write(
        tmp_path,
        "orchestration.yaml",
        (
            "nodes:\n"
            "  - id: a\n"
            "    name: agent1\n"
            "    component_type: AGENT\n"
            "    script: agent.py\n"
            "    manifest: agent.yaml\n"
            "edges: []\n"
            "ports:\n"
            "  - id: seed\n"
            "    node_id: a\n"
        ),
    )
    _write(tmp_path, "agent.py", "def run():\n    return 1\n")
    _write(
        tmp_path,
        "agent.yaml",
        "ports:\n  - name: in0\n    port_type: in\n  - not_a_dict\n",
    )
    loaded = _load_from_directory(str(tmp_path))
    assert loaded is not None
    comp = loaded["components"][0]
    assert comp["name"] == "agent1"
    assert comp["script"].startswith("def run")
    assert comp["manifest_yaml"].startswith("ports:")
    # A flat manifest ``ports:`` list is not a manifest form: it adds no graph
    # port, so only the graph's own seed port is present.
    assert [p["id"] for p in loaded["ports"]] == ["seed"]


def test_loader_missing_script_file_leaves_blank(tmp_path):
    _write(
        tmp_path,
        "orchestration.yaml",
        ("nodes:\n  - id: a\n    component_type: AGENT\n    script: nope.py\n"),
    )
    loaded = _load_from_directory(str(tmp_path))
    assert loaded is not None
    assert loaded["components"][0]["script"] == ""
    # None is no manifest file; an empty string is an empty one.
    assert loaded["components"][0]["manifest_yaml"] is None


def test_loader_manifest_ports_not_a_list(tmp_path):
    _write(
        tmp_path,
        "orchestration.yaml",
        (
            "nodes:\n"
            "  - id: a\n"
            "    component_type: AGENT\n"
            "    script: agent.py\n"
            "    manifest: agent.yaml\n"
        ),
    )
    _write(tmp_path, "agent.py", "x = 1\n")
    _write(tmp_path, "agent.yaml", "ports: not-a-list\n")
    loaded = _load_from_directory(str(tmp_path))
    assert loaded is not None
    # No ports were appended from the manifest.
    assert loaded["ports"] == []


def test_loader_manifest_malformed_yaml_swallowed(tmp_path):
    _write(
        tmp_path,
        "orchestration.yaml",
        (
            "nodes:\n"
            "  - id: a\n"
            "    component_type: AGENT\n"
            "    script: agent.py\n"
            "    manifest: agent.yaml\n"
        ),
    )
    _write(tmp_path, "agent.py", "x = 1\n")
    _write(tmp_path, "agent.yaml", "ports: [unclosed\n")
    loaded = _load_from_directory(str(tmp_path))
    assert loaded is not None
    # Malformed manifest yaml is swallowed; component still recorded.
    assert loaded["components"][0]["manifest_yaml"].startswith("ports:")
    assert loaded["ports"] == []
