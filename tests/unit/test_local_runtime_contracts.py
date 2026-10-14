"""Contracts for explicit local, mock, and disabled behavior."""

from __future__ import annotations

import builtins
import sys
import types
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import pytest
import yaml

import perceivo
from perceivo.config import Config, FilesConfig, MocksConfig, RedisConfig, SecretsConfig
from perceivo.local.adapters import secrets as secrets_adapter
from perceivo.local.adapters._safe_path import PathTraversalError
from perceivo.local.adapters.disabled import DisabledFilesService, DisabledOrgFilesService
from perceivo.local.adapters.redis import (
    InMemoryRedis,
    _RealRedisAdapter,
    create_redis_client,
)
from perceivo.local.context import LocalExtensionManager
from perceivo.local.mocks.seed import DEFAULT_SEED, load_seed
from perceivo.types.errors import ConfigurationError, ServiceError


def _config(tmp_path: Path, service: str, mode: str = "disabled") -> Config:
    return Config(
        files=FilesConfig(
            working_dir=str(tmp_path / "working"),
            org_dir=str(tmp_path / "org"),
        ),
        secrets=SecretsConfig(
            source="dotenv",
            dotenv_path=str(tmp_path / "must-not-be-read.env"),
        ),
        mocks=MocksConfig(modes={service: mode}),
    )


@pytest.mark.parametrize(
    "service",
    [
        "llm",
        "redis",
        "milvus",
        "elasticsearch",
        "database",
        "secrets",
        "mcp",
        "chat",
        "inference",
        "capabilities",
    ],
)
async def test_disabled_manager_services_raise_before_dispatch(
    tmp_path: Path, service: str
) -> None:
    ext = LocalExtensionManager(_config(tmp_path, service))
    call: Awaitable[object]

    if service == "llm":
        call = ext.invoke_ai_foundry(model="configured", messages=[])
    elif service == "redis":
        call = ext.get_redis()
    elif service == "milvus":
        call = ext.get_milvus()
    elif service == "elasticsearch":
        call = ext.get_elasticsearch()
    elif service == "database":
        call = ext.resolve_platform_database("database-id")
    elif service == "secrets":
        call = ext.fetch_secret_value("secret-id")
    elif service == "mcp":
        call = ext.get_mcp_client()
    elif service == "inference":
        call = ext.predict_inference(intelligence_id="i", features={}, timeout_seconds=1)
    elif service == "capabilities":
        call = ext.list_capabilities()
    else:
        call = ext.send_chat_message(channel="test", recipient="test", message="test")

    with pytest.raises(ConfigurationError) as raised:
        await call

    message = str(raised.value)
    assert service in message
    assert "disabled" in message
    assert "'mock'" in message
    if service not in {"mcp", "chat", "inference", "capabilities"}:
        assert "'local'" in message
    assert ext._mocks.trace.calls == []


async def test_every_disabled_file_operation_raises_without_creating_roots(tmp_path: Path) -> None:
    working_root = tmp_path / "working"
    org_root = tmp_path / "org"
    ext = LocalExtensionManager(_config(tmp_path, "files"))

    assert isinstance(ext.files, DisabledFilesService)
    assert isinstance(ext.org_files, DisabledOrgFilesService)
    assert not working_root.exists()
    assert not org_root.exists()

    operations = [
        ext.files.read("a.txt"),
        ext.files.write("data", "a.txt"),
        ext.files.list_blobs("working", ""),
        ext.files.read_blob("working", "a.txt"),
        ext.org_files.list(),
        ext.org_files.read("id"),
        ext.org_files.read_text("id"),
        ext.org_files.write("a.txt", "data"),
        ext.org_files.write_text("a.txt", "data"),
        ext.org_files.create_folder("folder"),
        ext.org_files.rename("id", "new.txt"),
        ext.org_files.delete("id"),
        ext.org_files.move("id", "parent"),
        ext.org_files.find("a.txt"),
    ]
    for operation in operations:
        with pytest.raises(ConfigurationError, match=r"files.*disabled"):
            await operation

    assert not working_root.exists()
    assert not org_root.exists()


def test_disabled_secrets_constructor_does_not_read_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_read(path: str) -> dict[str, str]:
        raise AssertionError(f"dotenv was read from {path}")

    monkeypatch.setattr(secrets_adapter, "_load_dotenv", forbidden_read)
    ext = LocalExtensionManager(_config(tmp_path, "secrets"))

    with pytest.raises(ConfigurationError, match=r"secrets.*disabled"):
        ext.get_node_secret_info()
    with pytest.raises(ConfigurationError, match=r"secrets.*disabled"):
        ext._secrets_service.get_all_keys()


