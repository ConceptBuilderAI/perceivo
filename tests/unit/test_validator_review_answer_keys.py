"""ORCH-REVIEW-004: two questions of one REVIEW node may not share an answer key.

The platform stores a reviewer's answers as one object keyed by each question's
``name`` (its ``id`` when it has none), and its orchestration validator refuses
a REVIEW node whose questions would collide there. The SDK mirrors that rule,
so a graph that validates clean locally is not refused on import.

The accepting rows matter as much as the rejecting ones: the key is ``name``
first, so two questions that share an ``id`` but carry distinct names do **not**
collide. A rule keyed on ``id`` would reject that graph, which the server
accepts.
"""

from __future__ import annotations

from perceivo.validator._orchestration._main import validate_graph

_RULE = "ORCH-REVIEW-004"
_RATING = "rating"
_COMMENTS = "comments"
_STARS = "RATING_STARS"
_TEXT = "TEXT_LONG"


def _question(type_: str, label: str, *, id_: str | None = None, name: str | None = None) -> dict:
    question: dict[str, object] = {"type": type_, "label": label}
    if id_ is not None:
        question["id"] = id_
    if name is not None:
        question["name"] = name
    return question


def _graph(survey_spec: list, *, node_type: str = "REVIEW") -> dict:
    """START -> AGENT -> END, plus one node the AGENT invokes through a review port."""
    nodes = [
        {"id": "s", "component_type": "START"},
        {"id": "a", "component_type": "AGENT", "config": {"mainflow_target_node_id": "e"}},
        {"id": "e", "component_type": "END"},
        {"id": "r", "component_type": node_type, "config": {"survey_spec": survey_spec}},
    ]
    ports = [
        {"id": "s_ext", "node_id": "s", "port_type": "external_in", "key": "external_in"},
        {"id": "s_out", "node_id": "s", "port_type": "out", "key": "output"},
        {"id": "a_in", "node_id": "a", "port_type": "in", "key": "input"},
        {"id": "e_in", "node_id": "e", "port_type": "in", "key": "input"},
        {"id": "e_ext", "node_id": "e", "port_type": "external_out", "key": "external_out"},
        {"id": "r_in", "node_id": "r", "port_type": "in", "key": "input"},
        {"id": "a_rev", "node_id": "a", "port_type": "review", "key": "review_port"},
    ]
    edges = [
        {"source_port_id": "s_out", "target_port_id": "a_in", "kind": "MAINFLOW"},
        {"source_port_id": "a_rev", "target_port_id": "r_in", "kind": "INVOKE"},
    ]
    return {"nodes": nodes, "ports": ports, "edges": edges}


def _rule_messages(survey_spec: list, *, node_type: str = "REVIEW") -> list[str]:
    graph = _graph(survey_spec, node_type=node_type)
    report = validate_graph(nodes=graph["nodes"], edges=graph["edges"], ports=graph["ports"])
    return [c.message for c in report.checks if c.id == _RULE and c.severity == "error"]


def _all_errors(survey_spec: list) -> list[str]:
    graph = _graph(survey_spec)
    report = validate_graph(nodes=graph["nodes"], edges=graph["edges"], ports=graph["ports"])
    return sorted(c.id for c in report.checks if c.severity == "error")


def test_distinct_keys_validate_clean():
    """The floor: without it, every rejection below could be the wrong error."""
    spec = [
        _question(_STARS, "Rating", id_=_RATING, name=_RATING),
        _question(_TEXT, "Comments", id_=_COMMENTS, name=_COMMENTS),
    ]
    assert _all_errors(spec) == []


def test_two_questions_with_one_name_are_rejected():
    spec = [
        _question(_STARS, "Rating", id_="q1", name=_RATING),
        _question(_STARS, "Rating again", id_="q2", name=_RATING),
    ]
    messages = _rule_messages(spec)
    assert messages == [
        "REVIEW node survey_spec has more than one question answered as 'rating'. "
        "Each question needs a distinct name (or id)."
    ]


def test_a_nameless_question_is_keyed_by_its_id():
    """``id`` is the key only when ``name`` is absent, so it collides with a name."""
    spec = [
        _question(_STARS, "Rating", id_="q1", name=_RATING),
        _question(_TEXT, "Also rating", id_=_RATING),
    ]
    assert len(_rule_messages(spec)) == 1


def test_a_shared_id_under_distinct_names_is_accepted():
    """``name`` wins over ``id``: these answers land under two different keys."""
    spec = [
        _question(_STARS, "Rating", id_="q1", name=_RATING),
        _question(_TEXT, "Comments", id_="q1", name=_COMMENTS),
    ]
    assert _rule_messages(spec) == []


def test_each_shared_key_is_reported_once():
    spec = [_question(_STARS, f"Rating {i}", id_=f"q{i}", name=_RATING) for i in range(3)]
    spec += [_question(_TEXT, f"Comments {i}", id_=f"c{i}", name=_COMMENTS) for i in range(2)]
    messages = _rule_messages(spec)
    assert len(messages) == 2
    assert "'rating'" in messages[0]
    assert "'comments'" in messages[1]


def test_a_non_review_node_is_not_checked():
    spec = [
        _question(_STARS, "Rating", id_="q1", name=_RATING),
        _question(_STARS, "Rating again", id_="q2", name=_RATING),
    ]
    assert _rule_messages(spec, node_type="TOOL") == []


def test_a_malformed_entry_is_left_to_the_other_rules():
    """A non-object entry is ORCH-REVIEW-002's finding, never a crash here."""
    spec = ["not a question", _question(_STARS, "Rating", id_=_RATING, name=_RATING)]
    errors = _all_errors(spec)
    assert "ORCH-REVIEW-002" in errors
    assert _RULE not in errors


def test_a_non_string_key_cannot_collide():
    """Answers arrive as a JSON object, whose keys are strings."""
    spec = [
        _question(_STARS, "One", id_="q1", name=["x"]),  # type: ignore[arg-type]
        _question(_STARS, "Two", id_="q2", name=["x"]),  # type: ignore[arg-type]
    ]
    assert _rule_messages(spec) == []
