"""How a run records the difference between a mocked call and a real one.

The real AI provider is a product surface: ``ai_foundry.enabled``
plus a key sends a component's model calls off-machine, and everything else
stays mocked. That split is only trustworthy if the run record says which of the
two happened, so these tests pin the recording on BOTH sides of the branch, the
refusal to invent a credential when one is absent, and the fact that no
credential value survives into a produced artifact (safety rules 3 and 4).

Split out of ``test_mock_system.py``: with this section inline that module
reached 662 lines.

Every test here stubs ``httpx.AsyncClient``. A test about whether a real request
would be made must never be able to make one.
"""

from __future__ import annotations

import json

import httpx
import pytest

from perceivo.config import Config
from perceivo.local.context import LocalExtensionManager
from perceivo.types.errors import ConfigurationError

#: Never a credential. Used where a test needs a key-shaped string to assert is
#: *absent* from a produced artifact.
_PLACEHOLDER_KEY = "PLACEHOLDER-NOT-A-REAL-KEY"


def _byo_key_config(**ai):
    """A config whose LLM mode resolves to ``byo-key`` rather than ``mock``."""
    cfg = Config()
    cfg.ai_foundry.enabled = True
    cfg.ai_foundry.api_key = _PLACEHOLDER_KEY
    for name, value in ai.items():
        setattr(cfg.ai_foundry, name, value)
    assert cfg.service_mode("llm") == "byo-key", "the fixture did not leave mock mode"
    return cfg


def _responds(body):
    """A handler that answers every request with ``body``, opening no socket."""
    return lambda request: httpx.Response(200, json=body)


def _transport(handler):
    """Patch-in for ``httpx.AsyncClient`` that answers from ``handler``."""
    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    return _Client


async def test_a_real_ai_call_is_recorded_and_stamped_with_its_mode(monkeypatch):
    """The real provider is a product surface, so the record has to show it.

    If only the mocked branch called ``record``, the run record would be
    unable to distinguish "this was a real call" from "no model was ever
    invoked": both produce ``mock_calls: []`` and both print "0 mock call(s)
    served". The mode field on ``MockCall`` exists for exactly this --
    "a single platform call served by a mock (or a real/local backend)".
    """
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _transport(_responds({"choices": [{"message": {"content": "real"}}]})),
    )
    ext = LocalExtensionManager(_byo_key_config())
    out = await ext.invoke_ai_foundry(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])

    assert out["choices"][0]["message"]["content"] == "real"
    assert [(c.service, c.operation, c.mode) for c in ext._mocks.trace.calls] == [
        ("llm", "invoke", "byo-key")
    ]


async def test_a_mocked_ai_call_is_still_stamped_mock():
    """The other side of the branch -- and it is what the preservation check rests on.

    Every corpus run on the local bench is fully mocked, so altering this stamp
    would move every baselined run record at once.
    """
    ext = LocalExtensionManager(Config())
    await ext.invoke_ai_foundry(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert [(c.operation, c.mode) for c in ext._mocks.trace.calls] == [("invoke", "mock")]


async def test_embed_and_list_models_record_their_mode_too(monkeypatch):
    """All three AI methods, not just ``invoke``.

    ``list_models`` opens no socket in either mode, so it is the case where a
    one-sided record is least visible and most misleading.
    """
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _transport(lambda r: httpx.Response(200, json={"data": [{"embedding": [0.1]}]})),
    )
    ext = LocalExtensionManager(_byo_key_config(model="gpt-5.2"))
    await ext.embed_ai_foundry(model="text-embedding-3-small", texts=["a"])
    await ext.list_ai_foundry_models()
    assert [(c.operation, c.mode) for c in ext._mocks.trace.calls] == [
        ("embed", "byo-key"),
        ("list_models", "byo-key"),
    ]


async def test_a_missing_credential_does_not_become_the_mock_secret(monkeypatch):
    """The local bench must not go off-machine to discover a key is missing.

    Secrets are mocked by default, and the mocked secrets service answers every
    alias with the canned string ``mock-secret``. The AI-Foundry adapter took
    that as the credential, so ``ai_foundry.enabled: true`` with no key anywhere
    opened a live HTTPS connection to ``api.openai.com`` carrying
    ``Authorization: Bearer mock-secret``. The 401 that came back reads as "your
    key is wrong" -- sending the developer to check a key they never set, which
    is the debugging loop that gets keys pasted into places keys should not go.

    The transport is stubbed so that a regression fails the assertion instead of
    quietly making the request this test exists to forbid.
    """
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _transport(pytest.fail),  # any request at all is the defect
    )
    cfg = Config()
    cfg.ai_foundry.enabled = True  # byo-key, but no credential anywhere
    assert cfg.service_mode("secrets") == "mock", "the fallback under test is not armed"
    ext = LocalExtensionManager(cfg)

    with pytest.raises(
        ConfigurationError, match="No API key found for the local AI-Foundry 'openai' provider"
    ):
        await ext.invoke_ai_foundry(model="gpt-4o", messages=[])

    # And the attempt is still recorded: the record is taken *before* the call,
    # so a run whose only model call blew up does not read as a run that never
    # made one.
    assert [(c.operation, c.mode) for c in ext._mocks.trace.calls] == [("invoke", "byo-key")]


