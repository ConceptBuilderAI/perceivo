"""Validate a review's answers against its survey spec, per question type.

The platform enforces the same rules server-side. A behaviour change here is
a contract change and moves :data:`ANSWER_CONTRACT_VERSION`.

Every default below is the web review UI's rendering fallback, so any value
the UI can produce is accepted. The one exception is a TEXT question with a
``config.min_length``: the UI shows that error but submits anyway, and the
server refuses the answer.

* The answer key is ``name`` when the question has one, else ``id``.
* Nothing is coerced. A valid answer is stored exactly as sent.
* A boolean is never accepted where an integer is expected.
* Text length is counted in UTF-16 code units, as the browser counts it.
* An unknown question ``type`` is checked for ``required`` only, so a malformed
  spec cannot make a review impossible to complete.

Per-type defaults (``||`` and ``??`` differ on zero, and the rules keep the
difference):

* stars are ``1..(config.max || 5)``; ``config.min`` is ignored, as the UI
  ignores it;
* numeric is ``(config.min ?? 0)..(config.max ?? 10)``;
* a slider is ``(config.min ?? 0)..(config.max ?? 100)`` in steps of
  ``config.step ?? 1``;
* a matrix without ``config.matrix`` has one row ``row_1`` and columns valued
  1..5.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The answer contract version.
ANSWER_CONTRACT_VERSION = 1

#: Artifact names the UI renders when a review carries no artifacts.
PLACEHOLDER_ARTIFACT_NAMES = ("Artifact 1", "Artifact 2", "Artifact 3")

#: How far ``(value - min) / step`` may sit from an integer and still count as on-step.
STEP_TOLERANCE = 1e-9

_WRONG_TYPE = "wrong_type"
_OUT_OF_RANGE = "out_of_range"
_UNKNOWN_ARTIFACT = "unknown_artifact"
_DUPLICATE_VALUE = "duplicate_value"
_NOT_AN_OPTION = "not_an_option"

_MESSAGES = {
    "answers_not_object": "The answers must be an object keyed by question.",
    _DUPLICATE_VALUE: "The answer repeats a value.",
    "empty_value": "The answer contains an empty value.",
    _NOT_AN_OPTION: "The answer is not one of the question's options.",
    _OUT_OF_RANGE: "The answer is outside the allowed range.",
    "ranking_incomplete": "The ranking must order every artifact exactly once.",
    "required_missing": "The question is required.",
    "step_mismatch": "The answer is not a multiple of the question's step.",
    "too_long": "The answer is longer than the question allows.",
    "too_short": "The answer is shorter than the question requires.",
    _UNKNOWN_ARTIFACT: "The answer names an artifact that is not under review.",
    "unknown_column_value": "The answer uses a value that is not one of the columns.",
    "unknown_question": "The answer does not match any question in the survey.",
    "unknown_row": "The answer names a row that is not in the matrix.",
    _WRONG_TYPE: "The answer has the wrong type for this question.",
}

#: Every code :func:`validate_answers` can emit.
ERROR_CODES = frozenset(_MESSAGES)

_DEFAULT_MATRIX_ROWS = ("row_1",)
_DEFAULT_MATRIX_COLUMN_VALUES = (1, 2, 3, 4, 5)


@dataclass(frozen=True)
class AnswerError:
    """One failing question: its answer key, a stable code and a sentence."""

    question: str
    code: str
    message: str


def answer_key(question: Mapping[str, Any]) -> Any:
    """The key a question's answer is stored under: ``name``, else ``id``.

    ``None`` when the question has neither.
    """
    name = question.get("name")
    if name:
        return name
    return question.get("id") or None


def artifact_names(artifacts: list[Any] | None) -> list[str]:
    """The artifact names the UI renders, in order."""
    names = []
    for index, artifact in enumerate(artifacts or []):
        label = artifact.get("label") if isinstance(artifact, Mapping) else None
        names.append(label or f"Artifact {index + 1}")
    return names or list(PLACEHOLDER_ARTIFACT_NAMES)


def duplicate_answer_keys(survey_spec: list[Any] | None) -> list[str]:
    """String answer keys shared by more than one question, in first-seen order.

    A key that is not a string is ignored rather than reported: answers arrive
    as a JSON object, whose keys are always strings, so such a question can
    never collide with another through its answer.
    """
    keys = [answer_key(q) for q in survey_spec or [] if isinstance(q, Mapping)]
    strings = [key for key in keys if isinstance(key, str) and key]
    counts = Counter(strings)
    return [key for key in dict.fromkeys(strings) if counts[key] > 1]


def _is_int(value: Any) -> bool:
    """An integer that is not a boolean (``True`` is an ``int`` in Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    """An integer or float that is not a boolean."""
    return _is_int(value) or isinstance(value, float)


def _is_empty(value: Any) -> bool:
    """``required`` treats these as unanswered. ``False`` and ``0`` are answers."""
    if value is None:
        return True
    if isinstance(value, bool) or _is_number(value):
        return False
    return isinstance(value, str | list | dict) and len(value) == 0


def _config(question: Mapping[str, Any]) -> Mapping[str, Any]:
    config = question.get("config")
    return config if isinstance(config, Mapping) else {}


def _coalesce(value: Any, default: Any) -> Any:
    """JavaScript ``??``: only ``null`` or absent falls back."""
    return default if value is None else value


def _utf16_length(text: str) -> int:
    """Length as the browser's ``String.length`` counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _option_values(question: Mapping[str, Any]) -> set[Any]:
    options = question.get("options") or []
    return {opt.get("value") for opt in options if isinstance(opt, Mapping)}


# Per-type rules: (question, value, artifact_names) -> error code or None.
# ``value`` is never None here: the caller has already applied ``required``.


def _text(question, value, _names):  # type: ignore[no-untyped-def]
    if not isinstance(value, str):
        return _WRONG_TYPE
    config = _config(question)
    length = _utf16_length(value)
    min_length = config.get("min_length")
    max_length = config.get("max_length")
    if min_length and length < min_length:
        return "too_short"
    if max_length and length > max_length:
        return "too_long"
    return None


def _stars(question, value, _names):  # type: ignore[no-untyped-def]
    if not _is_int(value):
        return _WRONG_TYPE
    maximum = _config(question).get("max") or 5
    return None if 1 <= value <= maximum else _OUT_OF_RANGE


def _numeric(question, value, _names):  # type: ignore[no-untyped-def]
    if not _is_int(value):
        return _WRONG_TYPE
    config = _config(question)
    low = _coalesce(config.get("min"), 0)
    high = _coalesce(config.get("max"), 10)
    return None if low <= value <= high else _OUT_OF_RANGE


def _slider(question, value, _names):  # type: ignore[no-untyped-def]
    if not _is_number(value):
        return _WRONG_TYPE
    config = _config(question)
    low = _coalesce(config.get("min"), 0)
    high = _coalesce(config.get("max"), 100)
    step = _coalesce(config.get("step"), 1)
    if not low <= value <= high:
        return _OUT_OF_RANGE
    if step:
        steps = (value - low) / step
        if abs(steps - round(steps)) > STEP_TOLERANCE:
            return "step_mismatch"
    return None


def _boolean(_question, value, _names):  # type: ignore[no-untyped-def]
    return None if isinstance(value, bool) else _WRONG_TYPE


def _single_choice(question, value, _names):  # type: ignore[no-untyped-def]
    if not isinstance(value, str):
        return _WRONG_TYPE
    return None if value in _option_values(question) else _NOT_AN_OPTION


def _multi_choice(question, value, _names):  # type: ignore[no-untyped-def]
    if not _string_list(value):
        return _WRONG_TYPE
    if len(set(value)) != len(value):
        return _DUPLICATE_VALUE
    allowed = _option_values(question)
    return None if all(item in allowed for item in value) else _NOT_AN_OPTION


def _tags(_question, value, _names):  # type: ignore[no-untyped-def]
    # Free text is allowed: the UI lets a reviewer type a tag that is not an option.
    if not _string_list(value):
        return _WRONG_TYPE
    if any(not item.strip() for item in value):
        return "empty_value"
    return None if len(set(value)) == len(value) else _DUPLICATE_VALUE


def _artifact_single(_question, value, names):  # type: ignore[no-untyped-def]
    if not isinstance(value, str):
        return _WRONG_TYPE
    return None if value in names else _UNKNOWN_ARTIFACT


def _artifact_ranking(_question, value, names):  # type: ignore[no-untyped-def]
    # The UI always emits the full order, so a partial ranking is refused.
    if not _string_list(value):
        return _WRONG_TYPE
    if any(item not in names for item in value):
        return _UNKNOWN_ARTIFACT
    return None if Counter(value) == Counter(names) else "ranking_incomplete"


def _artifact_pairwise(_question, value, names):  # type: ignore[no-untyped-def]
    if not isinstance(value, str):
        return _WRONG_TYPE
    return None if value in names[:2] else _UNKNOWN_ARTIFACT


def _artifact_comparative(question, value, names):  # type: ignore[no-untyped-def]
    # Partial answers are allowed: the UI lets a reviewer rate some artifacts only.
    if not isinstance(value, Mapping):
        return _WRONG_TYPE
    maximum = _config(question).get("max") or 5
    for name, rating in value.items():
        if name not in names:
            return _UNKNOWN_ARTIFACT
        if not _is_int(rating):
            return _WRONG_TYPE
        if not 1 <= rating <= maximum:
            return _OUT_OF_RANGE
    return None


def _matrix_axes(question) -> tuple[set[Any], list[Any]]:  # type: ignore[no-untyped-def]
    matrix = _config(question).get("matrix")
    matrix = matrix if isinstance(matrix, Mapping) else {}
    rows = matrix.get("rows") or [{"id": row} for row in _DEFAULT_MATRIX_ROWS]
    columns = matrix.get("columns") or [{"value": v} for v in _DEFAULT_MATRIX_COLUMN_VALUES]
    row_ids = {row.get("id") for row in rows if isinstance(row, Mapping)}
    column_values = [col.get("value") for col in columns if isinstance(col, Mapping)]
    return row_ids, column_values


def _matrix_likert(question, value, _names):  # type: ignore[no-untyped-def]
    # Partial answers are allowed: the UI does not require every row.
    if not isinstance(value, Mapping):
        return _WRONG_TYPE
    row_ids, column_values = _matrix_axes(question)
    for row_id, cell in value.items():
        if row_id not in row_ids:
            return "unknown_row"
        if not _is_number(cell):
            return _WRONG_TYPE
        if not any(_is_number(col) and col == cell for col in column_values):
            return "unknown_column_value"
    return None


#: The 14 validated question types. Anything else is checked for ``required`` only.
TYPE_RULES: dict[str, Callable[[Mapping[str, Any], Any, list[str]], str | None]] = {
    "TEXT_SHORT": _text,
    "TEXT_LONG": _text,
    "RATING_STARS": _stars,
    "RATING_NUMERIC": _numeric,
    "RATING_SLIDER": _slider,
    "BOOLEAN": _boolean,
    "SINGLE_CHOICE": _single_choice,
    "MULTI_CHOICE": _multi_choice,
    "TAGS": _tags,
    "ARTIFACT_PREFERENCE_SINGLE": _artifact_single,
    "ARTIFACT_RANKING": _artifact_ranking,
    "ARTIFACT_PAIRWISE_PREFERENCE": _artifact_pairwise,
    "ARTIFACT_COMPARATIVE_RATING": _artifact_comparative,
    "MATRIX_LIKERT": _matrix_likert,
}


def _error(key: str, code: str) -> AnswerError:
    return AnswerError(question=key, code=code, message=_MESSAGES[code])


def _unvalidated(question_type: Any) -> None:
    logger.warning(
        "Review answer for unvalidated question type %r checked for required only",
        question_type,
    )


def _check_question(
    question: Mapping[str, Any], key: Any, answers: Mapping[Any, Any], names: list[str]
) -> str | None:
    present = key in answers
    value = answers.get(key)
    if _is_empty(value):
        if question.get("required"):
            return "required_missing"
        if not present or value is None:
            return None
    rule = TYPE_RULES.get(question.get("type"))  # type: ignore[arg-type]
    if rule is None:
        _unvalidated(question.get("type"))
        return None
    try:
        return rule(question, value, names)
    except (TypeError, ValueError, ArithmeticError):
        # A malformed spec (a string maximum, an unhashable option value) must
        # not make the review impossible to complete: degrade to required-only.
        _unvalidated(question.get("type"))
        return None


def validate_answers(
    survey_spec: list[Any] | None, answers: Any, artifacts: list[Any] | None = None
) -> list[AnswerError]:
    """Validate ``answers`` against ``survey_spec``; an empty list means valid.

    At most one error per question. Errors come in survey order, followed by
    one ``unknown_question`` per unmatched answer key, in the answers' order.
    ``answers`` that are not an object give the single error
    ``answers_not_object``.
    """
    if not isinstance(answers, Mapping):
        return [_error("", "answers_not_object")]

    names = artifact_names(artifacts)
    errors: list[AnswerError] = []
    known: set[Any] = set()
    for question in survey_spec or []:
        if not isinstance(question, Mapping):
            continue
        key = answer_key(question)
        if not key:
            continue
        known.add(key)
        code = _check_question(question, key, answers, names)
        if code:
            errors.append(_error(key, code))

    errors.extend(_error(str(key), "unknown_question") for key in answers if key not in known)
    return errors
