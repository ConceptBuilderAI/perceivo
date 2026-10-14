"""Platform AI services: one verb, ``invoke``, selected by an enum.

Every managed AI capability is reached through ``managed_service.ai.invoke``
with a :class:`~perceivo.types.AIService` member. The handle is a module, so
it cannot be called.

Dual mode
---------

1. **Inside a run**, the call is forwarded to the runtime, which decides
   whether the platform deployment or a local mock serves it.
2. **Outside a run** (a notebook, a script, a test), :func:`perceivo.config.load`
   is read and ``config.service_mode("llm")`` decides: mocked by default, real
   once the developer has configured a provider.

Both modes share one request-assembly and response-unwrapping path, so
``invoke(AIService.EMBEDDING, ...)`` returns a bare ``list[list[float]]`` in
either mode.

A missing credential is never a mock
------------------------------------

If the real service is requested and no credential is configured, this raises
:class:`~perceivo.types.ConfigurationError` naming the absent config key; it
does not fall back to a mock. Outside a run the credential comes only from
config: ``ai_foundry.api_key`` in ``perceivo.yaml`` or the
``PERCEIVO_LLM_API_KEY`` override. Inside a local run it is
``ai_foundry.api_key``, else the secret named by ``ai_foundry.api_key_secret``,
else the provider's key aliases (for OpenAI ``api_key``, ``openai_api_key``
and ``OPENAI_API_KEY``), each looked up in perceivo secrets and then in the
environment. No credential value is logged, echoed into an exception or
written anywhere.

Unbacked members
----------------

``SPEECH_TO_TEXT``, ``TEXT_TO_SPEECH``, ``VISION``, ``TRANSLATION`` and
``DOCUMENT_INTEL`` raise ``NotImplementedError``. ``GROUNDEDNESS`` and
``CONTENT_SAFETY`` are served only by the platform: outside a run they raise
rather than mock, and on a runtime that does not serve them they raise
``NotImplementedError`` naming the runtime. A failed screening never returns a
result shaped like a clean one.
"""

from __future__ import annotations

from typing import Any

from .._internal.context import _ExecutionContext
from ..types.enums import AIService
from ..types.errors import ConfigurationError
from ..types.pagination import Page
from ._ai_standalone import _standalone_runtime

__all__ = ["invoke", "models"]

#: Members with no implementation. Listed explicitly so a new enum member must
#: be placed on one side deliberately.
_UNBACKED: frozenset[AIService] = frozenset(
    {
        AIService.SPEECH_TO_TEXT,
        AIService.TEXT_TO_SPEECH,
        AIService.VISION,
        AIService.TRANSLATION,
        AIService.DOCUMENT_INTEL,
    }
)

#: Members only the platform can serve; outside a run they refuse rather than
#: mock, because a mocked safety result is indistinguishable from a real one.
_PLATFORM_ONLY: frozenset[AIService] = frozenset({AIService.GROUNDEDNESS, AIService.CONTENT_SAFETY})

#: Closing clause of the outside-a-run refusal, one per ``_PLATFORM_ONLY`` member.
_PLATFORM_ONLY_MOCK_REFUSAL: dict[AIService, str] = {
    AIService.GROUNDEDNESS: (
        "a groundedness score for a check that never ran is worse than no score."
    ),
    AIService.CONTENT_SAFETY: (
        "an attackDetected: false for a prompt that was never screened is a "
        "safety gate reporting green having inspected nothing."
    ),
}


