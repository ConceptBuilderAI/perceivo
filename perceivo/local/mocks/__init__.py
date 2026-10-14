"""Local mock adapters and per-service mode resolution for offline runs.

Every platform call (LLM, Redis, Milvus, Elasticsearch, MCP, files, secrets,
database, chat) resolves per service, via ``perceivo.yaml``, to a mock (the
default), a real local instance, or a bring-your-own-key provider. Unmapped calls
return typed defaults rather than raising.
"""

from .database import MockDatabase
from .llm import MockLLM
from .mcp import MockMCPClient
from .registry import MockRegistry
from .search import MockElasticsearch
from .seed import DEFAULT_SEED, load_seed, scaffold_seed
from .trace import MockCall, MockTrace
from .vector import MockMilvus

__all__ = [
    "DEFAULT_SEED",
    "MockCall",
    "MockDatabase",
    "MockElasticsearch",
    "MockLLM",
    "MockMCPClient",
    "MockMilvus",
    "MockRegistry",
    "MockTrace",
    "load_seed",
    "scaffold_seed",
]
