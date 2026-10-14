"""Header discipline, request signing, hygiene, the HMAC size cap and retry safety.

Every assertion reads the request **as captured** by an httpx ``MockTransport``:
the headers and the bytes that would have gone on the wire, not what the SDK
meant to send.
"""

from __future__ import annotations

import hmac
import json
import logging
import sys

import httpx
import pytest

from perceivo._internal.signing import build_canonical_request, compute_signature
from perceivo.orchestration import publish
from perceivo.orchestration.publish import PublishError
from perceivo.types import (
    AccessDeniedError,
    AuthenticationError,
    Credentials,
    ServiceError,
)

from .fixtures_public_api import KEY_ID, SIGNING_SECRET

API_KEY = "pk-live-SECRET-api-key"
CLIENT_SECRET = "entra-SECRET-client-secret"

MODES = {
    "api_key": lambda: Credentials.api_key(API_KEY),
    "hmac": lambda: Credentials.hmac(API_KEY, KEY_ID, SIGNING_SECRET),
    "entra": lambda: Credentials.entra("tenant-1", "client-1", client_secret=CLIENT_SECRET),
}
AUTH_HEADERS = {"authorization", "x-api-key"}


def _perceivo_headers(request: httpx.Request) -> set[str]:
    return {name for name in request.headers if name.lower().startswith("x-perceivo-")}


# --- header discipline ----------------------------------------------------------


async def test_api_key_mode_sends_the_key_and_nothing_else_auth_related(api, project):
    await publish(project, credentials=MODES["api_key"]())
    (sent,) = api.api_requests
    assert sent.headers["X-API-Key"] == API_KEY
    assert sent.headers["Content-Type"] == "application/json"
    assert "authorization" not in sent.headers
    assert _perceivo_headers(sent) == set()
    assert "perceivo-organization" not in sent.headers


async def test_hmac_mode_signs_the_exact_captured_request(api, project):
    await publish(project, credentials=MODES["hmac"]())
    (sent,) = api.api_requests
    assert sent.url.raw_path == b"/v1/orchestrations/import"
    assert sent.headers["X-API-Key"] == API_KEY
    assert _perceivo_headers(sent) == {
        "x-perceivo-key-id",
        "x-perceivo-timestamp",
        "x-perceivo-nonce",
        "x-perceivo-signature",
    }
    assert "authorization" not in sent.headers
    assert "perceivo-organization" not in sent.headers

    def recompute(secret: str) -> str:
        canonical = build_canonical_request(
            audience="perceivo-api-prod",
            key_id=sent.headers["X-Perceivo-Key-Id"],
            method=sent.method,
            raw_path="/v1/orchestrations/import",
            query_string="",
            body=sent.content,  # the CAPTURED bytes
            content_type=sent.headers["Content-Type"],  # the CAPTURED header value
            organization=None,
            timestamp=sent.headers["X-Perceivo-Timestamp"],
            nonce=sent.headers["X-Perceivo-Nonce"],
        )
        assert canonical.split("\n")[8] == ""  # field 9: no organization
        return compute_signature(canonical, secret)

    assert hmac.compare_digest(recompute(SIGNING_SECRET), sent.headers["X-Perceivo-Signature"])
    # The control: the same recomputation under a different secret must differ.
    assert recompute("cd" * 32) != sent.headers["X-Perceivo-Signature"]
    assert sent.headers["X-Perceivo-Key-Id"] == KEY_ID


async def test_entra_mode_sends_only_the_bearer_even_with_an_api_key_in_the_env(
    api, project, monkeypatch
):
    monkeypatch.setenv("PERCEIVO_API_KEY", "env-key-ignored-because-explicit")
    await publish(project, credentials=MODES["entra"]())
    (sent,) = api.api_requests
    assert sent.headers["Authorization"] == f"Bearer {api.minted()}"
    assert set(sent.headers) & AUTH_HEADERS == {"authorization"}
    assert _perceivo_headers(sent) == set()


async def test_the_organization_is_never_sent(api, project):
    for make in MODES.values():
        await publish(project, credentials=make())
    assert all("perceivo-organization" not in r.headers for r in api.api_requests)


# --- hygiene --------------------------------------------------------------------

_SECRETS = (API_KEY, SIGNING_SECRET, CLIENT_SECRET)


def _leaks(text: str, api) -> list[str]:
    tokens = [api.minted(i) for i in range(len(api.token_requests))]
    return [s for s in (*_SECRETS, *tokens) if s in text]


@pytest.mark.parametrize("mode", sorted(MODES))
async def test_no_secret_reaches_a_log_line(api, project, caplog, mode):
    caplog.set_level(logging.DEBUG)
    await publish(project, credentials=MODES[mode]())
    assert _leaks(caplog.text, api) == []


@pytest.mark.parametrize("mode", sorted(MODES))
@pytest.mark.parametrize("status", [401, 403, 500, 503])
async def test_no_secret_reaches_an_error_message(api, project, mode, status):
    api.api = lambda request: httpx.Response(status, json={"error": "x", "reason": "y"})
    creds = MODES[mode]()
    with pytest.raises((AuthenticationError, AccessDeniedError, ServiceError)) as err:
        await publish(project, credentials=creds)
    assert _leaks(str(err.value) + repr(err.value), api) == []
    assert err.value.mode == mode


