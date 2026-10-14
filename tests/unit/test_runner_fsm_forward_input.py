"""``forward_input_to_states``: an FSM's own input reaches the states it invokes.

The engine stores a forwarding FSM's input when the node starts and merges it
underneath the request of each single ``perceivo.flow.invoke`` the FSM makes, so
keys the FSM passes win. Its parallel invoke path does not merge, and neither
does an FSM without the flag. These tests run the whole graph through the
executor, so the store, the merge and the callback binding are all exercised.
"""

from __future__ import annotations

import textwrap

import pytest

from perceivo.runner import executor as executor_module
from perceivo.runner.executor import run_orchestration

_MANIFEST = "ports: []\ninput_parameters: []\nrequired_secrets: []\n"

_START = """
import perceivo
from perceivo.primitives import Artifact, ArtifactType, StartInput, StartOutput


@perceivo.start
async def start(i: StartInput) -> StartOutput:
    return StartOutput(
        artifact=Artifact(type=ArtifactType.TEXT, data="start"),
        response_data={"topic": "rates", "shared": "from-start"},
    )
"""

# Invokes its one state twice, single then parallel, and reports what the state
# received each time.
_FSM = """
import perceivo
from perceivo.primitives import FsmInput, FsmOutput


@perceivo.fsm
async def run(i: FsmInput) -> FsmOutput:
    single = await perceivo.flow.invoke("state_1", {"shared": "from-fsm", "own": 1})
    parallel = await perceivo.flow.invoke_parallel(["state_1"], {"own": 2})
    return FsmOutput(
        artifact=i.artifact,
        response_data={
            "single": single["response_data"]["received"],
            "parallel": parallel["state_1"]["response_data"]["received"],
        },
    )
"""

_STATE = """
import perceivo
from perceivo.primitives import ToolInput, ToolOutput


@perceivo.tool
async def run(i: ToolInput) -> ToolOutput:
    return ToolOutput(artifact=None, response_data={"received": dict(i.request_data)})
"""

# The same invoke from an AGENT, which carries the flag but is not an FSM.
_AGENT = """
import perceivo
from perceivo.primitives import AgentInput, AgentOutput


@perceivo.agent
async def run(i: AgentInput) -> AgentOutput:
    single = await perceivo.flow.invoke("state_1", {"shared": "from-agent", "own": 1})
    return AgentOutput(artifact=i.artifact, response_data={"single": single["response_data"]["received"]})
"""

_END = """
import perceivo
from perceivo.primitives import EndInput, EndOutput


@perceivo.end
async def end(i: EndInput) -> EndOutput:
    return EndOutput(artifact=i.artifact, response_data=i.response_data)
"""

_ORCH = """
name: Forward Input
nodes:
- id: n_start
  name: START
  component_type: START
  script: start.py
  manifest: m.yaml
- id: n_fsm
  name: Executor
  component_type: {fsm_kind}
  script: {fsm_script}
  manifest: m.yaml
{fsm_config}
- id: n_state
  name: State
  component_type: TOOL
  script: state.py
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
- id: p_fsm_in
  node_id: n_fsm
  name: input
  port_type: in
- id: p_fsm_state_1
  node_id: n_fsm
  name: state_1
  port_type: tool
- id: p_state_in
  node_id: n_state
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
  target_port_id: p_fsm_in
  kind: MAINFLOW
- source_port_id: p_fsm_in
  target_port_id: p_end_in
  kind: MAINFLOW
- source_port_id: p_fsm_state_1
  target_port_id: p_state_in
  kind: INVOKE
"""


# An FSM whose state is another forwarding FSM. The outer FSM reaches the inner
# one through state_1 and a plain state directly through state_2.
_OUTER = """
import perceivo
from perceivo.primitives import FsmInput, FsmOutput


@perceivo.fsm
async def run(i: FsmInput) -> FsmOutput:
    nested = await perceivo.flow.invoke("state_1", {"own": 1})
    direct = await perceivo.flow.invoke("state_2", {"after": 1})
    return FsmOutput(
        artifact=i.artifact,
        response_data={
            "nested": nested["response_data"]["received"],
            "direct": direct["response_data"]["received"],
        },
    )
"""

_INNER = """
import perceivo
from perceivo.primitives import FsmInput, FsmOutput


@perceivo.fsm
async def run(i: FsmInput) -> FsmOutput:
    reply = await perceivo.flow.invoke("state_1", {"inner": 1})
    return FsmOutput(artifact=i.artifact, response_data={"received": reply["response_data"]["received"]})
"""

