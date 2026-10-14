"""Scoped httpx interception for raw LLM and Content Safety calls in mock mode.

Some components call LLM providers (OpenAI, Anthropic, Google) or Azure Content
Safety directly over httpx instead of through ``perceivo.managed_service.ai``.
While the LLM service is mocked, this module patches ``httpx.AsyncClient.post``
to answer requests to those hosts (``api.openai.com`` and ``*.openai.azure.com``,
``api.anthropic.com``, ``generativelanguage.googleapis.com``, and Content Safety
on ``*.cognitiveservices.azure.com``) with a canned response from the LLM mock.
Every other host passes through untouched. The patch is reference-counted, so it
installs once per run and restores cleanly. In ``byo-key`` mode httpx is never
patched.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any, Optional, cast
from urllib.parse import urlsplit

import httpx

_Post = Callable[..., Coroutine[Any, Any, httpx.Response]]
_orig_post: _Post | None = None
_refcount = 0
_registry: Any = None


def _is_openai_host(host: str) -> bool:
    return host == "api.openai.com" or host.endswith(".openai.azure.com")


def _match(url: str) -> Optional[str]:
    """Return the interception kind for *url*, or None to pass through."""
    parts = urlsplit(url.lower())
    host = (parts.hostname or "").rstrip(".")
    path = parts.path
    if host.endswith(".cognitiveservices.azure.com") and path.startswith("/contentsafety/"):
        return "content_safety"
    if _is_openai_host(host) and path.endswith("/chat/completions"):
        return "openai_chat"
    if _is_openai_host(host) and path.endswith("/embeddings"):
        return "openai_embed"
    if host == "api.anthropic.com":
        return "anthropic"
    if host == "generativelanguage.googleapis.com":
        return "google"
    return None


def _completion() -> str:
    return _registry.llm._completion_text if _registry else "mock response"


def _embedding_inputs(payload: Any) -> list[str]:
    """Return the texts an OpenAI embeddings request asks for, one per vector.

    ``input`` is a string, a list of strings, or pre-tokenized input (a list of
    integers for one text, or a list of such lists). The mock answers with one
    vector per text, as the provider does.
    """
    raw = payload.get("input") if isinstance(payload, dict) else None
    if raw is None:
        return [""]
    if isinstance(raw, (list, tuple)):
        if raw and all(isinstance(item, int) for item in raw):
            return [str(raw)]
        return [item if isinstance(item, str) else str(item) for item in raw]
    return [str(raw)]


def _request_payload(kwargs: dict[str, Any]) -> Any:
    """The JSON body of an intercepted ``post``, or ``None`` when it has none."""
    if kwargs.get("json") is not None:
        return kwargs["json"]
    body = kwargs.get("content")
    if body is None:
        body = kwargs.get("data")
    if isinstance(body, (bytes, str)):
        try:
            return json.loads(body)
        except ValueError:
            return None
    return None


def _embedding_body(payload: Any) -> dict[str, Any]:
    texts = _embedding_inputs(payload)
    if _registry is None:
        vectors = [[0.0] for _ in texts]
    else:
        dimensions = payload.get("dimensions") if isinstance(payload, dict) else None
        dim = int(dimensions) if dimensions else _registry.llm._embedding_dim
        vectors = [_registry.llm._vector(text, dim) for text in texts]
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": vec, "index": i} for i, vec in enumerate(vectors)
        ],
        "model": "mock-embed",
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


def _response(url: str, kind: str, payload: Any = None) -> httpx.Response:
    request = httpx.Request("POST", url)
    text = _completion()
    if kind == "content_safety":
        body: dict[str, Any] = {
            "userPromptAnalysis": {"attackDetected": False},
            "documentsAnalysis": [],
        }
    elif kind == "openai_chat":
        body = {
            "id": "mock-openai",
            "model": "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    elif kind == "openai_embed":
        body = _embedding_body(payload)
    elif kind == "anthropic":
        body = {
            "id": "mock-anthropic",
            "model": "mock-model",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    elif kind == "google":
        body = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
    else:  # pragma: no cover - defensive
        body = {"result": text}
    if _registry is not None:
        _registry.record("llm", f"http:{kind}", url[:60])
    return httpx.Response(200, json=body, request=request)


async def _mock_post(
    self: httpx.AsyncClient, url: Any, *args: Any, **kwargs: Any
) -> httpx.Response:
    kind = _match(str(url))
    if kind is not None:
        return _response(str(url), kind, _request_payload(kwargs))
    # install() saves the original method before making this wrapper reachable.
    return await cast(_Post, _orig_post)(self, url, *args, **kwargs)


def install(registry: Any) -> None:
    """Install the interception (reference-counted, idempotent)."""
    global _orig_post, _refcount, _registry
    _registry = registry
    if _refcount == 0:
        _orig_post = httpx.AsyncClient.post
        httpx.AsyncClient.post = _mock_post  # type: ignore[method-assign]
    _refcount += 1


def uninstall() -> None:
    """Remove the interception when the last holder releases it."""
    global _orig_post, _refcount, _registry
    if _refcount == 0:
        return
    _refcount -= 1
    if _refcount == 0 and _orig_post is not None:
        httpx.AsyncClient.post = _orig_post  # type: ignore[method-assign]
        _orig_post = None
        _registry = None
