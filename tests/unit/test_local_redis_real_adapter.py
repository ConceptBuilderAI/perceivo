"""Unit tests for perceivo.local.adapters.redis's real (non-mock) client wrapper.

Kept apart from ``test_local_redis_adapter.py`` to keep each file under the
500-line cap.

perceivo.cluster._backend.get_redis documents
``await redis.set("cache_key", "data", ttl_seconds=3600)``. The in-memory mock
in ``test_local_redis_adapter.py`` already resolves that keyword; the real
redis.asyncio client has no such parameter, so create_redis_client returning
it unwrapped raised TypeError against the exact published example.
_RealRedisAdapter closes that gap.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from perceivo.local.adapters.redis import (
    _RealRedisAdapter,
    create_redis_client,
)
from perceivo.types.errors import ConfigurationError


class _FakeRealRedisClient:
    """Mirrors redis.asyncio.Redis's real ``set``/``hset`` signatures.

    Neither accepts ``ttl_seconds`` -- calling ``.set(..., ttl_seconds=...)``
    directly against this fake raises ``TypeError``, exactly as the real
    client does, which is what makes the failing-before control genuine.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def ping(self) -> bool:
        return True

    async def set(self, name, value, ex=None, px=None, nx=False, xx=False):
        self.calls.append(("set", name, value, ex))
        return True

    async def hset(self, name, key=None, value=None, mapping=None, items=None):
        self.calls.append(("hset", name, key, value))
        return 1

    async def expire(self, name, ttl):
        self.calls.append(("expire", name, ttl))
        return True

    async def get(self, name):
        return None


async def test_real_redis_client_set_rejects_ttl_seconds_without_the_wrapper():
    """Failing-before control: the raw client has no ttl_seconds parameter."""
    client = _FakeRealRedisClient()
    with pytest.raises(TypeError):
        await client.set("k", "v", ttl_seconds=3600)


async def test_real_redis_adapter_set_honours_ttl_seconds():
    client = _FakeRealRedisClient()
    adapter = _RealRedisAdapter(client)

    result = await adapter.set("cache_key", "data", ttl_seconds=3600)

    assert result is True
    assert client.calls == [("set", "cache_key", "data", 3600)]


async def test_real_redis_adapter_set_without_ttl_seconds_passes_no_expiry():
    client = _FakeRealRedisClient()
    adapter = _RealRedisAdapter(client)

    await adapter.set("k", "v")

    assert client.calls == [("set", "k", "v", None)]


async def test_real_redis_adapter_set_ex_keyword_still_works_unwrapped():
    """A caller already using the real client's own ``ex=`` keeps working."""
    client = _FakeRealRedisClient()
    adapter = _RealRedisAdapter(client)

    await adapter.set("k", "v", ex=10)

    assert client.calls == [("set", "k", "v", 10)]


async def test_real_redis_adapter_hset_honours_ttl_seconds_via_expire():
    """Redis has no per-field hash TTL, so ttl_seconds on hset means EXPIRE."""
    client = _FakeRealRedisClient()
    adapter = _RealRedisAdapter(client)

    result = await adapter.hset("h", "field", "value", ttl_seconds=60)

    assert result == 1
    assert client.calls == [
        ("hset", "h", "field", "value"),
        ("expire", "h", 60),
    ]


async def test_real_redis_adapter_hset_without_ttl_seconds_does_not_call_expire():
    client = _FakeRealRedisClient()
    adapter = _RealRedisAdapter(client)

    await adapter.hset("h", "field", "value")

    assert client.calls == [("hset", "h", "field", "value")]


async def test_real_redis_adapter_forwards_unknown_attributes():
    """get/delete/pipelines/etc. are forwarded, not reimplemented."""
    client = _FakeRealRedisClient()
    adapter = _RealRedisAdapter(client)

    assert await adapter.get("missing") is None
    assert await adapter.ping() is True


@pytest.mark.parametrize(
    ("bad_ttl", "expected"),
    [
        (-1, ConfigurationError),
        (1.5, TypeError),
        (True, TypeError),
        (float("nan"), TypeError),
        ("1.5", ConfigurationError),
        ("abc", ConfigurationError),
        ("0", ConfigurationError),
        (-0.5, TypeError),
        (timedelta(seconds=5), TypeError),
    ],
)
async def test_real_redis_adapter_set_enforces_the_same_ttl_domain_as_the_mock(bad_ttl, expected):
    """Mirrors test_redis_rejects_a_ttl_the_hosted_path_rejects for the wrapper."""
    client = _FakeRealRedisClient()
    adapter = _RealRedisAdapter(client)

    with pytest.raises(expected, match="ttl_seconds"):
        await adapter.set("k", "v", ttl_seconds=bad_ttl)

    # Nothing was sent to the underlying client for a rejected ttl.
    assert client.calls == []


async def test_create_redis_client_returns_the_ttl_seconds_wrapper(monkeypatch):
    """create_redis_client returns a wrapper, not the raw client."""
    import redis.asyncio as aioredis

    fake_client = _FakeRealRedisClient()
    monkeypatch.setattr(aioredis, "from_url", lambda url, **kwargs: fake_client)

    adapter = await create_redis_client(enabled=True, url="redis://localhost:6379")

    assert isinstance(adapter, _RealRedisAdapter)
    # The exact example perceivo/cluster/_backend.py:33-34,50 gives does not raise.
    await adapter.set("cache_key", "data", ttl_seconds=3600)
    assert fake_client.calls == [("set", "cache_key", "data", 3600)]
