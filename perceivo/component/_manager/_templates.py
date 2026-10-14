"""Provide built-in component templates: script and manifest YAML per type.

Templates carry two placeholders that are not interchangeable. ``{{name}}`` is
the display name and belongs in the manifest and in log strings; ``{{symbol}}``
is the Python function name and must be a valid identifier (see
:func:`symbol_for`).

``{{name}}`` appears inside a double-quoted Python string in the script and as
a YAML scalar in the manifest, and each needs its own escaping: a raw ``no``
would read back as ``False``. Use :func:`render_script` and
:func:`render_manifest`, not a bare ``str.replace``.

A port declares ``artifact_type_output`` only where the template's ``return``
names a literal ``ArtifactType``. ``start``, ``agent`` and ``tool`` always
return JSON artifacts, so they declare ``json``. ``end``, ``policy`` and
``adapter`` pass ``input.artifact`` through, so their output type is unknown
and is not declared. On an ``in`` port the field describes what the invoked
component returns to its caller, which is why ``tool`` declares one on
``input``.

``fsm`` passes its input through but must still declare the field, because an
``in`` port on a mainflow type (AGENT, FSM) is bidirectional and the
platform requires it there. Its value is ``optional(json)``.

Ports are declared under ``input_ports`` / ``output_ports`` / ``policy_ports``
/ ``state_ports`` / ``external_in_port`` / ``external_out_port``, each item
keyed ``port_name``. A top-level ``ports:`` list parses to no ports at all.
"""

from __future__ import annotations

import json
import keyword
import re

import yaml


def symbol_for(name: str) -> str:
    """Derive a valid Python identifier from a component's display name.

    Display names are often hyphenated (``rag-ingest``), which is not a valid
    function name. The display name is kept for the manifest; the scaffolded
    function name is derived here.

    Args:
        name: The component's display name.

    Returns:
        A valid Python identifier.
    """
    symbol = re.sub(r"\W+", "_", name).strip("_")
    if not symbol:
        return "component"
    if symbol[0].isdigit():
        symbol = f"_{symbol}"
    if keyword.iskeyword(symbol) or keyword.issoftkeyword(symbol):
        symbol = f"{symbol}_"
    return symbol


def _yaml_scalar(value: str) -> str:
    """Render ``value`` as a YAML scalar that parses back as the same string.

    Quotes only when it has to, so ordinary names stay unquoted. The test is a
    round-trip rather than a character blacklist, because YAML 1.1 resolves
    ``no``, ``off``, ``null`` and ``2`` to non-strings.

    Args:
        value: The string to render.

    Returns:
        The YAML scalar text.
    """
    try:
        if value == yaml.safe_load(value):
            return value
    except yaml.YAMLError:
        pass
    return "'" + value.replace("'", "''") + "'"


def render_script(template: str, name: str) -> str:
    """Render a component script. ``{{name}}`` lands inside a Python string."""
    return template.replace("{{symbol}}", symbol_for(name)).replace(
        "{{name}}", json.dumps(name, ensure_ascii=False)[1:-1]
    )


def render_manifest(template: str, name: str) -> str:
    """Render a component manifest. ``{{name}}`` lands in YAML value position."""
    return template.replace("{{name}}", _yaml_scalar(name))


