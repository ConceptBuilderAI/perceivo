"""Unit tests for LocalExtensionManager wiring and config loading."""

from __future__ import annotations

import inspect
import sys
import types

import pytest

from perceivo._internal.context import _ExecutionContext
from perceivo.config import (
    Config,
    DatabaseConfig,
    FilesConfig,
    MocksConfig,
    SecretsConfig,
    load,
)
from perceivo.local.adapters.infra import LocalInfraStubs
from perceivo.local.context import LocalExtensionManager


@pytest.fixture
def manager(tmp_path):
    cfg = Config(
        files=FilesConfig(working_dir=str(tmp_path / "wf"), org_dir=str(tmp_path / "org")),
        secrets=SecretsConfig(source="inline", inline={"TOKEN": "secret"}),
    )
    ext = LocalExtensionManager(cfg)
    yield ext
    _ExecutionContext.clear_all()


def test_manager_wires_core_services(manager):
    assert manager.logging is not None
    assert manager.files is not None
    assert manager.org_files is not None
    # No `manager.storage`: there is no `perceivo.storage` package, so the
    # local extension manager carries no attribute for it either.
    assert not hasattr(manager, "storage")


def test_activate_deactivate_pushes_and_pops(manager):
    manager.activate()
    assert _ExecutionContext.get_ext() is manager
    manager.deactivate()
    with pytest.raises(RuntimeError):
        _ExecutionContext.get_ext()


async def test_fetch_secret_value_via_manager(manager):
    assert await manager.fetch_secret_value("TOKEN") == "secret"


async def test_an_unbound_secret_id_reaches_the_mock_fallback(manager):
    """The point of the adapter's ``None`` guard: this fallback becomes reachable.

    ``LocalExtensionManager.fetch_secret_value`` intends to fall back to a
    canned mock value when the adapter has nothing (``context.py:169-173``), so
    a secret-bound node does not hard-fail on an unbound secret. For a ``None``
    id it could never do so: ``LocalSecretsService.fetch_secret_value`` reached
    ``os.environ.get(None)`` and raised ``TypeError`` one frame earlier, so the
    ``if value is not None`` test on the next line never executed.

    This asserts the fallback *ran*, not merely that nothing raised — a test
    that only asserted "no exception" would also pass against a version that
    returned ``None`` and left the node dead. The value is the shipped seed's
    ``secrets.default``, which is the string ``"mock-secret"``: a MOCKED value,
    not a credential, and no credential appears anywhere in this test.
    """
    assert manager._mocks.is_mock("secrets") is True

    value = await manager.fetch_secret_value(None)

    assert value == "mock-secret"
    served = [
        c for c in manager._mocks.trace.calls if c.service == "secrets" and c.operation == "get"
    ]
    assert len(served) == 1, "the mock fallback did not record serving the call"
    assert served[0].mode == "mock"


async def test_an_unbound_secret_id_outside_mock_mode_yields_nothing_and_records_nothing(
    tmp_path,
):
    """Outside mock mode an unbound secret produces no value and no trace entry.

    This is the half of the guard's contract that matters. The adapter answers
    "not found here" for every source; what stops that from becoming a
    fabricated credential is that the substitution one frame up is gated on
    ``self._mocks.is_mock("secrets")`` and recorded when it fires
    (``context.py:169-173``). Both halves are asserted here in the negative:
    nothing comes back, and nothing in the trace claims a secret was served.

    An unrecorded mock secret is the failure this pins. A run whose trace shows
    no ``secrets``/``get`` call reads as a run that used real credentials, so a
    mock value delivered without a record is worse than a hard failure — the
    node succeeds and the evidence says it succeeded honestly.

    Mock mode is turned off the documented way, ``modes={"secrets": "local"}``
    (``config.py:145-153``), rather than by reaching into the registry, so the
    test fails if that opt-in ever stops resolving.
    """
    cfg = Config(
        files=FilesConfig(working_dir=str(tmp_path / "wf"), org_dir=str(tmp_path / "org")),
        secrets=SecretsConfig(source="inline", inline={"TOKEN": "secret"}),
        mocks=MocksConfig(modes={"secrets": "local"}),
    )
    ext = LocalExtensionManager(cfg)
    try:
        assert ext._mocks.is_mock("secrets") is False

        assert await ext.fetch_secret_value(None) is None
        assert await ext.fetch_secret_value("NOT_BOUND_ANYWHERE_XYZ") is None

        assert [c for c in ext._mocks.trace.calls if c.service == "secrets"] == []
    finally:
        _ExecutionContext.clear_all()


async def test_a_real_value_still_wins_over_the_mock_fallback(manager):
    """The guard must not turn every lookup into a mock.

    Pins the ordering the fallback depends on: the adapter is consulted first
    and a value it returns is handed back untouched, with nothing recorded
    against the mock trace.
    """
    assert await manager.fetch_secret_value("TOKEN") == "secret"
    assert [c for c in manager._mocks.trace.calls if c.service == "secrets"] == []


def test_node_env_get_set(manager):
    manager.set_node_env(values={"a": 1}, declarations={"a": {"type": "int"}})
    env = manager.get_node_env()
    assert env["values"] == {"a": 1}
    assert env["declarations"]["a"]["type"] == "int"


