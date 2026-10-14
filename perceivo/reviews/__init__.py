"""Completed review results.

Reviews are created by the platform, so there is no ``create`` and no
``update``. ``query`` reads completed results scoped by one subject.

Usage::

    import perceivo
    from perceivo.reviews import ReviewSubject

    # Results for one REVIEW component
    page = await perceivo.reviews.query(
        subject=ReviewSubject.COMPONENT,
        subject_id="uuid-of-review-component",
    )

    for result in page:
        answers = result.response_data
        questions = result.survey_spec
"""

import inspect
from typing import Optional

from ..types.errors import ConfigurationError
from ..types.pagination import Page
from ._results import get_results as _get_results
from ._types import ReviewResult, ReviewSubject

__all__ = ["ReviewResult", "ReviewSubject", "query"]

#: Every value :class:`~perceivo.reviews.ReviewSubject` defines, used only to decide
#: which of the backend's ``*_id`` parameters is a subject filter.
_SUBJECT_VALUES = frozenset(member.value for member in ReviewSubject)

#: ``ReviewSubject`` member -> the ``get_results`` parameter it selects. Derived
#: from the backend signature, so a removed id makes its subject raise.
_SUBJECT_IDS: dict[ReviewSubject, str] = {
    ReviewSubject(name.removesuffix("_id")): name
    for name in inspect.signature(_get_results).parameters
    if name.endswith("_id") and name.removesuffix("_id") in _SUBJECT_VALUES
}


def _subject_filter(
    subject: ReviewSubject | str | None, subject_id: Optional[str]
) -> dict[str, str]:
    """Map one ``subject``/``subject_id`` pair onto the backend's id parameter.

    Raises:
        ConfigurationError: Half a pair was given, ``subject`` is not a
            :class:`~perceivo.reviews.ReviewSubject` value, or it is a valid
            ``ReviewSubject`` the backing route cannot filter on. Each raises
            rather than degrades: half a pair would widen the query to every
            review the caller can see, and an unsupported subject would be
            silently ignored.
    """
    if subject is None and subject_id is None:
        return {}
    if subject is None or subject_id is None:
        given, missing = ("subject_id", "subject") if subject is None else ("subject", "subject_id")
        raise ConfigurationError(
            f"reviews.query() got {given}= without {missing}=. The two halves "
            f"select one filter and neither means anything alone -- honouring "
            f"{given}= by itself would widen the query to every review the "
            f"caller can see, which is the opposite of what it asks for. Pass "
            f"both, or neither for a deliberately unscoped query."
        )
    try:
        member = ReviewSubject(subject)
    except ValueError as exc:
        # Chained: the native ValueError names the enum.
        raise ConfigurationError(
            f"reviews.query() got subject={subject!r}, which is not a ReviewSubject "
            f"value. Pass a perceivo.reviews.ReviewSubject member or its value; the "
            f"ones this route can filter on are "
            f"{', '.join(sorted(s.value for s in _SUBJECT_IDS))}."
        ) from exc
    try:
        return {_SUBJECT_IDS[member]: subject_id}
    except KeyError:
        # ``from None``: this KeyError is a dict miss used as a branch.
        raise ConfigurationError(
            f"reviews.query() cannot scope by {member.value!r}. "
            f"ReviewServiceMixin.get_review_results filters on "
            f"{', '.join(sorted(_SUBJECT_IDS.values()))} and nothing else, so "
            f"this subject would be accepted and discarded and the answer would "
            f"be every review the caller can see. Reachable subjects: "
            f"{', '.join(sorted(s.value for s in _SUBJECT_IDS))}."
        ) from None


async def query(
    subject: ReviewSubject | str | None = None,
    subject_id: Optional[str] = None,
    status: str = "COMPLETED",
    limit: int = 50,
) -> Page[ReviewResult]:
    """Return completed review results, scoped by one subject.

    Args:
        subject: What the reviews are about: a
            :class:`~perceivo.reviews.ReviewSubject` member or its value,
            ``component``, ``orchestration`` or ``capability``.
        subject_id: The UUID of that subject. Required with ``subject`` and
            meaningless without it.
        status: Filter by review status.
        limit: Maximum results (platform-capped at 200).

    Returns:
        A :class:`~perceivo.types.pagination.Page` of
        :class:`~perceivo.reviews.ReviewResult`. Omitting both ``subject`` and
        ``subject_id`` is a deliberately unscoped query and is allowed.
        ``total`` stays ``0`` ("no total reported") because the result is capped
        by ``limit``: a full page means there may be more.

    Raises:
        ConfigurationError: Half a pair, a non-``ReviewSubject`` value, or a
            ``ReviewSubject`` this route cannot filter on. A ``PerceivoError``
            subclass, so ``except PerceivoError`` catches it.
        RuntimeError: Called outside an execution context.
    """
    return Page(
        data=await _get_results(
            status=status,
            limit=limit,
            **_subject_filter(subject, subject_id),
        ),
        page=1,
        page_size=limit,
    )
