"""LocalExtensionManager: the extension manager for local runs.

Implements the same interface as the hosted extension manager, so the SDK
modules (secrets, files, log, flow, http, infra and the rest) run in local mode
without code changes. A local run has no conversation channel, so
``perceivo.stream`` behaves as it does for a hosted run without one.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional

from .._internal.config_paths import file_roots as _file_roots
from .._internal.config_paths import trash_root as _trash_root
from ..config import Config, load
from . import _modes
from ._sql_read_only import refuse_write_in_external_read_only, refuse_write_in_read_only
from .adapters.disabled import (
    DisabledFilesService,
    DisabledOrgFilesService,
    DisabledSecretsService,
    require_enabled,
)
from .adapters.files import LocalFilesService
from .adapters.infra import LocalInfraStubs
from .adapters.logging import LocalLoggingService
from .adapters.org_files import LocalOrgFilesService
from .adapters.secrets import LocalSecretsService
from .adapters.stream import LocalStream
from .mocks.registry import MockRegistry

if TYPE_CHECKING:
    from .adapters.ai_foundry import LocalAIFoundryAdapter

logger = logging.getLogger("perceivo.local")


class LocalExtensionManager(LocalStream, LocalInfraStubs):
    """Local development implementation of the ExtensionManager interface.

    This class provides the same attributes and methods that SDK modules
    expect from ``_ExecutionContext.get_ext()``, but backed by local
    adapters (filesystem, .env, console, etc.) instead of cloud services.

    Usage::

        from perceivo.local.context import LocalExtensionManager

        ext = LocalExtensionManager.from_config("perceivo.yaml")
        ext.activate()

        # Now all perceivo.* calls work locally
        perceivo.log.info("Hello from local mode!")
        secret = await perceivo.secrets.get("my_key")

        ext.deactivate()
    """

    def __init__(self, config: Config):
        # Apply every perceivo.local.set_mode call made before this run existed.
        _modes.apply_overrides(config)
        self._config = config

        # Mock is the default; a service leaves it when its native section is enabled
        # or it is overridden in perceivo.yaml (mocks.modes).
        self._mocks = MockRegistry(config)

        self.logging = LocalLoggingService(
            level=config.logging.level,
            fmt=config.logging.format,
        )
        self.files: LocalFilesService | DisabledFilesService
        self.org_files: LocalOrgFilesService | DisabledOrgFilesService
        working_dir, org_dir = _file_roots(config)
        if config.service_mode("files") == "disabled":
            self.files = DisabledFilesService()
            self.org_files = DisabledOrgFilesService()
        elif config.is_mocked("files"):
            from .mocks.files import MockOrgFilesService

            self.files = LocalFilesService(working_dir=working_dir)
            self.org_files = MockOrgFilesService(
                org_dir, self._mocks.seed_for("files"), trash_dir=_trash_root(config)
            )
        else:
            self.files = LocalFilesService(working_dir=working_dir)
            self.org_files = LocalOrgFilesService(org_dir=org_dir, trash_dir=_trash_root(config))

        self._secrets_service: LocalSecretsService | DisabledSecretsService
        if config.service_mode("secrets") == "disabled":
            self._secrets_service = DisabledSecretsService()
        else:
            self._secrets_service = LocalSecretsService(
                source=config.secrets.source,
                dotenv_path=config.secrets.dotenv_path,
                inline=config.secrets.inline,
            )

        self._node_env: ContextVar[dict[str, Any] | None] = ContextVar(
            f"perceivo_node_env_{id(self)}", default=None
        )
        self._node_secret_info: ContextVar[dict[str, Any] | None] = ContextVar(
            f"perceivo_node_secrets_{id(self)}", default=None
        )

        self._invoke_port_callback: ContextVar[
            Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]]
        ] = ContextVar(f"perceivo_invoke_callback_{id(self)}", default=None)
        self._invoke_port_parallel_callback: ContextVar[
            Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]]
        ] = ContextVar(f"perceivo_parallel_callback_{id(self)}", default=None)

        self._redis_client: Any = None
        self._redis_initialized = False

        self._milvus_client: Any = None
        self._milvus_initialized = False

        self._es_client: Any = None
        self._es_initialized = False

    @classmethod
    def from_config(cls, config_path: Optional[str] = None) -> LocalExtensionManager:
        """Create a LocalExtensionManager from a YAML config file.

        Args:
            config_path: Path to perceivo.yaml. If None, uses default
                resolution (PERCEIVO_CONFIG env var, then ./perceivo.yaml).
        """
        config = load(config_path)
        return cls(config)

    def activate(self) -> None:
        """Push this context onto the SDK execution stack."""
        from .._internal.context import _ExecutionContext

        _ExecutionContext.set_ext(self)
        self._mocks.install_http()
        logger.info("perceivo SDK connected (local development mode)")

    def deactivate(self) -> None:
        """Pop this context from the SDK execution stack."""
        from .._internal.context import _ExecutionContext

        self._mocks.uninstall_http()
        # Publish this run's trace so perceivo.local.trace() still answers after the run.
        self._mocks.trace.publish()
        _ExecutionContext.clear()
        logger.info("perceivo SDK disconnected")

    def get_node_secret_info(self) -> dict[str, Any]:
        require_enabled(self._mocks, "secrets")
        return self._node_secret_info.get() or {"declarations": {}, "aliases": {}}

    def set_node_secret_info(
        self,
        declarations: dict[str, Any],
        aliases: dict[str, str],
    ) -> Token[dict[str, Any] | None]:
        return self._node_secret_info.set({"declarations": declarations, "aliases": aliases})

    def reset_node_secret_info(self, token: Token[dict[str, Any] | None]) -> None:
        """Restore the secret context that preceded a scoped node execution."""
        self._node_secret_info.reset(token)

    async def fetch_secret_value(self, secret_id: str) -> Optional[str]:
        require_enabled(self._mocks, "secrets")
        value = await self._secrets_service.fetch_secret_value(secret_id)
        if value is not None:
            return value
        # Under mock mode an unbound secret gets a canned value; a real value in .env or
        # the environment always wins.
        if self._mocks.is_mock("secrets"):
            mock_value = self._mocks.secret(secret_id)
            if mock_value is not None:
                self._mocks.record("secrets", "get", secret_id)
            return mock_value
        return None

    def get_node_env(self) -> dict[str, Any]:
        env = self._node_env.get() or {"values": {}, "declarations": {}}
        # When the LLM is mocked, rewrite a bring-your-own-key ``provider`` to "managed"
        # so the call goes through perceivo.managed_service.ai and reaches the mock.
        if self._mocks.is_mock("llm"):
            values = env.get("values") or {}
            if "provider" in values:
                forced = self._mocks.force_managed_provider("provider", values["provider"])
                if forced != values["provider"]:
                    values = {**values, "provider": forced}
                    env = {**env, "values": values}
        return env

    def set_node_env(
        self,
        values: dict[str, Any],
        declarations: dict[str, Any],
    ) -> Token[dict[str, Any] | None]:
        return self._node_env.set({"values": values, "declarations": declarations})

    def reset_node_env(self, token: Token[dict[str, Any] | None]) -> None:
        """Restore the environment context that preceded a scoped node execution."""
        self._node_env.reset(token)

    async def invoke_port(
        self,
        port_name: str,
        request_data: Optional[dict[str, Any]] = None,
        artifact: Any = None,
        artifacts: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        callback = self._invoke_port_callback.get()
        if callback is None:
            raise RuntimeError(
                "perceivo.flow.invoke() is not available outside the runner. "
                "Use 'perceivo run orchestration .' to execute orchestrations."
            )
        return await callback(
            port_name=port_name,
            request_data=request_data or {},
            artifact=artifact,
            artifacts=artifacts or {},
        )

    async def invoke_port_parallel(
        self,
        port_names: list[str],
        request_data: Optional[dict[str, Any]] = None,
        artifact: Any = None,
        artifacts: Optional[dict[str, Any]] = None,
        timeout_seconds: int = 300,
        failure_strategy: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        callback = self._invoke_port_parallel_callback.get()
        if callback is None:
            raise RuntimeError(
                "perceivo.flow.invoke_parallel() is not available outside the runner. "
                "Use 'perceivo run orchestration .' to execute orchestrations."
            )
        # Forward failure_strategy so local runs honour the same fail-closed and
        # ignore_failures semantics as hosted runs.
        return await callback(
            port_names=port_names,
            request_data=request_data or {},
            artifact=artifact,
            artifacts=artifacts or {},
            timeout_seconds=timeout_seconds,
            failure_strategy=failure_strategy,
        )

    async def get_redis(self) -> Any:
        require_enabled(self._mocks, "redis")
        if not self._redis_initialized:
            from .adapters.redis import InMemoryRedis, create_redis_client

            if self._mocks.is_mock("redis"):
                # The in-memory store IS the redis mock; seed it from mock data.
                client = InMemoryRedis()
                for key, value in self._mocks.redis_seed().items():
                    await client.set(key, value)
                self._mocks.record("redis", "get_redis", "in-memory mock store")
                self._redis_client = client
            else:
                self._redis_client = await create_redis_client(
                    enabled=self._config.redis.enabled,
                    url=self._config.redis.url,
                )
            self._redis_initialized = True
        return self._redis_client

    def set_invoke_port_callback(
        self,
        callback: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    ) -> Token[Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]]]:
        """Set the callback for perceivo.flow.invoke()."""
        return self._invoke_port_callback.set(callback)

    def reset_invoke_port_callback(
        self,
        token: Token[Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]]],
    ) -> None:
        self._invoke_port_callback.reset(token)

    def set_invoke_port_parallel_callback(
        self,
        callback: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    ) -> Token[Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]]]:
        """Set the callback for perceivo.flow.invoke_parallel()."""
        return self._invoke_port_parallel_callback.set(callback)

    def reset_invoke_port_parallel_callback(
        self,
        token: Token[Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]]],
    ) -> None:
        self._invoke_port_parallel_callback.reset(token)

    # Each override serves the mock adapter under ``mock`` mode and otherwise falls
    # through to the native LocalInfraStubs behaviour.

    # AI records mock and real attempts, before dispatch, so a failed call still
    # records its mode. Other services record mock calls only.

    def _ai_foundry_adapter(self) -> LocalAIFoundryAdapter:
        """Build the real-provider adapter with a resolver that cannot invent a key.

        Under mocked secrets, :meth:`fetch_secret_value` answers every alias with a
        canned value. That value must never become an ``Authorization`` header sent to
        a third party, so this resolver consults the real secrets service only: an
        absent key stays absent, and the adapter raises with the places to put one.
        """
        from .adapters.ai_foundry import LocalAIFoundryAdapter

        return LocalAIFoundryAdapter(
            getattr(self._config, "ai_foundry", None),
            secret_resolver=self._secrets_service.fetch_secret_value,
        )

    async def invoke_ai_foundry(self, **kwargs: Any) -> dict[str, Any]:
        require_enabled(self._mocks, "llm")
        self._mocks.record("llm", "invoke", f"model={kwargs.get('model', '?')}")
        if self._mocks.is_mock("llm"):
            return await self._mocks.llm.invoke(**kwargs)
        return await super().invoke_ai_foundry(**kwargs)

    async def embed_ai_foundry(self, **kwargs: Any) -> dict[str, Any]:
        require_enabled(self._mocks, "llm")
        self._mocks.record("llm", "embed", f"model={kwargs.get('model', '?')}")
        if self._mocks.is_mock("llm"):
            return await self._mocks.llm.embed(**kwargs)
        return await super().embed_ai_foundry(**kwargs)

    async def list_ai_foundry_models(self) -> list[Any]:
        require_enabled(self._mocks, "llm")
        self._mocks.record("llm", "list_models")
        if self._mocks.is_mock("llm"):
            return await self._mocks.llm.list_models()
        return await super().list_ai_foundry_models()

    async def get_milvus(self) -> Any:
        require_enabled(self._mocks, "milvus")
        if self._mocks.is_mock("milvus"):
            self._mocks.record("milvus", "get_milvus", "mock vector store")
            return self._mocks.milvus
        return await super().get_milvus()

    async def get_elasticsearch(self) -> Any:
        require_enabled(self._mocks, "elasticsearch")
        if self._mocks.is_mock("elasticsearch"):
            self._mocks.record("elasticsearch", "get_elasticsearch", "mock search index")
            return self._mocks.elasticsearch
        return await super().get_elasticsearch()

    async def resolve_platform_database(
        self, database_id: str, required_privilege: str = "DATABASE_QUERY"
    ) -> dict[str, Any]:
        require_enabled(self._mocks, "database")
        if self._mocks.is_mock("database"):
            self._mocks.record("database", "resolve", database_id)
            return await self._mocks.database.resolve(database_id, required_privilege)
        return await super().resolve_platform_database(database_id, required_privilege)

    async def execute_database_query(
        self,
        database_id: str,
        sql: str,
        read_only: bool = True,
        max_rows: int = 1000,
        # The base class calls back with five positional arguments, including ``params``.
        # The mock arm drops ``params``: MockDatabase parses literals and has no bind step.
        params: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        require_enabled(self._mocks, "database")
        if self._mocks.is_mock("database"):
            self._mocks.record("database", "query", (sql or "")[:60])
            if read_only:
                refuse_write_in_read_only(database_id, sql)
            return await self._mocks.database.query(database_id, sql, read_only, max_rows)
        return await super().execute_database_query(database_id, sql, read_only, max_rows, params)

    async def execute_external_database_query(
        self,
        connection_id: str,
        sql: str,
        params: Optional[list[Any]] = None,
        read_only: bool = True,
        max_rows: int = 1000,
    ) -> dict[str, Any]:
        require_enabled(self._mocks, "database")
        if self._mocks.is_mock("database"):
            self._mocks.record("database", "external_query", (sql or "")[:60])
            if read_only:
                refuse_write_in_external_read_only(connection_id, sql)
            return await self._mocks.database.query(connection_id, sql, read_only, max_rows)
        return await super().execute_external_database_query(
            connection_id, sql, params, read_only, max_rows
        )

    async def is_mcp_available(self) -> bool:
        if self._mocks.mode("mcp") == "disabled":
            return False
        if self._mocks.is_mock("mcp"):
            return True
        return await super().is_mcp_available()

    async def get_mcp_client(self) -> Any:
        require_enabled(self._mocks, "mcp")
        if self._mocks.is_mock("mcp"):
            self._mocks.record("mcp", "get_mcp_client", "mock MCP server")
            return self._mocks.mcp
        return await super().get_mcp_client()

    # Capability reads have no local store: the service is ``mock`` or ``disabled``,
    # so once ``require_enabled`` passes the seeded mock answers.

    async def list_capabilities(self) -> list[dict[str, Any]]:
        require_enabled(self._mocks, "capabilities")
        self._mocks.record("capabilities", "list", "mock capability catalog")
        return await self._mocks.capabilities.list_capabilities()

    async def list_execution_graphs(self, capability_id: str) -> list[dict[str, Any]]:
        require_enabled(self._mocks, "capabilities")
        self._mocks.record("capabilities", "graphs", str(capability_id))
        return await self._mocks.capabilities.list_execution_graphs(capability_id)

    async def get_execution_graph(
        self, *, graph_id: str, capability_id: str
    ) -> Optional[dict[str, Any]]:
        require_enabled(self._mocks, "capabilities")
        self._mocks.record("capabilities", "graph", f"{capability_id}/{graph_id}")
        return await self._mocks.capabilities.get_execution_graph(
            graph_id=graph_id, capability_id=capability_id
        )

    async def send_chat_message(self, **kwargs: Any) -> dict[str, Any]:
        require_enabled(self._mocks, "chat")
        if self._mocks.is_mock("chat"):
            # The recipient is not recorded: it can be an org secret, and ``mock_calls`` is
            # serialized into run records. Its length shows whether it resolved.
            recipient = str(kwargs.get("recipient") or "")
            target = f"<recipient {len(recipient)} chars>" if recipient else "<unset>"
            self._mocks.record("chat", "send", f"{kwargs.get('channel', '?')} -> {target}")
            # A constant ``delivery_id`` keeps two runs of an unchanged bundle identical.
            return {
                "delivery_id": "mock-delivery",
                "channel": kwargs.get("channel", "unknown"),
                "status": self._mocks.chat_status(),
                "error": self._mocks.chat_error(),
                "timestamp": "local",
            }
        return await super().send_chat_message(**kwargs)