@pytest.mark.parametrize("mode", sorted(MODES))
def test_the_credentials_repr_masks_every_secret(mode):
    shown = repr(MODES[mode]()) + str(MODES[mode]())
    assert not [s for s in _SECRETS if s in shown]
    assert "<redacted>" in shown


async def test_redirects_are_surfaced_not_followed(api, project):
    api.api = lambda request: httpx.Response(307, headers={"Location": "https://evil.test/x"})
    creds = MODES["api_key"]()
    with pytest.raises(PublishError) as err:
        await publish(project, credentials=creds)
    assert err.value.status_code == 307
    assert [r.url.host for r in api.requests] == ["api.perceivo.ai"]
    assert all(kwargs["follow_redirects"] is False for kwargs in api.client_kwargs)


async def test_a_public_api_path_must_be_v1(api):
    from perceivo._internal import transport
    from perceivo._internal.auth import resolve
    from perceivo.types import ConfigurationError

    auth = resolve(credentials=MODES["api_key"]())
    with pytest.raises(ConfigurationError, match="/v1/"):
        await transport.request(auth, "POST", "/api/v1/orchestrations/import", body=b"{}")
    assert api.requests == []


# --- the HMAC body cap ----------------------------------------------------------

LIMIT = 1_048_576


@pytest.fixture
def sized(monkeypatch):
    """Make the bundle serialize to exactly ``n`` bytes."""
    module = sys.modules["perceivo.orchestration.publish"]
    overhead = len(json.dumps({"p": ""}, separators=(",", ":")))

    def set_size(n: int) -> None:
        monkeypatch.setattr(
            module, "orchestration_to_bundle", lambda path: {"p": "x" * (n - overhead)}
        )

    return set_size


async def test_a_body_of_exactly_one_mebibyte_is_signed_and_sent(api, project, sized):
    sized(LIMIT)
    await publish(project, credentials=MODES["hmac"]())
    assert len(api.api_requests[0].content) == LIMIT


async def test_one_byte_over_is_refused_before_any_io_in_hmac_mode(api, project, sized):
    sized(LIMIT + 1)
    creds = MODES["hmac"]()
    with pytest.raises(PublishError) as err:
        await publish(project, credentials=creds)
    assert api.requests == []
    assert err.value.status_code is None
    assert "1 MiB" in str(err.value)
    assert "API-key and Entra ID" in str(err.value)


@pytest.mark.parametrize("mode", ["api_key", "entra"])
async def test_the_cap_applies_to_hmac_only(api, project, sized, mode):
    sized(LIMIT + 1)
    await publish(project, credentials=MODES[mode]())
    assert len(api.api_requests[0].content) == LIMIT + 1


# --- retry safety -----------------------------------------------------------------

_UNAVAILABLE = {"error": "service_unavailable", "reason": "verification_unavailable"}


async def test_only_the_verification_unavailable_503_is_retryable(api, project):
    api.api = lambda request: httpx.Response(503, json=_UNAVAILABLE, headers={"Retry-After": "2"})
    creds = MODES["hmac"]()
    with pytest.raises(ServiceError) as err:
        await publish(project, credentials=creds)
    assert (err.value.retryable, err.value.retry_after, err.value.status_code) == (True, 2.0, 503)
    assert "safe to retry after 2 seconds" in err.value.remedy
    assert len(api.api_requests) == 1


MUST_NOT_RETRY = [
    pytest.param(502, {"error": "internal_error"}, id="502-internal-error"),
    pytest.param(500, {"error": "internal_error"}, id="500-internal-error"),
    pytest.param(504, {"anything": True}, id="504-any-body"),
    pytest.param(504, None, id="504-no-body"),
    pytest.param(503, {"error": "error", "reason": "invalid_signature"}, id="503-reject-shape"),
    pytest.param(502, _UNAVAILABLE, id="unavailable-body-with-status-502"),
    pytest.param(503, {"error": "service_unavailable"}, id="503-without-reason"),
]


@pytest.mark.parametrize(("status", "body"), MUST_NOT_RETRY)
async def test_every_other_5xx_is_not_retryable_and_says_check_first(api, project, status, body):
    """Must-fail controls: each of these may have been answered after the route ran."""
    api.api = lambda request: (
        httpx.Response(status, json=body, headers={"Retry-After": "2"})
        if body is not None
        else httpx.Response(status, content=b"gateway timeout")
    )
    creds = MODES["api_key"]()
    with pytest.raises(ServiceError) as err:
        await publish(project, credentials=creds)
    assert err.value.retryable is False
    assert err.value.retry_after is None
    assert "check whether the orchestration exists before retrying" in err.value.remedy
    assert len(api.api_requests) == 1  # no internal retry


async def test_a_retryable_503_without_retry_after_has_no_wait(api, project):
    api.api = lambda request: httpx.Response(503, json=_UNAVAILABLE)
    creds = MODES["hmac"]()
    with pytest.raises(ServiceError) as err:
        await publish(project, credentials=creds)
    assert (err.value.retryable, err.value.retry_after) == (True, None)


async def test_a_request_that_does_not_complete_is_a_non_retryable_service_error(api, project):
    def fail(request):
        raise httpx.ReadTimeout("slow", request=request)

    api.api = fail
    creds = MODES["api_key"]()
    with pytest.raises(ServiceError) as err:
        await publish(project, credentials=creds)
    assert err.value.retryable is False
    assert "check whether the orchestration exists" in err.value.remedy
