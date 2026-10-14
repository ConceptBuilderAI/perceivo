"""Scaffolded manifests must declare the artifact type they actually produce.

The platform blocks an edge whose target requires a non-optional artifact type
while the source declares no ``artifact_type_output``.
A scaffolded manifest that omits that field makes every orchestration built
from a shipped template fail import even though the generated scripts
agree perfectly about what they pass.

The rule is replayed here rather than imported: the SDK cannot depend on
the platform data service, and a test that asserts only "the field is present" would keep
passing if a later change set it to the wrong value.
"""

from __future__ import annotations

import re

import pytest
import yaml

import perceivo
from perceivo._bundle.orchestration import orchestration_to_bundle
from perceivo.component._manager._templates import _TYPE_SKELETONS
from perceivo.orchestration._builder._templates import create

_OPTIONAL_RE = re.compile(r"^optional\(\s*([a-z0-9_]+)\s*\)$", re.IGNORECASE)

#: Templates whose script returns ``artifact=input.artifact``. Their output type
#: is whatever arrived, so declaring one would be a guess the platform enforces.
_PASSTHROUGH_TEMPLATES = ("end", "fsm", "policy", "adapter")

#: Manifest sections that hold ports. A top-level ``ports:`` key is not one of
#: them: it appears in zero authored manifests, and a manifest that uses it
#: parses to no ports at all.
_MANIFEST_PORT_SECTIONS = (
    "input_ports",
    "output_ports",
    "tool_ports",
    "policy_ports",
    "state_ports",
    "review_ports",
    "external_in_port",
    "external_out_port",
)

#: ``{template: {port_name}}`` — passthrough ports that must declare an output
#: artifact type anyway. An ``in`` port on a mainflow type is bidirectional and
#: the platform requires the field there (AGENT, LAYER, FSM). The declaration
#: is still not allowed to be a guess: it must be ``optional(...)``, because the
#: forwarded artifact is absent whenever nothing arrived.
_MAINFLOW_PASSTHROUGH_PORTS = {"fsm": {"input"}}


def _parse(raw):
    """``(base_type, is_optional)`` — the platform's artifact-type grammar."""
    text = str(raw or "").strip()
    match = _OPTIONAL_RE.match(text)
    if match:
        return match.group(1).strip().lower(), True
    return text.lower(), False


def _manifest_ports(template: str, sections=_MANIFEST_PORT_SECTIONS) -> dict[str, dict]:
    """``{port_name: port_def}`` across the requested port sections."""
    manifest = yaml.safe_load(_TYPE_SKELETONS[template]["manifest"].replace("{{name}}", "x"))
    assert "ports" not in manifest, (
        f"{template} declares a top-level `ports:` key; the platform reads "
        f"{', '.join(_MANIFEST_PORT_SECTIONS)} and would see no ports at all"
    )
    ports: dict[str, dict] = {}
    for section in sections:
        entry = manifest.get(section)
        if entry is None:
            continue
        # `external_in_port` / `external_out_port` are single-dict sections.
        for port in [entry] if isinstance(entry, dict) else entry:
            assert "port_name" in port, (
                f"{template}.{section} has a port keyed {sorted(port)}; ports are "
                f"keyed `port_name` and a `name` key is not read"
            )
            ports[port["port_name"]] = port
    assert ports, f"{template} declares no ports"
    return ports


def _unsatisfied_edges(bundle: dict) -> list[str]:
    """Edges the platform blocks for a missing source ``artifact_type_output``."""
    ref_by_node = {n["local_id"]: n["component_ref"] for n in bundle["nodes"]}
    port_def = {
        (ref, p["key"]): p for ref, comp in bundle["components"].items() for p in comp["ports"]
    }

    def resolve(index: int) -> tuple[str, dict]:
        row = bundle["ports"][index]
        label = f"{row['node_ref']}.{row['component_port_key']}"
        ref = ref_by_node.get(row["node_ref"], "")
        return label, port_def.get((ref, row["component_port_key"]), {})

    bad = []
    for edge in bundle["edges"]:
        src_label, src = resolve(edge["source_port_index"])
        tgt_label, tgt = resolve(edge["target_port_index"])
        target_input = tgt.get("artifact_type_input")
        base, optional = _parse(target_input)
        if target_input and base != "none" and not optional and not src.get("artifact_type_output"):
            bad.append(
                f"{edge.get('edge_kind')} {src_label} (no artifact_type_output) -> "
                f"{tgt_label} (requires {target_input!r})"
            )
    return bad


