"""The LLM HTTP intercept matches provider hosts exactly, not as substrings."""

from __future__ import annotations

import pytest

from perceivo.local.mocks.http_intercept import _match


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://api.anthropic.com/v1/complete", "anthropic"),
        ("https://generativelanguage.googleapis.com/v1/models", "google"),
    ],
)
def test_a_provider_host_is_intercepted(url, kind):
    assert _match(url) == kind


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/?next=api.anthropic.com",
        "https://api.anthropic.com.evil.example.com/v1/complete",
        "https://example.com/generativelanguage.googleapis.com",
        "https://proxy.example.com/v1/messages",
    ],
)
def test_a_host_named_only_in_the_path_or_as_a_prefix_passes_through(url):
    assert _match(url) is None