_NESTED_ORCH = """
name: Forward Input Nested
nodes:
- id: n_start
  name: START
  component_type: START
  script: start.py
  manifest: m.yaml
- id: n_outer
  name: Outer
  component_type: FSM
  script: outer.py
  manifest: m.yaml
  config:
    forward_input_to_states: true
- id: n_inner
  name: Inner
  component_type: FSM
  script: inner.py
  manifest: m.yaml
  config:
    forward_input_to_states: true
- id: n_state_a
  name: State A
  component_type: TOOL
  script: state.py
  manifest: m.yaml
- id: n_state_b
  name: State B
  component_type: TOOL
  script: state.py
  manifest: m.yaml
- id: n_end
  name: END
  component_type: END
  script: end.py
  manifest: m.yaml
ports:
- {id: p_start_out, node_id: n_start, name: output, port_type: out}
- {id: p_start_ext, node_id: n_start, name: trigger, port_type: external_in}
- {id: p_outer_in, node_id: n_outer, name: input, port_type: in}
- {id: p_outer_1, node_id: n_outer, name: state_1, port_type: tool}
- {id: p_outer_2, node_id: n_outer, name: state_2, port_type: tool}
- {id: p_inner_in, node_id: n_inner, name: request, port_type: in}
- {id: p_inner_1, node_id: n_inner, name: state_1, port_type: tool}
- {id: p_state_a_in, node_id: n_state_a, name: request, port_type: in}
- {id: p_state_b_in, node_id: n_state_b, name: request, port_type: in}
- {id: p_end_in, node_id: n_end, name: input, port_type: in}
- {id: p_end_ext, node_id: n_end, name: result, port_type: external_out}
edges:
- {source_port_id: p_start_out, target_port_id: p_outer_in, kind: MAINFLOW}
- {source_port_id: p_outer_in, target_port_id: p_end_in, kind: MAINFLOW}
- {source_port_id: p_outer_1, target_port_id: p_inner_in, kind: INVOKE}
- {source_port_id: p_inner_1, target_port_id: p_state_a_in, kind: INVOKE}
- {source_port_id: p_outer_2, target_port_id: p_state_b_in, kind: INVOKE}
"""


async def _run(tmp_path, monkeypatch, fsm_config: str, fsm_kind="FSM", orch=None):
    monkeypatch.chdir(tmp_path)
    script = "fsm.py" if fsm_kind == "FSM" else "agent.py"
    files = {
        "orchestration.yaml": orch
        or _ORCH.format(fsm_config=fsm_config, fsm_kind=fsm_kind, fsm_script=script),
        "start.py": _START,
        "fsm.py": _FSM,
        "agent.py": _AGENT,
        "outer.py": _OUTER,
        "inner.py": _INNER,
        "state.py": _STATE,
        "end.py": _END,
        "m.yaml": _MANIFEST,
    }
    for name, body in files.items():
        (tmp_path / name).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    result = await run_orchestration(str(tmp_path))
    assert result.success is True, result.error
    return result.response_data


async def test_a_state_receives_the_fsm_input_under_its_own_request(tmp_path, monkeypatch):
    data = await _run(tmp_path, monkeypatch, "  config:\n    forward_input_to_states: true")
    # The FSM's own keys win over the forwarded input on a collision.
    assert data["single"] == {"topic": "rates", "shared": "from-fsm", "own": 1}


async def test_a_parallel_invoke_does_not_merge_the_fsm_input(tmp_path, monkeypatch):
    data = await _run(tmp_path, monkeypatch, "  config:\n    forward_input_to_states: true")
    assert data["parallel"] == {"own": 2}


@pytest.mark.parametrize(
    "fsm_config",
    ["", "  config:\n    forward_input_to_states: false"],
    ids=["absent", "false"],
)
async def test_without_the_flag_a_state_receives_only_its_request(
    tmp_path, monkeypatch, fsm_config
):
    data = await _run(tmp_path, monkeypatch, fsm_config)
    assert data["single"] == {"shared": "from-fsm", "own": 1}
    assert data["parallel"] == {"own": 2}


async def test_a_node_that_is_not_an_fsm_merges_nothing(tmp_path, monkeypatch):
    data = await _run(
        tmp_path, monkeypatch, "  config:\n    forward_input_to_states: true", fsm_kind="AGENT"
    )
    assert data["single"] == {"shared": "from-agent", "own": 1}


async def test_the_stored_input_is_dropped_when_the_fsm_node_finishes(tmp_path, monkeypatch):
    executors = []
    original = executor_module._OrchestrationExecutor.__init__

    def capture(self, *args, **kwargs):
        original(self, *args, **kwargs)
        executors.append(self)

    monkeypatch.setattr(executor_module._OrchestrationExecutor, "__init__", capture)
    data = await _run(tmp_path, monkeypatch, "  config:\n    forward_input_to_states: true")
    # The input was stored while the FSM ran, since its state saw it merged.
    assert data["single"]["topic"] == "rates"
    assert len(executors) == 1
    assert executors[0]._fsm_forward_data == {}


async def test_an_fsm_invoking_a_forwarding_fsm_keeps_each_input_to_its_own_node(
    tmp_path, monkeypatch
):
    data = await _run(tmp_path, monkeypatch, "", orch=_NESTED_ORCH)
    # The inner FSM's input is the outer input merged under the outer request,
    # and the inner FSM forwards that whole input to its own state.
    assert data["nested"] == {"topic": "rates", "shared": "from-start", "own": 1, "inner": 1}
    # Once the inner FSM finishes, the outer FSM still forwards its own input,
    # with nothing left over from the inner one.
    assert data["direct"] == {"topic": "rates", "shared": "from-start", "after": 1}
