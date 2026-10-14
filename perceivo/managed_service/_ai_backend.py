"""Implement the AI Foundry calls behind :mod:`perceivo.managed_service.ai`.

Every call routes through the runtime's extension manager to the platform,
which handles Azure AD authentication. The public entry points are
:func:`perceivo.managed_service.ai.invoke` and its siblings.
"""

from __future__ import annotations

from typing import Any, Optional, cast

from .._internal.context import _ExecutionContext
from ..types.errors import ServiceError

__all__ = ["embed", "groundedness", "invoke", "list_models", "prompt_shield"]


# Usage spellings handled: OpenAI ``prompt_tokens``/``completion_tokens`` and
# Anthropic ``input_tokens``/``output_tokens``.
_TOKENS_IN_KEYS = ("prompt_tokens", "input_tokens")
_TOKENS_OUT_KEYS = ("completion_tokens", "output_tokens")


def _as_token_count(value: Any) -> Optional[int]:
    """Return one usage field as an ``int``, or ``None`` when it is not a count.

    ``bool`` is rejected, since ``True`` would otherwise count as 1. ``float`` is
    accepted because JSON has a single number type. Anything else reads as
    absent, so the caller falls through to the next spelling.

    Args:
        value: The raw usage field.

    Returns:
        The token count, or ``None``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _with_token_counts(response: Any) -> Any:
    """Return *response* with integer ``tokens_in`` and ``tokens_out`` keys.

    The provider body is copied through untouched; only the two top-level keys
    are added. A count the adapter already supplied is kept. A missing, null or
    malformed ``usage`` block yields zeros, and this never raises. A non-dict
    answer is returned untouched.

    Args:
        response: The adapter's response.

    Returns:
        The response with both token keys present.
    """
    if not isinstance(response, dict):
        return response
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    resolved: dict[str, int] = {}
    for key, spellings in (("tokens_in", _TOKENS_IN_KEYS), ("tokens_out", _TOKENS_OUT_KEYS)):
        count = _as_token_count(response.get(key))
        if count is None:
            for spelling in spellings:
                count = _as_token_count(usage.get(spelling))
                if count is not None:
                    break
        resolved[key] = 0 if count is None else count
    return {**response, **resolved}


async def invoke(
    model: str | None,
    messages: list[dict[str, str]],
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    reasoning_effort: Optional[str] = None,
    stream: bool = False,
) -> dict[str, Any]:
    """Invoke an AI Foundry serverless model.

    The platform handles Azure AD authentication, so no API keys are needed.

    Args:
        model: Model name (e.g. "gpt-4o", "llama-3.1-70b"). None delegates
            model selection to the runtime; a default is not guaranteed.
        messages: Chat messages in OpenAI format [{"role": ..., "content": ...}].
        system_prompt: Optional system prompt prepended to messages.
        temperature: Sampling temperature (0.0 to 2.0).
        max_tokens: Maximum tokens in the response.
        reasoning_effort: Optional per-call reasoning effort for reasoning models
            (e.g. gpt-5-nano: "minimal" | "low" | "medium" | "high"). Forwarded
            to the model verbatim only when supplied; the component chooses the
            value, never the platform. Lower effort means fewer hidden reasoning
            tokens and lower latency.
        stream: Relay this generation's token deltas to this run's own stream
            as they arrive. The call still returns the fully assembled response.
            On a run dispatched without a stream it relays nothing and does not
            raise, so the same component works in both cases. This is a flag,
            not a destination: the stream is resolved server-side from the run,
            and there is no channel argument.

    Returns:
        OpenAI-compatible response dict with "choices" and "usage" keys, plus
        ``tokens_in`` and ``tokens_out``. Those two are always present and
        always ``int``, derived from ``usage`` and ``0`` when it is absent, null
        or malformed. Nothing in the provider's body is removed or rewritten.

    Example::

        response = await perceivo.managed_service.ai.invoke(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Summarize this."}],
            system_prompt="You are a legal analyst.",
            temperature=0.3,
        )
        text = response["choices"][0]["message"]["content"]
    """
    ext = _ExecutionContext.get_ext()
    # Optional parameters are forwarded only when the caller set them, so adapters
    # without them keep working. There is no ``**kwargs``: no caller-supplied channel.
    extra: dict[str, Any] = {}
    if reasoning_effort is not None:
        extra["reasoning_effort"] = reasoning_effort
    if stream:
        extra["stream"] = True
    return cast(
        dict[str, Any],
        _with_token_counts(
            await ext.invoke_ai_foundry(
                model=model,
                messages=messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                **extra,
            )
        ),
    )


async def groundedness(
    text: str,
    grounding_sources: list[str],
    query: str = "",
) -> dict[str, Any]:
    """Score how well *text* is supported by *grounding_sources*.

    Reached as ``perceivo.managed_service.ai.invoke(AIService.GROUNDEDNESS, ...)``.
    The platform calls Azure AI Content Safety with its managed identity. No
    credential and no endpoint is resolved in the component.

    Args:
        text: The claim to check, typically a model's answer.
        grounding_sources: The documents it should be grounded in. The platform
            truncates and caps these; score in batches rather than assume the
            tail was read.
        query: The question the answer was for. Optional: when supplied, the
            answer is judged against the question; when omitted, the text is
            judged alone.

    Returns:
        The provider body, verbatim. ``ungroundedPercentage`` is a fraction in
        ``[0.0, 1.0]``; a groundedness score is ``1.0 - ungroundedPercentage``.

    Raises:
        NotImplementedError: The runtime does not serve groundedness.
        ServiceError: The runtime answered, but not with a body.
    """
    ext = _ExecutionContext.get_ext()
    # ``getattr``, not a bare call: the runtime may predate this method.
    forward = getattr(ext, "check_groundedness", None)
    if forward is None:
        raise NotImplementedError(
            f"perceivo.managed_service.ai.invoke(GROUNDEDNESS) needs "
            f"`check_groundedness` on the runtime's extension manager, and "
            f"{type(ext).__name__} does not have it. The SDK backs this "
            f"capability; this runtime does not serve it yet."
        )
    result = await forward(
        text=text,
        grounding_sources=list(grounding_sources),
        query=query,
    )
    # A failed HTTP call surfaces as ``None``; never pass it on as a result.
    if not isinstance(result, dict):
        raise ServiceError(
            "The groundedness check did not return a result body "
            f"(got {type(result).__name__}). The call failed upstream; the "
            "runtime log has the HTTP error. This is not a score of zero.",
            retryable=True,
        )
    return result


async def prompt_shield(
    user_prompt: str,
    documents: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Screen *user_prompt*, and any *documents*, for prompt-injection attacks.

    Reached as ``perceivo.managed_service.ai.invoke(AIService.CONTENT_SAFETY, ...)``.
    The platform calls Azure AI Content Safety with its managed identity. No
    credential and no endpoint is resolved in the component.

    A screening that could not run raises. It never returns a safe result.

    Args:
        user_prompt: The end-user text to screen. Empty or whitespace-only text
            is refused, because an empty prompt would come back looking clean.
        documents: Retrieved or attached documents to screen alongside it, for
            indirect (cross-prompt) injection. ``None`` and ``[]`` are the same
            request.

    Returns:
        The provider body, verbatim. ``userPromptAnalysis.attackDetected`` is
        the result, and ``documentsAnalysis`` carries one entry per document
        supplied. A body is returned only when it carries a boolean
        ``userPromptAnalysis.attackDetected``.

    Raises:
        httpx.HTTPStatusError: *user_prompt* is empty or whitespace-only. The
            status is 400, and the response body's ``detail`` says why.
        NotImplementedError: The runtime does not serve prompt shield.
        ServiceError: The runtime answered, but not with a body carrying a
            boolean result. ``retryable`` is true for a non-mapping (a transport
            failure) and false for a mapping with no result (a wrong answer
            that repeating reproduces).
    """
    ext = _ExecutionContext.get_ext()
    # ``getattr``, not a bare call: the runtime may predate this method.
    forward = getattr(ext, "check_prompt_shield", None)
    if forward is None:
        raise NotImplementedError(
            f"perceivo.managed_service.ai.invoke(CONTENT_SAFETY) needs "
            f"`check_prompt_shield` on the runtime's extension manager, and "
            f"{type(ext).__name__} does not have it. The SDK backs this "
            f"capability; this runtime does not serve it yet. Nothing was "
            f"screened -- this is not a verdict of safe."
        )
    result = await forward(
        user_prompt=user_prompt,
        documents=list(documents or []),
    )
    # A failed HTTP call surfaces as ``None``, which a caller would read as clean.
    if not isinstance(result, dict):
        raise ServiceError(
            "The prompt shield check did not return a result body "
            f"(got {type(result).__name__}). The call failed upstream; the "
            "runtime log has the HTTP error. This is NOT a verdict of safe -- "
            "nothing was screened.",
            retryable=True,
        )
    # A mapping with no boolean ``attackDetected`` would read as clean through
    # ``.get`` chains, so it is refused too.
    analysis = result.get("userPromptAnalysis")
    detected = analysis.get("attackDetected") if isinstance(analysis, dict) else None
    if not isinstance(detected, bool):
        raise ServiceError(
            "The prompt shield answered with no boolean "
            "userPromptAnalysis.attackDetected. A body with no verdict is an "
            "unevaluated prompt, and it is falsy in exactly the place a caller "
            "reads it, so passing it on would be answering a safety check with "
            "a value that means nothing. This is NOT a verdict of safe.",
            retryable=False,
        )
    return result


async def list_models() -> list[dict[str, Any]]:
    """List available AI Foundry model deployments.

    Returns:
        List of model dicts with keys: model_name, display_name, provider,
        capabilities, max_context_tokens, etc.

    Example::

        models = await perceivo.managed_service.ai.models()
        for m in models:
            print(f"{m['display_name']} ({m['provider']})")
    """
    ext = _ExecutionContext.get_ext()
    return cast(list[dict[str, Any]], await ext.list_ai_foundry_models())


async def embed(
    model: str | None,
    texts: list[str],
    dimensions: int | None = None,
) -> list[list[float]]:
    """Generate vectors using a platform-managed embedding deployment."""
    if not texts:
        raise ValueError("texts must contain at least one item")
    ext = _ExecutionContext.get_ext()
    response = await ext.embed_ai_foundry(
        model=model,
        texts=texts,
        dimensions=dimensions,
    )
    vectors = response.get("embeddings")
    if not isinstance(vectors, list):
        raise RuntimeError("Managed embedding response did not contain embeddings")
    return vectors
