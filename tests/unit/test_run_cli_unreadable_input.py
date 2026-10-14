"""``perceivo run`` refuses a file it cannot read in one ``Error:`` line, as export does."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli
from perceivo.validator import validate_orchestration
from tests.unit.test_runner_nested_modes import (
    _END,
    _START,
    _edge,
    _leaf,
    _node,
    _parent,
    _ports,
    _write,
)
from tests.unit.test_validator_unreadable_files import UNREADABLE, _not_utf8

_AGENT = """class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(
            artifact=Artifact(type=ArtifactType.TEXT, data="hi"), response_data={"ok": True}
        )
ENTRY_CLASS = "AgentC"
"""

# A declared encoding lets Python import the script; export still refuses it.
_LATIN1_AGENT = b"# -*- coding: latin-1 -*-\n# caf\xe9\n" + _AGENT.encode()


def _refused(args: list[str], reason: str) -> None:
    result = CliRunner().invoke(perceivo_cli, args)
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert result.stderr == f"Error: {reason}\n"
    assert result.stdout == ""


def test_orchestration_yaml_not_utf8(tmp_path: Path) -> None:
    (tmp_path / "orchestration.yaml").write_bytes(b"name: caf\xe9\nnodes: []\n")
    _refused(
        ["run", "orchestration", str(tmp_path)], "orchestration.yaml is not UTF-8 text (byte 9)"
    )


def test_orchestration_yaml_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "orchestration.yaml").mkdir()
    _refused(
        ["run", "orchestration", str(tmp_path)], "orchestration.yaml is a directory, not a file"
    )


@pytest.mark.parametrize("type_args", [["--type", "AGENT"], []], ids=["type", "no-type"])
def test_component_script_is_a_directory(tmp_path: Path, type_args: list[str]) -> None:
    script = tmp_path / "agent.py"
    script.mkdir()
    _refused(["run", "component", str(script), *type_args], f"{script} is a directory, not a file")


@pytest.mark.parametrize("declared", ["option", "manifest"])
def test_component_script_not_utf8_is_refused_as_export_refuses_it(
    tmp_path: Path, declared: str
) -> None:
    script = tmp_path / "agent.py"
    script.write_bytes(_LATIN1_AGENT)
    reason = f"{script} is not UTF-8 text (byte 31)"
    if declared == "option":
        type_args = ["--type", "AGENT"]
    else:
        (tmp_path / "agent.yaml").write_text("name: agent\nextension_type: AGENT\n", "utf-8")
        type_args = []
    _refused(["run", "component", str(script), *type_args], reason)
    exported = CliRunner().invoke(perceivo_cli, ["export", str(script), "-o", str(tmp_path / "b")])
    assert exported.exit_code == 1
    assert exported.stderr == f"Error: {reason}\n"


def test_a_missing_script_is_still_the_runner_report(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        perceivo_cli, ["run", "component", str(tmp_path / "gone.py"), "--type", "AGENT"]
    )
    assert result.exit_code == 1
    assert "RESULT: FAIL" in result.stdout
    assert "Component script not found" in result.stdout


def test_a_readable_component_runs_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent.py").write_text(_AGENT, encoding="utf-8")
    result = CliRunner().invoke(perceivo_cli, ["run", "component", "agent.py", "--type", "AGENT"])
    assert result.exit_code == 0, result.output
    assert result.stdout == (
        "RESULT: PASS  (0 mock call(s) served)\n"
        "\n"
        "Artifact type: ArtifactType.TEXT\n"
        "Artifact data: hi\n"
        "\n"
        "Response data:\n"
        '{\n  "ok": true\n}\n'
    )


def _readable_agent(tmp_path: Path) -> Path:
    script = tmp_path / "agent.py"
    script.write_text(_AGENT, encoding="utf-8")
    return script


@pytest.mark.parametrize("make", UNREADABLE)
def test_a_manifest_beside_the_script_export_cannot_read_is_refused_in_its_words(
    tmp_path: Path, make: Callable[[Path], str]
) -> None:
    script = _readable_agent(tmp_path)
    manifest = tmp_path / "agent.yaml"
    manifest.write_text("name: agent\nextension_type: AGENT\n", "utf-8")
    reason = make(manifest).replace(manifest.name, str(manifest), 1)
    _refused(["run", "component", str(script)], reason)
    exported = CliRunner().invoke(perceivo_cli, ["export", str(script), "-o", str(tmp_path / "b")])
    assert exported.exit_code == 1
    assert exported.stderr == f"Error: {reason}\n"


@pytest.mark.parametrize("make", UNREADABLE)
def test_a_named_manifest_that_cannot_be_read_is_refused(
    tmp_path: Path, make: Callable[[Path], str]
) -> None:
    script = _readable_agent(tmp_path)
    manifest = tmp_path / "other.yaml"
    manifest.write_text("name: agent\nextension_type: AGENT\n", "utf-8")
    reason = make(manifest).replace(manifest.name, str(manifest), 1)
    _refused(["run", "component", str(script), "--manifest", str(manifest)], reason)


def test_a_named_manifest_that_does_not_exist_is_one_error_line(tmp_path: Path) -> None:
    script = _readable_agent(tmp_path)
    gone = tmp_path / "gone.yaml"
    _refused(
        ["run", "component", str(script), "--manifest", str(gone)],
        f"{gone}: No such file or directory",
    )


def _validate_message(project: Path, check_id: str) -> str:
    report = validate_orchestration(str(project))
    [message] = [c.message for c in report.errors if c.id == check_id]
    return message


def _run_orchestration(project: Path) -> list[str]:
    return ["run", "orchestration", str(project), "--input", '{"q": "hi"}']


# A child is refused when the run reaches it, so the parent must run up to it.
@pytest.mark.parametrize("mode", ["subprocess", "inline"])
@pytest.mark.parametrize("make", UNREADABLE)
def test_a_nested_orchestration_yaml_the_run_reaches_is_refused_as_validate_names_it(
    tmp_path: Path,
    make: Callable[[Path], str],
    mode: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    parent = _parent(tmp_path / "parent", mode)
    make(parent / "nested" / "two" / "orchestration.yaml")
    message = _validate_message(parent, "n2/ORCH-LOAD-001")
    with caplog.at_level(logging.DEBUG):
        _refused(_run_orchestration(parent), message)
    assert not [r for r in caplog.records if r.exc_info or "Traceback" in r.getMessage()]


@pytest.mark.parametrize("mode", ["subprocess", "inline"])
@pytest.mark.parametrize("make", UNREADABLE)
def test_a_grandchild_orchestration_yaml_the_run_reaches_is_refused_as_validate_names_it(
    tmp_path: Path,
    make: Callable[[Path], str],
    mode: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    parent = _parent(tmp_path / "parent", mode)
    middle = _parent(parent / "nested" / "two", mode)
    make(middle / "nested" / "two" / "orchestration.yaml")
    message = _validate_message(parent, "n2/n2/ORCH-LOAD-001")
    with caplog.at_level(logging.DEBUG):
        _refused(_run_orchestration(parent), message)
    assert not [r for r in caplog.records if r.exc_info or "Traceback" in r.getMessage()]


_INVOKING_AGENT = """
class C:
    async def run(self, inp):
        import perceivo
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        data = dict(inp.request_data or {})
        if data.get("go"):
            await perceivo.flow.invoke("maybe", {}, inp.artifact)
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="a"),
                           response_data=data)
