"""Unit tests for perceivo.local.adapters.redis (in-memory, no network).

The ttl-domain and hash-expiry cases live here, apart from
``test_local_adapters.py``, to keep each file under the 500-line cap.

The real (non-mock) client wrapper's cases live in
``test_local_redis_real_adapter.py`` for the same reason.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from perceivo.local.adapters.redis import InMemoryRedis, create_redis_client
from perceivo.types.errors import ConfigurationError

# ---------------------------------------------------------------------------
# InMemoryRedis
# ---------------------------------------------------------------------------


async def test_redis_set_get_roundtrip():
    r = InMemoryRedis()
    assert await r.set("k", "v") is True
    assert await r.get("k") == b"v"


async def test_redis_ttl_seconds_expires_without_affecting_control(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("expiring", "gone", ttl_seconds=5)
    await r.set("control", "kept")

    now = 105.0

    assert await r.get("expiring") is None
    assert await r.exists("expiring", "control") == 1
    assert await r.keys("*") == ["control"]
    assert await r.get("control") == b"kept"


async def test_redis_set_without_ttl_clears_existing_expiry(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("k", "first", ttl_seconds=5)
    await r.set("k", "replacement")

    now = 106.0

    assert await r.get("k") == b"replacement"


@pytest.mark.parametrize("falsy_ttl", [0, False, None])
async def test_redis_falsy_ttl_means_no_expiry_like_hosted(monkeypatch, falsy_ttl):
    # Hosted adapter: ``if ttl_seconds: setex(...) else: set(...)``.
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("fresh", "v", ttl_seconds=falsy_ttl)
    assert await r.get("fresh") == b"v"

    await r.set("k", "first", ttl_seconds=5)
    await r.set("k", "second", ttl_seconds=falsy_ttl)

    now = 106.0

    assert await r.get("k") == b"second"
    assert await r.get("fresh") == b"v"
    assert r._expires_at == {}


async def test_redis_exists_omits_expired_key_without_prior_get(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("expiring", "gone", ttl_seconds=5)
    await r.set("control", "kept")

    now = 105.0

    assert await r.exists("expiring") == 0
    assert await r.exists("expiring", "control") == 1


async def test_redis_keys_omits_expired_key_without_prior_get(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("cache:expiring", "gone", ttl_seconds=5)
    await r.set("cache:control", "kept")

    now = 105.0

    assert await r.keys() == ["cache:control"]


async def test_redis_keys_glob_omits_expired_key_without_prior_get(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("cache:expiring", "gone", ttl_seconds=5)
    await r.set("cache:control", "kept")

    now = 105.0

    assert await r.keys("cache:*") == ["cache:control"]


async def test_redis_delete_expired_key_behaves_as_absent(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("expiring", "gone", ttl_seconds=5)

    now = 105.0

    assert await r.delete("expiring") == await r.delete("never-set") == 0
    assert "expiring" not in r._store
    assert "expiring" not in r._expires_at


async def test_redis_delete_live_key_clears_its_expiry(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("k", "v", ttl_seconds=5)
    assert await r.delete("k") == 1
    assert "k" not in r._expires_at
    await r.hset("k", "field", "value")

    now = 106.0

    # The old deadline must not evict the hash that reused the name.
    assert await r.exists("k") == 1
    assert await r.hget("k", "field") == b"value"


async def test_redis_ex_keyword_sets_expiry(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("k", "v", ex=5)

    now = 104.999
    assert await r.exists("k") == 1
    now = 105.0
    assert await r.exists("k") == 0


async def test_redis_positional_third_argument_sets_expiry(monkeypatch):
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("k", "v", 5)

    now = 104.999
    assert await r.exists("k") == 1
    now = 105.0
    assert await r.exists("k") == 0


# --- ttl domain --------------------------------------------------------------
#
# Each value below is decided by what redis-py and a Redis server do with it on
# the hosted path, not by what is convenient locally. The first four must not be
# accepted in silence. The digit strings must not raise an unreadable
# ``float + str`` TypeError locally while working in-cluster.


@pytest.mark.parametrize(
    ("bad_ttl", "expected"),
    [
        (-1, ConfigurationError),  # server: "invalid expire time"; local deleted it
        (1.5, TypeError),  # encoded b'1.5'; server: not an integer
        (True, TypeError),  # redis-py DataError; local silently meant 1 second
        (float("nan"), TypeError),  # local: never expired at all
        ("1.5", ConfigurationError),  # encoded b'1.5'; server rejects
        ("abc", ConfigurationError),
        ("0", ConfigurationError),  # truthy string, zero ttl; server rejects
        (-0.5, TypeError),
        (timedelta(seconds=5), TypeError),  # declared contract is int | None
    ],
)
async def test_redis_rejects_a_ttl_the_hosted_path_rejects(monkeypatch, bad_ttl, expected):
    """A rejected ttl is loud, and leaves the key exactly as it was.

    A wrong type raises ``TypeError``; a right type with a value the service
    refuses raises ``ConfigurationError``, which ``except PerceivoError``
    catches. Both are reachable, neither is silent.
    """
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("k", "old", ttl_seconds=50)

    with pytest.raises(expected, match="ttl_seconds"):
        await r.set("k", "new", ttl_seconds=bad_ttl)

    # Nothing was stored and the earlier deadline still stands.
    assert await r.get("k") == b"old"
    assert r._expires_at == {"k": 150.0}
    now = 150.0
    assert await r.get("k") is None


async def test_redis_nan_ttl_is_refused_rather_than_never_expiring(monkeypatch):
    """The ttl case that would give a wrong answer rather than an error.

    ``monotonic() + nan`` is ``nan`` and every ``>=`` comparison against it is
    ``False``, so an accepted nan ttl would pin the key in the store for the
    life of the process -- the opposite of what a caller asking for an expiry
    wants.
    """
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()

    with pytest.raises(TypeError, match="ttl_seconds"):
        await r.set("k", "v", ttl_seconds=float("nan"))

    assert await r.get("k") is None
    assert r._expires_at == {}


@pytest.mark.parametrize("ttl", ["5", 5])
async def test_redis_digit_string_ttl_expires_like_the_int(monkeypatch, ttl):
    """Hosted encodes ``"5"`` as ``b'5'`` and the server parses it as 5."""
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("k", "v", ttl_seconds=ttl)

    assert r._expires_at == {"k": 105.0}
    now = 104.999
    assert await r.get("k") == b"v"
    now = 105.0
    assert await r.get("k") is None


async def test_redis_reset_with_a_longer_ttl_replaces_the_deadline(monkeypatch):
    """The cache-refresh case.

    Under ``self._expires_at.setdefault(key, expires_at)`` the whole suite stays
    green while the refreshed value is dropped at the ORIGINAL deadline.
    """
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("k", "first", ttl_seconds=5)
    await r.set("k", "refreshed", ttl_seconds=500)

    assert r._expires_at == {"k": 600.0}
    now = 106.0
    assert await r.get("k") == b"refreshed"
    now = 600.0
    assert await r.get("k") is None


async def test_redis_reset_with_a_shorter_ttl_replaces_the_deadline(monkeypatch):
    """The other direction: the new deadline wins even when it is sooner."""
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("k", "first", ttl_seconds=500)
    await r.set("k", "second", ttl_seconds=5)

    assert r._expires_at == {"k": 105.0}
    now = 105.0
    assert await r.get("k") is None


async def test_redis_set_bytes_and_none():
    r = InMemoryRedis()
    await r.set("b", b"raw")
    assert await r.get("b") == b"raw"
    await r.set("n", None)
    assert await r.get("n") is None


async def test_redis_delete_and_exists():
    r = InMemoryRedis()
    await r.set("a", "1")
    await r.set("b", "2")
    assert await r.exists("a", "b", "missing") == 2
    assert await r.delete("a", "missing") == 1
    assert await r.exists("a") == 0


async def test_redis_keys_glob():
    r = InMemoryRedis()
    await r.set("user:1", "x")
    await r.set("user:2", "y")
    await r.set("other", "z")
    assert set(await r.keys("*")) == {"user:1", "user:2", "other"}
    assert set(await r.keys("user:*")) == {"user:1", "user:2"}


async def test_redis_hash_ops():
    r = InMemoryRedis()
    await r.hset("h", "f1", "v1")
    assert await r.hget("h", "f1") == b"v1"
    assert await r.hget("h", "missing") is None
    allmap = await r.hgetall("h")
    assert allmap == {b"f1": b"v1"}


# --- hash operations honour expiry ------------------------------------------


async def test_redis_hset_over_an_expired_name_rebuilds_the_hash(monkeypatch):
    """HSET over an expired string name does not raise ``TypeError``.

    If no hash operation ran ``_expire``, ``_store`` would still hold the
    expired bytes and ``self._store[name][key] = value`` would be item
    assignment on ``bytes``. Hosted, the name is gone and HSET creates a fresh
    hash.
    """
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("h", "a string", ttl_seconds=5)

    now = 106.0
    assert await r.hset("h", "field", "value") == 1
    assert await r.hgetall("h") == {b"field": b"value"}
    # The rebuilt hash inherits no deadline, so it survives its predecessor's.
    assert r._expires_at == {}
    now = 10_000.0
    assert await r.hget("h", "field") == b"value"


async def test_redis_hget_and_hgetall_do_not_read_an_expired_name(monkeypatch):
    """An expired name reads as absent for hash operations too.

    ``set`` is the only local way to put a deadline on a name, and it stores
    bytes, so the expired thing under the name is a string. A hash operation
    that read it anyway -- ``bytes.get`` -- would raise ``AttributeError`` from
    inside the adapter after the key should have been gone. The name reads as
    absent, which is what every other operation on this class reports for it.
    """
    now = 100.0
    monkeypatch.setattr("perceivo.local.adapters.redis.monotonic", lambda: now)
    r = InMemoryRedis()
    await r.set("h", "a string", ttl_seconds=5)

    # Live: a hash read of a string name is an error, exactly as it is for a
    # string name with no ttl at all. Expiry handling does not touch that.
    now = 104.999
    with pytest.raises(AttributeError):
        await r.hget("h", "field")

    now = 105.0
    assert await r.hget("h", "field") is None
    assert await r.hgetall("h") == {}
    assert await r.exists("h") == 0
    assert r._expires_at == {}


async def test_create_redis_client_requires_explicit_local_configuration():
    with pytest.raises(ConfigurationError, match=r"redis\.enabled: true"):
        await create_redis_client(enabled=False)


async def test_create_redis_client_rejects_a_blank_url_without_fallback():
    with pytest.raises(ConfigurationError, match=r"nonblank redis\.url"):
        await create_redis_client(enabled=True, url="  ")