@pytest.mark.asyncio
async def test_public_working_files_share_one_root_and_preserve_container_rules(
    tmp_path: Path,
) -> None:
    ext = LocalExtensionManager(_config(tmp_path, "files", "local"))
    ext.activate()
    try:
        await perceivo.files.working.write("nested/a.txt", "a")
        await perceivo.files.working.write("nested/b.txt", "b")

        assert await perceivo.files.working.read("nested/a.txt") == "a"
        page = await perceivo.files.working.list(prefix="nested/", limit=1)
        assert [entry.name for entry in page] == ["nested/a.txt"]
        assert await ext.files.read_blob("working", "nested/b.txt") == "b"

        await ext.files.write("platform", "platform/item.txt")
        platform = await ext.files.list_blobs("platform", "", limit=10)
        assert [entry["name"] for entry in platform] == ["item.txt"]
        with pytest.raises(PathTraversalError):
            await ext.files.list_blobs("../outside", "", limit=10)
    finally:
        ext.deactivate()


@pytest.mark.asyncio
async def test_explicit_mock_redis_keeps_seeded_and_direct_in_memory_behavior(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, "redis", "mock")
    config.mocks.seed = {"redis": {"keys": {"seeded": "ready"}}}
    ext = LocalExtensionManager(config)

    client = await ext.get_redis()

    assert isinstance(client, InMemoryRedis)
    assert await client.get("seeded") == b"ready"
    assert await client.set("direct", "value") is True
    assert await client.get("direct") == b"value"


class _DriverConnectionError(Exception):
    pass


class _DriverAuthenticationError(_DriverConnectionError):
    pass


class _DriverTimeoutError(Exception):
    pass


class _RedisClient:
    def __init__(self, error: Exception | None = None, cleanup_error: bool = False) -> None:
        self.error = error
        self.cleanup_error = cleanup_error
        self.closed = 0

    async def ping(self) -> bool:
        if self.error is not None:
            raise self.error
        return True

    async def aclose(self) -> None:
        self.closed += 1
        if self.cleanup_error:
            raise RuntimeError("cleanup detail must not replace the ping error")


def _install_redis_driver(
    monkeypatch: pytest.MonkeyPatch,
    client: _RedisClient,
) -> dict[str, object]:
    captured: dict[str, object] = {}
    redis_module = types.ModuleType("redis")
    asyncio_module = types.ModuleType("redis.asyncio")

    def from_url(url: str, *, decode_responses: bool) -> _RedisClient:
        captured.update(url=url, decode_responses=decode_responses)
        return client

    vars(redis_module)["__path__"] = []
    vars(asyncio_module)["from_url"] = from_url
    vars(redis_module)["asyncio"] = asyncio_module
    vars(redis_module)["exceptions"] = types.SimpleNamespace(
        AuthenticationError=_DriverAuthenticationError,
        ConnectionError=_DriverConnectionError,
        TimeoutError=_DriverTimeoutError,
    )
    monkeypatch.setitem(sys.modules, "redis", redis_module)
    monkeypatch.setitem(sys.modules, "redis.asyncio", asyncio_module)
    return captured


