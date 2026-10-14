"""SPEC-5: local bring-your-own-LLM adapter for perceivo.infra.ai_foundry.*.

Exercises key resolution (config / secret / env), the OpenAI-compatible path,
the Anthropic → OpenAI response translation, embeddings, and the disabled state.
All provider HTTP is mocked via httpx.MockTransport — no network is touched.
"""

from __future__ import annotations

import httpx
import pytest

from perceivo.config import AIFoundryConfig
from perceivo.local.adapters.ai_foundry import LocalAIFoundryAdapter, _anthropic_to_openai
from perceivo.types.errors import ConfigurationError, ServiceError


def _mock_client_factory(handler):
    """Patch httpx.AsyncClient so the adapter uses a MockTransport."""
    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    return _Client


async def test_disabled_raises_with_enablement_hint():
    adapter = LocalAIFoundryAdapter(AIFoundryConfig(enabled=False))
    with pytest.raises(RuntimeError, match="AI Foundry is not available"):
        await adapter.invoke(model="gpt-5.2", messages=[{"role": "user", "content": "hi"}])


async def test_openai_invoke_passes_through_and_prepends_system(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hello!"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))

    cfg = AIFoundryConfig(enabled=True, provider="openai", api_key="sk-test")
    adapter = LocalAIFoundryAdapter(cfg)
    out = await adapter.invoke(
        model="gpt-5.2",
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="You are terse.",
    )
    assert out["choices"][0]["message"]["content"] == "hello!"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["url"].endswith("/chat/completions")
    # system prompt is prepended as the first message
    assert captured["body"]["messages"][0] == {"role": "system", "content": "You are terse."}
    assert captured["body"]["model"] == "gpt-5.2"


async def test_anthropic_invoke_translated_to_openai_shape(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "translated answer"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))

    cfg = AIFoundryConfig(enabled=True, provider="anthropic", api_key="sk-ant")
    adapter = LocalAIFoundryAdapter(cfg)
    out = await adapter.invoke(
        model="claude-opus-4-8",
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="Be helpful.",
        temperature=0.9,
    )
    # OpenAI-compatible response shape so sample components keep working
    assert out["choices"][0]["message"]["content"] == "translated answer"
    assert out["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    # Anthropic wire contract: x-api-key + version header, system top-level, no temperature
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "sk-ant"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["body"]["system"] == "Be helpful."
    assert "temperature" not in captured["body"]


async def test_anthropic_default_model_when_unspecified(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "x"}], "usage": {}})

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="anthropic", api_key="k")
    )
    await adapter.invoke(messages=[{"role": "user", "content": "hi"}])
    assert captured["body"]["model"] == "claude-opus-4-8"


async def test_key_resolved_from_secret_alias(monkeypatch):
    seen_aliases = []

    async def resolver(alias):
        seen_aliases.append(alias)
        return "sk-from-secret" if alias == "openai_api_key" else None

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer sk-from-secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
    cfg = AIFoundryConfig(enabled=True, provider="openai")
    adapter = LocalAIFoundryAdapter(cfg, secret_resolver=resolver)
    out = await adapter.invoke(model="gpt-5.2", messages=[{"role": "user", "content": "hi"}])
    assert out["choices"][0]["message"]["content"] == "ok"
    # tried the default aliases in order; 'api_key' first (None), then 'openai_api_key'
    assert seen_aliases[:2] == ["api_key", "openai_api_key"]


async def test_key_resolved_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer sk-env"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
    adapter = LocalAIFoundryAdapter(AIFoundryConfig(enabled=True, provider="openai"))
    await adapter.invoke(model="gpt-5.2", messages=[{"role": "user", "content": "hi"}])


async def test_missing_key_raises_helpful_error():
    adapter = LocalAIFoundryAdapter(AIFoundryConfig(enabled=True, provider="openai"))
    with pytest.raises(
        ConfigurationError, match="No API key found for the local AI-Foundry 'openai' provider"
    ):
        await adapter.invoke(model="gpt-5.2", messages=[{"role": "user", "content": "hi"}])


async def test_embed_openai_returns_vectors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        assert body["input"] == ["a", "b"]
        assert request.url.path.endswith("/embeddings")
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
        )

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
    cfg = AIFoundryConfig(enabled=True, provider="openai", api_key="k")
    adapter = LocalAIFoundryAdapter(cfg)
    out = await adapter.embed(model="text-embedding-3-small", texts=["a", "b"])
    assert out == {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}


async def test_embed_anthropic_unsupported():
    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="anthropic", api_key="k")
    )
    with pytest.raises(RuntimeError, match="no embeddings API"):
        await adapter.embed(model="x", texts=["a"])


