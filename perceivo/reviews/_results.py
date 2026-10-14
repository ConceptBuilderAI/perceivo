"""Query review results from within an orchestration component."""

from typing import Optional

import httpx

from .._internal.context import _ExecutionContext
from ..types.errors import AccessDeniedError, NotFoundError, PerceivoError, ServiceError
from ..types.records import _from_wire
from ._types import ReviewResult

#: Statuses a read may simply be tried again on: the gateway or the service
#: behind it was briefly unavailable. The call is a read, so a retry repeats
#: nothing.
_RETRYABLE_STATUSES = frozenset({502, 503, 504})


def _platform_error(exc: httpx.HTTPError) -> PerceivoError:
    """Return the typed SDK error for a failed review-results read.

    A failed read raises rather than answering ``[]``, which would read as "no
    reviews". The error is a ``PerceivoError`` subclass.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return ServiceError(
            f"reviews.query: the platform could not be reached ({type(exc).__name__}). "
            "Nothing was read; it is safe to retry.",
            retryable=True,
        )
    status = exc.response.status_code
    if status == 404:
        return NotFoundError(
            "reviews.query: the platform answered 404. The review-results route is "
            "not available to this run."
        )
    if status in (401, 403):
        return AccessDeniedError(
            f"reviews.query: the platform answered {status}. This run may not read review results.",
            status_code=status,
        )
    return ServiceError(
        f"reviews.query: the platform answered {status}.",
        retryable=status in _RETRYABLE_STATUSES,
        status_code=status,
    )


async def get_results(
    component_id: Optional[str] = None,
    orchestration_id: Optional[str] = None,
    capability_id: Optional[str] = None,
    status: str = "COMPLETED",
    limit: int = 50,
) -> list[ReviewResult]:
    """Get review results from completed reviews.

    Query past review responses by component and/or orchestration context.
    At least one of component_id, orchestration_id, or capability_id should
    be provided to scope the query.

    Args:
        component_id: Filter by REVIEW component UUID (optional).
        orchestration_id: Filter by orchestration UUID (optional).
        capability_id: Filter by capability UUID (optional).
        status: Filter by review status (default: "COMPLETED").
        limit: Maximum results to return (default: 50, max: 200).

    Returns:
        A list of :class:`~perceivo.reviews.ReviewResult`. All twelve fields
        are always present. ``survey_spec`` is never ``None``, because the
        questions exist from the moment the request is created, while
        ``response_data`` is ``None`` until a reviewer answers. A
        ``status="PENDING"`` query therefore returns rows whose
        ``response_data`` is ``None``.

    Raises:
        NotFoundError: The platform answered 404.
        AccessDeniedError: The platform answered 401 or 403.
        ServiceError: Any other failed answer, or the platform could not be
            reached. ``retryable`` is true for 502, 503, 504 and an unreachable
            platform. A failed read is never reported as an empty list.

    Example:
        ```python
        import perceivo

        # Get all completed reviews for a specific REVIEW component
        page = await perceivo.reviews.query(
            subject=perceivo.reviews.ReviewSubject.COMPONENT,
            subject_id="abc-123",
        )

        for result in page:
            answers = result.response_data
            questions = result.survey_spec
            perceivo.log.info(
                f"Reviewer {result.reviewer_user_sub} answered: {answers}"
            )
        ```
    """
    ext = _ExecutionContext.get_ext()
    try:
        raw = await ext.get_review_results(
            component_id=component_id,
            orchestration_id=orchestration_id,
            capability_id=capability_id,
            status=status,
            limit=limit,
        )
    except httpx.HTTPError as exc:
        raise _platform_error(exc) from exc
    return [_from_wire(ReviewResult, r) for r in raw]
