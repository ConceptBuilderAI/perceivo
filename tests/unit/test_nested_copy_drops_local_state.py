"""A nested child's ``.env`` files and ``.perceivo/`` never reach the parent's tree.

Both copy paths are covered: ``Orchestration.save`` of a parent whose node is
embedded from a child directory (``write_embedded_children``), and a save that
carries an already-nested project over to a new destination (``_copy_project``).
"""

from __future__ import annotations

from pathlib import Path

from perceivo.orchestration import Orchestration, create, load
from perceivo.orchestration._builder._graph_edit import ignore_local_state

LOCAL_ONLY = "MARKER=local-only\n"
ENV, ENV_LOCAL, HOME_DIR = ".env", ".env.local", ".perceivo"
LOCAL_STATE = (ENV, ENV_LOCAL, HOME_DIR)
SCRIPT = "agent.py"
GRAPH = "orchestration.yaml"
UTF8 = "utf-8"


def _child(root: Path) -> Path:
    child = create("child", str(root))
    for name in (ENV, ENV_LOCAL):
        (child / name).write_text(LOCAL_ONLY, encoding=UTF8)
    runs = child / HOME_DIR / "runs"
    runs.mkdir(parents=True)
    (runs / "r1.json").write_text("{}", encoding=UTF8)
    return child


def _names(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


def _parent_with(child: Path) -> Orchestration:
    parent = Orchestration("parent")
    parent.add_orchestration_node("child", child)
    return parent


def test_ignore_hook_names_only_local_state():
    names = [*LOCAL_STATE, SCRIPT, "agent.yaml", ".envrc", "env"]
    assert ignore_local_state("x", names) == set(LOCAL_STATE)


def test_save_of_an_embedded_child_drops_its_local_state(tmp_path):
    child = _child(tmp_path / "src")
    out = tmp_path / "parent"
    _parent_with(child).save(str(out))
    copied = next(p for p in out.rglob(GRAPH) if p.parent != out).parent
    present = _names(copied)
    assert not present & set(LOCAL_STATE)
    assert {SCRIPT, "agent.yaml", GRAPH} <= present


def test_resave_of_a_nested_project_drops_its_local_state(tmp_path):
    child = _child(tmp_path / "src")
    first = tmp_path / "first"
    _parent_with(child).save(str(first))
    nested = next(p for p in first.rglob(GRAPH) if p.parent != first).parent
    # Plant local state inside the already-nested copy, then carry it elsewhere.
    for name in (ENV, ENV_LOCAL):
        (nested / name).write_text(LOCAL_ONLY, encoding=UTF8)
    (nested / HOME_DIR).mkdir()
    second = tmp_path / "second"
    load(str(first)).save(str(second))
    moved = second / nested.relative_to(first)
    present = _names(moved)
    assert not present & set(LOCAL_STATE)
    assert SCRIPT in present
