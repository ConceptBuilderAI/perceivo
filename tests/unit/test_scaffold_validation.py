"""A scaffolded project must pass the SDK's own validate().

Three defects made `create -> validate -> run` -- the flow every SDK-Reference
page opens with -- fail on the SDK's own output, before the author had edited
anything:

A. ``component.create()`` interpolated the display name into the ``async def``
   line, so any name that is not a Python identifier emitted a file that does
   not parse. The platform's own convention is hyphenated: the templates this
   SDK ships are ``rag-ingest``, ``chatbot-simple``, ``agent-tool-use``.
B. The validator's loader appended manifest-declared ports the graph already
   declared, under a second id scheme, so the duplicates could never be wired
   and ORCH-UNWIRED fired on every scaffolded project.
C. ``add_node`` could only add ports, never replace them, so an INVOKE-target
   node kept a default out port that has nothing it may
   legally connect to. ``validate()``'s advice, "remove it from the component",
   was not expressible through the builder, and
   ``create(template="agent-tool-use")`` raised outright.

The load-bearing assertion in each group is that ``valid`` is True: a gate that
passes precisely when it cannot judge is the defect class this suite guards
against, so these assert the positive, not merely the absence of a crash.
"""

from __future__ import annotations

import ast

import pytest
import yaml
from click.testing import CliRunner

import perceivo
from perceivo._cli.new_cmd import new as new_cmd
from perceivo.component._manager._create import create
from perceivo.component._manager._inspect import validate as validate_component
from perceivo.component._manager._templates import (
    render_manifest,
    render_script,
    symbol_for,
)
from perceivo.orchestration._builder._orchestration import Orchestration
from perceivo.validator._orchestration._loader import _load_from_directory

# ---------------------------------------------------------------------------
# A. create() emits Python that parses, for any display name
# ---------------------------------------------------------------------------

# Left column is what an author types; right is the identifier it must yield.
NAMES = [
    ("my-agent", "my_agent"),
    ("my agent", "my_agent"),
    ("My-Agent", "My_Agent"),
    ("rag-ingest", "rag_ingest"),
    ("agent-tool-use", "agent_tool_use"),
    ("my.agent", "my_agent"),
    ("my--agent", "my_agent"),
    ("-leading", "leading"),
    ("2fast", "_2fast"),
    ("class", "class_"),
    ("match", "match_"),
    ("!!!", "component"),
]


@pytest.mark.parametrize("name,expected", NAMES)
def test_symbol_for_derives_a_valid_identifier(name, expected):
    symbol = symbol_for(name)
    assert symbol == expected
    assert symbol.isidentifier(), f"{symbol!r} is not an identifier"


@pytest.mark.parametrize("name,expected", NAMES)
def test_create_emits_parseable_python(name, expected, tmp_path):
    create(name, "AGENT", str(tmp_path))
    source = (tmp_path / f"{name}.py").read_text(encoding="utf-8")

    ast.parse(source)  # the defect: this raised SyntaxError

    fn = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef))
    assert fn.name == expected


@pytest.mark.parametrize("name,_expected", NAMES)
def test_scaffolded_component_passes_its_own_validate(name, _expected, tmp_path):
    create(name, "AGENT", str(tmp_path))
    report = validate_component(str(tmp_path / f"{name}.py"))
    assert report.valid, [c.message for c in report.checks]


@pytest.mark.parametrize("ctype", ["AGENT", "TOOL", "START", "END", "FSM", "POLICY"])
def test_every_component_type_scaffolds_parseable_python(ctype, tmp_path):
    create("my-thing", ctype, str(tmp_path))
    ast.parse((tmp_path / "my-thing.py").read_text(encoding="utf-8"))


