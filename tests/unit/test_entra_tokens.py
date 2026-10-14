"""Entra ID token acquisition, refresh and cache isolation.

The mock identity platform mints a token that is a digest of the tenant path and
every form field, so a publish carrying the wrong service principal's bearer is
visible as a wrong token, not merely as a count.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from perceivo._internal import _entra
from perceivo.orchestration import publish
from perceivo.types import AuthenticationError, ConfigurationError, Credentials, ServiceError

SCOPE = "api://bf151286-9548-4e40-af09-48523cdfc3e7/.default"


def _bearer(request: httpx.Request) -> str:
    return request.headers["Authorization"].removeprefix("Bearer ")


# --- acquisition -------------------------------------------------------------------


async def test_a_client_secret_token_request_is_a_client_credentials_form_post(api, project):
    await publish(project, credentials=Credentials.entra("tenant-1", "client-1", client_secret="s"))
    (token,) = api.token_requests
    assert token.method == "POST"
    assert str(token.url) == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    assert token.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert api.token_form() == {
        "grant_type": "client_credentials",
        "client_id": "client-1",
        "scope": SCOPE,
        "client_secret": "s",
    }
    assert _bearer(api.api_requests[0]) == api.minted(0)
    assert "x-api-key" not in api.api_requests[0].headers


async def test_a_token_file_is_sent_as_a_jwt_bearer_client_assertion(api, project, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("federated-jwt-1\n", encoding="utf-8")
    creds = Credentials.entra("t", "c", federated_token_file=str(token_file))
    await publish(project, credentials=creds)
    assert api.token_form() == {
        "grant_type": "client_credentials",
        "client_id": "c",
        "scope": SCOPE,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": "federated-jwt-1",
    }


async def test_the_token_file_is_re_read_when_it_changes(api, project, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("jwt-one", encoding="utf-8")
    creds = Credentials.entra("t", "c", federated_token_file=str(token_file))
    api.token_lifetime = 60  # inside the refresh margin: every call refreshes
    await publish(project, credentials=creds)
    token_file.write_text("jwt-two", encoding="utf-8")
    await publish(project, credentials=creds)
    assert [api.token_form(i)["client_assertion"] for i in (0, 1)] == ["jwt-one", "jwt-two"]


async def test_an_unreadable_token_file_is_a_configuration_error(api, project, tmp_path):
    creds = Credentials.entra("t", "c", federated_token_file=str(tmp_path / "missing"))
    creds = creds
    creds = creds
    with pytest.raises(ConfigurationError, match="cannot be read"):
        await publish(project, credentials=creds)
    assert api.requests == []


@pytest.mark.parametrize(
    ("code", "remedy_fragment"),
    [
        ("AADSTS7000215", "client secret is wrong"),
        ("AADSTS700016", "not found in the tenant"),
        ("AADSTS500011", "admin consent"),
    ],
)
async def test_a_refused_token_request_names_the_aadsts_code_and_its_remedy(
    api, project, code, remedy_fragment
):
    api.token = lambda request: httpx.Response(
        400,
        json={"error": "invalid_client", "error_description": f"{code}: details. Trace ID: x"},
    )
    creds = Credentials.entra("t", "c", client_secret="s")
    with pytest.raises(AuthenticationError) as err:
        await publish(project, credentials=creds)
    assert (err.value.mode, err.value.reason, err.value.status_code) == ("entra", code, 400)
    assert remedy_fragment in err.value.remedy
    assert api.api_requests == []  # refused before any Perceivo call


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"error": "invalid_request", "error_codes": [90002]}, "AADSTS90002"),
        ({"error": "invalid_scope"}, "invalid_scope"),
        (None, "http_401"),
    ],
)
async def test_other_token_refusals_still_name_a_reason(api, project, body, reason):
    api.token = lambda request: (
        httpx.Response(401, json=body) if body else httpx.Response(401, content=b"no")
    )
    creds = Credentials.entra("t", "c", client_secret="s")
    with pytest.raises(AuthenticationError) as err:
        await publish(project, credentials=creds)
    assert err.value.reason == reason
    assert err.value.remedy


@pytest.mark.parametrize(
    "answer",
    [
        lambda request: httpx.Response(503, json={}),
        lambda request: httpx.Response(200, json={"expires_in": 60}),
        lambda request: httpx.Response(200, content=b"not json"),
    ],
)
async def test_an_identity_platform_fault_is_a_service_error(api, project, answer):
    api.token = answer
    creds = Credentials.entra("t", "c", client_secret="s")
    with pytest.raises(ServiceError) as err:
        await publish(project, credentials=creds)
    assert (err.value.mode, err.value.retryable) == ("entra", False)
    assert api.api_requests == []


async def test_an_unreachable_identity_platform_is_a_service_error(api, project):
    def fail(request):
        raise httpx.ConnectError("down", request=request)

    api.token = fail
    creds = Credentials.entra("t", "c", client_secret="s")
    creds = creds
    with pytest.raises(ServiceError, match="No request reached"):
        await publish(project, credentials=creds)


async def test_a_token_without_a_numeric_lifetime_is_used_once(api, project):
    api.token = lambda request: httpx.Response(200, json={"access_token": "t1", "expires_in": "x"})
    creds = Credentials.entra("t", "c", client_secret="s")
    await publish(project, credentials=creds)
    await publish(project, credentials=creds)
    assert len(api.token_requests) == 2


# --- cache and refresh ---------------------------------------------------------------


async def test_a_cached_token_is_reused(api, project):
    creds = Credentials.entra("t", "c", client_secret="s")
    await publish(project, credentials=creds)
    await publish(project, credentials=creds)
    assert len(api.token_requests) == 1
    assert _bearer(api.api_requests[0]) == _bearer(api.api_requests[1]) == api.minted(0)


@pytest.mark.parametrize(("lifetime", "requests"), [(300, 2), (301, 1)])
async def test_a_token_is_refreshed_once_300_seconds_or_less_remain(
    api, project, lifetime, requests
):
    api.token_lifetime = lifetime
    creds = Credentials.entra("t", "c", client_secret="s")
    await publish(project, credentials=creds)
    await publish(project, credentials=creds)
    assert len(api.token_requests) == requests


async def test_concurrent_calls_share_one_token_request(api, project):
    api.token = api.slow_mint
    creds = Credentials.entra("t", "c", client_secret="s")
    await asyncio.gather(*(publish(project, credentials=creds) for _ in range(10)))
    assert len(api.token_requests) == 1
    assert {_bearer(r) for r in api.api_requests} == {api.minted(0)}


# --- isolation --------------------------------------------------------------------------


def _pair(tmp_path, differ: str) -> tuple[Credentials, Credentials]:
    base = {"tenant_id": "t", "client_id": "c", "scope": SCOPE, "client_secret": "s1"}
    other = dict(base)
    if differ == "token_file":
        for name, creds in (("a", base), ("b", other)):
            path = tmp_path / name
            path.write_text("same-jwt", encoding="utf-8")
            creds.pop("client_secret")
            creds["federated_token_file"] = str(path)
    else:
        other[differ] = {"tenant_id": "t2", "client_id": "c2", "scope": "api://x/.default"}.get(
            differ, "s2"
        )

    def build(values: dict) -> Credentials:
        values = dict(values)
        return Credentials.entra(values.pop("tenant_id"), values.pop("client_id"), **values)

    return build(base), build(other)


@pytest.mark.parametrize(
    "differ", ["client_id", "tenant_id", "scope", "client_secret", "token_file"]
)
async def test_two_credentials_never_share_a_token(api, project, tmp_path, differ):
    first, second = _pair(tmp_path, differ)
    await publish(project, credentials=first)
    await publish(project, credentials=second)
    assert len(api.token_requests) == 2
    assert [_bearer(r) for r in api.api_requests] == [api.minted(0), api.minted(1)]
    # The first credential again: a cache hit, and it gets ITS token, not the latest one.
    await publish(project, credentials=first)
    assert len(api.token_requests) == 2
    assert _bearer(api.api_requests[2]) == api.minted(0)


def test_the_cache_key_never_contains_the_secret():
    creds = Credentials.entra("t", "c", client_secret="very-secret", scope=SCOPE)
    assert "very-secret" not in repr(_entra.cache_key(creds))


def test_one_credential_across_two_event_loops_uses_one_token(api, project):
    creds = Credentials.entra("t", "c", client_secret="s")
    asyncio.run(publish(project, credentials=creds))
    asyncio.run(publish(project, credentials=creds))
    assert len(api.token_requests) == 1


def test_a_contended_refresh_works_in_a_second_event_loop(api, project):
    """The control for a lock created once and reused: it binds to the first loop
    that contends it, and the next loop's contended refresh then raises."""
    api.token = api.slow_mint
    api.token_lifetime = 60  # always refresh
    creds = Credentials.entra("t", "c", client_secret="s")

    async def burst() -> None:
        await asyncio.gather(*(publish(project, credentials=creds) for _ in range(3)))

    asyncio.run(burst())
    asyncio.run(burst())
    assert len(api.api_requests) == 6
