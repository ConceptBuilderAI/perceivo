"""Shipped default seed data and merge and scaffold helpers for local mocks.

Every service ships a canned response, so orchestrations run with no
configuration. Override any value with ``mocks.seed`` inline in
``perceivo.yaml`` or in a ``perceivo.mock.yaml`` seed file.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from ...types.errors import ConfigurationError

# Shipped defaults: one canned block per mockable service.

_DEFAULT_LLM_COMPLETION = (
    "This is a mock response from the Perceivo local LLM mock. It stands in for "
    "a managed AI Foundry deployment so agents run offline. Configure "
    "`mocks.seed.llm.completion` in perceivo.yaml to customize it."
)

_DEFAULT_DOC_TEXT = (
    "Mock organization document. Remote work is permitted up to three days per "
    "week with manager approval. Paid time off accrues monthly. This canned text "
    "lets file/RAG orchestrations run locally without real org storage."
)

DEFAULT_SEED: dict[str, Any] = {
    "llm": {
        # Canned chat completion text (returned inside the OpenAI response shape).
        "completion": _DEFAULT_LLM_COMPLETION,
        # Deterministic embedding vector length used by the embeddings mock.
        "embedding_dim": 8,
        # Prompt shield and groundedness results served under the llm mock. The default
        # is safe and fully grounded; see mocks/content_safety.py for the other keys.
        "content_safety": {
            "attack_detected": False,
            "ungrounded_percentage": 0.0,
        },
    },
    "milvus": {
        # Collections that exist before the run: a list of names, or a mapping of name
        # to seeded documents ({text, metadata, id?, vector?}). See mocks/vector.py.
        "collections": [],
        # Returned per query by MockMilvus.search when a collection holds no vectors, so
        # RAG search is non-empty out of the box.
        "search_hits": [
            {
                "id": "mock-milvus-1",
                "distance": 0.95,
                "text": _DEFAULT_DOC_TEXT,
                "metadata": {"source": "mock"},
            }
        ],
    },
    "elasticsearch": {
        # Default hits returned by MockElasticsearch.search for an empty index.
        "search_hits": [
            {"id": "mock-es-1", "score": 1.0, "text": _DEFAULT_DOC_TEXT, "source": "mock"}
        ],
    },
    "mcp": {
        "tools": [
            {
                "name": "search",
                "description": "Mock MCP search tool (local development).",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ],
        # Default result for any call_tool invocation without a named override.
        "call": {"result": "mock MCP tool result", "source": "mock"},
        # Per-tool overrides: {tool_name: {<result dict>}}.
        "calls": {},
    },
    "secrets": {
        # Fallback value returned for any secret not present in .env/os.environ.
        "default": "mock-secret",
        # Per-alias overrides. Endpoint-style secrets must be valid URLs so components
        # that build a request URL from them do not fail.
        "values": {
            "CONTENT_SAFETY_ENDPOINT": "https://mock-content-safety.local",
        },
    },
    "files": {
        # Files written into the org storage root before a run: space-relative path to
        # text content. Parent folders are created for nested paths.
        "story_beginning.txt": (
            "The old lighthouse keeper lit the lamp one final time. Below, the "
            "tide pulled at the rocks as a single ship appeared on the horizon."
        ),
        "documents/remote_work_policy.txt": _DEFAULT_DOC_TEXT,
        # A folder named like a .pdf: some loaders read a document path as a directory
        # of files, so seeding a file inside gives them content to ingest.
        "documents/contract_2026.pdf/contract.txt": _DEFAULT_DOC_TEXT,
        "policies/remote_work.txt": _DEFAULT_DOC_TEXT,
        "policies/paid_time_off.txt": (
            "Paid time off accrues at 1.5 days per month and rolls over up to 30 "
            "days. Requests are approved by the employee's direct manager."
        ),
    },
    "redis": {
        # Pre-populated keys for the in-memory Redis mock.
        "keys": {},
    },
    "database": {
        # Canned result for managed_service.database queries.
        "columns": ["id", "name"],
        "rows": [{"id": 1, "name": "mock-row"}],
    },
    "chat": {
        # Mock delivery status. "sent" is the platform's success value; seed "failed",
        # optionally with ``error``, to make ``perceivo.alerts.send_direct`` raise.
        "status": "sent",
        # Detail carried on a seeded failure, e.g. "channel_not_found". Ignored
        # unless `status` is "failed".
        "error": "",
    },
    "inference": {
        # Default ``prediction`` for intelligence.inference.predict when the seed sets
        # neither ``prediction`` nor ``predictions``; a seeded value replaces it whole.
        "default_prediction": {
            "result": {
                "predicted_orchestration_id": "mock-orchestration",
                "confidence": 1.0,
                "class_probabilities": {"mock-orchestration": 1.0},
                "features_used": {},
            }
        },
        "model_id": "mock-model",
    },
    "capabilities": {
        # Active capabilities for perceivo.capabilities.list(): [{id, name}]. Empty by
        # default, so a component discovering capabilities sees an empty organization.
        "items": [],
        # Stored execution graphs per capability id: {capability_id: [{graph_id, ...}]}.
        # See mocks/capabilities.py for the per-graph keys.
        "graphs": {},
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* onto a copy of *base* (override wins)."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_seed_file(seed_file: str | None) -> dict[str, Any]:
    """Load the seed block from a ``perceivo.mock.yaml`` file.

    The file shape is ``{mocks: {seed: {service: payload}}}``, as written by
    ``perceivo mock init``. Any other shape raises rather than being read as no
    overrides.

    Raises:
        ConfigurationError: If the file is not the expected shape.
    """
    if not seed_file:
        return {}
    path = Path(seed_file)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    block = raw.get("mocks") if isinstance(raw, dict) else None
    seed = block.get("seed") if isinstance(block, dict) else None
    if not isinstance(seed, dict):
        raise ConfigurationError(
            f"{seed_file}: a mock seed file has the shape "
            "{mocks: {seed: {<service>: <payload>}}}. Regenerate it with "
            "`perceivo mock init`."
        )
    return seed


def load_seed(mocks_config: Any) -> dict[str, Any]:
    """Merge shipped defaults with inline seed and the optional seed file.

    Precedence (low → high): shipped ``DEFAULT_SEED`` → ``mocks.seed`` inline →
    ``perceivo.mock.yaml`` seed file.
    """
    seed = DEFAULT_SEED
    inline = getattr(mocks_config, "seed", None) or {}
    if inline:
        seed = _deep_merge(seed, inline)
    file_block = _load_seed_file(getattr(mocks_config, "seed_file", None))
    if file_block:
        seed = _deep_merge(seed, file_block)
    return seed


def scaffold_seed(services: list[str]) -> dict[str, Any]:
    """Build a seed-data scaffold containing only the referenced *services*.

    Used by ``perceivo mock init`` so nothing a project references is silently
    unmocked, while unused services are omitted to keep the file small.
    """
    scaffold: dict[str, Any] = {}
    for service in services:
        if service in DEFAULT_SEED:
            scaffold[service] = copy.deepcopy(DEFAULT_SEED[service])
    return scaffold
