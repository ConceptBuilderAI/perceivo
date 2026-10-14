"""Local bring-your-own-key LLM adapter for ``perceivo.managed_service.ai``.

On the platform, ``perceivo.managed_service.ai.invoke()`` calls managed Azure AI
Foundry deployments with no key needed. Locally there is no managed broker, so
this adapter sends those calls to a real provider (OpenAI, an OpenAI-compatible
endpoint, or Anthropic) using a key from the ``ai_foundry`` section of
``perceivo.yaml`` or the environment.

Anthropic responses are translated into the OpenAI Chat Completions shape the
managed broker returns, so components read
``response["choices"][0]["message"]["content"]`` unchanged.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import httpx

from ...types.errors import ConfigurationError, ServiceError

logger = logging.getLogger("perceivo.local.ai_foundry")

#: How much of a provider error body to quote: enough for the message and parameter
#: name, not enough to paste an echoed prompt into an exception string.
_BODY_LIMIT = 500


def _body_excerpt(resp: httpx.Response) -> str:
    """Quote the provider's error body, which is where the actual reason is."""
    text = resp.text.strip()
    if not text:
        return "<empty response body>"
    if len(text) > _BODY_LIMIT:
        return f"{text[:_BODY_LIMIT]}... (truncated, {len(text)} chars)"
    return text


def _raise_for_status(resp: httpx.Response, operation: str) -> None:
    """Turn a provider HTTP error into a :class:`ServiceError`.

    The message quotes the provider's error body, which says which parameter was
    rejected or that the key is out of quota. 429 and 5xx are marked retryable;
    any other 4xx is the key, the model name or the payload, and fails identically
    on retry.

    Raises:
        ServiceError: If ``resp`` is an error status.
    """
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise ServiceError(
            f"AI Foundry {operation} failed with HTTP {status}: {_body_excerpt(exc.response)}",
            retryable=status == 429 or status >= 500,
            status_code=status,
        ) from exc


_ANTHROPIC_DEFAULT_MODEL = "claude-opus-4-8"
_ANTHROPIC_VERSION = "2023-06-01"
_OPENAI_STYLE = {"openai", "openai_compatible", "azure_openai"}

# Alias sets tried, in order, when neither api_key nor api_key_secret is set.
_DEFAULT_ALIASES = {
    "openai": ("api_key", "openai_api_key", "OPENAI_API_KEY"),
    "openai_compatible": ("api_key", "openai_api_key", "OPENAI_API_KEY"),
    "azure_openai": ("api_key", "openai_api_key", "OPENAI_API_KEY"),
    "anthropic": ("api_key", "anthropic_api_key", "ANTHROPIC_API_KEY"),
}

#: The providers this adapter implements. A name outside this set is refused, never
#: defaulted, so a key is never sent to a provider the user did not name.
_PROVIDERS: frozenset[str] = frozenset(_DEFAULT_ALIASES)

# Only providers with a single well-known endpoint get a default. An
# OpenAI-compatible or Azure OpenAI endpoint must always be configured explicitly.
_DEFAULT_BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

SecretResolver = Callable[[str], Awaitable[Optional[str]]]

_NOT_ENABLED = (
    "AI Foundry is not available in local development mode. Enable a "
    "bring-your-own-LLM provider in perceivo.yaml to run AI-Foundry-backed "
    "orchestrations locally:\n"
    "  ai_foundry:\n"
    "    enabled: true\n"
    "    provider: openai        # or openai_compatible / azure_openai / anthropic\n"
    "    api_key_secret: api_key # a bound secret alias, or set api_key directly\n"
    "Or set env vars: PERCEIVO_AI_FOUNDRY_ENABLED=true, PERCEIVO_LLM_PROVIDER=..., "
    "and provide the key via a bound secret / OPENAI_API_KEY / ANTHROPIC_API_KEY."
)


