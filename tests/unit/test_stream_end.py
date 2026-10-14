"""Tests for ``perceivo.stream.end()`` -- the channel-less terminal stream verb.

``end`` closes the run's own stream; there is no channel to name and no
``perceivo.infra.stream.publish(channel, messages)``. The properties worth
pinning are not "it forwards", they are:

* it takes **no channel parameter** -- the signature is the contract, so a later edit that reintroduces a ``channel=`` argument for
  convenience has to fail here rather than pass review;
* it forwards to ``ext.stream_end()`` with **no arguments at all**, so nothing a
  component controls reaches the destination decision;
* it fails as a *named* error on an extension manager that cannot end a stream,
  rather than as an ``AttributeError`` that reads identically at a call site but
  means something different -- and that name is ``ContextError``, so the SDK's
  promise ("catch ``PerceivoError`` and you catch everything") holds here. The
  separate outside-a-run failure stays the plane-wide bare ``RuntimeError`` from
  ``_ExecutionContext.get_ext()``; both are pinned below, because the split is a
  decision and not an accident;
* it is **repeatable** -- ending is component-owned, so a component may stream
  several generations and end more than once without that being an error.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from perceivo import stream
from perceivo._internal.context import _ExecutionContext
from perceivo.types.errors import ContextError, PerceivoError


@pytest.fixture(autouse=True)
def clear_context():
    _ExecutionContext.clear_all()
    yield
    _ExecutionContext.clear_all()


@pytest.fixture
def mock_ext():
    ext = MagicMock()
    ext.stream_end = AsyncMock(return_value=None)
    return ext


@pytest.fixture
def mock_ext_without_stream_end():
    return MagicMock(spec=[])  # empty spec -- no attributes at all


def test_end_takes_no_parameters():
    """The signature IS the contract. Nothing here may accept a destination."""
    assert list(inspect.signature(stream.end).parameters) == []


def test_end_is_exported_from_the_stream_plane():
    assert "end" in stream.__all__
    # Everything __all__ advertises must actually resolve on the module, or
    # `from perceivo.stream import *` raises AttributeError at import time.
    assert [name for name in stream.__all__ if not hasattr(stream, name)] == []


@pytest.mark.asyncio
async def test_end_forwards_to_the_ext_with_no_arguments(mock_ext):
    _ExecutionContext.set_ext(mock_ext)

    assert await stream.end() is None

    mock_ext.stream_end.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_end_is_repeatable(mock_ext):
    """Ending is explicit and component-owned; a second call is not an error."""
    _ExecutionContext.set_ext(mock_ext)

    await stream.end()
    await stream.end()

    assert mock_ext.stream_end.await_count == 2


@pytest.mark.asyncio
async def test_end_outside_an_orchestration_raises_runtime_error():
    """Not ``ContextError``: this failure belongs to ``get_ext()``, not to this
    member, and every plane member raises it. Typing it here alone would make
    ``stream.end()`` differ from the ``stream.token()`` on the line above the
    call."""
    with pytest.raises(RuntimeError):
        await stream.end()


@pytest.mark.asyncio
async def test_end_on_an_ext_that_cannot_end_a_stream_raises_context_error(
    mock_ext_without_stream_end,
):
    _ExecutionContext.set_ext(mock_ext_without_stream_end)

    with pytest.raises(ContextError, match="stream"):
        await stream.end()


def test_that_refusal_is_catchable_as_a_perceivo_error():
    """Catching ``PerceivoError`` catches everything, including the one failure
    this member owns.

    A bare ``RuntimeError`` here would escape ``except PerceivoError`` and be
    counted against the budget in ``tests/unit/test_error_hierarchy.py``.
    """
    assert issubclass(ContextError, PerceivoError)
