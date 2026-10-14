"""``reviews.query`` on a failed platform read: a typed error, never ``[]``.

The platform extension raises ``httpx`` errors from ``get_review_results``
instead of answering an empty list, because an empty list reads as "no
reviews" and lets a component act on an answer nobody gave. These tests pin
the other half: the SDK turns each failure into the :class:`PerceivoError`
subclass a caller's ``except`` can name, keeps the original as the cause, and
still passes a successful read through untouched.

The planted row is ``test_a_mutant_that_swallows_the_error_is_caught``: a
wrapper that answers ``[]`` on error must fail the same assertion the real
tests make.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpx
import pytest

import perceivo.reviews as reviews
from perceivo._internal.context import _ExecutionContext
from perceivo.reviews import _results
from perceivo.types.errors import (
    AccessDeniedError,
    NotFoundError,
    PerceivoError,
    ServiceError,
)

_URL = "http://broker.invalid/internal/review-requests/results"
_ROW = {
    "review_request_id": "r-1",
    "workload_id": "w-1",
    "survey_spec": [{"id": "rating", "type": "RATING_STARS", "label": "Rating"}],
    "status": "COMPLETED",
    "created_at": "2026-09-29T00:00:00+00:00",
    "component_id": None,
    "orchestration_id": None,
    "capability_id": None,
    "response_data": {"rating": 4},
    "reviewer_user_sub": "user-1",
    "completed_at": "2026-09-29T00:01:00+00:00",
    "context_data": None,
}


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _URL)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


@pytest.fixture
def ext() -> Iterator[AsyncMock]:
    manager = AsyncMock()
    _ExecutionContext.set_ext(manager)
    try:
        yield manager
    finally:
        _ExecutionContext.clear()


async def _query_error(ext: AsyncMock, failure: Exception) -> PerceivoError:
    ext.get_review_results.side_effect = failure
    with pytest.raises(PerceivoError) as caught:
        await reviews.query()
    assert caught.value.__cause__ is failure
    return caught.value


async def test_a_successful_read_passes_through(ext):
    ext.get_review_results.return_value = [_ROW]
    page = await reviews.query()
    assert [row.review_request_id for row in page] == ["r-1"]


async def test_404_is_not_found(ext):
    error = await _query_error(ext, _status_error(404))
    assert type(error) is NotFoundError


@pytest.mark.parametrize("status", [401, 403])
async def test_401_and_403_are_access_denied(ext, status):
    error = await _query_error(ext, _status_error(status))
    assert type(error) is AccessDeniedError
    assert error.status_code == status


@pytest.mark.parametrize(
    ("status", "retryable"), [(500, False), (502, True), (503, True), (504, True)]
)
async def test_5xx_is_a_service_error(ext, status, retryable):
    error = await _query_error(ext, _status_error(status))
    assert type(error) is ServiceError
    assert error.status_code == status
    assert error.retryable is retryable


async def test_another_4xx_is_a_non_retryable_service_error(ext):
    error = await _query_error(ext, _status_error(422))
    assert type(error) is ServiceError
    assert error.retryable is False


async def test_an_unreachable_platform_is_a_retryable_service_error(ext):
    failure = httpx.ConnectError("refused", request=httpx.Request("GET", _URL))
    error = await _query_error(ext, failure)
    assert type(error) is ServiceError
    assert error.retryable is True


async def test_a_non_http_error_is_not_swallowed_or_renamed(ext):
    """Only transport failures are mapped; a bug elsewhere keeps its own type."""
    ext.get_review_results.side_effect = KeyError("boom")
    with pytest.raises(KeyError):
        await reviews.query()


async def _swallowing_get_results(**kwargs):
    """The planted mutant: a read that answers ``[]`` when the platform fails."""
    try:
        return await _results.get_results(**kwargs)
    except PerceivoError:
        return []


async def test_a_mutant_that_swallows_the_error_is_caught(ext, monkeypatch):
    """Planted must-fail row: a ``[]``-on-error read must not satisfy these tests."""
    monkeypatch.setattr(reviews, "_get_results", _swallowing_get_results)
    ext.get_review_results.side_effect = _status_error(503)
    outcome = await reviews.query()
    assert list(outcome) == []  # the mutant hides the failure...
    failure = _status_error(503)
    with pytest.raises(pytest.fail.Exception):
        await _query_error(ext, failure)  # ...and the real assertion catches it