@pytest.mark.parametrize(
    "template", [t.name for t in perceivo.templates.list(kind="orchestration")]
)
def test_shipped_template_exports_with_satisfiable_artifact_types(template, tmp_workspace):
    bundle = orchestration_to_bundle(
        perceivo.templates.use(
            template, into=str(tmp_workspace), as_name=template.replace("-", "_")
        )
    )
    assert not _unsatisfied_edges(bundle), _unsatisfied_edges(bundle)


def test_default_scaffold_exports_with_satisfiable_artifact_types(tmp_workspace):
    bundle = orchestration_to_bundle(create("plain", str(tmp_workspace)))
    assert not _unsatisfied_edges(bundle), _unsatisfied_edges(bundle)


def test_start_declares_the_json_artifact_its_script_returns():
    assert _manifest_ports("start")["output"]["artifact_type_output"] == "json"


def test_agent_declares_the_json_artifact_its_script_returns():
    ports = _manifest_ports("agent")
    assert ports["output"]["artifact_type_output"] == "json"
    # An `in` port's output type is the INVOKE return leg: what this component
    # hands back to whoever invoked it.
    assert ports["input"]["artifact_type_output"] == "json"


def test_tool_declares_the_json_artifact_it_returns_to_its_caller():
    assert _manifest_ports("tool")["input"]["artifact_type_output"] == "json"


@pytest.mark.parametrize("template", _PASSTHROUGH_TEMPLATES)
def test_passthrough_templates_declare_no_concrete_output_artifact_type(template):
    """A default here would be a guess, and the platform enforces guesses."""
    assert "input.artifact" in _TYPE_SKELETONS[template]["script"]
    exempt = _MAINFLOW_PASSTHROUGH_PORTS.get(template, set())
    guessed = []
    # Only the ports that carry this component's OWN output downstream. The
    # bidirectional sections (tool/policy/state/review) describe the invoke legs
    # to a child component -- a separate contract the platform makes mandatory.
    for name, port in _manifest_ports(template, ("input_ports", "output_ports")).items():
        declared = port.get("artifact_type_output")
        if not declared:
            continue
        if name in exempt:
            # Required by the platform, so it may be present — but only as
            # `optional(...)`, which is true of a forwarded artifact.
            assert _parse(declared)[1], (
                f"{template}.{name} must declare artifact_type_output, but it "
                f"forwards its input artifact, so {declared!r} must be optional(...)"
            )
            continue
        guessed.append(name)
    assert not guessed, (
        f"{template} passes its input artifact through, so its output type is not "
        f"knowable from the template; declared on: {guessed}"
    )


@pytest.mark.parametrize("template", sorted(_TYPE_SKELETONS))
def test_every_scaffold_declares_ports_the_platform_can_read(template):
    """The shape defect that leaves a scaffolded manifest portless.

    A top-level ``ports:`` list keyed ``name`` is read by neither half, so a
    template emitting one sends every component to the platform with no
    ports — and the first symptom a user sees is an unrelated contract error
    about undeclared ``response_data`` fields.
    """
    assert _manifest_ports(template)


def test_prebuilt_input_port_tolerates_a_missing_artifact(tmp_workspace):
    """The generated body guards on ``input.artifact``, so the port is optional."""
    path = perceivo.templates.use("agent-tool-use", into=str(tmp_workspace), as_name="tooluse")
    script = (path / "http_tool.py").read_text(encoding="utf-8")
    assert "if input.artifact and input.artifact.data:" in script

    manifest = yaml.safe_load((path / "http_tool.yaml").read_text(encoding="utf-8"))
    port = manifest["input_ports"][0]
    assert _parse(port["artifact_type_input"]) == ("json", True)
    assert port["artifact_type_output"] == "json"
