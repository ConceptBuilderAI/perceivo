"""Local infrastructure stubs/adapters for services not available locally.

When a service is configured in ``perceivo.yaml`` (e.g. milvus, elasticsearch,
databases), the adapter will lazily connect using the optional dependency.
If not configured, a clear error message is raised explaining how to enable it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from .._sql_read_only import refuse_write_in_external_read_only, refuse_write_in_read_only

if TYPE_CHECKING:
    from .ai_foundry import LocalAIFoundryAdapter

logger = logging.getLogger("perceivo.local.infra")

_METRICS_UNAVAILABLE = "Metrics are not available in local development mode."


class LocalInfraStubs:
    """Infrastructure adapters for local development mode.

    Milvus, Elasticsearch, and external databases are optionally available
    when configured in ``perceivo.yaml``.  AI Foundry, MCP, and metrics
    remain cloud-only and always raise helpful errors.
    """

    _milvus_initialized: bool
    _es_initialized: bool

    async def get_milvus(self) -> Any:
        if not self._milvus_initialized:
            config = getattr(self, "_config", None)
            if config and config.milvus.enabled:
                try:
                    from pymilvus import MilvusClient
                except ImportError:
                    raise RuntimeError(
                        "pymilvus package not installed. Install with: pip install perceivo[milvus]"
                    )
                try:
                    self._milvus_client = MilvusClient(uri=config.milvus.uri)
                    self._milvus_initialized = True
                    logger.info("Connected to Milvus at %s", config.milvus.uri)
                except Exception as e:
                    raise RuntimeError(f"Could not connect to Milvus at {config.milvus.uri}: {e}")
            else:
                raise RuntimeError(
                    "Milvus is not configured. Add a milvus section to perceivo.yaml:\n"
                    "  milvus:\n"
                    "    enabled: true\n"
                    "    uri: http://localhost:19530"
                )
        return self._milvus_client

    async def get_elasticsearch(self) -> Any:
        if not self._es_initialized:
            config = getattr(self, "_config", None)
            if config and config.elasticsearch.enabled:
                try:
                    from elasticsearch import AsyncElasticsearch
                except ImportError:
                    raise RuntimeError(
                        "elasticsearch package not installed. "
                        "Install with: pip install perceivo[elasticsearch]"
                    )
                try:
                    self._es_client = AsyncElasticsearch(config.elasticsearch.url)
                    self._es_initialized = True
                    logger.info(
                        "Connected to Elasticsearch at %s",
                        config.elasticsearch.url,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"Could not connect to Elasticsearch at {config.elasticsearch.url}: {e}"
                    )
            else:
                raise RuntimeError(
                    "Elasticsearch is not configured. Add an elasticsearch section "
                    "to perceivo.yaml:\n"
                    "  elasticsearch:\n"
                    "    enabled: true\n"
                    "    url: http://localhost:9200"
                )
        return self._es_client

    def _find_database(self, database_id: str) -> Optional[Any]:
        """Look up a database by id from the config databases list."""
        config = getattr(self, "_config", None)
        if config:
            for db in config.databases:
                if db.id == database_id:
                    return db
        return None

    async def resolve_platform_database(
        self, database_id: str, required_privilege: str = "DATABASE_QUERY"
    ) -> dict[str, Any]:
        db = self._find_database(database_id)
        if db is None:
            raise RuntimeError(
                f"Database '{database_id}' is not configured. Add it to perceivo.yaml:\n"
                "  databases:\n"
                f"    - id: {database_id}\n"
                "      url: postgresql://user:pass@localhost:5432/mydb\n"
                "      read_only: true"
            )
        return {
            "database_id": db.id,
            "url": db.url,
            "read_only": db.read_only,
            "privilege": required_privilege,
        }

    async def execute_database_query(
        self,
        database_id: str,
        sql: str,
        read_only: bool = True,
        max_rows: int = 1000,
        params: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        db = self._find_database(database_id)
        if db is None:
            raise RuntimeError(
                f"Database '{database_id}' is not configured. Add it to perceivo.yaml:\n"
                "  databases:\n"
                f"    - id: {database_id}\n"
                "      url: postgresql://user:pass@localhost:5432/mydb\n"
                "      read_only: true"
            )
        if db.read_only and not read_only:
            raise RuntimeError(
                f"Database '{database_id}' is configured as read_only. "
                "Set read_only: false in perceivo.yaml to allow writes."
            )
        if read_only:
            refuse_write_in_read_only(database_id, sql)
        try:
            import asyncpg
        except ImportError:
            raise RuntimeError(
                "asyncpg package not installed. Install with: pip install perceivo[database]"
            )
        try:
            conn = await asyncpg.connect(db.url)
            try:
                # Bind parameters go to the driver as parameters, matching hosted behaviour.
                rows = await conn.fetch(sql, *(params or []))
                columns: list[str] = []
                result_rows: list[list[Any]] = []
                for i, row in enumerate(rows):
                    if i >= max_rows:
                        break
                    if not columns:
                        columns = list(row.keys())
                    result_rows.append(list(row.values()))
                return {
                    "columns": columns,
                    "rows": result_rows,
                    "row_count": len(result_rows),
                    "truncated": len(rows) > max_rows,
                }
            finally:
                await conn.close()
        except Exception as e:
            raise RuntimeError(f"Database query failed for '{database_id}': {e}")

    async def execute_external_database_query(
        self,
        connection_id: str,
        sql: str,
        params: Optional[list[Any]] = None,
        read_only: bool = True,
        max_rows: int = 1000,
    ) -> dict[str, Any]:
        """Run an external-database query against a locally configured database.

        Hosted runs go through the platform, which owns the driver and the credential.
        Locally the connection id is looked up in ``perceivo.yaml`` like a platform
        database, so component code is identical in both modes.
        """
        db = self._find_database(connection_id)
        if db is None:
            raise RuntimeError(
                f"External database connection '{connection_id}' is not configured. "
                "Add it to perceivo.yaml:\n"
                "  databases:\n"
                f"    - id: {connection_id}\n"
                "      url: postgresql://user:pass@localhost:5432/mydb\n"
                "      read_only: true"
            )
        if read_only:
            refuse_write_in_external_read_only(connection_id, sql)
        return await self.execute_database_query(connection_id, sql, read_only, max_rows, params)

    def _ai_foundry_adapter(self) -> LocalAIFoundryAdapter:
        """Build a bring-your-own-key LLM adapter from config and local secrets.

        The adapter routes ``perceivo.managed_service.ai`` calls to a real provider
        (OpenAI, OpenAI-compatible, or Anthropic) using a developer key.
        """
        from .ai_foundry import LocalAIFoundryAdapter

        config = getattr(self, "_config", None)
        af_config = getattr(config, "ai_foundry", None)
        resolver = getattr(self, "fetch_secret_value", None)
        return LocalAIFoundryAdapter(af_config, secret_resolver=resolver)

    async def invoke_ai_foundry(self, **kwargs: Any) -> dict[str, Any]:
        return await self._ai_foundry_adapter().invoke(**kwargs)

    async def embed_ai_foundry(self, **kwargs: Any) -> dict[str, Any]:
        return await self._ai_foundry_adapter().embed(**kwargs)

    async def list_ai_foundry_models(self) -> list[Any]:
        return await self._ai_foundry_adapter().list_models()

    async def check_prompt_shield(self, **kwargs: Any) -> dict[str, Any]:
        return await self._content_safety_call("prompt_shield", **kwargs)

    async def check_groundedness(self, **kwargs: Any) -> dict[str, Any]:
        return await self._content_safety_call("groundedness", **kwargs)

    async def _content_safety_call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        """Serve Content Safety from the llm mock; there is no local real provider.

        Content Safety follows the ``llm`` mode. When mocked, the canned result is
        returned and recorded in the mock trace; otherwise the call raises rather than
        answering "safe".
        """
        from .disabled import require_enabled

        mocks = getattr(self, "_mocks", None)
        if mocks is not None:
            require_enabled(mocks, "llm")
            if mocks.is_mock("llm"):
                mocks.record("llm", operation, "content safety mock")
                return await getattr(mocks.content_safety, operation)(**kwargs)  # type: ignore[no-any-return]
        raise NotImplementedError(
            f"Content Safety ({operation}) has no local real-provider adapter. Run "
            "with the llm service mocked (the default) to get a seeded verdict. "
            "Nothing was screened -- this is not a verdict of safe."
        )

    async def predict_inference(self, **kwargs: Any) -> dict[str, Any]:
        """Serve ``intelligence.inference.predict`` from the inference mock.

        There is no local model server, so the service is ``mock`` (a seeded,
        deterministic prediction recorded in the mock trace) or ``disabled``.
        """
        from .disabled import require_enabled

        mocks = getattr(self, "_mocks", None)
        if mocks is not None:
            require_enabled(mocks, "inference")
            if mocks.is_mock("inference"):
                mocks.record(
                    "inference",
                    "predict",
                    "inference mock",
                    intelligence_id=kwargs.get("intelligence_id"),
                )
                return await mocks.inference.predict(**kwargs)  # type: ignore[no-any-return]
        raise NotImplementedError(
            "intelligence.inference.predict has no local backend. Run with the "
            "inference service mocked (the default) to get a seeded prediction."
        )

    async def is_mcp_available(self) -> bool:
        return False

    async def get_mcp_client(self) -> Any:
        raise RuntimeError("MCP server is not available in local development mode.")

    async def send_chat_message(self, **kwargs: Any) -> dict[str, Any]:
        """Refuse a chat send that local mode cannot perform.

        This path runs when chat is **not** mocked. There is no egress broker locally,
        so the message cannot be sent, and returning a success-shaped receipt would
        report a delivery that never happened. Mock chat (the default) to exercise the
        delivery path offline.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError(
            f"Chat dispatch to {kwargs.get('channel', 'unknown')!r} is not "
            f"available in local development mode: there is no egress broker to "
            f"send through, so the message would not be delivered. Run with chat "
            f"mocked (the default) to exercise this path locally, or run the "
            f"orchestration in the cloud to send for real."
        )

    async def send_alert(self, **kwargs: Any) -> dict[str, Any]:
        """Log a custom alert locally and report it as undelivered.

        Unlike :meth:`send_chat_message`, this returns rather than raises:
        :class:`~perceivo.alerts.AlertDelivery` can state non-delivery, so it returns
        ``delivered=False`` with ``suppressed_reason="local"``. A component under
        ``perceivo.runner`` exercises argument validation and the return contract
        without a success-shaped result.
        """
        import logging

        logger = logging.getLogger("perceivo.local.alerts")
        logger.info(
            "[ALERT] %s (%s): %s",
            kwargs.get("key", "unknown"),
            kwargs.get("severity") or "key default",
            kwargs.get("title", ""),
        )
        return {
            "event_id": "local-dev-noop",
            "key": str(kwargs.get("key", "")),
            "delivered": False,
            "suppressed_reason": "local",
        }

    async def get_review_results(self, **kwargs: Any) -> list[Any]:
        return []

    async def record_cost(
        self,
        label: str,
        amount: float,
        currency: str = "USD",
        metadata: Any = None,
    ) -> None:
        import logging

        logger = logging.getLogger("perceivo.local.cost")
        logger.info("[COST] %s: %.4f %s", label, amount, currency)

    async def get_llm_pricing(self, provider: str, model_name: str) -> Any:
        return None

    async def get_orchestration_summary(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(_METRICS_UNAVAILABLE)

    async def get_capability_summary(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(_METRICS_UNAVAILABLE)

    async def get_intelligence_summary(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(_METRICS_UNAVAILABLE)

    async def get_billing_usage(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(_METRICS_UNAVAILABLE)

    async def get_review_stats(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(_METRICS_UNAVAILABLE)

    async def get_runs_over_time(self, **kwargs: Any) -> list[Any]:
        raise RuntimeError(_METRICS_UNAVAILABLE)
