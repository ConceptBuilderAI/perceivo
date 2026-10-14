"""Mock LLM adapter: canned, offline responses for ``perceivo.managed_service.ai``.

Returns the OpenAI Chat Completions shape the managed broker returns
(``choices[0].message.content`` and ``usage``), so components read a response
unchanged. Embeddings are deterministic, so vector-store round-trips are stable
across runs.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional


class MockLLM:
    """Deterministic, offline stand-in for a managed LLM deployment."""

    def __init__(self, seed: dict[str, Any]):
        self._seed = seed or {}

    @property
    def _completion_text(self) -> str:
        return str(self._seed.get("completion") or "This is a mock LLM response.")

    def _completion_for(
        self, messages: Optional[list[dict[str, Any]]], model: Optional[str]
    ) -> str:
        """Return the canned completion, made deterministically distinct per call.

        A fixed string would collapse multi-agent deliberations to a single
        proposal (no consensus signal to test). Appending a short, stable variant
        marker derived from the model + last user message keeps distinct callers
        distinct while staying deterministic and offline. Set
        ``mocks.seed.llm.vary`` to false to return the raw completion verbatim.
        """
        base = self._completion_text
        if self._seed.get("vary") is False:
            return base
        last = ""
        for message in reversed(messages or []):
            content = message.get("content")
            if content:
                last = str(content)
                break
        marker = hashlib.sha256(f"{model}|{last}".encode()).hexdigest()[:6]
        return f"{base} [variant {marker}]"

    @property
    def _embedding_dim(self) -> int:
        try:
            return int(self._seed.get("embedding_dim", 8)) or 8
        except (TypeError, ValueError):
            return 8

    async def invoke(
        self,
        *,
        model: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **_: Any,
    ) -> dict[str, Any]:
        """Return an OpenAI-shaped completion for the given chat messages."""
        content = self._completion_for(messages, model)
        prompt_tokens = sum(len(str(m.get("content", "")).split()) for m in (messages or []))
        completion_tokens = len(content.split())
        return {
            "id": "mock-llm-completion",
            "model": model or "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    async def embed(
        self,
        *,
        model: Optional[str] = None,
        texts: Optional[list[str]] = None,
        dimensions: Optional[int] = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Return deterministic embedding vectors, one per input text."""
        dim = int(dimensions) if dimensions else self._embedding_dim
        vectors = [self._vector(text, dim) for text in (texts or [])]
        return {"embeddings": vectors}

    async def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "model_name": "mock-model",
                "display_name": "Perceivo Local Mock Model",
                "provider": "mock",
                "local": True,
            }
        ]

    @staticmethod
    def _vector(text: str, dim: int) -> list[float]:
        """Hash *text* into a stable pseudo-embedding of length *dim*."""
        digest = hashlib.sha256(str(text).encode("utf-8")).digest()
        # Repeat the digest to reach the requested dimension; scale to [0, 1).
        raw = (digest * ((dim // len(digest)) + 1))[:dim]
        return [b / 255.0 for b in raw]