@pytest.mark.asyncio
async def test_local_redis_returns_the_exact_pinged_driver_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The return value wraps the pinged client so ``ttl_seconds`` is
    honoured (see ``test_local_redis_real_adapter.py``); the wrapper forwards
    to this exact client instance rather than replacing it.
    """
    client = _RedisClient()
    captured = _install_redis_driver(monkeypatch, client)

    result = await create_redis_client(enabled=True, url="redis://configured")

    assert isinstance(result, _RealRedisAdapter)
    assert result._client is client
    assert captured == {"url": "redis://configured", "decode_responses": False}
    assert client.closed == 0


@pytest.mark.parametrize(
    ("error", "retryable", "message"),
    [
        (_DriverAuthenticationError("credential detail"), False, "authentication failed"),
        (_DriverConnectionError("socket detail"), True, "could not connect"),
        (_DriverTimeoutError("timeout detail"), True, "could not connect"),
        (RuntimeError("driver detail"), False, "initialization failed"),
    ],
)
@pytest.mark.asyncio
async def test_local_redis_failures_are_typed_sanitized_and_close_the_client(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    retryable: bool,
    message: str,
) -> None:
    client = _RedisClient(error, cleanup_error=True)
    _install_redis_driver(monkeypatch, client)
    credential_url = "redis://user:top-secret@host:6379"

    with pytest.raises(ServiceError, match=message) as raised:
        await create_redis_client(enabled=True, url=credential_url)

    assert raised.value.retryable is retryable
    assert raised.value.__cause__ is error
    assert credential_url not in str(raised.value)
    assert str(error) not in str(raised.value)
    assert client.closed == 1


@pytest.mark.asyncio
async def test_local_redis_missing_dependency_is_an_actionable_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in {"redis", "redis.asyncio"}:
            raise ImportError("redis intentionally absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(ConfigurationError) as raised:
        await create_redis_client(enabled=True, url="redis://configured")

    assert "perceivo[redis]" in str(raised.value)
    assert "mocks.modes.redis" in str(raised.value)
    assert isinstance(raised.value.__cause__, ImportError)


@pytest.mark.asyncio
async def test_failed_manager_redis_initialization_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, "redis", "local")
    config.redis = RedisConfig(enabled=True, url="redis://configured")
    ext = LocalExtensionManager(config)
    client = _RedisClient()
    attempts = 0

    async def create(*, enabled: bool, url: str) -> _RedisClient:
        nonlocal attempts
        attempts += 1
        assert enabled is True
        assert url == "redis://configured"
        if attempts == 1:
            raise ServiceError("first failure", retryable=True)
        return client

    import perceivo.local.adapters.redis as redis_adapter

    monkeypatch.setattr(redis_adapter, "create_redis_client", create)

    with pytest.raises(ServiceError, match="first failure"):
        await ext.get_redis()
    assert ext._redis_initialized is False
    assert await ext.get_redis() is client
    assert ext._redis_initialized is True
    assert attempts == 2


def _write_seed(path: Path, document: object) -> None:
    path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")


@pytest.mark.parametrize(
    "document",
    [
        {"mocks": {"mcp": {"calls": {"legacy": {"shape": "mocks"}}}}},
        {"mcp": {"calls": {"legacy": {"shape": "bare"}}}},
    ],
)
def test_seed_loader_refuses_the_retired_shapes(
    tmp_path: Path, document: dict[str, object]
) -> None:
    """Only ``{mocks: {seed: ...}}`` is a seed file; the older shapes raise."""
    seed_file = tmp_path / "seed.yaml"
    _write_seed(seed_file, document)

    with pytest.raises(ConfigurationError, match="perceivo mock init"):
        load_seed(MocksConfig(seed_file=str(seed_file)))


def test_nested_seed_file_wins_over_inline_and_preserves_defaults(tmp_path: Path) -> None:
    seed_file = tmp_path / "seed.yaml"
    _write_seed(
        seed_file,
        {"mocks": {"seed": {"mcp": {"calls": {"same": {"source": "file"}}}}}},
    )
    config = MocksConfig(
        seed={"mcp": {"calls": {"same": {"source": "inline"}, "inline": {"ok": True}}}},
        seed_file=str(seed_file),
    )

    loaded = load_seed(config)

    assert loaded["mcp"]["calls"]["same"] == {"source": "file"}
    assert loaded["mcp"]["calls"]["inline"] == {"ok": True}
    assert loaded["mcp"]["call"] == DEFAULT_SEED["mcp"]["call"]
    assert loaded["chat"] == DEFAULT_SEED["chat"]


@pytest.mark.parametrize("document", [["not", "a", "mapping"], {"mocks": "not-a-mapping"}])
def test_malformed_seed_shapes_raise(tmp_path: Path, document: object) -> None:
    """A seed file that would contribute nothing is an error, not a no-op."""
    seed_file = tmp_path / "seed.yaml"
    _write_seed(seed_file, document)

    with pytest.raises(ConfigurationError):
        load_seed(MocksConfig(seed_file=str(seed_file)))


def test_seed_yaml_parser_errors_still_propagate(tmp_path: Path) -> None:
    seed_file = tmp_path / "seed.yaml"
    seed_file.write_text("mocks: [unterminated", encoding="utf-8")
    config = MocksConfig(seed_file=str(seed_file))

    with pytest.raises(yaml.YAMLError):
        load_seed(config)
