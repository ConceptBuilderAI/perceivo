"""Mock registry: per-service resolver and adapter factory for local runs.

:class:`LocalExtensionManager` consults the registry, per platform call, to
decide whether to serve a mock (the default) or fall through to the real or local
backend, and to obtain the mock adapter. It also owns the run's
:class:`MockTrace` and the provider rewrite that routes bring-your-own-key LLM
components through the LLM mock.
"""

from __future__ import annotations

from typing import Any, Optional

from ...config import Config
from .capabilities import MockCapabilities
from .content_safety import MockContentSafety
from .database import MockDatabase
from .inference import MockInference
from .llm import MockLLM
from .mcp import MockMCPClient
from .search import MockElasticsearch
from .seed import load_seed
from .trace import MockTrace
from .vector import MockMilvus

# Provider values rewritten to "managed" when the LLM is mocked, so the component
# calls ``perceivo.managed_service.ai`` (the mock) instead of a real endpoint.
_LLM_PROVIDERS = {
    "openai",
    "anthropic",
    "google",
    "azure",
    "azure_openai",
    "openai_compatible",
    "cohere",
    "mistral",
}


class MockRegistry:
    """Resolves service modes and hands out mock adapters + the call trace."""

    def __init__(self, config: Config):
        self._config = config
        self._seed = load_seed(getattr(config, "mocks", None))
        self.trace = MockTrace()
        self._llm: Optional[MockLLM] = None
        self._content_safety: Optional[MockContentSafety] = None
        self._milvus: Optional[MockMilvus] = None
        self._es: Optional[MockElasticsearch] = None
        self._mcp: Optional[MockMCPClient] = None
        self._db: Optional[MockDatabase] = None
        self._inference: Optional[MockInference] = None
        self._capabilities: Optional[MockCapabilities] = None
        self._http_installed = False

    def mode(self, service: str) -> str:
        return self._config.service_mode(service)

    def is_mock(self, service: str) -> bool:
        return self._config.is_mocked(service)

    def seed_for(self, service: str) -> dict[str, Any]:
        block = self._seed.get(service)
        return block if isinstance(block, dict) else {}

    def record(self, service: str, operation: str, summary: str = "", **detail: Any) -> None:
        self.trace.record(service, operation, self.mode(service), summary, **detail)

    @property
    def llm(self) -> MockLLM:
        if self._llm is None:
            self._llm = MockLLM(self.seed_for("llm"))
        return self._llm

    @property
    def content_safety(self) -> MockContentSafety:
        if self._content_safety is None:
            self._content_safety = MockContentSafety(
                self.seed_for("llm").get("content_safety") or {}
            )
        return self._content_safety

    @property
    def milvus(self) -> MockMilvus:
        if self._milvus is None:
            self._milvus = MockMilvus(self.seed_for("milvus"))
        return self._milvus

    @property
    def elasticsearch(self) -> MockElasticsearch:
        if self._es is None:
            self._es = MockElasticsearch(self.seed_for("elasticsearch"))
        return self._es

    @property
    def mcp(self) -> MockMCPClient:
        if self._mcp is None:
            self._mcp = MockMCPClient(self.seed_for("mcp"))
        return self._mcp

    @property
    def database(self) -> MockDatabase:
        if self._db is None:
            self._db = MockDatabase(self.seed_for("database"))
        return self._db

    @property
    def inference(self) -> MockInference:
        if self._inference is None:
            self._inference = MockInference(self.seed_for("inference"))
        return self._inference

    @property
    def capabilities(self) -> MockCapabilities:
        if self._capabilities is None:
            self._capabilities = MockCapabilities(self.seed_for("capabilities"))
        return self._capabilities

    def secret(self, alias: str) -> Optional[str]:
        """Return the mock value for *alias* (explicit override or default)."""
        seed = self.seed_for("secrets")
        values = seed.get("values") or {}
        if alias in values:
            return str(values[alias])
        default = seed.get("default")
        return str(default) if default is not None else None

    def redis_seed(self) -> dict[str, Any]:
        return dict(self.seed_for("redis").get("keys") or {})

    def chat_status(self) -> str:
        # The fallback is the platform's own success value, which
        # ``perceivo.alerts.send_direct`` recognises.
        return str(self.seed_for("chat").get("status") or "sent")

    def chat_error(self) -> str:
        """Detail to attach to a seeded chat failure (empty unless seeded).

        Lets a seed reproduce a *specific* platform failure ("channel_not_found",
        "bot is not in the channel") rather than only the fact of one, so a
        component's failure branch can be tested against the message it will
        actually receive.
        """
        return str(self.seed_for("chat").get("error") or "")

    def install_http(self) -> None:
        """Install httpx interception for raw LLM/content-safety calls."""
        if self._http_installed or not self.is_mock("llm"):
            return
        from . import http_intercept

        http_intercept.install(self)
        self._http_installed = True

    def uninstall_http(self) -> None:
        """Remove the interception this registry installed (no-op if it did not).

        Paired on the fact of installation rather than on the current mode: the
        LLM mode can change between activate and deactivate (``local.set_mode``),
        and a mode-guarded uninstall would then leave a patched
        ``httpx.AsyncClient.post`` behind for every later run in the process.
        """
        if not self._http_installed:
            return
        from . import http_intercept

        http_intercept.uninstall()
        self._http_installed = False

    def sync_http(self) -> None:
        """Match the httpx interception to the LLM service's current mode."""
        if self.is_mock("llm"):
            self.install_http()
        else:
            self.uninstall_http()

    def force_managed_provider(self, key: str, value: Any) -> Any:
        """Rewrite an LLM ``provider`` env value to ``managed`` under mock mode."""
        if (
            key == "provider"
            and self.is_mock("llm")
            and isinstance(value, str)
            and value.strip().lower() in _LLM_PROVIDERS
        ):
            return "managed"
        return value
