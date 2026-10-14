"""Mock Milvus client: a stateful, in-memory vector store for local runs.

Implements the subset of the platform Milvus client components use
(``create_collection``, ``insert``, ``search``, ``list_collections``,
``drop_collection``, ``delete``, ``has_collection``). Inserts are stored per
collection, so a load then search round-trip returns what was written; an empty
collection returns the shipped seed hits.

Seed ``mocks.seed.milvus`` to shape the store a run starts with:

``search_hits`` (list of mappings)
    Returned by ``search`` for a collection that holds no vectors.
``collections`` (list of names, or mapping of name to a list of documents)
    Collections that already exist when the run starts, so ``list_collections``
    and ``has_collection`` report them. A component that refuses to run without
    its corpus can then run locally. Under the mapping form each document is
    ``{text, metadata}`` and optionally ``id`` and ``vector``, and ``search``
    returns those documents. A collection given by name only is empty, so
    ``search`` on it returns ``search_hits``.

Hits carry ``distance`` the way Milvus reports it for the collection's metric:
under COSINE (the default) and IP it is a similarity, so the best hit has the
highest value; under L2 it is a distance, so the best hit has the lowest.
"""

from __future__ import annotations

from typing import Any, Optional


class MockMilvus:
    """In-memory stand-in for the organization's Milvus vector database."""

    def __init__(self, seed: dict[str, Any]):
        self._seed = seed or {}
        # collection_name -> list of {"vector": [...], "text": str, "metadata": Any}
        self._collections: dict[str, list[dict[str, Any]]] = _seeded_collections(
            self._seed.get("collections")
        )
        # collection_name -> metric_type given to create_collection; COSINE when unset.
        self._metrics: dict[str, str] = {}

    async def create_collection(
        self,
        name: str,
        dimension: int = 8,
        metric_type: str = "COSINE",
        **_: Any,
    ) -> dict[str, Any]:
        self._collections.setdefault(name, [])
        self._metrics[name] = str(metric_type).upper()
        return {"collection_name": name, "dimension": dimension, "metric_type": metric_type}

    async def has_collection(self, name: str, **_: Any) -> bool:
        return name in self._collections

    async def list_collections(self, **_: Any) -> list[str]:
        return sorted(self._collections.keys())

    async def drop_collection(self, name: str, **_: Any) -> dict[str, Any]:
        self._collections.pop(name, None)
        return {"collection_name": name, "dropped": True}

    async def insert(
        self,
        collection_name: str,
        vectors: list[Any],
        metadata: Optional[list[dict[str, Any]]] = None,
        **_: Any,
    ) -> dict[str, Any]:
        store = self._collections.setdefault(collection_name, [])
        meta = metadata or []
        start = len(store)
        for i, vector in enumerate(vectors or []):
            entry = meta[i] if i < len(meta) else {}
            store.append(
                {
                    "id": f"{collection_name}-{start + i}",
                    "vector": vector,
                    "text": entry.get("text", ""),
                    "metadata": entry.get("metadata", {}),
                }
            )
        return {"insert_count": len(vectors or []), "collection_name": collection_name}

    async def search(
        self,
        collection_name: str,
        query_vectors: list[Any],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
        **_: Any,
    ) -> list[list[dict[str, Any]]]:
        stored = self._collections.get(collection_name, [])
        lower_is_better = self._metrics.get(collection_name, "COSINE") == "L2"
        results: list[list[dict[str, Any]]] = []
        for _q in query_vectors or [[]]:
            if stored:
                hits = [
                    {
                        "id": entry["id"],
                        "distance": _ranked_distance(rank, lower_is_better),
                        "entity": {"text": entry["text"], "metadata": entry["metadata"]},
                    }
                    for rank, entry in enumerate(stored[:top_k])
                ]
            else:
                hits = self._seed_hits(top_k)
            results.append(hits)
        return results

    async def delete(
        self,
        collection_name: str,
        ids: Optional[list[Any]] = None,
        filter_expr: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        store = self._collections.get(collection_name, [])
        if ids:
            id_set = {str(i) for i in ids}
            kept = [e for e in store if str(e["id"]) not in id_set]
            removed = len(store) - len(kept)
            self._collections[collection_name] = kept
            return {"delete_count": removed}
        return {"delete_count": 0}

    def _seed_hits(self, top_k: int) -> list[dict[str, Any]]:
        raw = self._seed.get("search_hits") or []
        hits: list[dict[str, Any]] = []
        for entry in raw[:top_k]:
            hits.append(
                {
                    "id": entry.get("id", "mock-milvus"),
                    "distance": entry.get("distance", 0.95),
                    "entity": {
                        "text": entry.get("text", ""),
                        "metadata": entry.get("metadata", {}),
                    },
                }
            )
        return hits


def _ranked_distance(rank: int, lower_is_better: bool) -> float:
    """The ``distance`` Milvus would report for the hit at ``rank`` (0 is best)."""
    if lower_is_better:
        return round(0.05 + 0.01 * rank, 4)
    return round(max(0.95 - 0.01 * rank, 0.0), 4)


def _seeded_collections(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """Build the starting store from the ``collections`` seed key.

    Accepts a list of collection names, or a mapping of name to its documents.
    Anything else seeds nothing.
    """
    if isinstance(raw, (list, tuple)):
        return {str(name): [] for name in raw if isinstance(name, str) and name}
    if not isinstance(raw, dict):
        return {}
    store: dict[str, list[dict[str, Any]]] = {}
    for name, documents in raw.items():
        entries: list[dict[str, Any]] = []
        for i, doc in enumerate(documents if isinstance(documents, (list, tuple)) else []):
            if not isinstance(doc, dict):
                continue
            entries.append(
                {
                    "id": str(doc.get("id") or f"{name}-{i}"),
                    "vector": doc.get("vector") or [],
                    "text": doc.get("text", ""),
                    "metadata": doc.get("metadata", {}),
                }
            )
        store[str(name)] = entries
    return store
