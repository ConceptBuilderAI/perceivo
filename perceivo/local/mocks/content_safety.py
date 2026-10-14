"""Mock Content Safety adapter: prompt shield and groundedness, offline.

Hosted runs answer ``ai.invoke(CONTENT_SAFETY)`` and ``ai.invoke(GROUNDEDNESS)``
through the extension manager's ``check_prompt_shield`` and
``check_groundedness``. Locally they are served here in the Azure Content Safety
response shape, so a component that screens input or scores answers runs
unchanged.

The default result is *safe* and *grounded*. It is a canned value, not an
evaluation, and the mock trace records every call. Seed
``mocks.seed.llm.content_safety`` to exercise the other branch:

``attack_detected`` (bool)
    Result for every user prompt.
``attack_patterns`` (list of str)
    Case-insensitive substrings; a prompt or document containing one is
    flagged. Lets one run exercise both branches deterministically.
``documents_attack_detected`` (bool)
    Result for every grounding document.
``ungrounded_percentage`` (float in [0, 1])
    Groundedness score for every answer; ``> 0`` also sets
    ``ungroundedDetected``.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

# The platform's refusal of an empty prompt, word for word, so a local run fails the same way.
_EMPTY_PROMPT_DETAIL = (
    "user_prompt is empty. Azure returns attackDetected: false for an empty prompt, "
    "which is indistinguishable from a screened-and-safe verdict, so this refuses "
    "rather than manufacturing one."
)


class MockContentSafety:
    """Deterministic, seedable stand-in for the Content Safety broker."""

    def __init__(self, seed: dict[str, Any]):
        self._seed = seed or {}

    def _patterns(self) -> list[str]:
        raw = self._seed.get("attack_patterns") or []
        if not isinstance(raw, list):
            return []
        return [str(p).lower() for p in raw if str(p)]

    def _matches(self, text: Any) -> bool:
        lowered = str(text or "").lower()
        return any(p in lowered for p in self._patterns())

    async def prompt_shield(
        self, *, user_prompt: str = "", documents: Optional[list[Any]] = None, **_: Any
    ) -> dict[str, Any]:
        """Return a Prompt Shields body: one prompt result, one per document.

        Raises:
            httpx.HTTPStatusError: *user_prompt* is empty or whitespace-only. The
                status is 400 and the body is the platform's ``{"detail": ...}``.
        """
        if not user_prompt or not user_prompt.strip():
            request = httpx.Request("POST", "http://local/ai-foundry/internal/prompt-shield")
            httpx.Response(
                400, json={"detail": _EMPTY_PROMPT_DETAIL}, request=request
            ).raise_for_status()
        prompt_flag = bool(self._seed.get("attack_detected", False)) or self._matches(user_prompt)
        doc_default = bool(self._seed.get("documents_attack_detected", False))
        return {
            "userPromptAnalysis": {"attackDetected": prompt_flag},
            "documentsAnalysis": [
                {"attackDetected": doc_default or self._matches(doc)} for doc in (documents or [])
            ],
        }

    async def groundedness(
        self,
        *,
        text: str = "",
        grounding_sources: Optional[list[Any]] = None,
        query: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Return a groundedness body with a seeded ungrounded percentage."""
        try:
            pct = float(self._seed.get("ungrounded_percentage", 0.0))
        except (TypeError, ValueError):
            pct = 0.0
        pct = min(max(pct, 0.0), 1.0)
        return {
            "ungroundedDetected": pct > 0.0,
            "ungroundedPercentage": pct,
            "ungroundedDetails": [],
        }
