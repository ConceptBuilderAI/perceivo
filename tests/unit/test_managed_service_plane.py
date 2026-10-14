"""``perceivo.managed_service`` — the dual-mode AI plane, and ``database``.

Three properties carry this module, and they are the acceptance criteria for the
AI half:

1. **Unconfigured is mocked.** ``import perceivo`` and call it; you get an
   answer, offline, with no credential and no account.
2. **Configured calls the real service.** Enter config and the real client is
   constructed and the request is attempted.
3. **Real-but-uncredentialed raises, and never degrades to the mock.** This is
   the one that has to be a test rather than a docstring. A silent fallback
   turns "I asked the model" into "something answered", and the run is green
   either way — so the only place the difference can be caught is here.

Every assertion is made **through the new name**. Asserting through the
backend would prove the implementation still works and say nothing about the
plane being built.

The three preservation comparison tests are the deliberate exception -- they call
both sides: the plane and the private backend module it forwards to
(:mod:`perceivo.managed_service._ai_backend`, :mod:`perceivo.managed_service._backend`).
What they catch is the plane growing a second implementation instead of
forwarding to the one backend -- which would let the plane and its backend produce
different run records for the same asset, and is an easy mistake to make, since a
private sibling module is an inconspicuous thing to bypass.

**No credential appears in this file.** ``_PLACEHOLDER_KEY`` is a literal string
that cannot authenticate against anything; the transport is patched at
``httpx.AsyncClient.post`` so nothing leaves the process either way. The suite
does not read ``OPENAI_API_KEY`` or any other ambient key — and
``test_an_ambient_provider_key_is_not_swept_up`` asserts the *product* does not
either.

This module covers how a call resolves. The plane's shape — which capabilities
the enum admits, the ``database`` segment, and the callability and import-graph
controls — is asserted in ``test_managed_service_surface_and_database.py``,
which carries the ``_backend`` half of the preservation comparison.
"""

from __future__ import annotations

import logging

import httpx
import pytest

import perceivo
from perceivo.managed_service import _ai_backend
from perceivo.types.errors import ConfigurationError

from .fixtures_managed_service import _AI

#: Not a credential. A syntactically valid string in the position a key would
#: occupy, so the code path that would use a key is exercised without one
#: existing. It authenticates against nothing, and the transport never runs.
_PLACEHOLDER_KEY = "placeholder-not-a-credential-unit-test-only"


