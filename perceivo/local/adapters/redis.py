"""Local Redis adapters for explicit real and in-memory modes."""

from __future__ import annotations

import logging
from inspect import isawaitable
from time import monotonic
from typing import Any, Optional

from ...types.errors import ConfigurationError, ServiceError

logger = logging.getLogger("perceivo.local.redis")

_TTL_DOMAIN = (
    "ttl_seconds must be a positive whole number of seconds, as an int or a "
    "string of digits; got {value!r}. A falsy ttl (None, 0, False) means no "
    "expiry."
)


def _resolve_ttl(ttl_seconds: Any) -> Optional[int]:
    """Resolve a ttl to whole seconds, or raise, before anything is stored.

    Each case matches what redis-py and a Redis server do on the hosted path:

    * falsy: plain ``SET``, no expiry. Returns ``None``.
    * ``int`` > 0: ``SETEX``. Accepted.
    * a string of digits: the server parses ``"5"`` as 5, so it is accepted and
      converted.
    * ``bool``: redis-py refuses it, so it is rejected rather than meaning one
      second.
    * ``float``, including ``nan``: the server refuses it, so it is rejected.
    * zero or negative after conversion: the server refuses it as an invalid
      expire time.
    * ``datetime.timedelta``: rejected; the contract is ``ttl_seconds: int | None``.

    Raises:
        TypeError: If the ttl has the wrong type.
        ConfigurationError: If the ttl has the right type but a value the server
            refuses.
    """
    if not ttl_seconds:
        return None
    value = ttl_seconds
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            raise ConfigurationError(_TTL_DOMAIN.format(value=ttl_seconds)) from None
    elif isinstance(value, bool) or not isinstance(value, int):
        # bool first: ``isinstance(True, int)`` is True, so the order matters.
        raise TypeError(_TTL_DOMAIN.format(value=ttl_seconds))
    if value <= 0:
        raise ConfigurationError(_TTL_DOMAIN.format(value=ttl_seconds))
    return value


class InMemoryRedis:
    """Minimal in-memory Redis-like store for local development.

    Supports basic get/set/delete/exists operations. Not thread-safe,
    not persistent, and does not implement the full Redis API. Sufficient
    for local development and testing of components that use simple caching.
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._expires_at: dict[str, float] = {}

    def _expire(self, key: str) -> None:
        expires_at = self._expires_at.get(key)
        if expires_at is not None and monotonic() >= expires_at:
            self._store.pop(key, None)
            self._expires_at.pop(key, None)

    async def get(self, key: str) -> Optional[bytes]:
        self._expire(key)
        value = self._store.get(key)
        if isinstance(value, str):
            return value.encode("utf-8")
        return value

    async def set(
        self,
        key: str,
        value: Any,
        ex: Optional[int] = None,
        **kwargs: Any,
    ) -> bool:
        ttl_seconds = kwargs.get("ttl_seconds", ex)
        # A falsy ttl (None, 0, False) is a plain SET with no expiry, as hosted. The ttl
        # is resolved before any mutation, so a rejected ttl leaves the key untouched.
        ttl = _resolve_ttl(ttl_seconds)
        expires_at = monotonic() + ttl if ttl is not None else None
        if isinstance(value, bytes):
            self._store[key] = value
        else:
            self._store[key] = str(value).encode("utf-8") if value is not None else None
        if expires_at is None:
            self._expires_at.pop(key, None)
        else:
            self._expires_at[key] = expires_at
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            self._expire(key)
            if key in self._store:
                del self._store[key]
                self._expires_at.pop(key, None)
                count += 1
        return count

    async def exists(self, *keys: str) -> int:
        for key in keys:
            self._expire(key)
        return sum(1 for k in keys if k in self._store)

    async def keys(self, pattern: str = "*") -> list[Any]:
        for key in self._expires_at.copy():
            self._expire(key)
        if pattern == "*":
            return list(self._store.keys())
        import fnmatch

        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    async def hset(self, name: str, key: str, value: Any) -> int:
        # An expired name is absent, so HSET rebuilds the hash rather than raising
        # against the expired string still in ``_store``.
        self._expire(name)
        if name not in self._store:
            self._store[name] = {}
        self._store[name][key] = value
        return 1

    async def hget(self, name: str, key: str) -> Any:
        """Return a stored hash value, encoding strings as UTF-8 bytes."""
        self._expire(name)
        hmap = self._store.get(name, {})
        value = hmap.get(key)
        if isinstance(value, str):
            return value.encode("utf-8")
        return value

    async def hgetall(self, name: str) -> dict[bytes, bytes]:
        self._expire(name)
        hmap = self._store.get(name, {})
        return {
            k.encode("utf-8") if isinstance(k, str) else k: v.encode("utf-8")
            if isinstance(v, str)
            else v
            for k, v in hmap.items()
        }


class _RealRedisAdapter:
    """Wrap a real ``redis.asyncio`` client so it honours the ``ttl_seconds`` keyword.

    The real client has no ``ttl_seconds`` parameter. This wraps ``set`` and
    ``hset`` only, resolving ``ttl_seconds`` through :func:`_resolve_ttl` and
    delegating with ``ex=`` for ``set`` and a separate ``EXPIRE`` for ``hset``,
    since Redis has no per-field hash TTL. Every other attribute is forwarded
    unchanged.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._client, attr)

    async def set(
        self,
        name: str,
        value: Any,
        *args: Any,
        ttl_seconds: Any = None,
        **kwargs: Any,
    ) -> Any:
        ttl = _resolve_ttl(ttl_seconds)
        if ttl is not None:
            kwargs.setdefault("ex", ttl)
        return await self._client.set(name, value, *args, **kwargs)

    async def hset(
        self,
        name: str,
        *args: Any,
        ttl_seconds: Any = None,
        **kwargs: Any,
    ) -> Any:
        ttl = _resolve_ttl(ttl_seconds)
        result = await self._client.hset(name, *args, **kwargs)
        if ttl is not None:
            await self._client.expire(name, ttl)
        return result