class LocalAIFoundryAdapter:
    """Route managed-AI-Foundry calls to a real provider using a developer key."""

    def __init__(self, config: Any, secret_resolver: Optional[SecretResolver] = None):
        self._config = config
        self._secret_resolver = secret_resolver

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False))

    @property
    def provider(self) -> str:
        """The configured provider, refused unless this adapter implements it.

        An unknown name is never resolved to a default, so a typo cannot send a key to
        another provider's endpoint.

        Raises:
            ConfigurationError: If the provider is not implemented.
        """
        name = (getattr(self._config, "provider", "openai") or "openai").strip().lower()
        if name not in _PROVIDERS:
            raise ConfigurationError(
                f"ai_foundry.provider={name!r} is not a provider this SDK "
                f"implements, so there is no endpoint to send the request to and "
                f"no correct one to assume. Admissible values: "
                f"{', '.join(sorted(_PROVIDERS))}."
            )
        return name

    def _base_url(self) -> str:
        configured = getattr(self._config, "base_url", None)
        if configured:
            return str(configured).rstrip("/")
        default = _DEFAULT_BASE_URL.get(self.provider)
        if default is None:
            raise ConfigurationError(
                f"ai_foundry.provider={self.provider!r} has no default endpoint — "
                f"it names a protocol or a per-deployment host, not a service — "
                f"so ai_foundry.base_url is required with it. Set it to your own "
                f"endpoint, e.g.\n"
                f"  ai_foundry:\n"
                f"    provider: {self.provider}\n"
                f"    base_url: https://<your-endpoint>\n"
                f"Not defaulting to https://api.openai.com/v1: that would send "
                f"your key to a service you did not name."
            )
        return default.rstrip("/")

    async def _resolve_key(self) -> str:
        """Resolve the provider API key from config, secret, then environment.

        Both routes to a real provider pass through here, so the credential's shape is
        checked once.

        Raises:
            ConfigurationError: If the configured key is not a string.
            ConfigurationError: If no key is found.
        """
        literal = getattr(self._config, "api_key", None)
        # YAML can hand over a list or a number here; anything but a string is refused.
        if literal is not None and not isinstance(literal, str):
            raise ConfigurationError(
                f"ai_foundry.api_key must be a string, but it is a "
                f"{type(literal).__name__}. Set exactly one value, quoted:\n"
                f"  ai_foundry:\n"
                f'    api_key: "<your key>"\n'
                f"Not sending it as-is: a credential that is not text cannot be "
                f"one, and the provider would answer 401 as though it were "
                f"merely wrong."
            )
        # A blank key is treated as absent, so it falls through to the alias sweep.
        if literal and literal.strip():
            return literal

        aliases: list[str] = []
        secret_alias = getattr(self._config, "api_key_secret", None)
        if secret_alias:
            aliases.append(secret_alias)
        # Indexed, not defaulted: ``self.provider`` already refused unknown names, and a
        # fallback would add OPENAI_API_KEY to the aliases for a non-OpenAI provider.
        aliases.extend(_DEFAULT_ALIASES[self.provider])

        for alias in aliases:
            if self._secret_resolver is not None:
                value = await self._secret_resolver(alias)
                if value:
                    return value
            env_value = os.environ.get(alias)
            if env_value:
                return env_value

        raise ConfigurationError(
            f"No API key found for the local AI-Foundry '{self.provider}' provider. "
            f"Set ai_foundry.api_key, bind a secret and set ai_foundry.api_key_secret, "
            f"or export one of: {', '.join(aliases)}."
        )

    def _resolve_model(self, model: Optional[str], *, embedding: bool = False) -> str:
        override: str | None = getattr(
            self._config, "embedding_model" if embedding else "model", None
        )
        if override:
            return override
        if model:
            return model
        if self.provider == "anthropic" and not embedding:
            return _ANTHROPIC_DEFAULT_MODEL
        raise RuntimeError("No model specified for the local AI-Foundry call.")

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
        """Invoke a chat model; returns an OpenAI-compatible response dict."""
        if not self.enabled:
            raise RuntimeError(_NOT_ENABLED)
        api_key = await self._resolve_key()
        resolved_model = self._resolve_model(model)
        chat_messages = list(messages or [])

        if self.provider == "anthropic":
            return await self._invoke_anthropic(
                api_key, resolved_model, chat_messages, system_prompt, max_tokens
            )
        return await self._invoke_openai(
            api_key, resolved_model, chat_messages, system_prompt, temperature, max_tokens
        )

    async def _invoke_openai(
        self,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        full_messages: list[dict[str, Any]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        payload = {
            "model": model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            _raise_for_status(resp, "chat completion")
            result: dict[str, Any] = resp.json()
            return result

    async def _invoke_anthropic(
        self,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        system_prompt: Optional[str],
        max_tokens: int,
    ) -> dict[str, Any]:
        # Anthropic Messages API: system prompt is a top-level field; temperature
        # is intentionally omitted (rejected with 400 on current Claude models).
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages
            ],
        }
        if system_prompt:
            payload["system"] = system_prompt
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url()}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
            )
            _raise_for_status(resp, "chat completion")
            return _anthropic_to_openai(resp.json(), model)

    async def embed(
        self,
        *,
        model: Optional[str] = None,
        texts: Optional[list[str]] = None,
        dimensions: Optional[int] = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Generate embeddings; returns ``{"embeddings": [[...], ...]}``."""
        if not self.enabled:
            raise RuntimeError(_NOT_ENABLED)
        if self.provider == "anthropic":
            raise RuntimeError(
                "The 'anthropic' provider has no embeddings API. Configure an "
                "OpenAI-compatible provider for perceivo.managed_service.ai.embed()."
            )
        api_key = await self._resolve_key()
        resolved_model = self._resolve_model(model, embedding=True)
        payload: dict[str, Any] = {"model": resolved_model, "input": list(texts or [])}
        if dimensions is not None:
            payload["dimensions"] = dimensions
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url()}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            _raise_for_status(resp, "embeddings")
            data = resp.json().get("data", [])
        return {"embeddings": [item.get("embedding") for item in data]}

    async def list_models(self) -> list[dict[str, Any]]:
        """Report the locally-configured BYO model(s)."""
        if not self.enabled:
            raise RuntimeError(_NOT_ENABLED)
        model = getattr(self._config, "model", None)
        entry = {
            "model_name": model or "byo-local",
            "display_name": model or f"Local BYO ({self.provider})",
            "provider": self.provider,
            "local": True,
        }
        return [entry]


def _anthropic_to_openai(response: dict[str, Any], model: str) -> dict[str, Any]:
    """Translate an Anthropic Messages response to the OpenAI Chat shape."""
    parts = response.get("content", []) or []
    text = "".join(
        block.get("text", "")
        for block in parts
        if isinstance(block, dict) and block.get("type") == "text"
    )
    usage = response.get("usage", {}) or {}
    prompt_tokens = usage.get("input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    return {
        "id": response.get("id"),
        "model": response.get("model", model),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": response.get("stop_reason"),
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
