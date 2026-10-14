"""Focused additions to the local adapter suite.

Both tests belong to :mod:`tests.unit.test_local_infra_adapters` by subject — they
exercise ``perceivo.local.adapters.secrets`` and
``perceivo.local.adapters.infra`` exactly as the rest of that suite does — and
live here only because that module is already over the 500-line source limit. The helpers are
imported from it rather than duplicated, so there is one definition of
``_Cfg``/``_stub_with_config`` and the stub cannot drift between the two files.

Both pin a *refusal* rather than a return value: an unbound secret id resolving
to ``None`` instead of a ``TypeError`` raised out of the standard library, and
an undeliverable chat message raising instead of reporting itself sent.
"""

import pytest

from perceivo.local.adapters.secrets import LocalSecretsService

from .test_local_infra_adapters import _Cfg, _stub_with_config

# ---------------------------------------------------------------------------
# LocalSecretsService
# ---------------------------------------------------------------------------


async def test_secrets_fetch_of_an_unbound_id_returns_none_rather_than_raising():
    """A ``None`` secret id is "not found here", not a crash.

    An exported bundle carries declared-but-unbound bindings as
    ``"secret_bindings": {"bindings": {"openai_api_key": null}}``. Any caller
    that forwards the binding's *value* as the id therefore hands this adapter a
    ``None``. Without the guard the last line of ``fetch_secret_value`` is
    ``os.environ.get(None)``, which raises ``TypeError: str expected, not
    NoneType`` from inside the standard library, naming neither the secret nor
    the node — and, worse, raising one frame *before*
    ``LocalExtensionContext.fetch_secret_value``'s mock fallback, so that
    fallback can never run.

    Asserted for every source, because the guard has to precede the
    ``self._secrets`` lookup as well as the ``os.environ`` one: ``None in
    self._secrets`` is legal and False, so an unguarded "inline" adapter falls
    through to exactly the same ``os.environ.get(None)``.
    """
    for svc in (
        LocalSecretsService(source="inline", inline={"X": "1"}),
        LocalSecretsService(source="env"),
        LocalSecretsService(source="dotenv", dotenv_path="does-not-exist.env"),
    ):
        assert await svc.fetch_secret_value(None) is None


# ---------------------------------------------------------------------------
# LocalInfraStubs
# ---------------------------------------------------------------------------


async def test_infra_chat_send_refuses_instead_of_reporting_a_noop_as_sent():
    """Unmocked local chat must raise rather than return a success object.

    A result of ``res["status"] == "logged_locally"`` would be success-shaped
    for a message that was never delivered, and no caller could tell it apart
    from a real send.
    """
    stub = _stub_with_config(_Cfg())
    with pytest.raises(RuntimeError, match=r"no egress broker"):
        await stub.send_chat_message(channel="slack", recipient="C1", message="hi")
