"""``log.query``'s ``limit`` is a count of the most recent entries, so zero reads none."""

from __future__ import annotations

import pytest

import perceivo.log as log


async def _write_three() -> None:
    for i in range(3):
        await log.write(f"entry {i}", "info")


async def test_a_zero_limit_returns_no_entries(local_context):
    await _write_three()
    page = await log.query(log.LogType.EXTENSION, limit=0)
    assert page.data == []


async def test_a_zero_limit_returns_no_conversation_entries(local_context):
    local_context.logging.add_conversation_log({"role": "agent", "text": "hi"})
    page = await log.query(log.LogType.CONVERSATION, limit=0)
    assert page.data == []


async def test_a_positive_limit_returns_the_most_recent_entries(local_context):
    await _write_three()
    page = await log.query(log.LogType.EXTENSION, limit=2)
    assert [e.message for e in page] == ["entry 1", "entry 2"]


async def test_a_negative_limit_is_refused(local_context):
    with pytest.raises(
        ValueError, match=r"^log.query\(\) got limit=-1; limit must be 0 or more\.$"
    ):
        await log.query(log.LogType.EXTENSION, limit=-1)


async def test_a_negative_limit_is_a_perceivo_error(local_context):
    from perceivo.types import PerceivoError

    with pytest.raises(PerceivoError):
        await log.query(log.LogType.EXTENSION, limit=-1)
