"""The AI Foundry client wrappers behind ``perceivo.managed_service.ai``.

The client lives in :mod:`perceivo.managed_service._ai_backend`; the functions
these tests cover run on every ``managed_service.ai`` call.
"""

from __future__ import annotations

import inspect

import pytest

from perceivo.managed_service import _ai_backend as ai_foundry
from perceivo.managed_service import ai
from perceivo.types.enums import AIService


async def test_invoke_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await ai_foundry.invoke(model="gpt-4o", messages=[])


async def test_invoke_delegates_full_kwargs(stub_ext):
    seen = {}

    async def invoke_ai_foundry(*, model, messages, system_prompt, temperature, max_tokens):
        seen.update(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {"choices": [{"message": {"content": "ok"}}]}

    stub_ext.invoke_ai_foundry = invoke_ai_foundry
    msgs = [{"role": "user", "content": "hi"}]
    out = await ai_foundry.invoke(
        model="gpt-4o",
        messages=msgs,
        system_prompt="be brief",
        temperature=0.2,
        max_tokens=512,
    )
    assert seen == {
        "model": "gpt-4o",
        "messages": msgs,
        "system_prompt": "be brief",
        "temperature": 0.2,
        "max_tokens": 512,
    }
    assert out["choices"][0]["message"]["content"] == "ok"


async def test_invoke_defaults_temperature_and_max_tokens(stub_ext):
    seen = {}

    async def invoke_ai_foundry(*, model, messages, system_prompt, temperature, max_tokens):
        seen.update(
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {}

    stub_ext.invoke_ai_foundry = invoke_ai_foundry
    await ai_foundry.invoke(model="m", messages=[])
    assert seen == {"system_prompt": None, "temperature": 0.7, "max_tokens": 4096}


async def test_invoke_forwards_reasoning_and_stream_only_when_supplied(stub_ext):
    seen = {}

    async def invoke_ai_foundry(**kwargs):
        seen.clear()
        seen.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    stub_ext.invoke_ai_foundry = invoke_ai_foundry

    # Omitted -> NOT forwarded, so an adapter predating these params (and the
    # existing fixed-signature stubs above) keeps working.
    await ai_foundry.invoke(model="m", messages=[])
    assert "reasoning_effort" not in seen
    assert "stream" not in seen

    # Explicit False is the same as omitted -- the mixin's own default is False,
    # so forwarding it would only break fixed-signature adapters.
    await ai_foundry.invoke(model="m", messages=[], stream=False)
    assert "stream" not in seen

    # Supplied -> forwarded verbatim (the component's choice, not the platform's).
    await ai_foundry.invoke(model="m", messages=[], reasoning_effort="minimal", stream=True)
    assert seen["reasoning_effort"] == "minimal"
    assert seen["stream"] is True
    # The flag is all that crosses. The destination is not the component's to
    # send, so nothing channel-shaped may appear in the forwarded kwargs.
    assert "stream_channel" not in seen


# ─── the component cannot name a stream destination ───
#
# The mirror of tests/unit/test_stream_end.py's
# `test_stream_end_takes_no_channel_argument` in the platform orchestration service,
# aimed at the OTHER door onto the same plane. Neither `perceivo.stream.end()`
# nor `managed_service.ai.invoke` takes a channel argument; one on `invoke` would
# be forwarded into a payload the platform data service publishes token deltas to
# verbatim. Both doors are asserted shut, in the repository that owns each.


def test_invoke_has_no_stream_channel_parameter():
    """The signature IS the control; a `stream_channel=` kwarg must never appear."""
    params = inspect.signature(ai_foundry.invoke).parameters
    assert "stream_channel" not in params
    assert list(params) == [
        "model",
        "messages",
        "system_prompt",
        "temperature",
        "max_tokens",
        "reasoning_effort",
        "stream",
    ]
    # A bool, not a string: a parameter typed `str` is a parameter that can carry
    # a destination, whatever it is named.
    assert params["stream"].annotation == "bool"
    assert params["stream"].default is False


def test_invoke_accepts_no_var_keyword():
    """No `**kwargs` for a caller-supplied channel to ride in on.

    `managed_service.ai.invoke(service, *, model=None, **params)` is open by
    design and forwards `**params` here by name. This function declaring every
    parameter it takes is therefore the only thing that closes that plane: add a
    `**kwargs` here and `ai.invoke(AIService.CHAT, stream_channel=...)` starts
    reaching Redis again, silently, with no other file changed.
    """
    kinds = [p.kind for p in inspect.signature(ai_foundry.invoke).parameters.values()]
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds


async def test_a_component_cannot_reach_a_channel_it_names(stub_ext):
    """The public plane refuses a `stream_channel=` kwarg, before any I/O.

    Driven through `perceivo.managed_service.ai.invoke` -- the spelling a
    component actually writes -- rather than through the private backend, because
    the open `**params` there is exactly what a reader would expect to let this
    through. The extension manager must never be called at all: a channel that
    dies at the forward has not been sent anywhere.
    """
    calls = []

    async def invoke_ai_foundry(**kwargs):
        calls.append(kwargs)
        return {}

    stub_ext.invoke_ai_foundry = invoke_ai_foundry

    with pytest.raises(TypeError, match="stream_channel"):
        await ai.invoke(
            AIService.CHAT,
            model="m",
            messages=[],
            stream_channel="ai:stream:22222222-2222-2222-2222-222222222222",
        )

    assert calls == []


async def test_list_models_delegates(stub_ext):
    async def list_ai_foundry_models():
        return [{"model_name": "gpt-4o", "provider": "openai"}]

    stub_ext.list_ai_foundry_models = list_ai_foundry_models
    out = await ai_foundry.list_models()
    assert out == [{"model_name": "gpt-4o", "provider": "openai"}]


async def test_list_models_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await ai_foundry.list_models()


async def test_embed_delegates_and_returns_vectors(stub_ext):
    seen = {}

    async def embed_ai_foundry(*, model, texts, dimensions):
        seen.update(model=model, texts=texts, dimensions=dimensions)
        return {"embeddings": [[0.1, 0.2], [0.3, 0.4]], "dimensions": 2}

    stub_ext.embed_ai_foundry = embed_ai_foundry
    out = await ai_foundry.embed("text-embedding-3-small", ["one", "two"], 2)
    assert seen == {
        "model": "text-embedding-3-small",
        "texts": ["one", "two"],
        "dimensions": 2,
    }
    assert out == [[0.1, 0.2], [0.3, 0.4]]


async def test_embed_rejects_empty_texts(stub_ext):
    with pytest.raises(ValueError, match="at least one"):
        await ai_foundry.embed("model", [])


async def test_embed_rejects_malformed_response(stub_ext):
    async def embed_ai_foundry(**kwargs):
        return {"dimensions": 2}

    stub_ext.embed_ai_foundry = embed_ai_foundry
    with pytest.raises(RuntimeError, match="did not contain embeddings"):
        await ai_foundry.embed("model", ["text"])


async def test_embed_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await ai_foundry.embed("model", ["text"])


# ─── tokens_in / tokens_out are guaranteed, not hoped for ───
#
# The AI Foundry utility ALWAYS reports both keys. Downstream orchestration END
# nodes read `request_data.get("tokens_out", 0)`, so a key that never arrived
# would become a zero that looked measured, and a run whose generation was never
# accounted for would complete green. These assertions are what make the guarantee a fact rather
# than an intention: each one drives the real `_ai_backend.invoke` and reads
# only what a component reads.


def _chat_body(**overrides):
    """An OpenAI-compatible chat body, as the adapters on this path build it."""
    body = {
        "id": "chatcmpl-1",
        "model": "gpt-4o",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    body.update(overrides)
    return body


def _returning(body):
    async def invoke_ai_foundry(**kwargs):
        return body

    return invoke_ai_foundry


async def test_invoke_reports_tokens_from_a_well_formed_usage_block(stub_ext):
    stub_ext.invoke_ai_foundry = _returning(_chat_body())
    out = await ai_foundry.invoke(model="gpt-4o", messages=[])
    assert out["tokens_in"] == 11
    assert out["tokens_out"] == 7


async def test_invoke_reports_tokens_when_usage_is_absent_entirely(stub_ext):
    """The commonest real case, and the one that produced the silent zeros."""
    body = _chat_body()
    del body["usage"]
    stub_ext.invoke_ai_foundry = _returning(body)
    out = await ai_foundry.invoke(model="gpt-4o", messages=[])
    # Present and integer -- NOT absent, and not None.
    assert out["tokens_in"] == 0
    assert out["tokens_out"] == 0
    assert isinstance(out["tokens_in"], int)
    assert isinstance(out["tokens_out"], int)


async def test_invoke_reports_tokens_when_usage_is_null(stub_ext):
    """`"usage": null` is a JSON body, not a programming error; it must not raise."""
    stub_ext.invoke_ai_foundry = _returning(_chat_body(usage=None))
    out = await ai_foundry.invoke(model="gpt-4o", messages=[])
    assert (out["tokens_in"], out["tokens_out"]) == (0, 0)


async def test_invoke_reports_tokens_when_the_counter_fields_are_missing(stub_ext):
    """A `usage` block carrying only a total. `{}` is the same case."""
    stub_ext.invoke_ai_foundry = _returning(_chat_body(usage={"total_tokens": 18}))
    out = await ai_foundry.invoke(model="gpt-4o", messages=[])
    assert (out["tokens_in"], out["tokens_out"]) == (0, 0)


async def test_invoke_reports_tokens_when_the_counters_are_malformed(stub_ext):
    """Null and non-numeric counters read as absent, never as an exception.

    `True` is in here because `bool` is an `int` subclass: without the explicit
    rejection it would normalize to a token count of 1, which is worse than 0 --
    it is a plausible-looking number.
    """
    stub_ext.invoke_ai_foundry = _returning(
        _chat_body(usage={"prompt_tokens": None, "completion_tokens": True})
    )
    out = await ai_foundry.invoke(model="gpt-4o", messages=[])
    assert (out["tokens_in"], out["tokens_out"]) == (0, 0)


async def test_invoke_reads_the_anthropic_usage_spelling(stub_ext):
    """`input_tokens`/`output_tokens` is a shape this repo really emits.

    `local.mocks.http_intercept` builds it on its `anthropic` branch and
    `local.adapters.ai_foundry._anthropic_to_openai` reads it. Handled because
    it occurs, not on speculation -- no other spelling does.
    """
    stub_ext.invoke_ai_foundry = _returning(
        _chat_body(usage={"input_tokens": 40, "output_tokens": 9})
    )
    out = await ai_foundry.invoke(model="gpt-4o", messages=[])
    assert (out["tokens_in"], out["tokens_out"]) == (40, 9)


async def test_invoke_does_not_clobber_counts_an_adapter_already_supplied(stub_ext):
    """A real count beats a derived one; the `usage` block does not win a fight."""
    stub_ext.invoke_ai_foundry = _returning(_chat_body(tokens_in=99, tokens_out=42))
    out = await ai_foundry.invoke(model="gpt-4o", messages=[])
    assert (out["tokens_in"], out["tokens_out"]) == (99, 42)


async def test_invoke_adds_the_keys_without_disturbing_the_response(stub_ext):
    """ADD, never replace. The OpenAI-compatible body survives intact."""
    body = _chat_body()
    original = dict(body)
    stub_ext.invoke_ai_foundry = _returning(body)
    out = await ai_foundry.invoke(model="gpt-4o", messages=[])

    for key, value in original.items():
        assert out[key] == value
    assert out["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert out["choices"][0]["message"]["content"] == "ok"
    assert set(out) - set(original) == {"tokens_in", "tokens_out"}


async def test_invoke_does_not_crash_on_a_non_dict_answer(stub_ext):
    """A mock or sentinel that is not a body is returned as it was.

    There is nothing to add keys to, and a caller holding this already cannot
    read `choices`. Wrapping it would hide that behind a shape no adapter
    returns; raising would trade a visible wrong answer for a new failure.
    """
    stub_ext.invoke_ai_foundry = _returning(None)
    assert await ai_foundry.invoke(model="m", messages=[]) is None

    sentinel = object()
    stub_ext.invoke_ai_foundry = _returning(sentinel)
    assert await ai_foundry.invoke(model="m", messages=[]) is sentinel


async def test_the_public_plane_carries_the_guarantee(stub_ext):
    """Driven through the spelling a component actually writes.

    `managed_service.ai.invoke(AIService.CHAT, ...)` reaches the backend via
    `ai._forward`; asserting only against the private function would leave the
    plane a component really uses unproven.
    """
    stub_ext.invoke_ai_foundry = _returning(_chat_body())
    out = await ai.invoke(AIService.CHAT, model="gpt-4o", messages=[])
    assert out["tokens_in"] == 11
    assert out["tokens_out"] == 7


# ─── and the rule stops where it stops ───


async def test_embed_is_not_given_token_keys(stub_ext):
    """`embed` returns bare vectors; it has no body to carry the keys on.

    Pinned rather than left implicit: the rule names the AI Foundry *invoke*
    path, and extending it here would mean changing a documented return type
    (`ai.invoke(AIService.EMBEDDING, ...)` promises `list[list[float]]`) to
    carry an accounting the SDK cannot even see -- `LocalAIFoundryAdapter.embed`
    rebuilds its answer as `{"embeddings": [...]}` and drops the provider's
    `usage` block before this function is ever reached.
    """

    async def embed_ai_foundry(*, model, texts, dimensions):
        return {"embeddings": [[0.1, 0.2]], "usage": {"prompt_tokens": 5}}

    stub_ext.embed_ai_foundry = embed_ai_foundry
    out = await ai_foundry.embed("text-embedding-3-small", ["one"])
    assert out == [[0.1, 0.2]]


async def test_groundedness_body_is_still_returned_verbatim(stub_ext):
    """A Content Safety body is not a chat completion and gets no token keys.

    It has no `usage` and never will; adding zeros would write fabricated
    accounting into a third-party body this function documents as verbatim.
    """

    async def check_groundedness(*, text, grounding_sources, query):
        return {"ungroundedPercentage": 0.25}

    stub_ext.check_groundedness = check_groundedness
    out = await ai_foundry.groundedness("claim", ["source"])
    assert out == {"ungroundedPercentage": 0.25}