def test_the_display_name_survives_into_the_manifest(tmp_path):
    """``symbol`` is for the ``def`` line only. The manifest names the component
    as the platform knows it, hyphens included -- deriving both from one
    substitution is what coupled them in the first place."""
    create("my-agent", "AGENT", str(tmp_path))
    manifest = yaml.safe_load((tmp_path / "my-agent.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "my-agent"
    assert "my-agent" in (tmp_path / "my-agent.py").read_text(encoding="utf-8")


def test_render_script_substitutes_both_placeholders():
    out = render_script('def {{symbol}}(): log("{{name}}")', "my-agent")
    assert out == 'def my_agent(): log("my-agent")'


# ---------------------------------------------------------------------------
# A2. the display name is free-form text; each target syntax needs its own
#     escaping. `create("no")` wrote `name: no`, which YAML reads as False.
# ---------------------------------------------------------------------------

# YAML 1.1 resolves each of these to something that is not the string typed.
YAML_SIGNIFICANT = ["no", "on", "yes", "off", "null", "~", "1.0", "2", "[b]", "{b}"]


@pytest.mark.parametrize("name", [*YAML_SIGNIFICANT, "my-agent", "it's", "café", "!!!"])
def test_manifest_name_round_trips_to_the_name_that_was_typed(name, tmp_path):
    create(name, "AGENT", str(tmp_path))
    manifest = yaml.safe_load((tmp_path / f"{name}.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == name
    assert isinstance(manifest["name"], str)


# "a: b", '*x', 'a"b' and 'a\\b' are excluded: the name is also the filename, so
# they fail in the filesystem before any of this is reached. That constraint is
# the OS's, not the SDK's.
@pytest.mark.parametrize("name", [*YAML_SIGNIFICANT, "!!!", "#c", "%p", "@a"])
def test_a_yaml_significant_name_still_yields_a_valid_component(name, tmp_path):
    """``!!!`` did not merely validate False -- ``validate()`` raised
    ``yaml.ConstructorError`` out of the SDK, on the path meant to report the
    problem."""
    create(name, "AGENT", str(tmp_path))
    report = validate_component(str(tmp_path / f"{name}.py"))
    assert report.valid, [c.message for c in report.checks]


def test_render_manifest_leaves_an_ordinary_name_unquoted():
    """Quote only where YAML forces it: an author reading the emitted manifest
    should see what they would have written."""
    assert render_manifest("name: {{name}}", "my-agent") == "name: my-agent"
    assert render_manifest("name: {{name}}", "it's") == "name: it's"  # legal plain
    assert render_manifest("name: {{name}}", "no") == "name: 'no'"
    assert render_manifest("name: {{name}}", "'quoted'") == "name: '''quoted'''"


def test_render_script_escapes_for_a_python_string_literal(tmp_path):
    """``{{name}}`` sits inside a double-quoted literal, so a quote in the name
    would end it early and the file would not parse."""
    out = render_script('log("{{name}}")', 'my "cool" agent')
    assert out == 'log("my \\"cool\\" agent")'
    ast.parse(out)


def test_no_placeholder_survives_rendering(tmp_path):
    """A half-wired substitution is the failure mode this pair invites: the
    template emits ``{{symbol}}`` and a caller that only replaces ``{{name}}``
    writes the placeholder into the file verbatim."""
    create("my-agent", "AGENT", str(tmp_path))
    for suffix in (".py", ".yaml"):
        text = (tmp_path / f"my-agent{suffix}").read_text(encoding="utf-8")
        assert "{{" not in text, f"unrendered placeholder in my-agent{suffix}"


def test_cli_new_agent_emits_parseable_python(tmp_path):
    """The CLI keeps its own copy of the templates, so it needs its own proof."""
    result = CliRunner().invoke(new_cmd, ["agent", "my-agent", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    source = (tmp_path / "my-agent.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "{{" not in source
    fn = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef))
    assert fn.name == "my_agent"


# ---------------------------------------------------------------------------
# B. the loader must not duplicate a port the graph already declares
# ---------------------------------------------------------------------------


def test_a_flat_manifest_ports_list_adds_no_graph_ports(tmp_path):
    """The flat ``ports:`` manifest form is not read: it cannot add graph ports."""
    from perceivo.validator import _orchestration

    assert not hasattr(_orchestration._loader, "_append_manifest_ports")


def test_scaffolded_project_declares_no_unreachable_port(tmp_path):
    path = perceivo.templates.use("chatbot-simple", into=str(tmp_path), as_name="summarise")
    loaded = _load_from_directory(str(path))

    port_ids = [p["id"] for p in loaded["ports"]]
    assert len(port_ids) == len(set(port_ids)), f"duplicate port ids: {port_ids}"
    assert len(port_ids) == 6, port_ids  # 3 nodes x 2 ports

    report = perceivo.orchestration.validate(str(path))
    assert report.valid, [c.message for c in report.checks]


# ---------------------------------------------------------------------------
# C. add_node must be able to replace the type's default ports
# ---------------------------------------------------------------------------


def test_add_node_ports_replaces_the_defaults():
    orch = Orchestration("x")
    node = orch.add_node("t", "TOOL", ports=[{"name": "input", "port_type": "in"}])
    assert [p["name"] for p in node.ports] == ["input"]
    assert node.ports[0]["id"] == "port_node_t_input"
    assert node.ports[0]["node_id"] == "node_t"


def test_add_node_extra_ports_still_extends_the_defaults():
    orch = Orchestration("x")
    node = orch.add_node("a", "AGENT", extra_ports=[{"name": "invoke_http", "port_type": "tool"}])
    assert [p["name"] for p in node.ports] == ["input", "output", "invoke_http"]


def test_add_node_rejects_ports_and_extra_ports_together():
    orch = Orchestration("x")
    with pytest.raises(TypeError, match="not both"):
        orch.add_node(
            "a",
            "AGENT",
            ports=[{"name": "input", "port_type": "in"}],
            extra_ports=[{"name": "output", "port_type": "out"}],
        )


def test_add_node_does_not_mutate_the_caller_s_port_dicts():
    """The defaults are copied per node; a caller-supplied list must be too, or
    two nodes built from one template share -- and overwrite -- each other's
    port ids."""
    shared = [{"name": "input", "port_type": "in"}]
    orch = Orchestration("x")
    orch.add_node("a", "TOOL", ports=shared)
    orch.add_node("b", "TOOL", ports=shared)
    assert "id" not in shared[0], shared
    assert orch.nodes["node_a"].ports[0]["id"] == "port_node_a_input"
    assert orch.nodes["node_b"].ports[0]["id"] == "port_node_b_input"


def test_an_invoke_only_tool_can_be_authored_without_an_open_out_port():
    """The agent-with-a-tool graph. The TOOL is reached only by INVOKE, so its
    default out port has nothing to connect to, and validate() must not report it
    unconnected with advice the builder gives no way to follow."""
    orch = Orchestration("agent-with-a-tool")
    start = orch.add_node("start", "START")
    agent = orch.add_node(
        "agent", "AGENT", extra_ports=[{"name": "invoke_http", "port_type": "tool"}]
    )
    tool = orch.add_node("http_tool", "TOOL", ports=[{"name": "input", "port_type": "in"}])
    end = orch.add_node("end", "END")

    orch.add_edge(start, agent)
    orch.add_edge(agent, end)
    invoke = orch.add_edge(agent, tool, source_port="invoke_http")

    assert invoke.kind == "INVOKE", "a tool-type source port infers an INVOKE edge"
    report = orch.validate()
    assert report.valid, [c.message for c in report.checks]


@pytest.mark.parametrize(
    "template",
    [
        "rag-ingest",
        "rag-query",
        "chatbot-simple",
        "chatbot-multi-turn",
        "agent-tool-use",
        "image-analyzer",
    ],
)
def test_every_shipped_template_creates_and_validates(template, tmp_path):
    """These are the SDK's own worked examples. ``agent-tool-use`` raised on
    create(); the rest validated False."""
    path = perceivo.templates.use(template, into=str(tmp_path), as_name=f"proj-{template}")
    report = perceivo.orchestration.validate(str(path))
    assert report.valid, [c.message for c in report.checks]


def test_scaffolding_a_template_raises_no_deprecation_warning(tmp_path, recwarn):
    """The scaffolder built its graphs with connect()/add_invoke(), both
    documented as scheduled for removal in 1.0.0. The warning fired from
    inside the SDK, naming a call the author had not made."""
    perceivo.templates.use("agent-tool-use", into=str(tmp_path), as_name="proj")
    deprecations = [str(w.message) for w in recwarn if issubclass(w.category, DeprecationWarning)]
    assert not deprecations, deprecations


def test_the_template_list_and_the_templates_that_work_are_the_same_set():
    """The catalog once advertised a template that could not be created."""
    advertised = {
        t.name
        for t in perceivo.templates.list(kind="orchestration")
        if t.template_source_id is None
    }
    assert advertised == {
        "rag-ingest",
        "rag-query",
        "chatbot-simple",
        "chatbot-multi-turn",
        "agent-tool-use",
        "image-analyzer",
    }


def test_the_default_scaffold_validates_and_declares_port_directions(tmp_path):
    """The contract check chooses a node's outgoing port by ``direction``; a
    scaffold whose ports had none declared no response contract, so END's
    required ``result`` was never provided."""
    path = perceivo.orchestration.create("proj", directory=str(tmp_path))
    report = perceivo.orchestration.validate(str(path))
    assert report.valid, [c.message for c in report.checks]
    doc = yaml.safe_load((path / "orchestration.yaml").read_text(encoding="utf-8"))
    mainflow = [p for p in doc["ports"] if p["port_type"] in ("in", "out")]
    assert mainflow
    assert all(p["direction"] == p["port_type"] for p in mainflow)
