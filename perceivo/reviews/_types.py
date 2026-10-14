"""The types :mod:`perceivo.reviews` takes and returns."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = ["ReviewResult", "ReviewSubject"]


class ReviewSubject(StrEnum):
    """What a review is about: the entity kinds the platform supports.

    Exactly the three the review-results route filters on (``component_id``,
    ``orchestration_id``, ``capability_id``). A ``StrEnum``, so the bare values
    are accepted wherever a member is.
    """

    COMPONENT = "component"
    ORCHESTRATION = "orchestration"
    CAPABILITY = "capability"


@dataclass(frozen=True)
class ReviewResult:
    """One review result, as ``reviews.query`` returns it.

    All twelve keys are always present; only the values are nullable.
    ``survey_spec`` (the questions) is never ``None``, while ``response_data``
    (the answers) is ``None`` until the review is answered.
    """

    review_request_id: str
    workload_id: str
    #: The questions. Element shape is not modelled.
    survey_spec: list[dict[str, Any]]
    #: A plain ``str``; the platform constrains no enum (default ``"COMPLETED"``).
    status: str
    #: ISO-8601.
    created_at: str

    component_id: str | None = None
    orchestration_id: str | None = None
    capability_id: str | None = None
    #: The answers. ``None`` until the review is answered.
    response_data: dict[str, Any] | None = None
    reviewer_user_sub: str | None = None
    #: ISO-8601.
    completed_at: str | None = None
    context_data: dict[str, Any] | None = None
