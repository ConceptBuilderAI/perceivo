"""SPEC-3 / SPEC-4: runner invoke-port conformance regression tests.

SPEC-3 — invoke targets must resolve against the CALLING node, not a global
port-name scan. Two nodes that expose an invoke port with the same name must each
reach their own target; a global scan misroutes (and, with shared FSM/policy port
names, recurses infinitely).

SPEC-4 — ``perceivo.flow.invoke`` must return the platform's dict contract
(``_format_invoke_result``): ``response_data`` spread at the top level, plus
``artifact`` (an Artifact object that also supports mapping access), ``artifacts``,
a nested ``response_data`` and ``log``.
"""

from __future__ import annotations

import textwrap

from perceivo.primitives import Artifact, ArtifactType
from perceivo.runner._invoke_result import format_invoke_result as _format_invoke_result
from perceivo.runner.executor import run_orchestration

_MANIFEST = "ports: []\ninput_parameters: []\nrequired_secrets: []\n"


def _write(ws, name, body):
    (ws / name).write_text(textwrap.dedent(body), encoding="utf-8")


# ---------------------------------------------------------------------------
# SPEC-4: _format_invoke_result shape (pure unit)
# ---------------------------------------------------------------------------


def test_format_invoke_result_matches_platform_shape():
    art = Artifact(type=ArtifactType.TEXT, data="hi")
    raw = {
        "artifact": art,
        "response_data": {"k": "v"},
        "artifacts": {"a": art},
        "conversation_log": "log-line",
    }
    shaped = _format_invoke_result(raw)
    # response_data spread at the top level
    assert shaped["k"] == "v"
    # nested response_data preserved
    assert shaped["response_data"] == {"k": "v"}
    # artifact is the Artifact OBJECT (attribute access) ...
    assert shaped["artifact"].data == "hi"
    # ... and also mapping access, for dict-style scripts (SPEC-4 / Artifact API)
    assert shaped["artifact"].get("data") == "hi"
    assert shaped["artifact"]["data"] == "hi"
    assert shaped["artifacts"]["a"].data == "hi"
    # conversation_log surfaced as the platform's `log` key
    assert shaped["log"] == "log-line"


def test_format_invoke_result_empty():
    assert _format_invoke_result(None) == {}
    assert _format_invoke_result({}) == {}


# ---------------------------------------------------------------------------
# SPEC-3: shared invoke-port name is scoped to the calling node
# ---------------------------------------------------------------------------

# Two agents A and B each expose a `tool` port literally named "shared".
# A.shared -> toolA (appends "A"); B.shared -> toolB (appends "B").
# Mainflow: START -> A -> B -> END, each agent invoking "shared" once.
# Correct scoping yields "A,B"; a global port-name scan misroutes B to toolA
# (yielding "A,A") or recurses.

_START = """
import perceivo
@perceivo.start
async def start(i: perceivo.primitives.StartInput) -> perceivo.primitives.StartOutput:
    return perceivo.primitives.StartOutput(
        artifact=perceivo.primitives.Artifact(type=perceivo.primitives.ArtifactType.TEXT, data=""),
        response_data={},
    )
"""

_AGENT = """
import perceivo
@perceivo.agent
async def run(i: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
    result = await perceivo.flow.invoke("shared", {}, i.artifact)
    art = result.get("artifact")
    data = art.get("data") if art else ""
    return perceivo.primitives.AgentOutput(
        artifact=perceivo.primitives.Artifact(type=perceivo.primitives.ArtifactType.TEXT, data=data),
        response_data=result.get("response_data", {}),
    )
"""


def _tool(marker: str) -> str:
    return f"""
import perceivo
@perceivo.tool
async def run(i: perceivo.primitives.ToolInput) -> perceivo.primitives.ToolOutput:
    prev = i.artifact.data if i.artifact and i.artifact.data else ""
    data = (prev + ",{marker}") if prev else "{marker}"
    return perceivo.primitives.ToolOutput(
        artifact=perceivo.primitives.Artifact(type=perceivo.primitives.ArtifactType.TEXT, data=data),
        response_data={{"tool": "{marker}"}},
    )
"""


_END = """
import perceivo
@perceivo.end
async def end(i: perceivo.primitives.EndInput) -> perceivo.primitives.EndOutput:
    return perceivo.primitives.EndOutput(artifact=i.artifact, response_data=i.response_data)
"""