async def invoke(
    service: AIService | str,
    *,
    model: str | None = None,
    **params: Any,
) -> Any:
    """Invoke a platform AI capability.

    Args:
        service: Which capability. A :class:`perceivo.types.AIService` member, or
            the equivalent string; ``invoke("chat", ...)`` and
            ``invoke(AIService.CHAT, ...)`` are the same call.
        model: Deployment name, e.g. ``"gpt-4o"``. Optional; forwarded verbatim,
            including ``None``. Refused for ``GROUNDEDNESS`` and
            ``CONTENT_SAFETY``, whose deployment is resolved server-side.
        **params: Passed through to the backend unchanged. Unknown names raise
            ``TypeError``.

            - ``CHAT``: ``messages``, ``system_prompt``, ``temperature``,
              ``max_tokens``, ``reasoning_effort``, ``stream``. ``stream`` is a
              flag; the destination is resolved server-side from the run.
            - ``EMBEDDING``: ``texts`` and ``dimensions``.
            - ``GROUNDEDNESS``: ``text``, ``grounding_sources`` and optional
              ``query``.
            - ``CONTENT_SAFETY``: ``user_prompt`` and optional ``documents``.

    Returns:
        For ``CHAT``, the OpenAI-compatible response dict. For ``EMBEDDING``,
        ``list[list[float]]``. For ``GROUNDEDNESS``, the Azure AI Content Safety
        ``detectGroundedness`` body, whose ``ungroundedPercentage`` is a fraction
        in ``[0.0, 1.0]``; a groundedness score is ``1.0 - ungroundedPercentage``.
        For ``CONTENT_SAFETY``, the ``shieldPrompt`` body:
        ``userPromptAnalysis.attackDetected`` is the result and
        ``documentsAnalysis`` carries one entry per document supplied. Provider
        bodies are returned verbatim.

        No return value means "could not screen": every failure to screen raises.

    Raises:
        ConfigurationError: *service* is not an ``AIService`` member; the real
            service was requested and no credential is configured; or a
            ``model=`` was supplied to a member that resolves its deployment
            server-side.
        httpx.HTTPStatusError: ``CONTENT_SAFETY`` was given an empty or
            whitespace-only ``user_prompt``. The status is 400.
        NotImplementedError: The capability is not implemented, or
            ``GROUNDEDNESS`` / ``CONTENT_SAFETY`` was requested on a runtime that
            does not serve it.

    Example::

        r = await perceivo.managed_service.ai.invoke(
            perceivo.types.AIService.CHAT,
            model="gpt-4o",
            messages=[{"role": "user", "content": "Summarize this."}],
        )
        text = r["choices"][0]["message"]["content"]

        v = await perceivo.managed_service.ai.invoke(
            perceivo.types.AIService.EMBEDDING,
            model="text-embedding-3-large",
            texts=["chunk one", "chunk two"],
        )

        g = await perceivo.managed_service.ai.invoke(
            perceivo.types.AIService.GROUNDEDNESS,
            text=answer,
            grounding_sources=[doc_a, doc_b],
            query=question,
        )
        score = 1.0 - g["ungroundedPercentage"]
    """
    resolved = _coerce(service)
    if resolved in _UNBACKED:
        raise NotImplementedError(
            f"perceivo.managed_service.ai.invoke({resolved.name}) is not backed "
            f"by the platform yet. It is server-side work still to be built; the "
            f"SDK has nothing to forward to. Today's backed capabilities are "
            f"CHAT, EMBEDDING, GROUNDEDNESS and CONTENT_SAFETY (the last two only "
            f"on a runtime whose extension manager serves them)."
        )
    if _ExecutionContext.is_active():
        return await _forward(resolved, model, params)
    if resolved in _PLATFORM_ONLY:
        raise ConfigurationError(
            f"perceivo.managed_service.ai.invoke({resolved.name}) is served by "
            f"the platform's own Azure AI Content Safety resource, using the "
            f"platform's managed identity, and has no bring-your-own-key "
            f"equivalent to fall back to outside a run. Run the component on "
            f"the platform. Not answering from the local mock: "
            f"{_PLATFORM_ONLY_MOCK_REFUSAL[resolved]}"
        )
    with _standalone_runtime():
        return await _forward(resolved, model, params)


async def models() -> Page[dict[str, Any]]:
    """List the AI deployments this organization can invoke.

    Resolves mock or real by the same two steps as :func:`invoke`. The listing
    is not paged on the server, so the result is one complete page:
    ``page_size == total == len(data)`` and ``has_next`` is ``False``. ``Page``
    defines no ``__len__``; use ``len(page.data)`` or ``page.total``.

    Returns:
        A :class:`~perceivo.types.pagination.Page` of deployment dicts.
    """
    if _ExecutionContext.is_active():
        return Page.of(await _list_models())
    with _standalone_runtime():
        return Page.of(await _list_models())


def _coerce(service: AIService | str) -> AIService:
    """Normalize *service* to an ``AIService``.

    Raises:
        ConfigurationError: *service* is not an ``AIService`` value. The
            message lists the admissible values.
    """
    try:
        return AIService(service)
    except ValueError as exc:
        admissible = ", ".join(member.value for member in AIService)
        raise ConfigurationError(
            f"{service!r} is not a perceivo.types.AIService. Admissible values: {admissible}."
        ) from exc


async def _forward(service: AIService, model: str | None, params: dict[str, Any]) -> Any:
    """Call the backend client, which owns request assembly and response shape."""
    from . import _ai_backend as client

    if service is AIService.EMBEDDING:
        return await client.embed(model=model, **params)
    if service is AIService.GROUNDEDNESS:
        # The deployment is resolved server-side from the catalog; a ``model=`` would
        # be silently discarded, so it is refused here.
        if model is not None:
            raise ConfigurationError(
                f"invoke(AIService.GROUNDEDNESS, model={model!r}) -- "
                f"groundedness takes no model. The deployment is the "
                f"platform's Content Safety resource, resolved server-side "
                f"from the ai_foundry_models catalog; there is no per-call "
                f"choice for this argument to express."
            )
        return await client.groundedness(**params)
    if service is AIService.CONTENT_SAFETY:
        # Same as groundedness: the deployment is resolved server-side.
        if model is not None:
            raise ConfigurationError(
                f"invoke(AIService.CONTENT_SAFETY, model={model!r}) -- prompt "
                f"shield takes no model. The deployment is the platform's "
                f"Content Safety resource, resolved server-side from the "
                f"ai_foundry_models catalog; there is no per-call choice for "
                f"this argument to express."
            )
        return await client.prompt_shield(**params)
    return await client.invoke(model=model, **params)


async def _list_models() -> list[dict[str, Any]]:
    from . import _ai_backend as client

    return await client.list_models()
