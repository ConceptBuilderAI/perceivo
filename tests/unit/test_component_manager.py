"""Unit tests for perceivo.component._manager (info, inspect, io, create)."""

from __future__ import annotations

import json

import pytest

from perceivo.component._manager._create import create
from perceivo.component._manager._info import ComponentInfo, _detect_entry_class
from perceivo.component._manager._inspect import details, list_components, validate
from perceivo.component._manager._io import load_bundle, save_bundle

# ---------------------------------------------------------------------------
# _info
# ---------------------------------------------------------------------------


def test_component_info_to_dict():
    info = ComponentInfo(name="x", component_type="TOOL", tags=["a"])
    d = info.to_dict()
    assert d["name"] == "x"
    assert d["component_type"] == "TOOL"
    assert d["tags"] == ["a"]


def test_detect_entry_class_string_assignment():
    code = "ENTRY_CLASS = 'MyTool'\nclass MyTool: pass\n"
    assert _detect_entry_class(code) == "MyTool"


def test_detect_entry_class_decorated_async_function():
    code = "import perceivo\n@perceivo.agent\nasync def my_agent(inp):\n    return None\n"
    assert _detect_entry_class(code) == "my_agent"


def test_detect_entry_class_none_when_absent():
    assert _detect_entry_class("x = 1\n") is None


def test_detect_entry_class_syntax_error_returns_none():
    assert _detect_entry_class("def f(:\n") is None


# ---------------------------------------------------------------------------
# _inspect.details / list_components
# ---------------------------------------------------------------------------


def _write_component(directory, name="mytool", ctype="TOOL"):
    py = directory / f"{name}.py"
    py.write_text(f"ENTRY_CLASS = '{name.title()}'\nclass {name.title()}: pass\n", encoding="utf-8")
    yaml_f = directory / f"{name}.yaml"
    yaml_f.write_text(
        f"name: {name}\nextension_type: {ctype}\ndescription: a tool\ntags: [t1]\nports: []\n",
        encoding="utf-8",
    )
    return py, yaml_f


def test_details_reads_manifest(tmp_path):
    py, _ = _write_component(tmp_path)
    info = details(str(py))
    assert info.name == "mytool"
    assert info.component_type == "TOOL"
    assert info.description == "a tool"
    assert info.entry_class == "Mytool"


def test_details_missing_script_raises(tmp_path):
    missing = str(tmp_path / "nope.py")
    with pytest.raises(FileNotFoundError):
        details(missing)


def test_details_explicit_manifest_path(tmp_path):
    py, yaml_f = _write_component(tmp_path, name="other")
    info = details(str(py), str(yaml_f))
    assert info.manifest_path.endswith("other.yaml")


def test_list_components_finds_pairs(tmp_path):
    _write_component(tmp_path, name="alpha")
    _write_component(tmp_path, name="beta")
    # A yaml without extension_type should be skipped.
    (tmp_path / "ignore.yaml").write_text("name: x\n", encoding="utf-8")
    (tmp_path / "ignore.py").write_text("# noop\n", encoding="utf-8")
    infos = list_components(str(tmp_path))
    names = {i.name for i in infos}
    assert {"alpha", "beta"} <= names
    assert "x" not in names


def test_list_components_nonexistent_dir(tmp_path):
    assert list_components(str(tmp_path / "missing")) == []


def test_validate_returns_report(tmp_path):
    py, yaml_f = _write_component(tmp_path)
    report = validate(str(py), str(yaml_f))
    # ValidationReport exposes a `valid` flag and a list of checks.
    assert hasattr(report, "valid")
    assert hasattr(report, "checks")


def test_validate_missing_script_raises(tmp_path):
    missing = str(tmp_path / "nope.py")
    with pytest.raises(FileNotFoundError):
        validate(missing)


# ---------------------------------------------------------------------------
# _io export / import
# ---------------------------------------------------------------------------