_SHARED_PORT_ORCH = """
name: Shared Port Scoping
nodes:
- id: n_start
  name: START
  component_type: START
  script: start.py
  manifest: m.yaml
- id: n_a
  name: Agent A
  component_type: AGENT
  script: agent.py
  manifest: m.yaml
- id: n_b
  name: Agent B
  component_type: AGENT
  script: agent.py
  manifest: m.yaml
- id: n_tool_a
  name: Tool A
  component_type: TOOL
  script: tool_a.py
  manifest: m.yaml
- id: n_tool_b
  name: Tool B
  component_type: TOOL
  script: tool_b.py
  manifest: m.yaml
- id: n_end
  name: END
  component_type: END
  script: end.py
  manifest: m.yaml
ports:
- id: p_start_out
  node_id: n_start
  name: output
  port_type: out
- id: p_start_ext
  node_id: n_start
  name: trigger
  port_type: external_in
- id: p_a_in
  node_id: n_a
  name: input
  port_type: in
- id: p_a_shared
  node_id: n_a
  name: shared
  port_type: tool
- id: p_b_in
  node_id: n_b
  name: input
  port_type: in
- id: p_b_shared
  node_id: n_b
  name: shared
  port_type: tool
- id: p_tool_a_in
  node_id: n_tool_a
  name: request
  port_type: in
- id: p_tool_b_in
  node_id: n_tool_b
  name: request
  port_type: in
- id: p_end_in
  node_id: n_end
  name: input
  port_type: in
- id: p_end_ext
  node_id: n_end
  name: result
  port_type: external_out
edges:
- source_port_id: p_start_out
  target_port_id: p_a_in
  kind: MAINFLOW
- source_port_id: p_a_in
  target_port_id: p_b_in
  kind: MAINFLOW
- source_port_id: p_b_in
  target_port_id: p_end_in
  kind: MAINFLOW
- source_port_id: p_a_shared
  target_port_id: p_tool_a_in
  kind: INVOKE
- source_port_id: p_b_shared
  target_port_id: p_tool_b_in
  kind: INVOKE
"""


async def test_shared_invoke_port_name_scoped_to_calling_node(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "orchestration.yaml", _SHARED_PORT_ORCH)
    _write(tmp_path, "start.py", _START)
    _write(tmp_path, "agent.py", _AGENT)
    _write(tmp_path, "tool_a.py", _tool("A"))
    _write(tmp_path, "tool_b.py", _tool("B"))
    _write(tmp_path, "end.py", _END)
    _write(tmp_path, "m.yaml", _MANIFEST)

    result = await run_orchestration(str(tmp_path))
    assert result.success is True, result.error
    # Agent A -> tool A ("A"); Agent B -> tool B (appends "B") => "A,B".
    # A global port-name scan would send B's "shared" invoke to tool A ("A,A").
    assert result.artifact.data == "A,B", result.artifact.data


async def test_shared_invoke_port_response_data_shape(tmp_path, monkeypatch):
    """The agent reads result['response_data']['tool'] and result['artifact'].get()
    — proving the SPEC-4 return shape is delivered end-to-end."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "orchestration.yaml", _SHARED_PORT_ORCH)
    _write(tmp_path, "start.py", _START)
    _write(tmp_path, "agent.py", _AGENT)
    _write(tmp_path, "tool_a.py", _tool("A"))
    _write(tmp_path, "tool_b.py", _tool("B"))
    _write(tmp_path, "end.py", _END)
    _write(tmp_path, "m.yaml", _MANIFEST)

    result = await run_orchestration(str(tmp_path))
    assert result.success is True, result.error
    # END echoes B's invoke response_data, whose tool marker is "B".
    assert result.response_data.get("tool") == "B", result.response_data


# ---------------------------------------------------------------------------
# Unconnected-port failure is the platform's failure.
# ---------------------------------------------------------------------------
#
# The platform raises
#
#     ValueError(f"No connection found for port {port_name} on node {node_id}")
#
# from ``InvokePortMixin._execute_single_target``. Components enumerate their
# state slots by catching exactly that -- by type AND by message substring --
# and treat anything else as a fail-closed error. The three seeded
# system templates users clone (Sequential Executor, Parallel Executor,
# Conditional Router) are all written that way, so a local runner that raises a
# different exception cannot run any of them.


def test_unconnected_port_raises_the_platform_exception_verbatim():
    """Type and text, not merely 'an error' -- both are what components match on."""
    from perceivo.runner._ports import NO_CONNECTION_PREFIX, no_connection_error

    exc = no_connection_error("state_2", "node_7")
    # Exact type: a subclass would still miss `except ValueError` on older
    # Pythons only for non-ValueError bases, but a *wider* type here would let a
    # future refactor to RuntimeError pass, which is the regression itself.
    assert type(exc) is ValueError, type(exc)
    assert str(exc) == "No connection found for port state_2 on node node_7", str(exc)
    # The literal the seeded components substring-match against.
    assert "No connection found" in str(exc)
    assert NO_CONNECTION_PREFIX == "No connection found for port"


async def test_invoke_of_unconnected_port_raises_platform_exception(tmp_path, monkeypatch):
    """End-to-end through the executor, not just the helper."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "orchestration.yaml", _STATE_ENUMERATION_ORCH)
    _write(tmp_path, "start.py", _START)
    _write(tmp_path, "layer.py", _RAW_INVOKE_LAYER)
    _write(tmp_path, "tool_a.py", _tool("A"))
    _write(tmp_path, "end.py", _END)
    _write(tmp_path, "m.yaml", _MANIFEST)

    result = await run_orchestration(str(tmp_path))
    assert result.success is False
    # The node-level wrap ("Component <name> failed: ...") is the executor's,
    # and matches what the engine records; the *inner* text is the contract.
    assert result.error == (
        "Component Sequential-style Layer failed: "
        "No connection found for port state_2 on node n_layer"
    ), result.error