ENTRY_CLASS = "C"
"""


_INVOKING_REVIEW = """
class C:
    async def review(self, inp):
        import perceivo
        await perceivo.flow.invoke("maybe", {}, inp.artifact)
ENTRY_CLASS = "C"
"""

_INVOKING_SETUP = """
class C:
    async def setup(self):
        import perceivo
        await perceivo.flow.invoke("maybe", {}, None)

    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="a"),
                           response_data=dict(inp.request_data or {}))
ENTRY_CLASS = "C"
"""


def _invoking_parent(
    root: Path, mode: str, ctype: str = "AGENT", script: str = _INVOKING_AGENT
) -> Path:
    """START -> a -> END, where a invokes the nested node cond through its port."""
    _leaf(root / "nested" / "c", "c")
    graph = {
        "nodes": [
            _node("s", "START"),
            _node("a", ctype),
            _node("cond", "ORCHESTRATION", project="nested/c", config={"execution_mode": mode}),
            _node("e", "END"),
        ],
        "ports": [
            *_ports("s", "a", "cond", "e"),
            {"id": "a_maybe", "node_id": "a", "name": "maybe", "port_type": "tool"},
        ],
        "edges": [
            _edge("s", "a"),
            _edge("a", "e"),
            {"source_port_id": "a_maybe", "target_port_id": "cond_in", "kind": "INVOKE"},
        ],
    }
    return _write(root, graph, {"s": _START, "a": script, "e": _END})


@pytest.mark.parametrize("mode", ["subprocess", "inline"])
@pytest.mark.parametrize("make", UNREADABLE)
def test_a_nested_orchestration_yaml_reached_through_a_port_is_refused_as_validate_names_it(
    tmp_path: Path,
    make: Callable[[Path], str],
    mode: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    parent = _invoking_parent(tmp_path / "parent", mode)
    make(parent / "nested" / "c" / "orchestration.yaml")
    message = _validate_message(parent, "cond/ORCH-LOAD-001")
    args = ["run", "orchestration", str(parent), "--input", '{"q": "hi", "go": 1}']
    with caplog.at_level(logging.DEBUG):
        _refused(args, message)
    assert not [r for r in caplog.records if r.exc_info or "Traceback" in r.getMessage()]


# Both entries otherwise swallow what they raise, a REVIEW to auto-approve locally.
@pytest.mark.parametrize("mode", ["subprocess", "inline"])
@pytest.mark.parametrize(
    ("ctype", "script"),
    [("REVIEW", _INVOKING_REVIEW), ("AGENT", _INVOKING_SETUP)],
    ids=["review", "setup"],
)
def test_a_child_a_review_or_setup_reaches_through_a_port_is_refused_as_validate_names_it(
    tmp_path: Path,
    ctype: str,
    script: str,
    mode: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    parent = _invoking_parent(tmp_path / "parent", mode, ctype, script)
    _not_utf8(parent / "nested" / "c" / "orchestration.yaml")
    message = _validate_message(parent, "cond/ORCH-LOAD-001")
    with caplog.at_level(logging.DEBUG):
        _refused(_run_orchestration(parent), message)
    assert not [r for r in caplog.records if r.exc_info or "Traceback" in r.getMessage()]


@pytest.mark.parametrize("mode", ["subprocess", "inline"])
def test_an_unreadable_child_behind_a_port_the_run_does_not_invoke_does_not_affect_it(
    tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    parent = _invoking_parent(tmp_path / "parent", mode)
    _not_utf8(parent / "nested" / "c" / "orchestration.yaml")
    result = CliRunner().invoke(perceivo_cli, _run_orchestration(parent))
    assert result.exit_code == 0, result.output
    assert "RESULT: PASS" in result.stdout, result.stdout


def _add_node(project: Path, node: dict, *, before: str | None = None) -> None:
    path = project / "orchestration.yaml"
    graph = yaml.safe_load(path.read_text(encoding="utf-8"))
    nodes = graph["nodes"]
    at = next(i for i, n in enumerate(nodes) if n["id"] == before) if before else len(nodes)
    nodes.insert(at, node)
    path.write_text(yaml.safe_dump(graph), encoding="utf-8")


@pytest.mark.parametrize("layout", ["unreached", "duplicate-id"])
def test_an_unreadable_child_the_run_never_loads_does_not_affect_it(
    tmp_path: Path, layout: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    parent = _parent(tmp_path / "parent", "subprocess")
    _not_utf8(_leaf(parent / "nested" / "bad", "bad") / "orchestration.yaml")
    if layout == "unreached":
        _add_node(parent, _node("spare", "ORCHESTRATION", project="nested/bad"))
    else:
        # The compiler keeps the last node with an id, so this one is never run.
        _add_node(parent, _node("n2", "ORCHESTRATION", project="nested/bad"), before="n2")
    result = CliRunner().invoke(perceivo_cli, _run_orchestration(parent))
    assert result.exit_code == 0, result.output
    assert "RESULT: PASS" in result.stdout, result.stdout
