"""Configure the SDK for local development from YAML.

Load settings from ``perceivo.yaml`` (or environment variables) to configure
how the SDK resolves secrets, files, logging, and infrastructure when running
outside the Perceivo Cloud platform::

    perceivo.config.load(path=None)        -> Config
    perceivo.config.get(key, default=None) -> Any
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

from ._internal.config_discovery import ApiConfig, build_api_config, refuse_url_keys
from ._internal.config_paths import resolve_file_relative as _resolve_file_relative
from ._internal.home import home as _home
from .types.errors import ConfigurationError

__all__ = ["Config", "get", "load"]


@dataclass
class FilesConfig:
    """Local file storage paths.

    Empty means "under the local root": ``<root>/files/working`` and
    ``<root>/files/org``. A relative path written in ``perceivo.yaml`` is resolved
    against that file's directory, not the working directory.
    """

    working_dir: str = ""
    org_dir: str = ""


@dataclass
class SecretsConfig:
    """How secrets are resolved locally."""

    source: Literal["dotenv", "env", "inline"] = "dotenv"
    dotenv_path: str = ".env"
    inline: dict[str, str] = field(default_factory=dict)


@dataclass
class RedisConfig:
    """Optional local Redis connection."""

    enabled: bool = False
    url: str = "redis://localhost:6379"


@dataclass
class MilvusConfig:
    """Milvus vector database configuration."""

    enabled: bool = False
    uri: str = "http://localhost:19530"


@dataclass
class ElasticsearchConfig:
    """Elasticsearch configuration."""

    enabled: bool = False
    url: str = "http://localhost:9200"


@dataclass
class DatabaseConfig:
    """External database configuration for ``perceivo.managed_service.database.query()``."""

    id: str = ""
    url: str = ""
    read_only: bool = True


@dataclass
class AIFoundryConfig:
    """Bring-your-own-LLM configuration for ``perceivo.managed_service.ai.*``.

    On the platform, ``perceivo.managed_service.ai`` calls managed Azure AI Foundry
    deployments. Locally there is no managed broker, so this section points
    AI Foundry calls at a real provider using the developer's own key. When
    ``enabled`` is false the local adapter raises a "not available locally" error.

    ``provider`` selects the wire protocol: ``openai``, ``openai_compatible`` and
    ``azure_openai`` use the OpenAI Chat Completions shape (the shape the managed
    broker returns), while ``anthropic`` uses the Anthropic Messages API and is
    translated back to the OpenAI response shape.

    The key is resolved in order: ``api_key`` (literal), then ``api_key_secret``
    (a bound-secret alias), then the first present of the provider's default
    aliases (``api_key``, ``openai_api_key`` / ``OPENAI_API_KEY`` or
    ``anthropic_api_key`` / ``ANTHROPIC_API_KEY``) via the local secrets service.
    """

    enabled: bool = False
    provider: Literal["openai", "openai_compatible", "azure_openai", "anthropic"] = "openai"
    api_key: Optional[str] = None
    api_key_secret: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    embedding_model: Optional[str] = None


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    format: Literal["console", "json"] = "console"


# Every service defaults to ``mock``. Must match the keys of
# ``_cli.mock_cmd._SERVICE_MARKERS`` and ``local.mocks.seed.DEFAULT_SEED``.
MOCKABLE_SERVICES = (
    "llm",
    "redis",
    "milvus",
    "elasticsearch",
    "mcp",
    "files",
    "secrets",
    "database",
    "chat",
    "inference",
    "capabilities",
)

#: Services with no local backend; their mode is ``mock`` or ``disabled``.
NO_LOCAL_BACKEND = frozenset({"mcp", "chat", "inference", "capabilities"})

#: Modes accepted by ``perceivo.local.set_mode``. The config also stores
#: ``byo-key``; ``perceivo.local._modes`` translates between them.
DECLARED_MODES = ("mock", "local", "disabled")


@dataclass
class MocksConfig:
    """Per-service mode selection and seed data for local mock execution.

    Every service is mocked by default, with ``local`` or ``byo-key`` as
    per-service opt-ins. ``modes`` overrides a single service; ``seed`` supplies
    inline canned data merged over the shipped defaults; ``seed_file`` points at a
    ``perceivo.mock.yaml`` whose ``mocks:`` block is merged in as well.
    """

    enabled: bool = True
    default_mode: str = "mock"  # mock | local | disabled
    modes: dict[str, str] = field(default_factory=dict)
    seed: dict[str, Any] = field(default_factory=dict)
    seed_file: Optional[str] = None

    def mode(self, service: str, *, native_local: bool = False) -> str:
        """Resolve the effective mode for a service.

        An explicit ``modes[service]`` entry wins; otherwise a service whose native
        section is enabled runs ``local`` (``byo-key`` for the LLM); otherwise
        ``default_mode`` when mocking is enabled, else ``disabled``. A service in
        :data:`NO_LOCAL_BACKEND` resolves to ``disabled`` whenever that would
        otherwise select a real backend.

        Args:
            service: The service name, one of ``MOCKABLE_SERVICES``.
            native_local: Whether the service's native configuration section is
                enabled.

        Returns:
            The mode name.
        """
        explicit = self.modes.get(service)
        if native_local:
            fallback = "byo-key" if service == "llm" else "local"
        elif self.enabled:
            fallback = self.default_mode
        else:
            fallback = "disabled"
        resolved = explicit or fallback
        if service in NO_LOCAL_BACKEND and resolved not in ("mock", "disabled"):
            return "disabled"
        return resolved


@dataclass
class Config:
    """Root configuration for local perceivo SDK usage."""

    version: int = 1
    files: FilesConfig = field(default_factory=FilesConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    milvus: MilvusConfig = field(default_factory=MilvusConfig)
    elasticsearch: ElasticsearchConfig = field(default_factory=ElasticsearchConfig)
    databases: list[DatabaseConfig] = field(default_factory=list)
    ai_foundry: AIFoundryConfig = field(default_factory=AIFoundryConfig)
    mocks: MocksConfig = field(default_factory=MocksConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    #: Non-secret public-API settings (``api:``). A secret or a base URL there is
    #: refused: the base URL comes only from ``PERCEIVO_API_URL`` or an argument.
    api: ApiConfig = field(default_factory=ApiConfig)
    #: The local-runner root for every local write, resolved by :func:`load`.
    #: Empty on a hand-built ``Config``, which resolves the root when used.
    root: str = ""

    def _native_local(self, service: str) -> bool:
        """Whether *service* has a real/local backend configured natively."""
        return {
            "llm": self.ai_foundry.enabled,
            "redis": self.redis.enabled,
            "milvus": self.milvus.enabled,
            "elasticsearch": self.elasticsearch.enabled,
            "database": bool(self.databases),
        }.get(service, False)

    def service_mode(self, service: str) -> str:
        """Resolve the effective execution mode for a platform *service*."""
        return self.mocks.mode(service, native_local=self._native_local(service))

    def is_mocked(self, service: str) -> bool:
        """Whether *service* resolves to the mock adapter."""
        return self.service_mode(service) == "mock"


#: The configuration most recently returned by :func:`load`, which is what
#: :func:`get` reads. ``None`` until the first load.
_LOADED: Optional[Config] = None

_ENV_OVERRIDES = {
    "PERCEIVO_FILES_DIR": ("files", "working_dir"),
    "PERCEIVO_ORG_DIR": ("files", "org_dir"),
    "PERCEIVO_SECRETS_SOURCE": ("secrets", "source"),
    "PERCEIVO_SECRETS_DOTENV": ("secrets", "dotenv_path"),
    "PERCEIVO_REDIS_ENABLED": ("redis", "enabled"),
    "PERCEIVO_REDIS_URL": ("redis", "url"),
    "PERCEIVO_MILVUS_ENABLED": ("milvus", "enabled"),
    "PERCEIVO_MILVUS_URI": ("milvus", "uri"),
    "PERCEIVO_ES_ENABLED": ("elasticsearch", "enabled"),
    "PERCEIVO_ES_URL": ("elasticsearch", "url"),
    "PERCEIVO_AI_FOUNDRY_ENABLED": ("ai_foundry", "enabled"),
    "PERCEIVO_LLM_PROVIDER": ("ai_foundry", "provider"),
    "PERCEIVO_LLM_API_KEY": ("ai_foundry", "api_key"),
    "PERCEIVO_LLM_API_KEY_SECRET": ("ai_foundry", "api_key_secret"),
    "PERCEIVO_LLM_BASE_URL": ("ai_foundry", "base_url"),
    "PERCEIVO_LLM_MODEL": ("ai_foundry", "model"),
    "PERCEIVO_LLM_EMBEDDING_MODEL": ("ai_foundry", "embedding_model"),
    "PERCEIVO_MOCKS_ENABLED": ("mocks", "enabled"),
    "PERCEIVO_MOCKS_DEFAULT_MODE": ("mocks", "default_mode"),
    "PERCEIVO_MOCKS_SEED_FILE": ("mocks", "seed_file"),
    "PERCEIVO_LOG_LEVEL": ("logging", "level"),
    "PERCEIVO_LOG_FORMAT": ("logging", "format"),
}


def _validate_modes(modes: dict[str, str], default_mode: str) -> None:
    """Reject a ``mocks`` block that names an unknown service or mode.

    Raises:
        ConfigurationError: If a service or mode is unknown, or a service in
            :data:`NO_LOCAL_BACKEND` is given a mode other than ``mock`` or
            ``disabled``.
    """
    for service, mode in modes.items():
        if service not in MOCKABLE_SERVICES:
            raise ConfigurationError(
                f"perceivo.yaml: mocks.modes names unknown service {service!r}. "
                f"Valid services: {', '.join(sorted(MOCKABLE_SERVICES))}"
            )
        permitted = ("mock", "disabled") if service in NO_LOCAL_BACKEND else DECLARED_MODES
        if mode not in permitted:
            hint = (
                f" Service {service!r} has no local backend to point at."
                if service in NO_LOCAL_BACKEND and mode in DECLARED_MODES
                else ""
            )
            raise ConfigurationError(
                f"perceivo.yaml: mocks.modes[{service!r}] is {mode!r}.{hint} "
                f"Valid modes: {', '.join(permitted)}"
            )
    if default_mode not in DECLARED_MODES:
        raise ConfigurationError(
            f"perceivo.yaml: mocks.default_mode is {default_mode!r}. "
            f"Valid modes: {', '.join(DECLARED_MODES)}"
        )


def _build_mocks(raw: dict[str, Any]) -> MocksConfig:
    """Build a :class:`MocksConfig` from the ``mocks`` section of the YAML."""
    section = raw.get("mocks", {})
    if not isinstance(section, dict):
        section = {}
    modes = section.get("modes", {})
    seed = section.get("seed", {})
    modes = {str(k): str(v) for k, v in modes.items()} if isinstance(modes, dict) else {}
    default_mode = str(section.get("default_mode", "mock"))
    _validate_modes(modes, default_mode)
    return MocksConfig(
        enabled=_coerce(str(section.get("enabled", True)), bool),
        default_mode=default_mode,
        modes=modes,
        seed=seed if isinstance(seed, dict) else {},
        seed_file=section.get("seed_file"),
    )


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Layer environment-variable overrides on top of the parsed YAML dict."""
    for env_key, (section, key) in _ENV_OVERRIDES.items():
        value = os.environ.get(env_key)
        if value is not None:
            raw.setdefault(section, {})[key] = value
    return raw


