"""Mock Elasticsearch client: a stateful, in-memory full-text index for local runs.

Implements the client API components call on the platform Elasticsearch client
(``create_index``, ``bulk_index``, ``search``, ``delete_document``,
``delete_by_query``, ``list_indices``, ``delete_index``). ``search`` returns the
standard response envelope (``{"hits": {"total": {...}, "hits": [...]}}``),
populated from indexed documents, or the shipped seed hits when the index is
empty.
"""

from __future__ import annotations

from typing import Any, Optional


class MockElasticsearch:
    """In-memory stand-in for the organization's Elasticsearch cluster."""

    def __init__(self, seed: dict[str, Any]):
        self._seed = seed or {}
        # index_name -> list of {"_id": str, "_source": {...}}
        self._indices: dict[str, list[dict[str, Any]]] = {}

    async def create_index(
        self,
        name: str,
        mappings: Optional[dict[str, Any]] = None,
        settings: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict[str, Any]:
        self._indices.setdefault(name, [])
        return {"index": name, "acknowledged": True}

    async def list_indices(self, **_: Any) -> list[str]:
        return sorted(self._indices.keys())

    async def delete_index(self, name: str, **_: Any) -> dict[str, Any]:
        self._indices.pop(name, None)
        return {"index": name, "acknowledged": True}

    async def bulk_index(
        self,
        index: str,
        documents: list[dict[str, Any]],
        doc_ids: Optional[list[Any]] = None,
        **_: Any,
    ) -> dict[str, Any]:
        store = self._indices.setdefault(index, [])
        start = len(store)
        for i, doc in enumerate(documents or []):
            doc_id = str(doc_ids[i]) if doc_ids and i < len(doc_ids) else f"{index}-{start + i}"
            store.append({"_id": doc_id, "_source": dict(doc)})
        return {"index": index, "indexed": len(documents or [])}

    async def search(
        self,
        index: str,
        query: Optional[dict[str, Any]] = None,
        size: int = 10,
        **_: Any,
    ) -> dict[str, Any]:
        store = self._indices.get(index, [])
        if store:
            raw = store[:size]
            hits = [
                {"_id": e["_id"], "_score": round(1.0 - 0.01 * rank, 4), "_source": e["_source"]}
                for rank, e in enumerate(raw)
            ]
        else:
            hits = self._seed_hits(size)
        return {"hits": {"total": {"value": len(hits)}, "hits": hits}}

    async def delete_document(self, index: str, doc_id: str, **_: Any) -> dict[str, Any]:
        store = self._indices.get(index, [])
        kept = [e for e in store if str(e["_id"]) != str(doc_id)]
        removed = len(store) - len(kept)
        self._indices[index] = kept
        return {"index": index, "deleted": removed}

    async def delete_by_query(self, index: str, query: dict[str, Any], **_: Any) -> dict[str, Any]:
        # A mock cannot faithfully evaluate a DSL query; treat it as clearing the
        # index and report the count removed (sufficient for local round-trips).
        store = self._indices.get(index, [])
        removed = len(store)
        self._indices[index] = []
        return {"index": index, "deleted": removed}

    def _seed_hits(self, size: int) -> list[dict[str, Any]]:
        raw = self._seed.get("search_hits") or []
        hits: list[dict[str, Any]] = []
        for entry in raw[:size]:
            source = {k: v for k, v in entry.items() if k not in ("id", "score")}
            hits.append(
                {
                    "_id": entry.get("id", "mock-es"),
                    "_score": entry.get("score", 1.0),
                    "_source": source,
                }
            )
        return hits
