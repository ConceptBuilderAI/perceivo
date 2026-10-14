"""Prebuilt-pattern components written into scaffolded orchestration projects."""

from __future__ import annotations

from pathlib import Path


def _write_prebuilt(
    project_dir: Path,
    node_name: str,
    prebuilt_name: str,
    component_type: str,
) -> None:
    """Write a prebuilt component's script and manifest into a project."""
    ctype = component_type.upper().strip()
    if ctype in ("START", "END"):
        # A terminal's external port must match its orchestration.yaml node, which
        # only the builder writes.
        raise ValueError(f"{ctype} is not a prebuilt component type")
    type_lower = ctype.lower()

    script = f'''\
"""
{node_name} — Uses prebuilt:{prebuilt_name} pattern.
Auto-generated from orchestration template.

@extension_type: {type_lower}
"""

ENTRY_CLASS = "{_to_class_name(node_name)}"

import perceivo
import json as _json
'''

    if ctype == "AGENT":
        script += f"""

class {_to_class_name(node_name)}(perceivo.primitives.Agent):
    async def run(self, input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
        rd = input.request_data or {{}}
        model = perceivo.params.get("model", "gpt-4o")
        system_prompt = perceivo.params.get("system_prompt", "You are a helpful assistant.")
        max_tokens = int(perceivo.params.get("max_tokens", 4096))

        user_message = ""
        if rd.get("prompt"):
            user_message = str(rd["prompt"])
        elif input.artifact and input.artifact.data:
            user_message = input.artifact.data if isinstance(input.artifact.data, str) else _json.dumps(input.artifact.data)
        elif rd:
            user_message = _json.dumps(rd)

        if not user_message:
            raise ValueError("No input provided.")

        # AI Foundry handles auth via the platform managed identity — no API keys.
        response = await perceivo.managed_service.ai.invoke(
            perceivo.types.AIService.CHAT, model=model,
            messages=[{{"role": "user", "content": user_message}}],
            system_prompt=system_prompt,
            max_tokens=max_tokens,
        )
        text = response["choices"][0]["message"]["content"]
        usage = response.get("usage") or {{}}
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)

        return perceivo.primitives.AgentOutput(
            artifact=perceivo.primitives.Artifact(
                type=perceivo.primitives.ArtifactType.JSON,
                data={{"response": text, "tokens_in": tokens_in, "tokens_out": tokens_out}},
                mime_type="application/json",
            ),
            response_data={{"response": text, "result": text}},
        )
"""
    elif ctype == "TOOL":
        script += f'''

class {_to_class_name(node_name)}(perceivo.primitives.Tool):
    async def run(self, input: perceivo.primitives.ToolInput) -> perceivo.primitives.ToolOutput:
        rd = input.request_data or {{}}
        perceivo.log.info("{node_name}: processing", {{"keys": list(rd.keys())}})

        result = rd
        if input.artifact and input.artifact.data:
            result = input.artifact.data if isinstance(input.artifact.data, dict) else {{"data": input.artifact.data}}

        return perceivo.primitives.ToolOutput(
            artifact=perceivo.primitives.Artifact(
                type=perceivo.primitives.ArtifactType.JSON,
                data=result,
                mime_type="application/json",
            ),
            response_data=result,
        )
'''
    else:
        script += f"""

class {_to_class_name(node_name)}(perceivo.primitives.Agent):
    async def run(self, input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
        rd = input.request_data or {{}}
        return perceivo.primitives.AgentOutput(
            artifact=perceivo.primitives.Artifact(
                type=perceivo.primitives.ArtifactType.JSON, data=rd, mime_type="application/json",
            ),
            response_data=rd,
        )
"""

    manifest = f"""\
extension_type: {type_lower}
name: {node_name}
description: "Auto-generated from template (prebuilt: {prebuilt_name})"
tags:
  - template
  - {prebuilt_name}
"""
    # ``optional(json)``: every body falls back to request_data when no artifact
    # arrives. ``artifact_type_output`` stays bare: the return is unconditional.
    if ctype in ("AGENT", "TOOL", "FSM", "POLICY", "ADAPTER"):
        manifest += """\
input_ports:
  - port_name: input
    port_type: in
    artifact_type_input: optional(json)
    artifact_type_output: json
"""
    if ctype in ("TOOL", "FSM", "POLICY", "ADAPTER"):
        # The key set echoes the caller's input and cannot be enumerated; an undeclared
        # computed response_data is rejected on import as unverifiable.
        manifest += """    response_data_contract: dynamic
    response_data_contract_reason: >-
      Echoes the inbound artifact's data (or the request_data when no artifact
      arrives), so the keys are those the caller sends.
"""
    if ctype == "AGENT":
        # What the body returns, declared: the mainflow contract check reads the
        # out port's response_data, and END requires ``result``.
        manifest += """output_ports:
  - port_name: output
    port_type: out
    artifact_type_output: json
    response_data:
      response:
        type: string
        description: The model's reply.
      result:
        type: string
        description: The model's reply, as the result handed downstream.
"""
    (project_dir / f"{node_name}.py").write_text(script, encoding="utf-8")
    (project_dir / f"{node_name}.yaml").write_text(manifest, encoding="utf-8")


def _to_class_name(name: str) -> str:
    """Convert a snake_case or kebab-case name to PascalCase."""
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_"))