async def create_redis_client(
    enabled: bool = False,
    url: str = "redis://localhost:6379",
) -> Any:
    """Create and verify the configured real Redis client.

    In-memory Redis is selected explicitly by mock mode in the local manager;
    this factory never changes the requested backend after a failure. The
    returned object is wrapped in :class:`_RealRedisAdapter` so ``set`` and
    ``hset`` accept the published ``ttl_seconds`` keyword; ``connect()``, the
    public spelling callers use, is untouched.
    """
    recovery = "Set mocks.modes.redis to 'mock' in perceivo.yaml for offline use."
    if not enabled:
        raise ConfigurationError(f"Local Redis requires redis.enabled: true. {recovery}")
    if not isinstance(url, str) or not url.strip():
        raise ConfigurationError(f"Local Redis requires a nonblank redis.url. {recovery}")

    try:
        import redis.asyncio as aioredis
        from redis import exceptions as redis_exceptions
    except ImportError as exc:
        raise ConfigurationError(
            f"Local Redis requires the optional redis package. Install perceivo[redis]. {recovery}"
        ) from exc

    client: Any = None
    try:
        client = aioredis.from_url(url, decode_responses=False)
        await client.ping()
    except redis_exceptions.AuthenticationError as exc:
        await _close_failed_client(client)
        raise ServiceError(
            "Redis authentication failed for the configured local service.",
            retryable=False,
        ) from exc
    except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as exc:
        await _close_failed_client(client)
        raise ServiceError(
            "Redis could not connect to the configured local service.",
            retryable=True,
        ) from exc
    except Exception as exc:
        await _close_failed_client(client)
        raise ServiceError(
            "Redis client initialization failed for the configured local service.",
            retryable=False,
        ) from exc

    logger.info("Connected to the configured local Redis service")
    return _RealRedisAdapter(client)


async def _close_failed_client(client: Any) -> None:
    """Close a failed client best-effort without replacing its original error."""
    if client is None:
        return
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if isawaitable(result):
            await result
    except Exception:
        logger.debug("Redis client cleanup failed after initialization error")
