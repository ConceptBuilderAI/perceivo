"""Review answer validation: the edges the shared contract fixture does not pin.

The contract fixture proves every per-type rule. These rows pin what happens
around the rules: a malformed survey spec degrades to ``required``-only instead
of making a review impossible to complete, entries that are not questions are
skipped, and the two ``wrong_type`` paths inside mapping-shaped answers.
"""

from __future__ import annotations

import logging

from perceivo.reviews._answers import validate_answers

_RATING = "rating"
_MATRIX = "MATRIX_LIKERT"


def _codes(survey_spec: list, answers: object, artifacts: list | None = None) -> list[str]:
    return [error.code for error in validate_answers(survey_spec, answers, artifacts)]


def test_a_malformed_spec_degrades_to_required_only(caplog):
    """A string maximum would raise inside the rule; the answer is accepted instead."""
    question = {"name": _RATING, "type": "RATING_STARS", "config": {"max": "five"}}
    with caplog.at_level(logging.WARNING, logger="perceivo.reviews._answers"):
        codes = _codes([question], {_RATING: 3})
    assert codes == []
    assert "checked for required only" in caplog.text


def test_a_malformed_required_question_still_requires_an_answer():
    question = {
        "name": _RATING,
        "type": "RATING_STARS",
        "required": True,
        "config": {"max": "five"},
    }
    assert _codes([question], {}) == ["required_missing"]


def test_entries_that_are_not_questions_are_skipped():
    """Neither entry has an answer key, so neither can be answered or required.

    The keyless question is marked ``required`` on purpose: if it were not
    skipped it would report ``required_missing`` under a ``None`` key.
    """
    spec = ["not a question", {"type": "TEXT_SHORT", "label": "No key", "required": True}]
    assert _codes(spec, {}) == []


def test_a_zero_slider_step_disables_the_step_check():
    question = {"name": "level", "type": "RATING_SLIDER", "config": {"step": 0}}
    assert _codes([question], {"level": 12.345}) == []


def test_a_comparative_rating_that_is_not_an_integer_is_the_wrong_type():
    question = {"name": "compare", "type": "ARTIFACT_COMPARATIVE_RATING"}
    assert _codes([question], {"compare": {"Artifact 1": "4"}}) == ["wrong_type"]


def test_a_matrix_cell_that_is_not_a_number_is_the_wrong_type():
    question = {"name": "grid", "type": _MATRIX}
    assert _codes([question], {"grid": {"row_1": "3"}}) == ["wrong_type"]


def test_a_matrix_cell_outside_the_columns_is_refused():
    """Control for the row above: a number that is not a column value is a different code."""
    question = {"name": "grid", "type": _MATRIX}
    assert _codes([question], {"grid": {"row_1": 9}}) == ["unknown_column_value"]


def test_a_pairwise_preference_that_is_not_a_name_is_the_wrong_type():
    question = {"name": "pick", "type": "ARTIFACT_PAIRWISE_PREFERENCE"}
    assert _codes([question], {"pick": 1}) == ["wrong_type"]


def test_a_matrix_answer_that_is_not_an_object_is_the_wrong_type():
    question = {"name": "grid", "type": _MATRIX}
    assert _codes([question], {"grid": [3]}) == ["wrong_type"]
