"""ORCH-REVIEW-004: two questions of one REVIEW node share an answer key.

A reviewer's answers are stored as one object keyed by each question's
``name`` (its ``id`` when it has none). Two questions with the same key would
silently collapse into a single answer, so the platform refuses the node. This
rule reports each shared key once, with the platform's message.

Like the other REVIEW survey rules it runs only on a non-empty ``survey_spec``
list: a node with none is ORCH-REVIEW-001's finding, and a malformed entry is
ORCH-REVIEW-002's.
"""

from __future__ import annotations

from typing import Any, Callable

from ...reviews._answers import duplicate_answer_keys
from .._report import ValidationCheck
from ._node_rules import _node_key, _node_type_of

_TITLE = "REVIEW survey spec"


def validate_review_answer_keys(
    nodes: list[dict[str, Any]], node_type: Callable[[str], str]
) -> list[ValidationCheck]:
    """One ORCH-REVIEW-004 error per answer key shared within one REVIEW node."""
    checks: list[ValidationCheck] = []
    for index, node in enumerate(nodes):
        if _node_type_of(node, _node_key(node, index), node_type) != "REVIEW":
            continue
        survey_spec = (node.get("config") or {}).get("survey_spec")
        if not survey_spec or not isinstance(survey_spec, list):
            continue
        checks.extend(
            ValidationCheck(
                id="ORCH-REVIEW-004",
                severity="error",
                category="graph",
                title=_TITLE,
                message=(
                    f"REVIEW node survey_spec has more than one question answered as "
                    f"'{key}'. Each question needs a distinct name (or id)."
                ),
            )
            for key in duplicate_answer_keys(survey_spec)
        )
    return checks