_TYPE_SKELETONS: dict[str, dict[str, str]] = {
    "agent": {
        "script": '''\
import perceivo


@perceivo.agent
async def {{symbol}}(input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
    """Answer the incoming request."""
    perceivo.log.info("Agent {{name}} running")

    query = input.request_data.get("query", "")

    return perceivo.primitives.AgentOutput(
        artifact=perceivo.primitives.Artifact(
            type=perceivo.primitives.ArtifactType.JSON,
            data={"query": query},
        ),
        response_data={"answer": query, "result": query},
    )
''',
        "manifest": """\
extension_type: AGENT
name: {{name}}
description: Agent component
tags:
  - agent
input_ports:
  - port_name: input
    port_type: in
    artifact_type_input: json
    artifact_type_output: json
    request_data:
      query:
        type: string
        description: The request for the agent to answer.
output_ports:
  - port_name: output
    port_type: out
    artifact_type_input: json
    artifact_type_output: json
    response_data:
      answer:
        type: string
        description: The agent's answer.
      result:
        type: string
        description: The agent's answer, as the result handed downstream.
input_parameters: []
required_secrets: []
""",
    },
    "tool": {
        "script": '''\
import perceivo


@perceivo.tool
async def {{symbol}}(input: perceivo.primitives.ToolInput) -> perceivo.primitives.ToolOutput:
    """Perform a specific utility function."""
    perceivo.log.info("Tool {{name}} running")

    value = input.request_data.get("value", "")

    return perceivo.primitives.ToolOutput(
        artifact=perceivo.primitives.Artifact(
            type=perceivo.primitives.ArtifactType.JSON,
            data={"value": value},
        ),
        response_data={"result": value},
    )
''',
        "manifest": """\
extension_type: TOOL
name: {{name}}
description: Tool component
tags:
  - tool
input_ports:
  - port_name: input
    port_type: in
    artifact_type_input: json
    artifact_type_output: json
    request_data:
      value:
        type: string
        description: The value for the tool to act on.
    response_data:
      result:
        type: string
        description: The tool's result.
input_parameters: []
required_secrets: []
""",
    },
    "start": {
        "script": '''\
import perceivo


@perceivo.start
async def {{symbol}}(input: perceivo.primitives.StartInput) -> perceivo.primitives.StartOutput:
    """Entry point - receive the initial input and produce the first artifact."""
    perceivo.log.info("Start component running")

    query = input.request_data.get("query", "")

    return perceivo.primitives.StartOutput(
        artifact=perceivo.primitives.Artifact(
            type=perceivo.primitives.ArtifactType.JSON,
            data={"query": query},
        ),
        response_data={"query": query},
    )
''',
        "manifest": """\
extension_type: START
name: {{name}}
description: Start component
tags:
  - start
external_in_port:
  port_name: external_input
  port_type: external_in
  artifact_type_input: json
  artifact_type_output: json
  request_data:
    query:
      type: string
      description: The input that starts the orchestration.
output_ports:
  - port_name: output
    port_type: out
    artifact_type_input: json
    artifact_type_output: json
    response_data:
      query:
        type: string
        description: The input forwarded to the next component.
input_parameters: []
input_prompt: Provide the input data for this orchestration.
required_secrets: []
""",
    },
    "end": {
        "script": '''\
import perceivo


@perceivo.end
async def {{symbol}}(input: perceivo.primitives.EndInput) -> perceivo.primitives.EndOutput:
    """Exit point - format and return the final output."""
    perceivo.log.info("End component running")

    return perceivo.primitives.EndOutput(
        artifact=input.artifact,
        response_data={"result": input.response_data.get("result", "")},
    )
''',
        "manifest": """\
extension_type: END
name: {{name}}
description: End component
tags:
  - end
input_ports:
  - port_name: input
    port_type: in
    artifact_type_input: json
    request_data:
      result:
        type: string
        description: The result handed to the end component.
external_out_port:
  port_name: external_output
  port_type: external_out
  artifact_type_input: json
  response_data:
    result:
      type: string
      description: The final result of the orchestration.
input_parameters: []
required_secrets: []
""",
    },
    "fsm": {
        "script": '''\
import perceivo


@perceivo.fsm
async def {{symbol}}(input: perceivo.primitives.FsmInput) -> perceivo.primitives.FsmOutput:
    """State machine - invoke a state and republish its result."""
    perceivo.log.info("FSM {{name}} running")

    result = await perceivo.flow.invoke(
        "state_1",
        request_data=input.request_data,
        artifact=input.artifact,
    )

    return perceivo.primitives.FsmOutput(
        artifact=input.artifact,
        response_data={
            "visited_state": "state_1",
            "state_response": result,
        },
    )
''',
        "manifest": """\
extension_type: FSM
name: {{name}}
description: FSM component
tags:
  - fsm
input_ports:
  - port_name: input
    port_type: in
    artifact_type_input: json
    artifact_type_output: optional(json)
    response_data:
      visited_state:
        type: string
        description: The state port that was invoked.
      state_response:
        type: object
        description: The response_data returned by the invoked state.
policy_ports:
  - port_name: policy
    artifact_type_input: none
    artifact_type_output: none
state_ports:
  - port_name: state_1
    artifact_type_input: json
    artifact_type_output: json
  - port_name: state_2
    artifact_type_input: json
    artifact_type_output: json
input_parameters: []
required_secrets: []
""",
    },
    "policy": {
        "script": '''\
import perceivo


@perceivo.policy
async def {{symbol}}(input: perceivo.primitives.PolicyInput) -> perceivo.primitives.PolicyOutput:
    """Decide the next state transition."""
    perceivo.log.info("Policy {{name}} running")

    return perceivo.primitives.PolicyOutput(
        next_state="state_1",
        artifact=input.artifact,
        response_data={"next_state": "state_1"},
    )
''',
        "manifest": """\
extension_type: POLICY
name: {{name}}
description: Policy component
tags:
  - policy
input_ports:
  - port_name: input
    port_type: in
    artifact_type_input: json
    response_data:
      next_state:
        type: string
        description: The state port to route to next.
policy_states:
  - state_1
  - state_2
input_parameters: []
required_secrets: []
""",
    },
    "adapter": {
        "script": '''\
import perceivo


@perceivo.adapter
async def {{symbol}}(input: perceivo.primitives.AdapterInput) -> perceivo.primitives.AdapterOutput:
    """Transform data schema between components."""
    perceivo.log.info("Adapter {{name}} running")

    value = input.request_data.get("value", "")

    return perceivo.primitives.AdapterOutput(
        artifact=input.artifact,
        response_data={"value": value},
    )
''',
        "manifest": """\
extension_type: ADAPTER
name: {{name}}
description: Adapter component
tags:
  - adapter
input_ports:
  - port_name: input
    port_type: in
    artifact_type_input: json
    request_data:
      value:
        type: string
        description: The value to adapt.
    response_data:
      value:
        type: string
        description: The adapted value.
input_parameters: []
required_secrets: []
""",
    },
    "review": {
        "script": '''\
import perceivo


@perceivo.review
async def {{symbol}}(input: perceivo.primitives.ReviewInput) -> perceivo.primitives.ReviewOutput:
    """Present the incoming artifact for human review.

    A required REVIEW node pauses the run until a reviewer answers its survey,
    and the answers become the node's response data; an optional one does not
    wait. On the platform this function is not run. The local runner has no reviewer, so it runs this function
    and approves automatically; any response data returned here overrides the
    fixed local decision.
    """
    perceivo.log.info("Review {{name}} running")

    return perceivo.primitives.ReviewOutput(artifact=input.artifact)
''',
        "manifest": """\
extension_type: REVIEW
name: {{name}}
description: Review component
tags:
  - review
input_ports:
  - port_name: input
    port_type: in
    artifact_type_input: json
    request_data:
      survey_spec:
        type: array
        required: false
        description: Questions that replace the node's configured survey, each with type, label and name. An item missing any of these, or with an unknown type, is dropped; a non-empty list with no valid item leaves the review with no questions, and an empty list keeps the configured survey.
      review_artifacts:
        type: array
        required: false
        description: Items shown to the reviewer in place of the incoming artifact, each with content (cut to 10000 characters) and an optional type and label. An item without content is dropped.
      context:
        type: object
        required: false
        description: Context data stored on the review request.
      assignee_user_sub:
        type: string
        required: false
        description: User assigned the review when no workload assignment applies to this node. A workload's only assignment applies to every review node, even one it names for another node.
      assignee_group_id:
        type: string
        required: false
        description: Group UUID assigned the review when no workload assignment applies to this node. A workload's only assignment applies to every review node, even one it names for another node.
input_parameters: []
required_secrets: []
""",
    },
}