async def test_a_real_secret_still_resolves_the_key(monkeypatch):
    """The narrowing took the canned value, not the mechanism.

    ``api_key_secret: my_alias`` against a secret that genuinely exists is the
    supported bring-your-own-key route, and it works -- otherwise the refusal
    above would close the hole by removing the feature.
    """
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _transport(handler))
    cfg = Config()
    cfg.ai_foundry.enabled = True
    cfg.ai_foundry.api_key_secret = "my_llm_alias"
    ext = LocalExtensionManager(cfg)
    monkeypatch.setattr(
        ext._secrets_service,
        "fetch_secret_value",
        lambda alias: _resolved(_PLACEHOLDER_KEY if alias == "my_llm_alias" else None),
    )

    await ext.invoke_ai_foundry(model="gpt-4o", messages=[])
    assert captured["auth"] == f"Bearer {_PLACEHOLDER_KEY}"


async def _resolved(value):
    return value


async def test_the_real_ai_call_record_does_not_capture_the_credential(monkeypatch):
    """A wider record widens what gets serialized into run records.

    No credential value may reach any produced artifact.
    ``to_list()`` is literally what ends up in a run record and pasted into a
    bug report, so the assertion is made against that and not against the object.
    """
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _transport(_responds({"choices": [{"message": {"content": "x"}}]})),
    )
    ext = LocalExtensionManager(_byo_key_config())
    await ext.invoke_ai_foundry(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    rendered = json.dumps(ext._mocks.trace.to_list(), default=str)
    assert _PLACEHOLDER_KEY not in rendered
    assert "byo-key" in rendered  # the mode is the point, and it is not sensitive


# ---------------------------------------------------------------------------
# The run record and the human line keep the two counts apart
# ---------------------------------------------------------------------------


def test_run_cmd_counts_mocked_and_real_calls_separately():
    """``mock_call_count`` keeps meaning what its name says.

    A real call is recorded too, so an unfiltered count would grow into
    "recorded calls" -- which would silently restate every existing run
    record, and would make a key named ``mock_call_count`` report calls that
    were not mocked. It is filtered on the mode instead, and the remainder gets
    its own key.
    """
    from perceivo._cli import run_cmd
    from perceivo.runner.types import ExecutionResult

    result = ExecutionResult(
        success=True,
        mock_calls=[
            {"service": "llm", "operation": "invoke", "mode": "mock"},
            {"service": "llm", "operation": "invoke", "mode": "byo-key"},
            {"service": "redis", "operation": "get", "mode": "local"},
        ],
    )
    payload = run_cmd._result_dict(result)
    assert payload["mock_call_count"] == 1
    assert payload["real_call_count"] == 2


def test_run_cmd_reports_real_calls_to_a_human_only_when_there_were_some(capsys):
    """A fully-mocked run prints exactly the line it printed before.

    That is not cosmetic: "0 mock call(s) served" reads as "nothing was faked"
    when it can equally mean "nothing was called", so the real count is the
    disambiguating half and appending it unconditionally would add noise to the
    ~39 corpus runs that never make one.
    """
    from perceivo._cli import run_cmd
    from perceivo.runner.types import ExecutionResult

    def rendered(calls):
        # ``capsys`` rather than ``CliRunner().isolation()``: that context manager
        # yields two values on click 8.1.x and three on later lines, so unpacking a
        # fixed arity ties this assertion about our own output to click's version.
        run_cmd._print_human(ExecutionResult(success=True, mock_calls=calls))
        return capsys.readouterr().out

    mocked_only = rendered([{"service": "llm", "operation": "invoke", "mode": "mock"}])
    assert "RESULT: PASS  (1 mock call(s) served)" in mocked_only
    assert "real call" not in mocked_only

    with_real = rendered(
        [
            {"service": "llm", "operation": "invoke", "mode": "mock"},
            {"service": "llm", "operation": "invoke", "mode": "byo-key"},
        ]
    )
    assert "RESULT: PASS  (1 mock call(s) served, 1 real call(s) made)" in with_real