def test_node_secret_info_get_set(manager):
    manager.set_node_secret_info(declarations={"s": {}}, aliases={"s": "s"})
    info = manager.get_node_secret_info()
    assert info["aliases"] == {"s": "s"}


async def test_invoke_port_without_callback_raises(manager):
    with pytest.raises(RuntimeError, match="not available outside the runner"):
        await manager.invoke_port("p")
    with pytest.raises(RuntimeError, match="not available outside the runner"):
        await manager.invoke_port_parallel(["p"])


async def test_invoke_port_callbacks_are_used(manager):
    async def cb(**kwargs):
        return {"echo": kwargs["port_name"]}

    async def cb_par(**kwargs):
        return {p: {"ok": True} for p in kwargs["port_names"]}

    manager.set_invoke_port_callback(cb)
    manager.set_invoke_port_parallel_callback(cb_par)
    assert (await manager.invoke_port("downstream"))["echo"] == "downstream"
    par = await manager.invoke_port_parallel(["a", "b"])
    assert par == {"a": {"ok": True}, "b": {"ok": True}}


async def test_get_redis_lazy_inmemory(manager):
    from perceivo.local.adapters.redis import InMemoryRedis

    client = await manager.get_redis()
    assert isinstance(client, InMemoryRedis)
    # Second call returns the cached client.
    assert await manager.get_redis() is client


def test_from_config_uses_loader(tmp_path, monkeypatch):
    cfg_file = tmp_path / "perceivo.yaml"
    cfg_file.write_text(
        "version: 1\nfiles:\n  working_dir: ./wf\nlogging:\n  level: DEBUG\n",
        encoding="utf-8",
    )
    ext = LocalExtensionManager.from_config(str(cfg_file))
    assert ext._config.logging.level == "DEBUG"


# ---------------------------------------------------------------------------
# config loader
# ---------------------------------------------------------------------------


def test_load_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PERCEIVO_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = load()
    assert cfg.version == 1
    assert cfg.redis.enabled is False


def test_load_parses_databases_and_redis(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "redis:\n  enabled: true\n  url: redis://h:1\n"
        "databases:\n  - id: db1\n    url: postgresql://x\n    read_only: false\n",
        encoding="utf-8",
    )
    cfg = load(str(cfg_file))
    assert cfg.redis.enabled is True
    assert cfg.databases[0].id == "db1"
    assert cfg.databases[0].read_only is False


def test_load_env_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERCEIVO_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PERCEIVO_REDIS_ENABLED", "true")
    cfg = load()
    assert cfg.logging.level == "WARNING"
    assert cfg.redis.enabled is True


def test_load_via_env_path(tmp_path, monkeypatch):
    cfg_file = tmp_path / "custom.yaml"
    cfg_file.write_text("milvus:\n  enabled: true\n  uri: http://m:1\n", encoding="utf-8")
    monkeypatch.setenv("PERCEIVO_CONFIG", str(cfg_file))
    cfg = load()
    assert cfg.milvus.enabled is True
    assert cfg.milvus.uri == "http://m:1"


# ---------------------------------------------------------------------------
# python:S2638 — the override must be callable with the parameters
# the superclass is callable with. `LocalInfraStubs.execute_external_database_query`
# re-enters `self.execute_database_query` with FIVE positional arguments, so an
# override that dropped `params` would turn every parameterised external query
# in local non-mock mode into `TypeError: takes from 3 to 5 positional arguments
# but 6 were given`. Both tests below fail on such a signature.
# ---------------------------------------------------------------------------
def test_execute_database_query_override_is_call_compatible_with_its_base():
    """Liskov, checked by binding rather than by eye."""
    base = inspect.signature(LocalInfraStubs.execute_database_query)
    override = inspect.signature(LocalExtensionManager.execute_database_query)
    assert list(base.parameters) == list(override.parameters)
    # The exact call adapters/infra.py makes. `bind` raises the same TypeError
    # the call would, so this is the defect and not a proxy for it.
    override.bind(object(), "db1", "SELECT $1", True, 1000, ["x"])


@pytest.mark.asyncio
async def test_external_query_with_bind_params_reaches_the_driver(tmp_path, monkeypatch):
    """End to end through the parameterised dispatch, with a fake driver.

    Non-mock arm on purpose: the mock arm returns before reaching the call.
    """
    captured: dict[str, object] = {}

    class _Conn:
        async def fetch(self, sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        async def close(self):
            captured["closed"] = True

    async def _connect(url):
        captured["url"] = url
        return _Conn()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=_connect))

    cfg = Config(
        files=FilesConfig(working_dir=str(tmp_path / "wf"), org_dir=str(tmp_path / "org")),
        databases=[DatabaseConfig(id="db1", url="postgresql://u@h/db", read_only=True)],
    )
    ext = LocalExtensionManager(cfg)
    try:
        result = await ext.execute_external_database_query("db1", "SELECT $1", ["x"])
    finally:
        _ExecutionContext.clear_all()

    assert result["rows"] == []
    assert captured["sql"] == "SELECT $1"
    assert captured["args"] == ("x",), "bind parameters must reach the driver"
    assert captured["closed"] is True
