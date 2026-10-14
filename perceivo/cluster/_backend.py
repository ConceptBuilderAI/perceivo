"""The in-cluster service accessors, private to :mod:`perceivo.cluster`.

Each function forwards to the platform's service backend.
:mod:`perceivo.cluster._registry` derives its handle catalog from
:data:`__all__` here, so this module's export list is the contract.
"""

from typing import Any

from .._internal.context import _ExecutionContext
from ._mcp_backend import get_mcp

__all__ = ["get_elasticsearch", "get_mcp", "get_milvus", "get_redis"]


async def get_redis() -> Any:
    """
    Get a Redis client for the organization's cache.

    Returns a Redis client connected to the organization's Redis instance.
    Use this for caching, session management, and other key-value storage.

    Returns:
        The runtime's organization-scoped Redis adapter. Its ``set`` method
        accepts ``ttl_seconds`` for expiration.

    Raises:
        RuntimeError: If Redis is not configured for this organization.

    Example:
        ```python
        redis = await perceivo.cluster.redis.connect()

        # Set a value
        await redis.set("my_key", "my_value")

        # Get a value
        value = await redis.get("my_key")

        # Use with expiration
        await redis.set("cache_key", "data", ttl_seconds=3600)
        ```
    """
    ext = _ExecutionContext.get_ext()
    return await ext.get_redis()


async def get_milvus() -> Any:
    """Get a Milvus client for vector database operations.

    Returns a Milvus client connected to the organization's Milvus instance.
    Use this for vector similarity search and embeddings storage.

    Returns:
        The runtime's organization-scoped async Milvus service. It is not a
        ``pymilvus.MilvusClient``: its ``search`` is a coroutine taking
        ``query_vectors=`` and ``top_k=``. Local mock mode returns an in-memory
        stand-in with the same interface; local development against a real
        Milvus server returns ``pymilvus.MilvusClient`` itself.

    Raises:
        RuntimeError: If Milvus is not configured for this organization.

    Example:
        ```python
        milvus = await perceivo.cluster.milvus.connect()

        await milvus.create_collection("embeddings", dimension=384)

        # Search for similar vectors
        results = await milvus.search(
            collection_name="embeddings",
            query_vectors=[query_vector],
            top_k=10,
        )
        ```
    """
    ext = _ExecutionContext.get_ext()
    return await ext.get_milvus()


async def get_elasticsearch() -> Any:
    """
    Get an Elasticsearch client for search operations.

    Returns an Elasticsearch client connected to the organization's
    Elasticsearch instance. Use this for full-text search and analytics.

    Returns:
        On the platform, the organization's Elasticsearch client. In a local
        run with elasticsearch mocked, the default, an in-memory index offering
        ``create_index``, ``bulk_index``, ``search``, ``delete_document``,
        ``delete_by_query``, ``list_indices`` and ``delete_index``.

    Raises:
        RuntimeError: If Elasticsearch is not configured for this organization.

    Example:
        ```python
        es = await perceivo.cluster.elasticsearch.connect()

        # Search documents
        results = await es.search(
            index="documents",
            query={"match": {"content": "search term"}}
        )
        ```
    """
    ext = _ExecutionContext.get_ext()
    return await ext.get_elasticsearch()