# ---------------------------------------------------------------------------
# The transport boundary
#
# ``clean_config`` — the other half of the isolation every test below needs —
# is shared with ``test_managed_service_surface_and_database.py``, so it lives
# in ``fixtures_managed_service.py`` and is registered through
# ``tests/unit/conftest.py``.
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_transport(monkeypatch):
    """Patch ``httpx.AsyncClient.post`` — the transport boundary — and record.

    Patched at the boundary rather than at the adapter, because the claim under
    test is that the *real client* was built and the request was *attempted*.
    Stubbing the adapter would assert the stub.
    """
    calls: list[dict] = []

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    payload = {
        "id": "real-provider-response",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "real"}}],
    }

    async def _post(self, url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response(payload)

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return calls


# ---------------------------------------------------------------------------
# 1. Unconfigured -> mock
# ---------------------------------------------------------------------------


async def test_unconfigured_invoke_returns_a_mock(clean_config, cleared_context):
    """No config, no context, no credential — and still an answer.

    Outside a run the plane answers from the mock rather than raising "not
    available outside component execution". The mock is identifiable from the response itself —
    ``MockLLM`` stamps its own id — which is what stops a mocked answer being
    mistaken for a real one two files downstream.
    """
    response = await _AI.invoke(
        perceivo.types.AIService.CHAT,
        model="gpt-4o",
        messages=[{"role": "user", "content": "hello"}],
    )
    assert response["id"] == "mock-llm-completion"
    assert response["choices"][0]["message"]["content"]


async def test_the_mock_says_it_is_a_mock_in_the_run_output(clean_config, cleared_context, caplog):
    """A mocked call is labelled MOCKED where a human will see it.

    The response shape is deliberately *not* modified to carry a marker — that
    would make the mock's payload differ from the platform's and break the one
    property the mock exists to provide. The label goes in the log instead, at
    WARNING, because "this proved nothing" is not a debug-level fact.
    """
    with caplog.at_level(logging.WARNING, logger="perceivo.managed_service.ai"):
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert any("MOCKED" in record.getMessage() for record in caplog.records)


async def test_unconfigured_embedding_returns_vectors_not_an_envelope(
    clean_config, cleared_context
):
    """``invoke(EMBEDDING, …)`` returns what ``ai_foundry.embed`` returns.

    The adapters (mock and real alike) return ``{"embeddings": [...]}``;
    ``ai_foundry.embed`` unwraps it to ``list[list[float]]`` and that is what the
    6 rewritable corpus call sites consume. The unwrap is not duplicated here —
    the standalone provider is pushed onto the execution context so the *same*
    ``ai_foundry.embed`` runs in both modes. This asserts that arrangement holds.
    """
    vectors = await _AI.invoke(
        perceivo.types.AIService.EMBEDDING, model="text-embedding-3-large", texts=["a", "b"]
    )
    assert isinstance(vectors, list)
    assert len(vectors) == 2
    assert all(isinstance(value, float) for value in vectors[0])


async def test_embedding_refuses_input_in_place_of_texts(clean_config, cleared_context):
    """Embeddings take ``texts=`` only; ``input=`` is an unknown keyword."""
    with pytest.raises(TypeError, match="input"):
        await _AI.invoke(perceivo.types.AIService.EMBEDDING, model="m", input=["a"])


async def test_unconfigured_models_returns_the_mock_catalog(clean_config, cleared_context):
    listed = await _AI.models()
    assert [entry["model_name"] for entry in listed] == ["mock-model"]


# ---------------------------------------------------------------------------
# 2. Configured -> the real call is attempted
# ---------------------------------------------------------------------------


async def test_configured_constructs_the_real_client_and_attempts_the_call(
    clean_config, cleared_context, captured_transport
):
    """Config entered the way a user enters it; request stopped at the socket.

    Asserting on the URL *and* the Authorization header matters more than
    asserting the response: the header is the only evidence that the key the
    call carried is the key the user configured, rather than one picked up from
    somewhere else on the machine.
    """
    clean_config(
        "ai_foundry:\n"
        "  enabled: true\n"
        "  provider: openai\n"
        "  base_url: https://provider.invalid/v1\n"
        f"  api_key: {_PLACEHOLDER_KEY}\n"
    )

    response = await _AI.invoke(
        perceivo.types.AIService.CHAT,
        model="gpt-4o",
        messages=[{"role": "user", "content": "hello"}],
        system_prompt="be brief",
    )

    assert len(captured_transport) == 1, "the real call was not attempted"
    attempt = captured_transport[0]
    assert attempt["url"] == "https://provider.invalid/v1/chat/completions"
    assert attempt["headers"]["Authorization"] == f"Bearer {_PLACEHOLDER_KEY}"
    assert attempt["json"]["model"] == "gpt-4o"
    assert attempt["json"]["messages"][0] == {"role": "system", "content": "be brief"}
    assert response["id"] == "real-provider-response"


async def test_configured_never_serves_the_mock(clean_config, cleared_context, captured_transport):
    """The other direction of the same claim, and it is not redundant.

    A resolver that returned *both* providers, or that fell through to the mock
    after a successful real call, would still satisfy the assertion above. This
    one fails if a mock response reaches the caller at all.
    """
    clean_config(
        f"ai_foundry:\n  enabled: true\n  provider: openai\n  api_key: {_PLACEHOLDER_KEY}\n"
    )
    response = await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert response["id"] != "mock-llm-completion"


async def test_config_can_be_entered_by_environment_variable(
    clean_config, cleared_context, captured_transport, monkeypatch
):
    """``PERCEIVO_LLM_API_KEY`` is the documented no-file route, and it works.

    Separate from the YAML test because the two reach ``ai_foundry.api_key``
    through different code in ``config.load``, and a user with no
    ``perceivo.yaml`` has only this one.
    """
    monkeypatch.setenv("PERCEIVO_AI_FOUNDRY_ENABLED", "true")
    monkeypatch.setenv("PERCEIVO_LLM_API_KEY", _PLACEHOLDER_KEY)
    await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert len(captured_transport) == 1


# ---------------------------------------------------------------------------
# 3. Real requested, credential absent -> raises. Never a mock.
# ---------------------------------------------------------------------------


async def test_real_requested_without_a_credential_raises(clean_config, cleared_context):
    """The acceptance criterion this plane exists for.

    The message has to name the key, because the whole failure is "you did not
    tell me which" and a caller who cannot see which key is missing will guess.
    """
    clean_config("ai_foundry:\n  enabled: true\n  provider: openai\n")

    with pytest.raises(ConfigurationError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])

    message = str(excinfo.value)
    assert "ai_foundry.api_key" in message
    assert "PERCEIVO_LLM_API_KEY" in message


