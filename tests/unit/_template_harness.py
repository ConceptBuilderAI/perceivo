"""Harness: fill a slot template's open FSM state ports, on a copy.

The platform library's executor templates (``conditional-router``,
``parallel-executor``, ``sequential-executor``) are *slot* templates: their FSM
declares state ports in its manifest (``state_ports``) and ships them unwired,
because the user supplies what each slot does. The materialised graph carries
no port or edge for them, so a run reaches the FSM's first ``flow.invoke`` and
fails with ``No connection found for port ...`` -- exactly what the engine does
on the platform.

:func:`fill_slots` completes a copy the way a user would: one stub AGENT per
open state port, wired to it by an INVOKE edge. The template's own files are
never edited. This is test code; it reaches builder internals to add a state
port to an existing node, because the public ``add_node`` accepts ports only at
creation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

import perceivo.orchestration as orchestration

#: The stub's ``response_data`` -- how a test finds it in a run trace.
SLOT_MARKER = {"slot_ran": True}

STUB_SCRIPT = """import perceivo


@perceivo.agent
async def slot_stub(input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
    return perceivo.primitives.AgentOutput(
        artifact=input.artifact,
        response_data={"slot_ran": True},
    )
"""

#: An INVOKE target returns through its IN port, so the stub declares no OUT
#: port: the platform refuses a declared port that nothing connects to.
STUB_MANIFEST = """name: slot_stub
extension_type: AGENT
input_ports:
  - port_name: input
    port_type: in
    response_data:
      slot_ran: boolean - the stub ran
"""


def fill_slots(project: Path, dest: Path) -> tuple[Path, list[str]]:
    """Copy *project* to *dest* and wire a stub to every open FSM state port.

    Returns:
        ``(dest, stub node names)``. An empty list means the template had no
        open slot and *dest* is an unmodified copy.
    """
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(project, dest)
    orch = orchestration.load(str(dest))
    raw = yaml.safe_load((dest / "orchestration.yaml").read_text(encoding="utf-8"))
    declared = {
        (node["id"], port["key"]): port
        for node in raw.get("nodes", [])
        for port in (node.get("component_ports") or [])
    }
    wired = {edge.source_port_id for edge in orch.edges}
    stubs: list[str] = []
    for node in list(orch.nodes.values()):
        if node.component_type != "FSM" or not node.manifest:
            continue
        manifest = yaml.safe_load((dest / node.manifest).read_text(encoding="utf-8")) or {}
        for state_port in manifest.get("state_ports") or []:
            key = state_port["port_name"]
            existing = [p for p in node.ports if p.get("key") == key]
            if existing and existing[0]["id"] in wired:
                continue
            if not existing:
                port_id = f"port_{node.id}_{key}"
                node.ports.append(
                    {
                        "id": port_id,
                        "node_id": node.id,
                        "name": key,
                        "key": key,
                        "port_type": "state",
                        "direction": "out",
                    }
                )
                orch._port_registry[port_id] = node.id
            stub = f"slot_stub_{len(stubs) + 1}"
            (dest / f"{stub}.py").write_text(STUB_SCRIPT, encoding="utf-8", newline="\n")
            (dest / f"{stub}.yaml").write_text(STUB_MANIFEST, encoding="utf-8", newline="\n")
            spec = declared.get((node.id, key), {})
            art_in = spec.get("artifact_type_input") or "json"
            art_out = spec.get("artifact_type_output") or "json"
            stub_node = orch.add_node(
                stub,
                "AGENT",
                script=f"{stub}.py",
                manifest=f"{stub}.yaml",
                ports=[
                    {
                        "name": "input",
                        "key": "input",
                        "port_type": "in",
                        "direction": "in",
                        "artifact_type_input": art_in,
                        "artifact_type_output": art_out,
                        "artifact_types": spec.get("artifact_types") or [art_in],
                    }
                ],
            )
            orch.add_edge(node, stub_node, source_port=key, target_port="input", kind="INVOKE")
            stubs.append(stub)
    if stubs:
        orch.save(str(dest))
    return dest, stubs


def stubs_that_ran(result: object, stubs: list[str]) -> list[str]:
    """The stub nodes whose ``slot_ran`` output appears in a run trace."""
    ran = []
    for entry in getattr(result, "trace", None) or []:
        text = str(entry)
        for stub in stubs:
            if stub in text and stub not in ran:
                ran.append(stub)
    return ran
