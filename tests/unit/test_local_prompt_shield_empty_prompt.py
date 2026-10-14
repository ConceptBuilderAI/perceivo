"""A local prompt shield refuses an empty prompt exactly as the platform does: HTTP 400."""

from __future__ import annotations

import httpx
import pytest

from perceivo.config import Config
from perceivo.local.context import LocalExtensionManager

_DETAIL = (
    "user_prompt is empty. Azure returns attackDetected: false for an empty prompt, "
    "which is indistinguishable from a screened-and-safe verdict, so this refuses "
    "rather than manufacturing one."
)


@pytest.mark.parametrize("prompt", ["", "   ", "\n\t"], ids=["empty", "spaces", "whitespace"])
async def test_an_empty_prompt_is_refused_with_the_platform_400(prompt):
    ext = LocalExtensionManager(Config())
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await ext.check_prompt_shield(user_prompt=prompt, documents=["d"])
    assert caught.value.response.status_code == 400
    assert caught.value.response.json() == {"detail": _DETAIL}


async def test_a_non_empty_prompt_is_screened():
    ext = LocalExtensionManager(Config())
    out = await ext.check_prompt_shield(user_prompt=" hi ")
    assert out["userPromptAnalysis"] == {"attackDetected": False}
