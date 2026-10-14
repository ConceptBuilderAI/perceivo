"""``perceivo.types.Credentials``: three constructors, one mode each, secrets masked."""

from __future__ import annotations

import dataclasses

import pytest

from perceivo.types import ConfigurationError, Credentials

SECRETS = ("the-api-key", "ab" * 32, "the-client-secret")


def test_each_constructor_sets_its_mode_and_only_its_fields():
    assert Credentials.api_key(" the-api-key ") == Credentials(mode="api_key", key="the-api-key")
    hmac_creds = Credentials.hmac("the-api-key", "kid", "ab" * 32, audience="aud")
    assert (hmac_creds.mode, hmac_creds.key_id, hmac_creds.audience) == ("hmac", "kid", "aud")
    entra = Credentials.entra("t", "c", federated_token_file="/f", scope="s")
    assert (entra.mode, entra.client_secret, entra.federated_token_file) == ("entra", None, "/f")


def test_credentials_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Credentials.api_key("k").key = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "build",
    [
        lambda: Credentials.api_key(""),
        lambda: Credentials.api_key(None),  # type: ignore[arg-type]
        lambda: Credentials.hmac("k", " ", "s"),
        lambda: Credentials.hmac("k", "i", "s", audience=""),
        lambda: Credentials.entra("t", "c"),
        lambda: Credentials.entra("t", "c", client_secret="s", federated_token_file="/f"),
        lambda: Credentials.entra("", "c", client_secret="s"),
    ],
)
def test_an_incomplete_or_ambiguous_credential_is_refused(build):
    with pytest.raises(ConfigurationError):
        build()


@pytest.mark.parametrize(
    "creds",
    [
        Credentials.api_key("the-api-key"),
        Credentials.hmac("the-api-key", "kid", "ab" * 32),
        Credentials.entra("t", "c", client_secret="the-client-secret"),
    ],
    ids=["api_key", "hmac", "entra"],
)
def test_repr_and_str_mask_every_secret(creds):
    shown = repr(creds) + str(creds)
    assert not [secret for secret in SECRETS if secret in shown]
    assert "<redacted>" in shown
    assert creds.mode in shown


# --- the dataclass init holds the constructors' guarantees -------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"mode": "api_key"}, "key must be a non-empty string"),
        ({"mode": "bogus", "key": "k"}, "mode must be one of"),
        ({"mode": None}, "mode must be one of"),
        ({"mode": "hmac", "key": "k", "key_id": "i"}, "signing_secret"),
        ({"mode": "api_key", "key": "k", "tenant_id": "t"}, "tenant_id cannot be set"),
        (
            {"mode": "hmac", "key": "k", "key_id": "i", "signing_secret": "s", "scope": "x"},
            "scope cannot be set",
        ),
        ({"mode": "entra", "tenant_id": "t", "client_id": "c"}, "exactly one of"),
        (
            {"mode": "entra", "tenant_id": "t", "client_id": "c", "client_secret": "s", "key": "k"},
            "key cannot be set",
        ),
        ({"mode": "entra", "tenant_id": "t", "client_id": "c", "client_secret": ""}, "client_s"),
    ],
)
def test_a_direct_init_is_validated_like_the_constructors(kwargs, match):
    """``Credentials(mode="api_key")`` must not resolve and send ``X-API-Key: None``."""
    with pytest.raises(ConfigurationError, match=match):
        Credentials(**kwargs)


def test_a_direct_init_of_a_complete_credential_is_accepted():
    # Control: the validation refuses shapes, not the direct init itself.
    assert Credentials(mode="api_key", key="k") == Credentials.api_key("k")
    assert Credentials(
        mode="entra", tenant_id="t", client_id="c", federated_token_file="/f"
    ) == Credentials.entra("t", "c", federated_token_file="/f")
    assert dataclasses.replace(Credentials.hmac("k", "i", "s"), audience="a").audience == "a"