async def test_list_models_reports_configured_model():
    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="openai", model="gpt-5.2")
    )
    models = await adapter.list_models()
    assert models[0]["model_name"] == "gpt-5.2"
    assert models[0]["provider"] == "openai"


# ---------------------------------------------------------------------------
# A provider name is not a value to guess at
# ---------------------------------------------------------------------------


def _explode(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
    raise AssertionError(f"a request was sent to {request.url}")


async def test_unknown_provider_is_refused_rather_than_routed_to_openai(monkeypatch):
    """A typo must not become a credential disclosure.

    If ``provider: anthropicc`` fell through ``_DEFAULT_BASE_URL.get(name, ...)``
    to ``https://api.openai.com/v1`` *and* through the alias table to
    ``OPENAI_API_KEY``, an Anthropic key would go out in an Authorization header
    addressed to a different company — and the run would look like an ordinary
    auth failure, which is the shape that gets debugged by pasting the key
    somewhere.
    """
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(_explode))
    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="anthropicc", api_key="sk-ant")
    )
    with pytest.raises(ConfigurationError) as excinfo:
        await adapter.invoke(model="m", messages=[{"role": "user", "content": "hi"}])
    message = str(excinfo.value)
    assert "anthropicc" in message
    # The admissible set is named, because "not a provider" without the list
    # leaves the user guessing at exactly the value that just misfired.
    assert "anthropic" in message
    assert "azure_openai" in message


@pytest.mark.parametrize("provider", ["openai_compatible", "azure_openai"])
async def test_a_provider_without_a_well_known_host_requires_base_url(monkeypatch, provider):
    """These two are *supported*, and defaulted to another vendor's server.

    "OpenAI-compatible" names a protocol, not a host, and every Azure OpenAI
    deployment lives at its own ``https://<resource>.openai.azure.com``. Neither
    has a correct default, so there is nothing to fall back to; falling back
    anyway sent the user's key to a service they did not name.
    """
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(_explode))
    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider=provider, api_key="sk-test")
    )
    with pytest.raises(ConfigurationError) as excinfo:
        await adapter.invoke(model="m", messages=[{"role": "user", "content": "hi"}])
    assert "base_url" in str(excinfo.value)


