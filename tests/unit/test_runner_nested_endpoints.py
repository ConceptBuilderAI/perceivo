"""Pure helpers behind nested ORCHESTRATION nodes in the local runner.

Split from ``test_runner_nested_modes.py``; the
graph fixtures are shared from there.
"""

from __future__ import annotations

from typing import Any

import pytest

from perceivo.primitives import Artifact, ArtifactType
from perceivo.runner import _nested
from perceivo.types.errors import ConfigurationError

from .test_runner_nested_modes import _edge, _node, _ports

# --------------------------------------------------------------------------- #
# inline endpoint rules                                                       #
# --------------------------------------------------------------------------- #


def _graph(nodes: list[dict[str, Any]], edges: list[tuple[str, str]]) -> dict[str, Any]:
    ids = [n["id"] for n in nodes]
    return {"nodes": nodes, "ports": _ports(*ids), "edges": [_edge(a, b) for a, b in edges]}


def test_inline_endpoints_union_edges_and_config():
    graph = _graph(
        [
            _node("s", "START", config={"mainflow_target_node_ref": "a"}),
            _node("a", "AGENT"),
            _node("b", "AGENT", config={"mainflow_target_node_ref": "e"}),
            _node("t", "TOOL"),
            _node("e", "END"),
        ],
        [("s", "a"), ("a", "b")],
    )
    assert _nested.inline_endpoints(graph, "orch") == ("a", "b")


@pytest.mark.parametrize(
    ("nodes", "edges", "message"),
    [
        ([_node("a", "AGENT"), _node("e", "END")], [], "missing a START and/or END"),
        (
            [_node("s", "START"), _node("a", "AGENT"), _node("e", "END")],
            [("a", "e")],
            "not connected to real nodes",
        ),
        (
            [_node("s", "START"), _node("a", "AGENT"), _node("b", "AGENT"), _node("e", "END")],
            [("s", "a"), ("s", "b"), ("a", "e")],
            "multiple entry/exit mainflow nodes",
        ),
        (
            [_node("s", "START"), _node("e", "END")],
            [("s", "e")],
            "no real node between START and END",
        ),
    ],
)
def test_inline_endpoints_reject_what_the_engine_rejects(nodes, edges, message):
    graph = _graph(nodes, edges)
    with pytest.raises(ConfigurationError, match=message):
        _nested.inline_endpoints(graph, "orch")


# --------------------------------------------------------------------------- #
# subprocess response merge
# --------------------------------------------------------------------------- #


def _art(data: Any) -> Artifact:
    return Artifact(type=ArtifactType.TEXT, data=data)


@pytest.mark.parametrize(
    ("response", "artifact", "expected"),
    [
        ({"tokens_in": 1}, _art("answer"), {"tokens_in": 1, "response": "answer"}),
        ({}, _art({"a": 1, "b": 2}), {"a": 1, "b": 2}),
        ({"real": True}, _art("answer"), {"real": True}),
        ({}, _art(None), {}),
        ({}, None, {}),
        ({}, _art(["list"]), {}),
    ],
)
def test_merge_artifact_into_response(response, artifact, expected):
    _nested.merge_artifact_into_response(response, artifact)
    assert response == expected
