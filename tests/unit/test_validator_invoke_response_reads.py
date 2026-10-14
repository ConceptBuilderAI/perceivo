"""``VAL-CONTRACT-RESP``: fields read from a ``flow.invoke`` result.

The platform refuses at import a component that reads a field from an invoke
result's ``response_data`` that the invoked port does not declare.
``validate_component`` did not check it, so the component validated clean
locally. Each refusal here is paired with a control that must stay clean.
"""

from __future__ import annotations

import textwrap

import yaml

from perceivo.validator._component import validate_source

_TITLE = "Script reads undeclared invoke response_data"


def _manifest(tool_response_data: dict) -> dict:
    return {
        "extension_type": "AGENT",
        "name": "agent",
        "input_ports": [
            {
                "port_name": "input",
                "port_type": "in",
                "artifact_type_input": "none",
                "artifact_type_output": "none",
                "request_data": {"query": "string"},
                "response_data": {"answer": "string"},
            }
        ],
        "tool_ports": [
            {
                "port_name": "search",
                "artifact_type_input": "none",
                "artifact_type_output": "none",
                "request_data": {"query": "string"},
                "response_data": tool_response_data,
            }
        ],
    }


def _script(read: str) -> str:
    return f"""
    import perceivo
    @perceivo.agent
    async def f(input):
        r = await perceivo.flow.invoke("search", request_data={{"query": "q"}})
        answer = {read}
        return perceivo.primitives.AgentOutput(response_data={{"answer": answer}})
    """


def _reads(script: str, manifest: dict) -> list[tuple[str, str, str, str]]:
    report = validate_source(
        script=textwrap.dedent(script),
        component_type="AGENT",
        manifest_yaml=yaml.safe_dump(manifest),
    )
    return [
        (c.id, c.severity, c.title, c.message)
        for c in report.checks
        if c.id.startswith("VAL-CONTRACT-RESP")
    ]


def test_subscript_read_of_undeclared_field_is_refused():
    script = _script('r["response_data"]["score"]')
    assert _reads(script, _manifest({"hits": "list"})) == [
        (
            "VAL-CONTRACT-RESP-001",
            "error",
            _TITLE,
            "flow.invoke('search') result reads response_data fields not declared on the "
            "port: ['score']",
        )
    ]


def test_get_reads_and_alias_reads_are_seen():
    script = """
    import perceivo
    @perceivo.agent
    async def f(input):
        r = await perceivo.flow.invoke("search", request_data={"query": "q"})
        rd = r["response_data"]
        a = r.get("response_data", {}).get("score")
        b = rd.get("rank")
        c = rd["hits"]
        return perceivo.primitives.AgentOutput(response_data={"answer": [a, b, c]})
    """
    assert [m for *_x, m in _reads(script, _manifest({"hits": "list"}))] == [
        "flow.invoke('search') result reads response_data fields not declared on the "
        "port: ['rank', 'score']"
    ]


def test_read_of_a_port_the_manifest_does_not_declare_is_refused():
    script = _script('r["response_data"]["hits"]').replace('"search"', '"lookup"')
    assert [m for *_x, m in _reads(script, _manifest({"hits": "list"}))] == [
        "flow.invoke('lookup') result reads response_data fields not declared on the port: ['hits']"
    ]


def test_read_of_declared_field_is_clean():
    assert _reads(_script('r["response_data"]["hits"]'), _manifest({"hits": "list"})) == []


def test_result_variable_reused_across_ports_is_not_judged():
    script = """
    import perceivo
    @perceivo.agent
    async def f(input):
        r = await perceivo.flow.invoke("search", request_data={"query": "q"})
        r = await perceivo.flow.invoke("other", request_data={"query": "q"})
        return perceivo.primitives.AgentOutput(response_data={"answer": r["response_data"]["x"]})
    """
    assert _reads(script, _manifest({"hits": "list"})) == []


def test_empty_manifest_skips_invoke_reads_but_not_returned_fields():
    titles = [t for _i, _s, t, _m in _reads(_script('r["response_data"]["score"]'), {})]
    assert titles == ["Undeclared response_data fields in script"]