async def test_the_openai_key_sweep_is_not_reachable_for_another_provider(monkeypatch):
    """The alias table is indexed, not ``.get(..., openai)``.

    Refusing the unknown provider closes the base-URL half of the disclosure;
    this closes the credential half. An ambient ``OPENAI_API_KEY`` must not be
    swept up on behalf of a provider that is not OpenAI.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-openai")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "x"}], "usage": {}})

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-configured")
    adapter = LocalAIFoundryAdapter(AIFoundryConfig(enabled=True, provider="anthropic"))
    await adapter.invoke(model="claude-opus-4-8", messages=[{"role": "user", "content": "hi"}])
    assert captured["headers"]["x-api-key"] == "sk-ant-configured"
    assert "sk-ambient-openai" not in str(captured["headers"])


async def test_the_two_providers_with_a_well_known_host_still_have_one(monkeypatch):
    """The narrowing did not take the defaults that were correct.

    ``openai`` and ``anthropic`` each have exactly one endpoint everybody uses,
    so requiring ``base_url`` from them would be a gratuitous break.
    """
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
    adapter = LocalAIFoundryAdapter(AIFoundryConfig(enabled=True, provider="openai", api_key="k"))
    await adapter.invoke(model="m", messages=[{"role": "user", "content": "hi"}])
    assert seen == ["https://api.openai.com/v1/chat/completions"]


@pytest.mark.parametrize(
    "api_key, described",
    [(12345, "int"), (["a", "b"], "list"), ({"k": "v"}, "dict")],
)
async def test_a_credential_that_is_not_a_string_is_refused(monkeypatch, api_key, described):
    """``AIFoundryConfig.api_key`` is declared ``Optional[str]``; YAML disagrees.

    ``api_key:\\n  - a\\n  - b`` reached the wire as ``Bearer ['a', 'b']`` and
    came back 401 — the provider reporting that the key is wrong, for something
    that was never a key. The 401 sends the user to check a credential rather
    than to fix the two lines of YAML that produced a list.
    """
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(_explode))
    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="openai", api_key=api_key)
    )
    with pytest.raises(ConfigurationError, match=described):
        await adapter.invoke(model="m", messages=[{"role": "user", "content": "hi"}])


async def test_a_blank_credential_is_absent_rather_than_sent(monkeypatch):
    """Blank falls through to the alias sweep and then to a message that helps.

    Sent, ``api_key: "   "`` fails as ``LocalProtocolError: Illegal header value
    b'Bearer    '`` — an HTTP framing complaint, four layers below the config
    mistake, and only after the request was already being built.
    """
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(_explode))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = LocalAIFoundryAdapter(AIFoundryConfig(enabled=True, provider="openai", api_key="   "))
    with pytest.raises(
        ConfigurationError, match="No API key found for the local AI-Foundry 'openai' provider"
    ):
        await adapter.invoke(model="m", messages=[{"role": "user", "content": "hi"}])


def test_anthropic_to_openai_helper_joins_text_blocks():
    resp = {
        "id": "m",
        "content": [
            {"type": "text", "text": "part1 "},
            {"type": "thinking", "text": "ignored"},
            {"type": "text", "text": "part2"},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    out = _anthropic_to_openai(resp, "claude-opus-4-8")
    assert out["choices"][0]["message"]["content"] == "part1 part2"
    assert out["usage"]["total_tokens"] == 3


# ---------------------------------------------------------------------------
# Provider HTTP errors surface as ServiceError, with the body
#
# A bare resp.raise_for_status() would let httpx.HTTPStatusError escape the
# adapter: a component would have to catch an httpx type to handle a local
# provider failure, and the provider's body -- the part naming the rejected
# parameter or the exhausted quota -- would be reachable only through
# exc.response.
# ---------------------------------------------------------------------------


def _error_client(status: int, body: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    return _mock_client_factory(handler)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [
        (400, False),  # bad parameter: identical on a retry
        (401, False),  # bad key
        (404, False),  # unknown deployment
        (429, True),  # rate limited: the one 4xx worth retrying
        (500, True),
        (503, True),
    ],
)
async def test_openai_invoke_http_error_is_a_service_error(monkeypatch, status, retryable):
    body = '{"error": {"message": "the provider said why", "param": "max_tokens"}}'
    monkeypatch.setattr(httpx, "AsyncClient", _error_client(status, body))

    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="openai", api_key="sk-test")
    )
    with pytest.raises(ServiceError) as excinfo:
        await adapter.invoke(model="gpt-5.2", messages=[{"role": "user", "content": "hi"}])

    err = excinfo.value
    assert err.status_code == status
    assert err.retryable is retryable
    # The reason the call failed is in the message, not only in __cause__.
    assert "the provider said why" in str(err)
    assert "max_tokens" in str(err)
    assert f"HTTP {status}" in str(err)
    assert "chat completion" in str(err)
    # The httpx type is still available for anyone who wants it, but it is no
    # longer what escapes the adapter.
    assert isinstance(err.__cause__, httpx.HTTPStatusError)


@pytest.mark.parametrize(("status", "retryable"), [(403, False), (502, True)])
async def test_anthropic_invoke_http_error_is_a_service_error(monkeypatch, status, retryable):
    monkeypatch.setattr(httpx, "AsyncClient", _error_client(status, "upstream said no"))

    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="anthropic", api_key="sk-ant")
    )
    with pytest.raises(ServiceError) as excinfo:
        await adapter.invoke(model="claude-opus-4-8", messages=[{"role": "user", "content": "hi"}])

    assert excinfo.value.status_code == status
    assert excinfo.value.retryable is retryable
    assert "upstream said no" in str(excinfo.value)
    assert "chat completion" in str(excinfo.value)


@pytest.mark.parametrize(("status", "retryable"), [(422, False), (500, True)])
async def test_embed_http_error_is_a_service_error(monkeypatch, status, retryable):
    monkeypatch.setattr(httpx, "AsyncClient", _error_client(status, "bad input"))

    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="openai", api_key="sk-test")
    )
    with pytest.raises(ServiceError) as excinfo:
        await adapter.embed(model="text-embedding-3-small", texts=["a"])

    assert excinfo.value.status_code == status
    assert excinfo.value.retryable is retryable
    assert "bad input" in str(excinfo.value)
    # The operation name distinguishes an embeddings failure from a chat one.
    assert "embeddings" in str(excinfo.value)


async def test_a_long_error_body_is_truncated_not_dropped(monkeypatch):
    """A provider that echoes the prompt must not paste it into the message."""
    body = "x" * 5000
    monkeypatch.setattr(httpx, "AsyncClient", _error_client(500, body))

    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="openai", api_key="sk-test")
    )
    with pytest.raises(ServiceError) as excinfo:
        await adapter.invoke(model="gpt-5.2", messages=[{"role": "user", "content": "hi"}])

    message = str(excinfo.value)
    assert "truncated, 5000 chars" in message
    assert len(message) < 700
    assert "x" * 500 in message


async def test_an_empty_error_body_says_so_rather_than_nothing(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _error_client(500, ""))

    adapter = LocalAIFoundryAdapter(
        AIFoundryConfig(enabled=True, provider="openai", api_key="sk-test")
    )
    with pytest.raises(ServiceError, match="<empty response body>"):
        await adapter.invoke(model="gpt-5.2", messages=[{"role": "user", "content": "hi"}])
