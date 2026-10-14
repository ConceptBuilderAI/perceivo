"""Under an extension manager with no conversation support, the refusal names that.

Whether a channel is attached decides only what the calls return; the refusal
fires when the extension manager has no conversation hooks at all, so its
message must not send the reader looking for a channel.
"""

from __future__ import annotations

import pytest

import perceivo
from perceivo._internal.context import _ExecutionContext


class _TrainingExt:
    """An extension manager with no conversation hooks, as in training or inference."""


@pytest.fixture
def no_conversation_support():
    _ExecutionContext.clear_all()
    _ExecutionContext.set_ext(_TrainingExt())
    yield
    _ExecutionContext.clear_all()


async def test_the_refusal_names_the_missing_support_not_a_channel(no_conversation_support):
    with pytest.raises(RuntimeError) as raised:
        await perceivo.stream.token("t")
    message = str(raised.value)
    assert "active conversation channel" not in message
    assert "no conversation support" in message
    assert "perceivo.stream.token()" in message


def test_is_active_refuses_the_same_way(no_conversation_support):
    with pytest.raises(RuntimeError, match="no conversation support"):
        perceivo.stream._channel.is_active()