# A component written against the PLATFORM: it walks state_1, state_2, ... and
# stops when the engine tells it the next slot is unwired. The `except Exception`
# arm is the fail-closed branch -- reaching it is the defect.
_PLATFORM_STYLE_LAYER = """
import perceivo

@perceivo.agent
async def run(i: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
    visited = []
    for n in range(1, 5):
        port_name = f"state_{n}"
        try:
            await perceivo.flow.invoke(port_name, {}, i.artifact)
            visited.append(port_name)
        except ValueError as e:
            if "No connection found for port" in str(e):
                visited.append("enumerated")
                break
            raise
        except Exception as e:
            # Fail closed: a state slot that raises fails the run.
            raise RuntimeError(
                f"FAIL_CLOSED took the wrong branch: {type(e).__name__}: {e}"
            ) from e
    return perceivo.primitives.AgentOutput(
        artifact=perceivo.primitives.Artifact(
            type=perceivo.primitives.ArtifactType.TEXT, data=",".join(visited)
        ),
        response_data={"visited": visited},
    )
"""

# Same shape, but with no catch at all: proves the exception that escapes the
# executor is the platform's, independently of the component's handling.
_RAW_INVOKE_LAYER = """
import perceivo

@perceivo.agent
async def run(i: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
    await perceivo.flow.invoke("state_1", {}, i.artifact)
    await perceivo.flow.invoke("state_2", {}, i.artifact)
    return perceivo.primitives.AgentOutput(artifact=i.artifact, response_data={})
"""

# START -> LAYER -> END. The layer declares state_1 (wired to a tool) and
# state_2 (declared, deliberately UNWIRED). state_3+ are not declared at all.
_STATE_ENUMERATION_ORCH = """
name: State Enumeration
nodes:
- id: n_start
  name: START
  component_type: START
  script: start.py
  manifest: m.yaml
- id: n_layer
  name: Sequential-style Layer
  component_type: AGENT
  script: layer.py
  manifest: m.yaml
- id: n_tool_a
  name: Tool A
  component_type: TOOL
  script: tool_a.py
  manifest: m.yaml
- id: n_end
  name: END
  component_type: END
  script: end.py
  manifest: m.yaml
ports:
- id: p_start_out
  node_id: n_start
  name: output
  port_type: out
- id: p_start_ext
  node_id: n_start
  name: trigger
  port_type: external_in
- id: p_layer_in
  node_id: n_layer
  name: input
  port_type: in
- id: p_layer_state_1
  node_id: n_layer
  name: state_1
  port_type: tool
- id: p_layer_state_2
  node_id: n_layer
  name: state_2
  port_type: tool
- id: p_tool_a_in
  node_id: n_tool_a
  name: request
  port_type: in
- id: p_end_in
  node_id: n_end
  name: input
  port_type: in
- id: p_end_ext
  node_id: n_end
  name: result
  port_type: external_out
edges:
- source_port_id: p_start_out
  target_port_id: p_layer_in
  kind: MAINFLOW
- source_port_id: p_layer_in
  target_port_id: p_end_in
  kind: MAINFLOW
- source_port_id: p_layer_state_1
  target_port_id: p_tool_a_in
  kind: INVOKE
"""


async def test_platform_style_state_enumeration_takes_the_intended_branch(tmp_path, monkeypatch):
    """The enumeration the three system templates depend on.

    A runner that raised ``RuntimeError("No component connected to port ...")``
    would slip past the component's ``except ValueError``, so the component
    would fall into its fail-closed arm and the run would die. The enumeration
    ends the way it does on the platform.
    """
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "orchestration.yaml", _STATE_ENUMERATION_ORCH)
    _write(tmp_path, "start.py", _START)
    _write(tmp_path, "layer.py", _PLATFORM_STYLE_LAYER)
    _write(tmp_path, "tool_a.py", _tool("A"))
    _write(tmp_path, "end.py", _END)
    _write(tmp_path, "m.yaml", _MANIFEST)

    result = await run_orchestration(str(tmp_path))
    assert result.success is True, result.error
    # Positive control on the discrimination: had the fail-closed arm run, the
    # error would name it. Assert the *reason* the run passed, not just success.
    assert result.artifact.data == "state_1,enumerated", result.artifact.data
    assert result.error is None, result.error
