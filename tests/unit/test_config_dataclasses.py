"""Direct unit tests for the perceivo.config dataclasses.

``load`` exercises them only indirectly, and not ``ElasticsearchConfig`` at all.
Test each dataclass's defaults and construction directly so the config contract
is pinned.
"""

from __future__ import annotations

from perceivo.config import (
    AIFoundryConfig,
    Config,
    DatabaseConfig,
    ElasticsearchConfig,
    FilesConfig,
    LoggingConfig,
    MilvusConfig,
    RedisConfig,
    SecretsConfig,
)


def test_files_config_defaults():
    c = FilesConfig()
    # Empty means "under the local root" (<root>/files/working, <root>/files/org).
    assert c.working_dir == ""
    assert c.org_dir == ""


def test_secrets_config_defaults():
    c = SecretsConfig()
    assert c.source == "dotenv"
    assert c.dotenv_path == ".env"
    assert c.inline == {}


def test_redis_config_defaults_and_override():
    assert RedisConfig().enabled is False
    assert RedisConfig().url == "redis://localhost:6379"
    assert RedisConfig(enabled=True, url="redis://r:6379").url == "redis://r:6379"


def test_milvus_config_defaults():
    c = MilvusConfig()
    assert c.enabled is False
    assert c.uri == "http://localhost:19530"


def test_elasticsearch_config_defaults():
    c = ElasticsearchConfig()
    assert c.enabled is False
    assert c.url == "http://localhost:9200"
    assert ElasticsearchConfig(enabled=True).enabled is True


def test_database_config_defaults():
    c = DatabaseConfig()
    assert c.id == ""
    assert c.url == ""
    assert c.read_only is True


def test_logging_config_defaults():
    c = LoggingConfig()
    assert c.level == "INFO"
    assert c.format == "console"


def test_ai_foundry_config_defaults():
    c = AIFoundryConfig()
    assert c.enabled is False
    assert c.provider == "openai"
    assert c.api_key is None


def test_perceivo_config_composes_all_sections_by_default():
    c = Config()
    assert isinstance(c.files, FilesConfig)
    assert isinstance(c.secrets, SecretsConfig)
    assert isinstance(c.redis, RedisConfig)
    assert isinstance(c.milvus, MilvusConfig)
    assert isinstance(c.elasticsearch, ElasticsearchConfig)
    assert isinstance(c.ai_foundry, AIFoundryConfig)
    assert isinstance(c.logging, LoggingConfig)
    assert c.databases == []
    # default: every service resolves to the mock adapter out of the box
    assert c.is_mocked("redis") is True