def _coerce(value: str, target_type: type) -> Any:
    """Coerce a string env-var value to the target field type."""
    if target_type is bool:
        return value.lower() in ("1", "true", "yes")
    return value


def _build_config(
    raw: dict[str, Any], *, source: str = "perceivo.yaml", base_dir: Optional[Path] = None
) -> Config:
    """Build a Config from a (possibly empty) dict."""
    refuse_url_keys(raw, source=source)
    databases: list[DatabaseConfig] = []
    for db_raw in raw.get("databases", []):
        if isinstance(db_raw, dict):
            databases.append(
                DatabaseConfig(
                    id=db_raw.get("id", ""),
                    url=db_raw.get("url", ""),
                    read_only=_coerce(str(db_raw.get("read_only", True)), bool),
                )
            )

    return Config(
        version=raw.get("version", 1),
        files=FilesConfig(
            **{
                k: v
                for k, v in raw.get("files", {}).items()
                if k in FilesConfig.__dataclass_fields__
            }
        ),
        secrets=SecretsConfig(
            **{
                k: v
                for k, v in raw.get("secrets", {}).items()
                if k in SecretsConfig.__dataclass_fields__
            }
        ),
        redis=RedisConfig(
            enabled=_coerce(str(raw.get("redis", {}).get("enabled", False)), bool),
            url=raw.get("redis", {}).get("url", "redis://localhost:6379"),
        ),
        milvus=MilvusConfig(
            enabled=_coerce(str(raw.get("milvus", {}).get("enabled", False)), bool),
            uri=raw.get("milvus", {}).get("uri", "http://localhost:19530"),
        ),
        elasticsearch=ElasticsearchConfig(
            enabled=_coerce(str(raw.get("elasticsearch", {}).get("enabled", False)), bool),
            url=raw.get("elasticsearch", {}).get("url", "http://localhost:9200"),
        ),
        databases=databases,
        ai_foundry=AIFoundryConfig(
            **{
                k: (_coerce(str(v), bool) if k == "enabled" else v)
                for k, v in raw.get("ai_foundry", {}).items()
                if k in AIFoundryConfig.__dataclass_fields__
            }
        ),
        mocks=_build_mocks(raw),
        logging=LoggingConfig(
            **{
                k: v
                for k, v in raw.get("logging", {}).items()
                if k in LoggingConfig.__dataclass_fields__
            }
        ),
        api=build_api_config(raw.get("api"), source=source, base_dir=base_dir),
    )


