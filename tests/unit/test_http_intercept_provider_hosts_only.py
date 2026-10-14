"""The LLM HTTP intercept answers only the provider hosts it mocks; every other host passes."""

from __future__ import annotations

import pytest

from perceivo.local.mocks.http_intercept import _match


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://api.openai.com/v1/chat/completions", "openai_chat"),
        ("https://api.openai.com/v1/embeddings", "openai_embed"),
        (
            "https://acme.openai.azure.com/openai/deployments/d/chat/completions",
            "openai_chat",
        ),
        ("https://acme.openai.azure.com/openai/deployments/d/embeddings", "openai_embed"),
        ("https://api.anthropic.com/v1/messages", "anthropic"),
        ("https://generativelanguage.googleapis.com/v1/m:generateContent", "google"),
        (
            "https://acme.cognitiveservices.azure.com/contentsafety/text:shieldPrompt",
            "content_safety",
        ),
        (
            "https://acme.cognitiveservices.azure.com/contentsafety/text:analyze",
            "content_safety",
        ),
    ],
)
def test_a_mocked_provider_host_is_intercepted(url, kind):
    assert _match(url) == kind


@pytest.mark.parametrize(
    "url",
    [
        "https://search.internal.example.com/api/v2/embeddings",
        "https://my-gateway.example.com/v1/messages",
        "https://crm.example.com/chat/completions-history",
        "https://cs.example.com/contentsafety/text:shieldPrompt",
        "https://tools.example.com/x:generateContent",
        "https://api.openai.com.evil.example.com/v1/chat/completions",
        "https://acme.cognitiveservices.azure.com/vision/analyze",
    ],
)
def test_any_other_host_passes_through(url):
    assert _match(url) is None