def test_export_writes_a_platform_bundle(tmp_path):
    py, yaml_f = _write_component(tmp_path)
    out = tmp_path / "export.json"
    bundle = save_bundle(str(py), str(yaml_f), output=str(out))
    assert bundle["export_type"] == "component"
    assert bundle["component"]["name"] == "mytool"
    assert bundle["component"]["category"] == "TOOL"
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["component"]["script"].startswith("ENTRY_CLASS")


def test_export_missing_script(tmp_path):
    missing = str(tmp_path / "nope.py")
    with pytest.raises(FileNotFoundError):
        save_bundle(missing)


def test_export_rejects_traversal_in_the_output_path(tmp_path):
    py, yaml_f = _write_component(tmp_path)
    out = str(tmp_path / ".." / "escape.json")
    with pytest.raises(ValueError, match="must not contain"):
        save_bundle(str(py), str(yaml_f), output=out)


def test_import_writes_files(tmp_path):
    py, yaml_f = _write_component(tmp_path, name="src")
    bundle = save_bundle(str(py), str(yaml_f))
    src_json = tmp_path / "exp.json"
    src_json.write_text(json.dumps(bundle), encoding="utf-8")

    dest = tmp_path / "imported"
    out_dir = load_bundle(str(src_json), str(dest), name_override="dest")
    assert (out_dir / "dest.py").exists()
    assert (out_dir / "dest.yaml").exists()


def test_import_accepts_a_bundle_already_in_hand(tmp_path):
    """The dict leg of the type dispatch: no file has to exist at all."""
    py, yaml_f = _write_component(tmp_path, name="src")
    bundle = save_bundle(str(py), str(yaml_f))

    out_dir = load_bundle(bundle, str(tmp_path / "direct"))
    assert (out_dir / "src.py").exists()
    assert (out_dir / "src.yaml").exists()


def test_import_accepts_a_path_object(tmp_path):
    py, yaml_f = _write_component(tmp_path, name="src")
    src_json = tmp_path / "exp.json"
    src_json.write_text(json.dumps(save_bundle(str(py), str(yaml_f))), encoding="utf-8")

    out_dir = load_bundle(src_json, str(tmp_path / "pathobj"))
    assert (out_dir / "src.py").exists()


def test_import_rejects_a_source_that_is_neither_path_nor_bundle():
    with pytest.raises(TypeError, match="must be a path"):
        load_bundle(42)


def test_import_missing_source(tmp_path):
    missing = str(tmp_path / "absent.json")
    with pytest.raises(FileNotFoundError):
        load_bundle(missing)


def test_import_rejects_a_non_component_bundle(tmp_path):
    src = tmp_path / "bad.json"
    src.write_text(json.dumps({"export_type": "orchestration"}), encoding="utf-8")
    src_path = str(src)
    with pytest.raises(ValueError, match="export_type != 'component'"):
        load_bundle(src_path)


def test_import_rejects_a_bundle_with_no_script(tmp_path):
    py, yaml_f = _write_component(tmp_path, name="src")
    bundle = save_bundle(str(py), str(yaml_f))
    bundle["component"]["script"] = ""
    src = tmp_path / "noscript.json"
    src.write_text(json.dumps(bundle), encoding="utf-8")
    src_path = str(src)
    with pytest.raises(ValueError, match="no script"):
        load_bundle(src_path)


# ---------------------------------------------------------------------------
# _create scaffolding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ctype", ["AGENT", "TOOL", "START", "END", "FSM", "POLICY", "ADAPTER"])
def test_create_scaffolds_each_type(tmp_path, ctype):
    out = create("widget", ctype, str(tmp_path / ctype.lower()))
    assert (out / "widget.py").exists()
    assert (out / "widget.yaml").exists()


def test_create_invalid_type_raises(tmp_path):
    out_path = str(tmp_path)
    with pytest.raises(ValueError, match="Invalid component type"):
        create("x", "BOGUS", out_path)


def test_create_applies_description_and_tags(tmp_path):
    import yaml as _yaml

    out = create("desc", "AGENT", str(tmp_path / "d"), description="hello", tags=["x", "y"])
    manifest = _yaml.safe_load((out / "desc.yaml").read_text(encoding="utf-8"))
    assert manifest["description"] == "hello"
    assert manifest["tags"] == ["x", "y"]
