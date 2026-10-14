"""A local run has no conversation channel, and ``perceivo.stream`` says so rather than raising."""

from __future__ import annotations

import pytest

import perceivo
from perceivo.config import Config, FilesConfig
from perceivo.local.context import LocalExtensionManager
from perceivo.log.conversation import ContentPart


@pytest.fixture
def local_run(tmp_path):
    ext = LocalExtensionManager(
        Config(files=FilesConfig(working_dir=str(tmp_path / "w"), org_dir=str(tmp_path / "o")))
    )
    ext.activate()
    try:
        yield ext
    finally:
        ext.deactivate()


async def test_no_channel_is_active(local_run):
    assert await perceivo.stream.active() is False
    assert perceivo.stream._channel.is_active() is False


async def test_output_is_delivered_nowhere(local_run):
    assert await perceivo.stream.token("t") is None
    assert await perceivo.stream.send([ContentPart("text/plain", "m")]) is None
    assert await perceivo.stream.end() is None


async def test_no_message_is_waiting(local_run):
    assert await perceivo.stream.peek() is None
    assert await perceivo.stream.drain() == []
    page = await perceivo.stream.history(limit=5)
    assert page.data == []


async def test_receive_refuses_because_nothing_can_arrive(local_run):
    with pytest.raises(RuntimeError, match=r"^Conversation channel is not active$"):
        await perceivo.stream.receive(timeout=0.01)


async def test_the_refusal_is_a_context_error(local_run):
    from perceivo.types import ContextError

    with pytest.raises(ContextError):
        await perceivo.stream.receive(timeout=0.01)