def load(path: Optional[str] = None) -> Config:
    """Load perceivo configuration from a YAML file.

    The file is the explicit *path*, else ``PERCEIVO_CONFIG``, else
    ``perceivo.yaml`` in the current working directory. Environment variables
    (e.g. ``PERCEIVO_FILES_DIR``) override values from the file. If no file is
    found, defaults are used. A relative ``files.*`` path in the file is resolved
    against the file's directory, and ``Config.root`` is set to the resolved
    local-runner root: ``PERCEIVO_HOME``; else the file's ``root:``, or
    ``.perceivo`` beside the file. With no file loaded, the same rule is
    applied to the nearest ``perceivo.yaml`` at or above the working
    directory, whose other values are not loaded; with none, ``./.perceivo``.

    Public-API calls such as ``publish`` read a different file: the nearest
    ``perceivo.yaml`` at or above the project directory.

    Args:
        path: Path to the YAML file.

    Returns:
        The resolved :class:`Config`.

    Raises:
        ConfigurationError: If the ``api:`` section holds a secret or an unknown
            key, or any key names the API base URL (``api.url`` and its aliases,
            or a top-level ``api_url`` / ``base_url``).
    """
    if path is None:
        path = os.environ.get("PERCEIVO_CONFIG", "perceivo.yaml")

    raw: dict[str, Any] = {}
    config_path = Path(path)
    base_dir = config_path.resolve().parent
    if config_path.exists():
        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        raw = _resolve_file_relative(raw, base_dir, SecretsConfig.dotenv_path)

    raw = _apply_env_overrides(raw)
    config = _build_config(raw, source=f"perceivo.yaml:{config_path}", base_dir=base_dir)
    config.root = str(_home(config_path=config_path if config_path.is_file() else None))
    global _LOADED
    _LOADED = config
    return config


def get(key: str, default: Any = None) -> Any:
    """Read one value from the configuration by dotted path.

    Reads the configuration most recently returned by :func:`load`, loading it
    first if nothing has been loaded.

    Args:
        key: A dotted path such as ``"redis.url"`` or ``"mocks.default_mode"``.
        default: Returned when the path does not resolve.

    Returns:
        The value at *key*, or *default*.
    """
    config: Any = _LOADED if _LOADED is not None else load()
    for part in key.split("."):
        if isinstance(config, dict):
            if part not in config:
                return default
            config = config[part]
            continue
        if not hasattr(config, part):
            return default
        config = getattr(config, part)
    return config
