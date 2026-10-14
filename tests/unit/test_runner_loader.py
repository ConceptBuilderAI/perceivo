"""T6: Runner loader (load_component_class, load_manifest)."""

from __future__ import annotations

import pytest

from perceivo.runner.loader import load_component_class, load_manifest


def test_load_missing_script_raises(tmp_workspace):
    missing = str(tmp_workspace / "does_not_exist.py")
    with pytest.raises(FileNotFoundError, match="Component script not found"):
        load_component_class(missing)


def test_load_component_string_entry_class(tmp_workspace):
    script = tmp_workspace / "comp.py"
    script.write_text(
        "ENTRY_CLASS = 'MyTool'\nclass MyTool:\n    def __init__(self): self.x = 7\n",
        encoding="utf-8",
    )
    instance = load_component_class(str(script))
    assert instance.x == 7


def test_load_component_class_entry_class(tmp_workspace):
    script = tmp_workspace / "comp.py"
    script.write_text(
        'class MyTool:\n    def __init__(self): self.y = 9\nENTRY_CLASS = "MyTool"\n',
        encoding="utf-8",
    )
    instance = load_component_class(str(script))
    assert instance.y == 9


def test_load_entry_class_bare_class_is_rejected(tmp_workspace):
    # The orchestration sandbox resolves ENTRY_CLASS with getattr(module, name),
    # so a class object raises TypeError there. Accepting it here would make the
    # local run a gate that passes what production cannot start.
    script = tmp_workspace / "comp.py"
    script.write_text(
        "class MyTool:\n    def __init__(self): self.y = 9\nENTRY_CLASS = MyTool\n",
        encoding="utf-8",
    )
    script_path = str(script)
    with pytest.raises(RuntimeError, match="must be the class name as a string"):
        load_component_class(script_path)


def test_load_entry_class_instance_is_rejected(tmp_workspace):
    script = tmp_workspace / "comp.py"
    script.write_text(
        "class MyTool:\n    def __init__(self): self.y = 9\nENTRY_CLASS = MyTool()\n",
        encoding="utf-8",
    )
    script_path = str(script)
    with pytest.raises(RuntimeError, match="must be the class name as a string"):
        load_component_class(script_path)


def test_load_missing_entry_class_raises(tmp_workspace):
    script = tmp_workspace / "comp.py"
    script.write_text("class X: pass\n", encoding="utf-8")
    script_path = str(script)
    with pytest.raises(RuntimeError, match="No ENTRY_CLASS"):
        load_component_class(script_path)


def test_load_entry_class_string_not_defined_raises(tmp_workspace):
    script = tmp_workspace / "comp.py"
    script.write_text("ENTRY_CLASS = 'Ghost'\n", encoding="utf-8")
    script_path = str(script)
    with pytest.raises(RuntimeError, match="not defined"):
        load_component_class(script_path)


def test_load_instantiation_failure_propagated(tmp_workspace):
    script = tmp_workspace / "comp.py"
    script.write_text(
        "ENTRY_CLASS = 'Bad'\nclass Bad:\n    def __init__(self): raise ValueError('boom')\n",
        encoding="utf-8",
    )
    script_path = str(script)
    with pytest.raises(RuntimeError, match="Failed to instantiate"):
        load_component_class(script_path)


def test_load_module_syntax_error_propagated(tmp_workspace):
    script = tmp_workspace / "comp.py"
    script.write_text("def f(:\n  pass\n", encoding="utf-8")
    script_path = str(script)
    with pytest.raises(RuntimeError, match="Error loading component"):
        load_component_class(script_path)


def test_load_manifest_returns_parsed_dict(tmp_workspace):
    m = tmp_workspace / "m.yaml"
    m.write_text(
        "name: foo\ntype: AGENT\nports:\n  - key: in\n    port_type: in\n", encoding="utf-8"
    )
    parsed = load_manifest(str(m))
    assert parsed["name"] == "foo"
    assert parsed["ports"][0]["key"] == "in"


def test_load_manifest_missing_file_returns_empty_dict(tmp_workspace):
    assert load_manifest(str(tmp_workspace / "nope.yaml")) == {}
