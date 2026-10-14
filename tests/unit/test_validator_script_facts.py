"""Script facts behind the declaration-truth checks.

Each case is the platform's own ``script_facts`` test input with its expected
answer, so the SDK port classifies a script exactly as the import does. A fact
that drifts here changes which components ``perceivo validate`` accepts.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from perceivo.validator._declaration_truth import parse_artifact_type
from perceivo.validator._script_facts import (
    analyze_response_data,
    artifact_emit_mode,
    artifact_read_mode,
    dropped_passthrough_keys,
    request_data_derived_vars,
    spread_is_request_data_passthrough,
)


def _tree(src: str) -> ast.AST:
    return ast.parse(textwrap.dedent(src))


@pytest.mark.parametrize(
    ("raw", "base", "optional"),
    [
        ("optional(file)", "file", True),
        ("optional( json )", "json", True),
        ("OPTIONAL(TEXT)", "text", True),
        ("file", "file", False),
        ("none", "none", False),
        ("", "", False),
        (None, "", False),
    ],
)
def test_parse_artifact_type(raw, base, optional):
    assert parse_artifact_type(raw) == (base, optional)


_READS = {
    "tolerant": [
        # Conditional expression guard.
        """
        async def f(input):
            data = getattr(input.artifact, "data", None) if input.artifact else None
            return AdapterOutput(artifact=input.artifact, response_data={"ok": True})
        """,
        # Branch body under a test that checks the artifact.
        """
        async def f(input):
            if input.artifact is not None:
                x = input.artifact.data
            return AgentOutput(response_data={})
        """,
        # And-operand guard.
        """
        async def f(input):
            if input.artifact and input.artifact.data:
                pass
            return AgentOutput(response_data={})
        """,
        # Forwards into invoke: keyword, positional slot, parallel.
        """
        async def route(input):
            result = await perceivo.flow.invoke(
                port_name="a", request_data=input.request_data, artifact=input.artifact
            )
            return FsmOutput(response_data=result["response_data"], artifact=input.artifact)
        """,
        """
        async def route(input):
            result = await perceivo.flow.invoke("a", input.request_data, input.artifact)
            return FsmOutput(response_data=result["response_data"])
        """,
        """
        async def fan(input):
            results = await perceivo.flow.invoke_parallel(
                port_names=["a", "b"], request_data=input.request_data, artifact=input.artifact
            )
            return FsmOutput(response_data={"n": len(results)})
        """,
    ],
    "required": [
        """
        async def f(input):
            payload = input.artifact.data["rows"]
            return AgentOutput(response_data={"n": len(payload)})
        """,
        # A dereference whose result is handed to invoke is still a read.
        """
        async def route(input):
            result = await perceivo.flow.invoke(
                "a", input.request_data, artifact=input.artifact.data
            )
            return FsmOutput(response_data=result["response_data"])
        """,
    ],
    "none": ["x = 1"],
}


@pytest.mark.parametrize(
    ("expected", "src"), [(mode, src) for mode, srcs in _READS.items() for src in srcs]
)
def test_artifact_read_mode(expected, src):
    assert artifact_read_mode(_tree(src)) == expected


_EMITS = {
    "unconditional": """
        async def f(input):
            return AgentOutput(
                artifact=Artifact(type=ArtifactType.JSON, data={}), response_data={"a": 1}
            )
    """,
    "conditional": """
        async def f(input):
            if input.request_data.get("x"):
                return AgentOutput(artifact=made, response_data={"a": 1})
            return AgentOutput(artifact=None, artifacts=[], response_data={"a": 1})
    """,
    "none": """
        async def f(input):
            return AgentOutput(response_data={"a": 1})
    """,
}


@pytest.mark.parametrize(("expected", "src"), list(_EMITS.items()))
def test_artifact_emit_mode(expected, src):
    assert artifact_emit_mode(_tree(src)) == expected


def test_forwarding_the_inbound_artifact_is_a_conditional_emit():
    src = """
        def f(input):
            return StartOutput(request_data={}, artifact=input.artifact)
    """
    assert artifact_emit_mode(_tree(src)) == "conditional"


def test_rebound_var_is_nonliteral_and_dynamic():
    shape = analyze_response_data(
        _tree(
            """
            async def f(input):
                merged = {}
                merged = other.get("response_data", {})
                return PolicyOutput(next_state="done", response_data=merged)
            """
        )
    )
    assert shape.has_nonliteral is True
    assert shape.qualifies_as_dynamic is True


def test_mutated_literal_alias_is_dynamic():
    shape = analyze_response_data(
        _tree(
            """
            async def f(input):
                merged = {"a": 1}
                merged.update(extra)
                return AgentOutput(response_data=merged)
            """
        )
    )
    assert shape.mutated_alias is True
    assert shape.has_nonliteral is False
    assert shape.qualifies_as_dynamic is True


def test_call_result_is_nonliteral():
    shape = analyze_response_data(
        _tree("async def f(input):\n    return AgentOutput(response_data=build_it())\n")
    )
    assert shape.has_nonliteral is True


def test_computed_key_and_literal_keys():
    shape = analyze_response_data(
        _tree(
            """
            async def f(input):
                field = perceivo.env.get("texts_field")
                return AgentOutput(response_data={"n": 1, field: []})
            """
        )
    )
    assert shape.has_computed_key is True
    assert shape.literal_keys == {"n"}
    assert shape.qualifies_as_dynamic is True


def test_literal_dict_is_fixed():
    shape = analyze_response_data(
        _tree("def f(input):\n    return AgentOutput(response_data={'a': 1, 'b': 2})\n")
    )
    assert shape.literal_keys == {"a", "b"}
    assert not shape.spread_exprs
    assert shape.qualifies_as_dynamic is False


_LOOP_DROP = """
    async def f(input):
        passthrough = dict(input.request_data)
        for key in ("embeddings", "vectors"):
            passthrough.pop(key, None)
        return AgentOutput(response_data={**passthrough})
"""

_KEY_DROP = """
    async def f(input):
        passthrough = dict(input.request_data)
        passthrough.pop("embeddings", None)
        return AgentOutput(response_data={**passthrough})
"""


def test_dropped_keys_literal_loop_and_literal_key():
    assert dropped_passthrough_keys(_tree(_LOOP_DROP), {"passthrough"}) == {"embeddings", "vectors"}
    assert dropped_passthrough_keys(_tree(_KEY_DROP), {"passthrough"}) == {"embeddings"}
    # Not a request_data-derived var: nothing it drops is a passthrough drop.
    assert dropped_passthrough_keys(_tree(_KEY_DROP), {"other"}) == set()


def test_request_data_spread_is_recognised_through_derived_vars():
    tree = _tree(_KEY_DROP)
    assert "passthrough" in request_data_derived_vars(tree)
    (spread,) = analyze_response_data(tree).spread_exprs
    assert spread_is_request_data_passthrough(tree, spread) is True

    other = _tree("def f(input):\n    o = {'a': 1}\n    return AgentOutput(response_data={**o})\n")
    (spread,) = analyze_response_data(other).spread_exprs
    assert spread_is_request_data_passthrough(other, spread) is False