async def test_mocking_off_with_no_credential_raises_rather_than_mocking(
    clean_config, cleared_context
):
    """Turning the mock off is also "asking for the real service".

    ``mocks.enabled: false`` with nothing configured resolves to mode
    ``disabled``. The tempting reading is "nothing to do, serve the mock"; the
    correct one is that the developer has explicitly said not to. Both config
    keys are named because both are unset.
    """
    clean_config("mocks:\n  enabled: false\n")

    with pytest.raises(ConfigurationError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])

    message = str(excinfo.value)
    assert "ai_foundry.enabled" in message
    assert "ai_foundry.api_key" in message


async def test_a_blank_credential_is_treated_as_absent_not_as_a_credential(
    clean_config, cleared_context, captured_transport
):
    """Clause (c) says *absent or malformed*, and a blank key is both.

    ``api_key: "   "`` is truthy, so a truthiness guard would pass it and fail
    four layers down as ``LocalProtocolError: Illegal header value b'Bearer    '``
    -- an HTTP framing message for what is really "you left the key blank", from
    a call that had already been dispatched. It is refused here instead, before
    the socket.
    """
    clean_config('ai_foundry:\n  enabled: true\n  provider: openai\n  api_key: "   "\n')

    with pytest.raises(ConfigurationError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert "ai_foundry.api_key" in str(excinfo.value)
    assert captured_transport == [], "a call was dispatched with a blank credential"


async def test_a_provider_this_sdk_does_not_implement_is_refused(
    clean_config, cleared_context, captured_transport
):
    """The same refusal seen through the plane the components actually call.

    The adapter-level test covers the mechanism; this one covers the route, and
    the two are not interchangeable -- the plane resolves config independently
    and could have normalised the provider before the adapter ever saw it.
    """
    clean_config(
        f"ai_foundry:\n  enabled: true\n  provider: not-a-provider\n  api_key: {_PLACEHOLDER_KEY}\n"
    )
    with pytest.raises(ConfigurationError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert "not-a-provider" in str(excinfo.value)
    assert captured_transport == [], "the key went out to a host nobody named"


async def test_an_ambient_provider_key_is_not_swept_up(
    clean_config, cleared_context, captured_transport, monkeypatch
):
    """A key the caller did not point at this call is not a credential for it.

    ``LocalAIFoundryAdapter`` walks ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``
    out of the environment when its own config has no key — reasonable for a
    developer's bring-your-own-key loop, wrong here. Picking one up means the
    call succeeds against whatever account that variable belongs to, billed to
    it, with nothing in the run saying so. The credential must come from config.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-value-that-must-not-be-used")
    clean_config("ai_foundry:\n  enabled: true\n  provider: openai\n")

    with pytest.raises(ConfigurationError):
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert captured_transport == [], "the ambient key was used to make a call"


async def test_a_bound_secret_alias_is_refused_outside_a_run_by_name(clean_config, cleared_context):
    """``api_key_secret`` is resolved by the runtime's secrets service.

    Outside a run there is no such service, so the alias is unresolvable — and
    the error has to say *that*, not "no credential", or the user re-checks a
    binding that was fine all along.
    """
    clean_config("ai_foundry:\n  enabled: true\n  provider: openai\n  api_key_secret: my_alias\n")
    with pytest.raises(ConfigurationError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert "api_key_secret" in str(excinfo.value)


async def test_the_credential_never_appears_in_a_failure(
    clean_config, cleared_context, monkeypatch, caplog
):
    """A failing provider call must not echo the key into the error or the log.

    This is the leak shape that reads as helpful: an exception rendering the
    config object, or a "calling {url} with {headers}" debug line. Either one
    puts the credential into a CI log, a bug report and a ticket comment at
    once. Asserted over the whole exception chain because ``raise … from`` and
    implicit chaining both re-render the cause.
    """

    async def _post(self, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    clean_config(
        f"ai_foundry:\n  enabled: true\n  provider: openai\n  api_key: {_PLACEHOLDER_KEY}\n"
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(httpx.ConnectError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])

    rendered = []
    error: BaseException | None = excinfo.value
    while error is not None:
        rendered += [str(error), repr(error)]
        error = error.__cause__ or error.__context__
    rendered += [record.getMessage() for record in caplog.records]

    assert not any(_PLACEHOLDER_KEY in text for text in rendered)


# ---------------------------------------------------------------------------
# Inside a run: forwarded unchanged
# ---------------------------------------------------------------------------


async def test_inside_a_run_the_payload_is_identical_to_the_backends(stub_ext):
    """The claim is preservation, so the payload is the thing to compare.

    Both names are driven through the same extension manager with the same
    arguments and the captured kwargs are compared to each other — not to a
    hand-written expected dict, which would only prove that whoever wrote the
    test and whoever wrote the code agreed.
    """
    seen: list[dict] = []

    async def invoke_ai_foundry(**kwargs):
        seen.append(kwargs)
        return {"choices": [{"message": {"content": "from the runtime"}}]}

    stub_ext.invoke_ai_foundry = invoke_ai_foundry
    arguments = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "system_prompt": "be brief",
        "temperature": 0.2,
        "max_tokens": 512,
    }

    through_plane = await _AI.invoke(perceivo.types.AIService.CHAT, **arguments)
    through_backend = await _ai_backend.invoke(**arguments)

    assert seen[0] == seen[1]
    assert through_plane == through_backend


async def test_inside_a_run_the_mock_config_is_never_consulted(stub_ext, clean_config):
    """A run's answer comes from its runtime, not from a ``perceivo.yaml``.

    Reversing this is the failure that would matter most and would be caught
    last: config-first resolution inside a Ray pod finds no ``perceivo.yaml``,
    defaults to mock, and every AI call in production becomes canned text that
    still parses.
    """
    clean_config("ai_foundry:\n  enabled: true\n  api_key: unused\n")

    async def invoke_ai_foundry(**kwargs):
        return {"id": "from-the-runtime"}

    stub_ext.invoke_ai_foundry = invoke_ai_foundry
    response = await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert response["id"] == "from-the-runtime"


async def test_inside_a_run_embedding_is_unwrapped_the_same_way(stub_ext):
    async def embed_ai_foundry(**kwargs):
        return {"embeddings": [[0.25, 0.5]]}

    stub_ext.embed_ai_foundry = embed_ai_foundry
    assert await _AI.invoke(perceivo.types.AIService.EMBEDDING, model="m", texts=["a"]) == [
        [0.25, 0.5]
    ]


async def test_the_standalone_context_does_not_outlive_the_call(clean_config, cleared_context):
    """Pushed for one call, popped in ``finally``.

    A leaked context is invisible until the *next* call in the same task takes
    the inside-a-run branch and talks to a provider chosen by a config that has
    since changed.
    """
    from perceivo._internal.context import _ExecutionContext

    assert _ExecutionContext.is_active() is False
    await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert _ExecutionContext.is_active() is False

    clean_config("ai_foundry:\n  enabled: true\n")
    with pytest.raises(ConfigurationError):
        await _AI.invoke(perceivo.types.AIService.CHAT, model="m", messages=[])
    assert _ExecutionContext.is_active() is False
